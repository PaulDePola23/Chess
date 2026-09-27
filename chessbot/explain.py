"""Plain-language reasons for a chess move, for the Play tab's "Why this move?" panel.

``explain_move`` looks at a move the way a coach would: what it does straight
away (captures, checks, forks, pins, discovered attacks, threats, saving or
defending a piece, castling, development, the centre, passed pawns, open
files), what it leads to (a short search shows whether the line wins or
loses material, or mates), and how it changes each part of the engine's
evaluation (``chessbot.evaluation.evaluation_terms``).
"""

from __future__ import annotations

import chess

from .evaluation import TERM_NAMES, evaluation_terms
from .search import Searcher

EXPLAIN_NODES = 5_000  # the short search behind "wins a knight"
THREAT_NODES = 1_000  # and the one behind "threatens mate"
PV_PLIES = 8  # how far along that search's main line to count material
MAX_REASONS = 4

VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
NAMES = {
    chess.PAWN: "pawn",
    chess.KNIGHT: "knight",
    chess.BISHOP: "bishop",
    chess.ROOK: "rook",
    chess.QUEEN: "queen",
    chess.KING: "king",
}
CENTRE = chess.BB_D4 | chess.BB_E4 | chess.BB_D5 | chess.BB_E5
# How each part of the evaluation reads when a move improves or worsens it (for the mover).
TERM_WORDS = {
    "King safety": ("Shelters the king better", "Weakens the pawns in front of the king"),
    "Pawn structure": ("Improves the pawn structure", "Weakens the pawn structure"),
    "Piece activity": ("Activates its pieces", "Makes its pieces less active"),
    "Rooks": ("Gives a rook an open file", "Takes a rook off an open file"),
}
TERM_THRESHOLD = 20  # centipawns before a change is worth mentioning

# Reasons are ranked; the top MAX_REASONS are shown, most important first.
# A tactic comes before what it wins ("Forks the king and rook", then "Wins a rook").
MATE, TACTIC, MATERIAL, THREAT, SAFETY, CHECK, POSITIONAL, TERM = 100, 90, 80, 70, 60, 50, 40, 30


def hanging_pieces(board: chess.Board, color: chess.Color) -> list[str]:
    """Squares of ``color``'s pieces that the other side can win: attacked and
    either undefended or attacked by something cheaper. (A simple count that
    ignores pins and exchanges further down the line; good enough to coach.)"""
    return sorted(chess.square_name(square) for square in _hanging(board, color))


def _hanging(board: chess.Board, color: chess.Color) -> set[int]:
    squares = set()
    for square, piece in board.piece_map().items():
        if piece.color != color or piece.piece_type == chess.KING:
            continue
        attackers = board.attackers(not color, square)
        if not attackers:
            continue
        cheapest = min(VALUES[board.piece_type_at(attacker)] for attacker in attackers)
        if not board.attackers(color, square) or cheapest < VALUES[piece.piece_type]:
            squares.add(square)
    return squares


def _material_words(pawns: int, gained: list[int], lost: list[int]) -> str:
    if sorted(gained) == [chess.ROOK] and sorted(lost) in ([chess.KNIGHT], [chess.BISHOP]):
        return "the exchange (a rook for a minor piece)"
    if len(gained) == 1 and not lost and gained[0] != chess.PAWN:
        return f"a {NAMES[gained[0]]}"
    words = {1: "a pawn", 2: "two pawns", 3: "a piece", 4: "a piece and a pawn", 5: "a rook", 6: "a rook and a pawn"}
    words.update({8: "a rook and a piece", 9: "a queen", 10: "a queen and a pawn"})
    return words.get(pawns, f"{pawns} pawns' worth of material")


