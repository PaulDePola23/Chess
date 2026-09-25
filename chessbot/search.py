"""Game-tree search: pick the best move for the side to move.

The engine is a classic alpha-beta searcher:

* iterative deepening, so there is always a best move ready when time runs out;
* negamax with principal variation search (PVS);
* a transposition table, so positions reached by different move orders are
  only searched once;
* quiescence search on captures, so the static evaluation is never taken in
  the middle of an exchange;
* move ordering (hash move, MVV-LVA captures, killer moves, history heuristic),
  which makes alpha-beta cut off far more of the tree;
* null-move pruning, late move reductions and check extensions.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import chess

from .evaluation import PIECE_VALUES, evaluate

MATE_SCORE = 100_000
# Scores beyond this are "mate in N"; anything a real evaluation returns is far smaller.
MATE_THRESHOLD = MATE_SCORE - 1_000
INFINITY = 1_000_000
MAX_PLY = 100

# Transposition table entry flags.
EXACT, LOWER_BOUND, UPPER_BOUND = 0, 1, 2

# How often (in nodes) to look at the clock and the stop flag.
CHECK_INTERVAL = 1024


class SearchAborted(Exception):
    """Raised inside the search when time is up or a stop was requested."""


@dataclass
class SearchResult:
    """Outcome of a search, also reported after each completed iteration."""

    best_move: chess.Move | None
    score: int
    depth: int
    nodes: int
    elapsed: float
    pv: list[chess.Move] = field(default_factory=list)

    @property
    def mate_in(self) -> int | None:
        """Moves until mate: positive if the side to move mates, negative if it gets mated."""
        if self.score >= MATE_THRESHOLD:
            return (MATE_SCORE - self.score + 1) // 2
        if self.score <= -MATE_THRESHOLD:
            return -((MATE_SCORE + self.score) // 2)
        return None


def _position_key(board: chess.Board) -> int:
    # A hash of the position (pieces, side to move, castling rights and en
    # passant square). python-chess builds the underlying tuple for its own
    # repetition detection; hashing it is ~20x faster than computing a Zobrist
    # hash in Python, and storing an int keeps the hash table small.
    return hash(board._transposition_key())


def _victim_value(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        return PIECE_VALUES[chess.PAWN]
    victim = board.piece_type_at(move.to_square)
    return PIECE_VALUES[victim] if victim else 0


class Searcher:
    """A chess engine. Keep one instance per game so the hash table carries over between moves."""

    def __init__(self, hash_entries: int = 300_000):
        self.hash_entries = hash_entries
        self.tt: dict[int, tuple] = {}
        self.new_game()

    def new_game(self) -> None:
        """Forget everything learned from the previous game."""
        self.tt.clear()
        self.history = [[[0] * 64 for _ in range(64)] for _ in chess.COLORS]

    # ------------------------------------------------------------------ API

    def search(
        self,
        board: chess.Board,
        *,
        depth: int | None = None,
        time_limit: float | None = None,
        nodes: int | None = None,
        stop_event: threading.Event | None = None,
        on_iteration: Callable[[SearchResult], None] | None = None,
    ) -> SearchResult:
        """Search ``board`` and return the best move found.

        ``depth`` caps the iterative deepening depth, ``time_limit`` (seconds)
        and ``nodes`` bound the effort, and ``stop_event`` aborts the search
        from another thread. With no limits at all the search runs until
        ``stop_event`` is set or ``MAX_PLY`` is reached. ``on_iteration`` is
        called after each completed depth, e.g. to print UCI ``info`` lines.
        The caller's board is never modified.
        """
        start = time.monotonic()
        self.board = board.copy()
        self.nodes = 0
        self.stop_event = stop_event
        self.deadline = start + time_limit if time_limit is not None else None
        self.node_limit = nodes
        self.killers = [[None, None] for _ in range(MAX_PLY + 2)]
        self.pv_table: list[list[chess.Move]] = [[] for _ in range(MAX_PLY + 2)]
        self.keys = self._game_history_keys()
        if len(self.tt) >= self.hash_entries:
            self.tt.clear()

        legal_moves = list(self.board.legal_moves)
        if not legal_moves:
            score = -MATE_SCORE if self.board.is_check() else 0
            return SearchResult(None, score, 0, 0, time.monotonic() - start)

        max_depth = min(depth, MAX_PLY) if depth else MAX_PLY
        result = SearchResult(legal_moves[0], 0, 0, 0, 0.0, [legal_moves[0]])
        if len(legal_moves) == 1 and time_limit is not None:
            # Nothing to think about; don't waste the clock.
            return result

        for current_depth in range(1, max_depth + 1):
            # Always finish depth 1 so that we have a sensible move to play.
            self.can_abort = current_depth > 1
            self.root_best: tuple[chess.Move, int] | None = None
            try:
                score = self._negamax(current_depth, -INFINITY, INFINITY, 0)
            except SearchAborted:
                # A move that was fully searched and beat the previous best at
                # this depth is still better than last iteration's choice.
                if self.root_best is not None:
                    move, score = self.root_best
                    if move != result.best_move:
                        result = SearchResult(move, score, result.depth, self.nodes, 0.0, [move])
                break

            pv = self.pv_table[0] or [self.root_best[0]]
            result = SearchResult(pv[0], score, current_depth, self.nodes, time.monotonic() - start, list(pv))
            if on_iteration:
                on_iteration(result)

            # A forced mate within the searched depth cannot get any shorter.
            if abs(score) >= MATE_THRESHOLD and MATE_SCORE - abs(score) <= current_depth:
                break
            # The next iteration takes several times longer than this one, so
            # don't start it if it is unlikely to finish in time.
            if self.deadline is not None and time.monotonic() - start > (self.deadline - start) * 0.5:
                break
            if self.node_limit is not None and self.nodes >= self.node_limit:
                break

        result.nodes = self.nodes
        result.elapsed = time.monotonic() - start
        return result

    # ------------------------------------------------------------- internals

    def _game_history_keys(self) -> list[int]:
        """Position keys since the last capture or pawn move, oldest first, for repetition detection."""
        board = self.board.copy()
        keys = [_position_key(board)]
        for _ in range(min(board.halfmove_clock, len(board.move_stack))):
            board.pop()
            keys.append(_position_key(board))
        keys.reverse()
        return keys

    def _is_repetition(self) -> bool:
        keys = self.keys
        current = keys[-1]
        # Only positions with the same side to move, at least four plies ago,
        # and after the last irreversible move can repeat the current one.
        last = min(self.board.halfmove_clock, len(keys) - 1)
        for back in range(4, last + 1, 2):
            if keys[-1 - back] == current:
                return True
        return False

    def _check_limits(self) -> None:
        if not self.can_abort:
            return
        if self.stop_event is not None and self.stop_event.is_set():
            raise SearchAborted
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise SearchAborted
        if self.node_limit is not None and self.nodes >= self.node_limit:
            raise SearchAborted

    def _push(self, move: chess.Move) -> None:
        self.board.push(move)
        self.keys.append(_position_key(self.board))

    def _pop(self) -> None:
        self.board.pop()
        self.keys.pop()

    def _order_moves(self, moves: list[chess.Move], tt_move: chess.Move | None, ply: int) -> list[chess.Move]:
        board = self.board
        killers = self.killers[ply]
        history = self.history[board.turn]

        def score(move: chess.Move) -> int:
            if move == tt_move:
                return 10_000_000
            if board.is_capture(move):
                # Most valuable victim, least valuable attacker.
                attacker = board.piece_type_at(move.from_square)
                return 1_000_000 + 10 * _victim_value(board, move) - PIECE_VALUES[attacker] // 10
            if move.promotion == chess.QUEEN:
                return 900_000
            if move == killers[0]:
                return 800_000
            if move == killers[1]:
                return 700_000
            return history[move.from_square][move.to_square]

        moves.sort(key=score, reverse=True)
        return moves

    def _negamax(self, depth: int, alpha: int, beta: int, ply: int, allow_null: bool = True) -> int:
        self.nodes += 1
        if self.nodes % CHECK_INTERVAL == 0:
            self._check_limits()

        board = self.board
        self.pv_table[ply] = []
        is_pv_node = beta - alpha > 1

        if ply > 0:
            if board.halfmove_clock >= 100 or self._is_repetition():
                return 0
            # Mate distance pruning: no line from here can beat a mate we already found.
            alpha = max(alpha, -MATE_SCORE + ply)
            beta = min(beta, MATE_SCORE - ply - 1)
            if alpha >= beta:
                return alpha
            if ply >= MAX_PLY:
                return evaluate(board)

        in_check = board.is_check()
        if in_check:
            depth += 1  # Don't stop searching in the middle of a checking sequence.
        if depth <= 0:
            return self._quiescence(alpha, beta, ply)

        key = self.keys[-1]
        entry = self.tt.get(key)
        tt_move = None
        if entry is not None:
            entry_depth, entry_score, entry_flag, tt_move = entry
            if ply > 0 and not is_pv_node and entry_depth >= depth:
                entry_score = self._score_from_tt(entry_score, ply)
                if entry_flag == EXACT:
                    return entry_score
                if entry_flag == LOWER_BOUND and entry_score >= beta:
                    return entry_score
                if entry_flag == UPPER_BOUND and entry_score <= alpha:
                    return entry_score

        # Null move pruning: if we are so far ahead that even passing keeps us
        # above beta, a real move will too. Skipped when in check and in pawn
        # endgames, where zugzwang makes passing a genuine advantage.
        if (
            allow_null
            and not is_pv_node
            and not in_check
            and depth >= 3
            and abs(beta) < MATE_THRESHOLD
            and board.occupied_co[board.turn] & ~(board.pawns | board.kings)
            and evaluate(board) >= beta
        ):
            reduction = 3 if depth >= 6 else 2
            self._push(chess.Move.null())
            try:
                score = -self._negamax(depth - 1 - reduction, -beta, -beta + 1, ply + 1, allow_null=False)
            finally:
                self._pop()
            if score >= beta:
                return beta

        moves = list(board.legal_moves)
        if not moves:
            return -MATE_SCORE + ply if in_check else 0
        self._order_moves(moves, tt_move, ply)

        original_alpha = alpha
        best_score = -INFINITY
        best_move = None
        for index, move in enumerate(moves):
            quiet = not board.is_capture(move) and not move.promotion
            self._push(move)
            try:
                if index == 0:
                    score = -self._negamax(depth - 1, -beta, -alpha, ply + 1)
                else:
                    # Late move reductions: moves ordered late are rarely best,
                    # so search them shallower first and only re-search the
                    # ones that turn out to be interesting.
                    reduction = 0
                    if depth >= 3 and index >= 3 and quiet and not in_check and not board.is_check():
                        reduction = 2 if index >= 8 and depth >= 5 else 1
                    score = -self._negamax(depth - 1 - reduction, -alpha - 1, -alpha, ply + 1)
                    if score > alpha and (reduction or score < beta):
                        score = -self._negamax(depth - 1, -beta, -alpha, ply + 1)
            finally:
                self._pop()

            if score > best_score:
                best_score = score
                best_move = move
                if score > alpha:
                    alpha = score
                    self.pv_table[ply] = [move] + self.pv_table[ply + 1]
                    if ply == 0:
                        self.root_best = (move, score)
                    if alpha >= beta:
                        if quiet:
                            self._record_quiet_cutoff(move, depth, ply)
                        break

        if best_score >= beta:
            flag = LOWER_BOUND
        elif best_score > original_alpha:
            flag = EXACT
        else:
            flag = UPPER_BOUND
        # Once the table is full, only refresh positions already in it, so a
        # long analysis can't use up all the memory.
        if len(self.tt) < self.hash_entries or key in self.tt:
            self.tt[key] = (depth, self._score_to_tt(best_score, ply), flag, best_move)
        return best_score

    def _quiescence(self, alpha: int, beta: int, ply: int) -> int:
        """Search captures only, until the position is quiet enough to evaluate."""
        self.nodes += 1
        if self.nodes % CHECK_INTERVAL == 0:
            self._check_limits()

        board = self.board
        self.pv_table[ply] = []
        if ply >= MAX_PLY:
            return evaluate(board)

        in_check = board.is_check()
        if in_check:
            # No "stand pat" when in check: every evasion has to be tried.
            best_score = -MATE_SCORE + ply
            moves = list(board.legal_moves)
            if not moves:
                return best_score
        else:
            best_score = evaluate(board)
            if best_score >= beta:
                return best_score
            alpha = max(alpha, best_score)
            moves = [m for m in board.generate_legal_moves() if board.is_capture(m) or m.promotion == chess.QUEEN]
        self._order_moves(moves, None, ply)

        for move in moves:
            if not in_check and not move.promotion:
                # Delta pruning: even winning this piece for free can't raise alpha.
                if best_score + _victim_value(board, move) + 200 <= alpha:
                    continue
            self._push(move)
            try:
                score = -self._quiescence(-beta, -alpha, ply + 1)
            finally:
                self._pop()
            if score > best_score:
                best_score = score
                if score > alpha:
                    alpha = score
                    self.pv_table[ply] = [move] + self.pv_table[ply + 1]
                    if alpha >= beta:
                        break
        return best_score

    def _record_quiet_cutoff(self, move: chess.Move, depth: int, ply: int) -> None:
        killers = self.killers[ply]
        if move != killers[0]:
            killers[1] = killers[0]
            killers[0] = move
        history = self.history[self.board.turn]
        history[move.from_square][move.to_square] += depth * depth
        if history[move.from_square][move.to_square] > 500_000:
            # Keep history scores below the killer/capture ranges.
            for row in history:
                for i in range(64):
                    row[i] //= 2

    # Mate scores are stored relative to the node, not the root, so that a
    # position reached at a different ply still reports the right distance.
    @staticmethod
    def _score_to_tt(score: int, ply: int) -> int:
        if score >= MATE_THRESHOLD:
            return score + ply
        if score <= -MATE_THRESHOLD:
            return score - ply
        return score

    @staticmethod
    def _score_from_tt(score: int, ply: int) -> int:
        if score >= MATE_THRESHOLD:
            return score - ply
        if score <= -MATE_THRESHOLD:
            return score + ply
        return score


def time_budget(
    time_left: float, increment: float = 0.0, moves_to_go: int | None = None, overhead: float = 0.05
) -> float:
    """How many seconds to spend on this move, given the clock."""
    moves = moves_to_go if moves_to_go else 30
    budget = time_left / moves + increment * 0.75
    # Never risk flagging, however the clock is configured.
    budget = min(budget, time_left * 0.5 - overhead)
    return max(budget, 0.01)
