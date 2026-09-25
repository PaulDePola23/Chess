"""Game functions for the browser UI, returning plain JSON-friendly dicts.

The web page keeps the game as a list of UCI moves and asks for three things:
the state of the game after those moves (legal moves, notation, result), the
engine's reply, and, once the game is over, a review of each of the player's
moves. ``chessbot serve`` exposes these over HTTP; any other front end can call
them directly.
"""

from __future__ import annotations

import math
from collections.abc import Callable

import chess
import chess.pgn

from .levels import choose_move, get_level
from .openings import opening_name
from .search import MATE_SCORE, MATE_THRESHOLD, Searcher, SearchResult

MIN_THINK_TIME = 0.05
MAX_THINK_TIME = 30.0

# How deep the post-game review searches each position. Depth 3 plus the
# quiescence search catches hanging pieces and short tactics, and keeps a
# whole game's review to a few seconds even in the browser.
REVIEW_DEPTH = 3

# Drops in winning chances (on a -1..1 scale) that make a move an
# inaccuracy, a mistake or a blunder. These are the thresholds lichess uses.
VERDICTS = [(0.3, "blunder"), (0.2, "mistake"), (0.1, "inaccuracy")]

# How hard the engine thinks about a hint.
HINT_NODES = 20_000

# Piece values for spotting pieces that can be taken for free.
_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}


def _board_from(moves: list[str], fen: str | None = None) -> chess.Board:
    if not isinstance(moves, list) or not all(isinstance(m, str) for m in moves):
        raise ValueError("moves must be a list of UCI strings")
    board = chess.Board(fen) if fen else chess.Board()
    for uci in moves:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            raise ValueError(f"not a UCI move: {uci!r}") from None
        if move not in board.legal_moves:
            raise ValueError(f"illegal move {uci} in position {board.fen()}")
        board.push(move)
    return board


def _pgn(board: chess.Board) -> str:
    game = chess.pgn.Game.from_board(board)
    game.headers["Event"] = "Casual game"
    game.headers["Site"] = "ChessBot web"
    for tag in ("Date", "Round", "White", "Black"):
        game.headers.pop(tag, None)
    return str(game)


def hanging_pieces(board: chess.Board, color: chess.Color) -> list[str]:
    """Squares of ``color``'s pieces that the other side can win: attacked and
    either undefended or attacked by something cheaper. (A simple count that
    ignores pins and exchanges further down the line; good enough to coach.)"""
    squares = []
    for square, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        attackers = board.attackers(not color, square)
        if not attackers:
            continue
        cheapest = min(_VALUES[board.piece_type_at(attacker)] for attacker in attackers)
        if not board.attackers(color, square) or cheapest < _VALUES[piece.piece_type]:
            squares.append(chess.square_name(square))
    return sorted(squares)


def game_state(moves: list[str], fen: str | None = None) -> dict:
    """Describe the position after ``moves`` (played from ``fen``, default the start position)."""
    board = _board_from(moves, fen)
    replay = chess.Board(fen) if fen else chess.Board()
    san = []
    for move in board.move_stack:
        san.append(replay.san(move))
        replay.push(move)

    outcome = board.outcome(claim_draw=True)
    king = board.king(board.turn)
    return {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "legal": [move.uci() for move in board.legal_moves],
        "legal_san": [board.san(move) for move in board.legal_moves],
        "san": san,
        "check": chess.square_name(king) if board.is_check() and king is not None else None,
        "last": board.peek().uci() if board.move_stack else None,
        "over": outcome is not None,
        "result": outcome.result() if outcome else None,
        "reason": outcome.termination.name.replace("_", " ").lower() if outcome else None,
        "pgn": _pgn(board),
        "hanging": {"white": hanging_pieces(board, chess.WHITE), "black": hanging_pieces(board, chess.BLACK)},
        "opening": None if fen else opening_name(board),
    }


def winning_chances(score: int) -> float:
    """Map a score for the side to move onto -1 (lost) .. 1 (won), as lichess does."""
    if score >= MATE_THRESHOLD:
        return 1.0
    if score <= -MATE_THRESHOLD:
        return -1.0
    score = max(-1000, min(1000, score))
    return 2 / (1 + math.exp(-0.00368208 * score)) - 1


def move_accuracy(before: int, after: int) -> float:
    """Accuracy of a move, 0-100, from the mover's score before and after it (lichess's formula)."""
    win_before = 50 + 50 * winning_chances(before)
    win_after = 50 + 50 * winning_chances(after)
    if win_after >= win_before:
        return 100.0
    accuracy = 103.1668100711649 * math.exp(-0.04354415386753951 * (win_before - win_after)) - 3.166924740191411
    return max(0.0, min(100.0, accuracy))