def _line_material(board: chess.Board, line: list[chess.Move], mover: chess.Color) -> tuple[int, list[int], list[int]]:
    """Net material (pawn units) ``mover`` wins along ``line``, and the piece types won and lost."""
    board = board.copy(stack=False)
    gained, lost = [], []
    net = 0
    for move in line:
        if move not in board.legal_moves:
            break
        captured = chess.PAWN if board.is_en_passant(move) else board.piece_type_at(move.to_square)
        side = board.turn
        change = 0
        if captured:
            change += VALUES[captured]
            (gained if side == mover else lost).append(captured)
        if move.promotion:
            change += VALUES[move.promotion] - VALUES[chess.PAWN]
        net += change if side == mover else -change
        board.push(move)
    return net, gained, lost


def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _square_text(board: chess.Board, square: int) -> str:
    return f"{NAMES[board.piece_type_at(square)]} on {chess.square_name(square)}"


def explain_move(board: chess.Board, move: chess.Move, nodes: int = EXPLAIN_NODES) -> dict:
    """Why ``move`` is good or bad in ``board``.

    Returns the move's SAN, up to four reasons ({"kind": "good" | "bad" |
    "neutral", "text"}), the short search's score after the move (White's
    side, in centipawns, or "mate" in moves, positive when White mates), and
    the evaluation's parts after the move (White's side) with how much each
    changed for the side that moved.
    """
    if move not in board.legal_moves:
        raise ValueError(f"{move.uci()} is not a legal move here")
    mover, them = board.turn, not board.turn
    sign = 1 if mover == chess.WHITE else -1
    piece = board.piece_at(move.from_square)
    name = NAMES[piece.piece_type]
    after = board.copy()
    after.push(move)
    reasons: list[tuple[int, str, str]] = []  # (rank, kind, text)

    def add(rank: int, kind: str, text: str) -> None:
        if all(text != existing for _, _, existing in reasons):
            reasons.append((rank, kind, text))

    terms_before = evaluation_terms(board)
    terms_after = evaluation_terms(after)
    terms = [
        {
            "name": term,
            "value": round(terms_after[term]),
            "change": round(sign * (terms_after[term] - terms_before[term])),
        }
        for term in TERM_NAMES
    ]
    outcome = {"san": board.san(move), "move": move.uci(), "terms": terms}
    if after.is_checkmate():
        return {**outcome, "reasons": [{"kind": "good", "text": "Checkmate."}], "score": None, "mate": None}
    if after.is_stalemate():
        add(MATE, "neutral", "Stalemate: the game ends in a draw.")

    # What a short search says the move leads to.
    score = mate = None
    line: list[chess.Move] = []
    if not after.is_game_over():
        result = Searcher().search(after, nodes=nodes)
        line = result.pv[:PV_PLIES]
        if result.mate_in is not None:
            mate = -result.mate_in  # moves to mate, positive when the mover delivers it
            if mate > 0:
                add(MATE, "good", f"Forces checkmate in {mate}." if mate > 1 else "Threatens checkmate next move.")
            else:
                add(MATE + 1, "bad", f"Allows checkmate in {-mate}.")
        else:
            score = -result.score  # for the mover
    net, gained, lost = _line_material(board, [move, *line], mover)
    hanging_before = _hanging(board, mover)
    hanging_after = _hanging(after, mover)
    newly_hanging = hanging_after - (hanging_before - {move.from_square})
    captured = chess.PAWN if board.is_en_passant(move) else board.piece_type_at(move.to_square)
    if captured and net >= 0:
        newly_hanging.discard(move.to_square)  # an exchange: the capturer is meant to be taken back
    loses_material = False
    if net >= 1:
        add(MATERIAL, "good", f"Wins {_material_words(net, gained, lost)}.")
    elif net <= -1 and (mate is None or mate < 0):
        given = _material_words(-net, lost, gained)
        # How much the move cost, judged against the static evaluation before it:
        # a small drop is a sacrifice with something in return, a big one a blunder.
        drop = sign * sum(terms_before.values()) - score if score is not None else 0
        if score is not None and drop <= 80:
            add(MATERIAL, "neutral", f"Gives up {given} for activity and attack.")
        elif score is not None and drop <= 150:
            add(MATERIAL, "neutral", f"Gives up {given}.")
        else:
            loses_material = True
            if newly_hanging:
                square = max(newly_hanging, key=lambda sq: VALUES[after.piece_type_at(sq)])
                add(MATERIAL + 5, "bad", f"Leaves the {_square_text(after, square)} to be taken.")
            else:
                add(MATERIAL + 5, "bad", f"Loses {given}.")

    # Tactics by the piece that moved: forks and threats.
    if move.promotion:
        add(MATERIAL + 2, "good", f"Promotes to a {NAMES[move.promotion]}.")
    safe_landing = move.to_square not in hanging_after
    targets = []
    for square in after.attacks(move.to_square) & after.occupied_co[them]:
        target = after.piece_type_at(square)
        valuable = target == chess.KING or VALUES[target] > VALUES[piece.piece_type]
        loose = target != chess.PAWN and not after.attackers(them, square)
        if valuable or loose:
            targets.append(target)
    targets.sort(key=lambda t: -VALUES[t])
    forks_king = False
    if len(targets) >= 2 and safe_landing:
        forks_king = targets[0] == chess.KING
        add(TACTIC + 2, "good", f"Forks the {_join([NAMES[t] for t in targets[:3]])}.")
    attacked = {targets[0]} if len(targets) == 1 and targets[0] != chess.KING and safe_landing else set()

    # Pins against the king (of pieces, not pawns), and attacks uncovered by moving out of the way.
    pinned = set()
    for square in chess.scan_forward(after.occupied_co[them] & ~after.kings & ~after.pawns):
        if after.is_pinned(them, square) and not board.is_pinned(them, square):
            pinned.add(after.piece_type_at(square))
            add(TACTIC + 1, "good", f"Pins the {NAMES[after.piece_type_at(square)]} to the king.")
    for square in chess.scan_forward(after.occupied_co[them] & (after.kings | after.queens | after.rooks)):
        uncovered = after.attackers(mover, square) & ~board.attackers(mover, square)
        uncovered &= ~chess.BB_SQUARES[move.to_square]
        if uncovered:
            target = after.piece_type_at(square)
            text = "Discovers check." if target == chess.KING else f"Uncovers an attack on the {NAMES[target]}."
            add(TACTIC, "good", text)

    # Threats: what the mover would do if it were its turn again (a "null move" by the opponent).
    threatened = False
    if mate is None and not after.is_check() and not after.is_game_over():
        probe = after.copy()
        probe.push(chess.Move.null())
        threat = Searcher().search(probe, nodes=THREAT_NODES)
        if threat.best_move is not None:
            threat_san = probe.san(threat.best_move)
            if threat.mate_in is not None and threat.mate_in > 0:
                threatened = True
                add(THREAT + 5, "good", f"Threatens checkmate with {threat_san}.")
            else:
                threat_net, won, given = _line_material(probe, threat.pv[:4], mover)
                if threat_net >= 2 and threat.score > 150:
                    threatened = True
                    add(THREAT, "good", f"Threatens {threat_san}, winning {_material_words(threat_net, won, given)}.")
    if attacked and not threatened and not attacked & pinned:
        add(THREAT - 5, "good", f"Attacks the {NAMES[next(iter(attacked))]}.")
    if after.is_check() and not forks_king:
        add(CHECK, "good", "Gives check.")

    # Looking after its own pieces.
    if move.from_square in hanging_before and safe_landing:
        add(SAFETY + 5, "good", f"Moves the attacked {name} to safety.")
    for square in hanging_before - hanging_after - {move.from_square}:
        add(SAFETY, "good", f"Defends the {_square_text(board, square)}.")
    if newly_hanging and not loses_material and (score is None or score < 50):
        square = max(newly_hanging, key=lambda sq: VALUES[after.piece_type_at(sq)])
        add(SAFETY + 2, "bad", f"Leaves the {_square_text(after, square)} unprotected.")

    # Trades and positional ideas.
    if captured and net == 0:
        text = f"Trades {name}s." if captured == piece.piece_type else f"Trades its {name} for the {NAMES[captured]}."
        add(POSITIONAL + 5, "neutral", text)
    positional = False
    if board.is_castling(move):
        positional = True
        add(POSITIONAL + 8, "good", "Castles: the king is safer and a rook joins the game.")
    elif piece.piece_type == chess.KING and chess.popcount(board.occupied) <= 12:
        if chess.square_distance(move.to_square, chess.E4) < chess.square_distance(move.from_square, chess.E4):
            positional = True
            add(POSITIONAL, "good", "Brings the king towards the centre, where it helps in the endgame.")
    back_rank = chess.BB_RANK_1 if mover == chess.WHITE else chess.BB_RANK_8
    if piece.piece_type in (chess.KNIGHT, chess.BISHOP) and board.fullmove_number <= 15:
        if chess.BB_SQUARES[move.from_square] & back_rank:
            positional = True
            add(POSITIONAL + 2, "good", f"Develops the {name}.")
    if piece.piece_type == chess.PAWN and chess.BB_SQUARES[move.to_square] & CENTRE:
        positional = True
        add(POSITIONAL, "good", "Takes space in the centre.")
    elif piece.piece_type != chess.KING:
        centre_after = chess.popcount(after.attacks_mask(move.to_square) & CENTRE)
        if centre_after > chess.popcount(board.attacks_mask(move.from_square) & CENTRE):
            positional = True
            add(POSITIONAL - 4, "good", "Adds control of the centre.")
    if piece.piece_type == chess.PAWN and not move.promotion:
        file, rank = chess.square_file(move.to_square), chess.square_rank(move.to_square)
        ahead = range(rank + 1, 8) if mover == chess.WHITE else range(0, rank)
        blockers = [chess.square(f, r) for r in ahead for f in (file - 1, file, file + 1) if 0 <= f < 8]
        if not any(after.piece_at(sq) == chess.Piece(chess.PAWN, them) for sq in blockers):
            positional = True
            add(POSITIONAL + 6, "good", "Pushes a passed pawn.")
    if piece.piece_type == chess.ROOK:
        to_file = chess.square_file(move.to_square)
        opened = not after.pawns & chess.BB_FILES[to_file]
        if opened and board.pawns & chess.BB_FILES[chess.square_file(move.from_square)]:
            positional = True
            add(POSITIONAL + 4, "good", f"Puts the rook on the open {chess.FILE_NAMES[to_file]}-file.")

    # What changed in the engine's evaluation (material is covered above; piece
    # activity only when nothing more specific already says it).
    for term in terms:
        if term["name"] not in TERM_WORDS or abs(term["change"]) < TERM_THRESHOLD:
            continue
        if term["name"] == "Piece activity" and positional and term["change"] > 0:
            continue
        better, worse = TERM_WORDS[term["name"]]
        add(TERM, "good" if term["change"] > 0 else "bad", f"{better if term['change'] > 0 else worse}.")

    if not reasons:
        add(0, "neutral", "A quiet move that keeps the balance.")
    reasons.sort(key=lambda item: -item[0])
    # A move that loses material or allows mate is a mistake first: drop the small consolations.
    if any(kind == "bad" and rank >= MATERIAL for rank, kind, _ in reasons):
        reasons = [r for r in reasons if r[1] == "bad" or r[0] >= THREAT]
    return {
        **outcome,
        "reasons": [{"kind": kind, "text": text} for _, kind, text in reasons[:MAX_REASONS]],
        "score": None if score is None else sign * score,  # White's side, like the eval bar
        "mate": None if mate is None else sign * mate,
    }
