"""Static evaluation of a chess position.

The score is material plus piece-square tables, blended ("tapered") between
middlegame and endgame tables according to how much material is left. Scores
are in centipawns from the point of view of the side to move, as a negamax
search expects.

The piece values and tables are based on Tomasz Michniewski's "Simplified
Evaluation Function", with endgame tables for pawns (push passers) and the
king (walk to the centre).
"""

import chess

PIECE_VALUES = {
    chess.PAWN: 100,
    chess.KNIGHT: 320,
    chess.BISHOP: 330,
    chess.ROOK: 500,
    chess.QUEEN: 900,
    chess.KING: 0,
}

BISHOP_PAIR_BONUS = 30

# Game phase: 24 with all minor and major pieces on the board, 0 with none.
PHASE_WEIGHTS = {chess.KNIGHT: 1, chess.BISHOP: 1, chess.ROOK: 2, chess.QUEEN: 4}
MAX_PHASE = 24

# Piece-square tables from White's point of view, laid out as the board is
# printed: the first row is rank 8, the last row is rank 1.
# fmt: off
PAWN_MG = [
      0,   0,   0,   0,   0,   0,   0,   0,
     50,  50,  50,  50,  50,  50,  50,  50,
     10,  10,  20,  30,  30,  20,  10,  10,
      5,   5,  10,  25,  25,  10,   5,   5,
      0,   0,   0,  20,  20,   0,   0,   0,
      5,  -5, -10,   0,   0, -10,  -5,   5,
      5,  10,  10, -20, -20,  10,  10,   5,
      0,   0,   0,   0,   0,   0,   0,   0,
]
PAWN_EG = [
      0,   0,   0,   0,   0,   0,   0,   0,
     80,  80,  80,  80,  80,  80,  80,  80,
     50,  50,  50,  50,  50,  50,  50,  50,
     30,  30,  30,  30,  30,  30,  30,  30,
     20,  20,  20,  20,  20,  20,  20,  20,
     10,  10,  10,  10,  10,  10,  10,  10,
      0,   0,   0,   0,   0,   0,   0,   0,
      0,   0,   0,   0,   0,   0,   0,   0,
]
KNIGHT = [
    -50, -40, -30, -30, -30, -30, -40, -50,
    -40, -20,   0,   0,   0,   0, -20, -40,
    -30,   0,  10,  15,  15,  10,   0, -30,
    -30,   5,  15,  20,  20,  15,   5, -30,
    -30,   0,  15,  20,  20,  15,   0, -30,
    -30,   5,  10,  15,  15,  10,   5, -30,
    -40, -20,   0,   5,   5,   0, -20, -40,
    -50, -40, -30, -30, -30, -30, -40, -50,
]
BISHOP = [
    -20, -10, -10, -10, -10, -10, -10, -20,
    -10,   0,   0,   0,   0,   0,   0, -10,
    -10,   0,   5,  10,  10,   5,   0, -10,
    -10,   5,   5,  10,  10,   5,   5, -10,
    -10,   0,  10,  10,  10,  10,   0, -10,
    -10,  10,  10,  10,  10,  10,  10, -10,
    -10,   5,   0,   0,   0,   0,   5, -10,
    -20, -10, -10, -10, -10, -10, -10, -20,
]
ROOK = [
      0,   0,   0,   0,   0,   0,   0,   0,
      5,  10,  10,  10,  10,  10,  10,   5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
     -5,   0,   0,   0,   0,   0,   0,  -5,
      0,   0,   0,   5,   5,   0,   0,   0,
]
QUEEN = [
    -20, -10, -10,  -5,  -5, -10, -10, -20,
    -10,   0,   0,   0,   0,   0,   0, -10,
    -10,   0,   5,   5,   5,   5,   0, -10,
     -5,   0,   5,   5,   5,   5,   0,  -5,
      0,   0,   5,   5,   5,   5,   0,  -5,
    -10,   5,   5,   5,   5,   5,   0, -10,
    -10,   0,   5,   0,   0,   0,   0, -10,
    -20, -10, -10,  -5,  -5, -10, -10, -20,
]
KING_MG = [
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -30, -40, -40, -50, -50, -40, -40, -30,
    -20, -30, -30, -40, -40, -30, -30, -20,
    -10, -20, -20, -20, -20, -20, -20, -10,
     20,  20,   0,   0,   0,   0,  20,  20,
     20,  30,  10,   0,   0,  10,  30,  20,
]
KING_EG = [
    -50, -40, -30, -20, -20, -30, -40, -50,
    -30, -20, -10,   0,   0, -10, -20, -30,
    -30, -10,  20,  30,  30,  20, -10, -30,
    -30, -10,  30,  40,  40,  30, -10, -30,
    -30, -10,  30,  40,  40,  30, -10, -30,
    -30, -10,  20,  30,  30,  20, -10, -30,
    -30, -30,   0,   0,   0,   0, -30, -30,
    -50, -30, -30, -30, -30, -30, -30, -50,
]
# fmt: on