def _score_dict(score: int, mover: chess.Color) -> dict:
    """A score for ``mover`` as {"score": centipawns, "mate": moves} from White's point of view."""
    sign = 1 if mover == chess.WHITE else -1
    if score >= MATE_THRESHOLD:
        return {"score": None, "mate": sign * ((MATE_SCORE - score + 1) // 2)}
    if score <= -MATE_THRESHOLD:
        return {"score": None, "mate": -sign * ((MATE_SCORE + score) // 2)}
    return {"score": sign * score, "mate": None}


def _white_pov(result: SearchResult, board: chess.Board) -> tuple[int | None, int | None]:
    """(centipawns, mate-in) from White's point of view; one of them is None."""
    sign = 1 if board.turn == chess.WHITE else -1
    if result.mate_in is not None:
        return None, sign * result.mate_in
    return sign * result.score, None


def engine_reply(
    moves: list[str],
    think_time: float | None,
    searcher: Searcher,
    fen: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
    level: int | None = None,
) -> dict:
    """Let the engine choose a move after ``moves`` and return it with the new game state.

    The engine plays at play ``level`` (see ``chessbot.levels``) if one is
    given, and otherwise at full strength for ``think_time`` seconds.
    ``on_progress`` receives a small dict after each completed search depth,
    for showing the engine's thinking as it happens.
    """
    board = _board_from(moves, fen)
    if board.is_game_over(claim_draw=True):
        raise ValueError("the game is already over")

    def summary(result: SearchResult) -> dict:
        score, mate = _white_pov(result, board)
        return {
            "depth": result.depth,
            "score": score,
            "mate": mate,
            "nodes": result.nodes,
            "time": round(result.elapsed, 3),
            "pv": board.variation_san(result.pv) if result.pv else "",
            "book": result.book,
        }

    callback = (lambda result: on_progress(summary(result))) if on_progress else None
    if level is not None:
        if not isinstance(level, int):
            raise ValueError("level must be a whole number")
        result = choose_move(board, get_level(level), searcher, on_iteration=callback)
    else:
        think_time = min(max(float(think_time or 1.5), MIN_THINK_TIME), MAX_THINK_TIME)
        result = searcher.search(board, time_limit=think_time, on_iteration=callback)
    move = result.best_move
    reply = summary(result)
    reply["move"] = move.uci()
    reply["san"] = board.san(move)
    return {"reply": reply, "state": game_state([*moves, move.uci()], fen)}


def review_move(
    moves: list[str],
    ply: int,
    searcher: Searcher,
    fen: str | None = None,
    depth: int = REVIEW_DEPTH,
) -> dict:
    """Judge ``moves[ply]``: how much it lost compared with the engine's choice.

    Searches the position before the move, and, if the move was not the
    engine's choice, the position after it, one ply shallower so that both
    scores come from the same horizon. Scores are reported from White's point
    of view; ``loss`` is the drop in the mover's winning chances (0..2).
    """
    if not isinstance(ply, int) or not 0 <= ply < len(moves):
        raise ValueError(f"ply must be between 0 and {len(moves) - 1}")
    board = _board_from(moves[: ply + 1], fen)
    played = board.pop()
    mover = board.turn
    san = board.san(played)

    best = searcher.search(board, depth=depth)
    before = best.score
    if played == best.best_move:
        after = before
    else:
        board.push(played)
        if board.is_checkmate():
            after = MATE_SCORE - 1
        elif board.is_game_over(claim_draw=True):
            after = 0
        else:
            after = -searcher.search(board, depth=max(depth - 1, 1)).score
            # Mate distances were counted from the position after the move.
            if after >= MATE_THRESHOLD:
                after -= 1
            elif after <= -MATE_THRESHOLD:
                after += 1
        board.pop()

    loss = max(0.0, winning_chances(before) - winning_chances(after)) if played != best.best_move else 0.0
    verdict = next((name for threshold, name in VERDICTS if loss >= threshold), None)
    return {
        "ply": ply,
        "number": board.fullmove_number,
        "color": "white" if mover == chess.WHITE else "black",
        "fen": board.fen(),
        "uci": played.uci(),
        "san": san,
        "best_uci": best.best_move.uci(),
        "best_san": board.san(best.best_move),
        "line": board.variation_san(best.pv[:4]) if best.pv else board.san(best.best_move),
        "before": _score_dict(before, mover),
        "after": _score_dict(after, mover),
        "loss": round(loss, 3),
        "accuracy": round(move_accuracy(before, after), 1),
        "verdict": verdict,
    }


def replay_game(moves: list[str], fen: str | None = None) -> dict:
    """Every position of a game, for stepping through it: {"fens", "san", "checks", "opening"}.

    ``fens[0]`` is the starting position and ``fens[i]`` the position after
    ``moves[:i]``; ``checks[i]`` is the checked king's square there, if any.
    """
    board = _board_from([], fen)
    fens, san, checks = [board.fen()], [], [None]
    for uci in moves:
        move = chess.Move.from_uci(uci) if isinstance(uci, str) else None
        if move is None or move not in board.legal_moves:
            raise ValueError(f"illegal move {uci!r} in position {board.fen()}")
        san.append(board.san(move))
        board.push(move)
        fens.append(board.fen())
        king = board.king(board.turn)
        checks.append(chess.square_name(king) if board.is_check() and king is not None else None)
    return {"fens": fens, "san": san, "checks": checks, "opening": None if fen else opening_name(board)}


def suggest_move(moves: list[str], searcher: Searcher, fen: str | None = None, nodes: int = HINT_NODES) -> dict:
    """A strong move for the side to move after ``moves``, for the hint button."""
    board = _board_from(moves, fen)
    if board.is_game_over(claim_draw=True):
        raise ValueError("the game is already over")
    result = searcher.search(board, nodes=nodes)
    return {"move": result.best_move.uci(), "san": board.san(result.best_move)}