_MG_TABLES = {
    chess.PAWN: PAWN_MG,
    chess.KNIGHT: KNIGHT,
    chess.BISHOP: BISHOP,
    chess.ROOK: ROOK,
    chess.QUEEN: QUEEN,
    chess.KING: KING_MG,
}
_EG_TABLES = {**_MG_TABLES, chess.PAWN: PAWN_EG, chess.KING: KING_EG}


def _square_values(tables):
    """Index tables by [color][piece_type][square], with material included.

    python-chess numbers squares from a1 = 0 to h8 = 63, so for White the
    printed tables above are flipped vertically (square ^ 56). Black sees the
    board from the other side, so the printed layout already matches.
    """
    return {
        chess.WHITE: {pt: [PIECE_VALUES[pt] + t[sq ^ 56] for sq in chess.SQUARES] for pt, t in tables.items()},
        chess.BLACK: {pt: [PIECE_VALUES[pt] + t[sq] for sq in chess.SQUARES] for pt, t in tables.items()},
    }


_MG = _square_values(_MG_TABLES)
_EG = _square_values(_EG_TABLES)


def _is_material_draw(board: chess.Board) -> bool:
    """True when neither side can win: no pawns, rooks or queens, and at most one minor piece each."""
    if board.pawns or board.rooks or board.queens:
        return False
    minors = board.knights | board.bishops
    return (
        chess.popcount(minors & board.occupied_co[chess.WHITE]) <= 1
        and chess.popcount(minors & board.occupied_co[chess.BLACK]) <= 1
    )


def _mop_up(board: chess.Board, winner: chess.Color) -> int:
    """Bonus that helps convert endgames like K+Q vs K or K+R vs K.

    Without it the search cannot see far enough to find the mate, so we reward
    driving the lone king to the edge and bringing our own king closer.
    """
    loser_king = board.king(not winner)
    winner_king = board.king(winner)
    if loser_king is None or winner_king is None:
        return 0
    file, rank = chess.square_file(loser_king), chess.square_rank(loser_king)
    centre_distance = max(3 - file, file - 4) + max(3 - rank, rank - 4)
    king_distance = chess.square_manhattan_distance(winner_king, loser_king)
    return 10 * centre_distance + 4 * (14 - king_distance)


def evaluate(board: chess.Board) -> int:
    """Return the static score of ``board`` in centipawns for the side to move."""
    if _is_material_draw(board):
        return 0

    mg = [0, 0]
    eg = [0, 0]
    phase = 0
    for color in chess.COLORS:
        mg_values = _MG[color]
        eg_values = _EG[color]
        occupied = board.occupied_co[color]
        for piece_type in chess.PIECE_TYPES:
            mask = board.pieces_mask(piece_type, color)
            if not mask:
                continue
            mg_table = mg_values[piece_type]
            eg_table = eg_values[piece_type]
            for square in chess.scan_forward(mask):
                mg[color] += mg_table[square]
                eg[color] += eg_table[square]
            phase += PHASE_WEIGHTS.get(piece_type, 0) * chess.popcount(mask)
        if chess.popcount(board.bishops & occupied) >= 2:
            mg[color] += BISHOP_PAIR_BONUS
            eg[color] += BISHOP_PAIR_BONUS

    phase = min(phase, MAX_PHASE)
    mg_score = mg[chess.WHITE] - mg[chess.BLACK]
    eg_score = eg[chess.WHITE] - eg[chess.BLACK]
    # Keep the blend scaled by MAX_PHASE until the end so that rounding is
    # identical for both colours (evaluate(board) == evaluate(board.mirror())).
    score = mg_score * phase + eg_score * (MAX_PHASE - phase)

    # Lone king against a rook or queen: help the search finish the job.
    if not board.pawns:
        for winner in chess.COLORS:
            loser_pieces = board.occupied_co[not winner]
            if loser_pieces == loser_pieces & board.kings and board.occupied_co[winner] & (board.rooks | board.queens):
                bonus = _mop_up(board, winner) * MAX_PHASE
                score += bonus if winner == chess.WHITE else -bonus

    if board.turn == chess.BLACK:
        score = -score
    return score // MAX_PHASE
