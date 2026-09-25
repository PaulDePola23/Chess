"""Static evaluation of a chess position.

The score is material plus piece-square tables, blended ("tapered") between
middlegame and endgame tables according to how much material is left. Scores
are in centipawns from the point of view of the side to move, as a negamax
search expects.

The piece values and tables are based on Tomasz Michniewski's "Simplified
Evaluation Function", with endgame tables for pawns (push passers) and the
king (walk to the centre). On top of that come the usual structural terms:
doubled, isolated and passed pawns, rooks on open files, and the pawn shield
in front of a castled king.
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


# Structural terms as (middlegame, endgame) centipawns.
DOUBLED_PAWN = (-10, -20)
ISOLATED_PAWN = (-12, -16)
# Passed pawn bonus by how far it has advanced (0 = its own back rank).
PASSED_PAWN = [(0, 0), (5, 10), (8, 15), (15, 30), (30, 55), (50, 90), (80, 140), (0, 0)]
ROOK_OPEN_FILE = (25, 10)
ROOK_SEMI_OPEN_FILE = (12, 8)
# A castled king missing the pawn in front of it on a file (middlegame only),
# and extra when that file has no friendly pawn at all.
KING_SHIELD_MISSING = -14
KING_OPEN_FILE = -18

_FILES = [chess.BB_FILES[f] for f in range(8)]
_ADJACENT_FILES = [(_FILES[f - 1] if f > 0 else 0) | (_FILES[f + 1] if f < 7 else 0) for f in range(8)]


def _passed_masks(color: chess.Color) -> list[int]:
    """For each square, the squares an enemy pawn would have to be on to stop a pawn there."""
    masks = []
    for square in chess.SQUARES:
        file, rank = chess.square_file(square), chess.square_rank(square)
        ahead = range(rank + 1, 8) if color == chess.WHITE else range(0, rank)
        mask = 0
        for r in ahead:
            for f in (file - 1, file, file + 1):
                if 0 <= f < 8:
                    mask |= chess.BB_SQUARES[chess.square(f, r)]
        masks.append(mask)
    return masks


_PASSED_MASKS = {chess.WHITE: _passed_masks(chess.WHITE), chess.BLACK: _passed_masks(chess.BLACK)}
_pawn_cache: dict[tuple[int, int], tuple[int, int]] = {}


def _pawn_structure(white_pawns: int, black_pawns: int) -> tuple[int, int]:
    """(middlegame, endgame) pawn-structure score from White's side. Cached: it only depends on the pawns."""
    key = (white_pawns, black_pawns)
    cached = _pawn_cache.get(key)
    if cached is not None:
        return cached
    mg = eg = 0
    for color, own, enemy, sign in (
        (chess.WHITE, white_pawns, black_pawns, 1),
        (chess.BLACK, black_pawns, white_pawns, -1),
    ):
        for file in range(8):
            count = chess.popcount(own & _FILES[file])
            if count > 1:
                mg += sign * DOUBLED_PAWN[0] * (count - 1)
                eg += sign * DOUBLED_PAWN[1] * (count - 1)
        for square in chess.scan_forward(own):
            file = chess.square_file(square)
            if not own & _ADJACENT_FILES[file]:
                mg += sign * ISOLATED_PAWN[0]
                eg += sign * ISOLATED_PAWN[1]
            if not enemy & _PASSED_MASKS[color][square]:
                rank = chess.square_rank(square)
                advanced = rank if color == chess.WHITE else 7 - rank
                mg += sign * PASSED_PAWN[advanced][0]
                eg += sign * PASSED_PAWN[advanced][1]
    if len(_pawn_cache) > 100_000:
        _pawn_cache.clear()
    _pawn_cache[key] = (mg, eg)
    return mg, eg


def _rooks_and_king(board: chess.Board, color: chess.Color) -> tuple[int, int]:
    """(middlegame, endgame) bonuses for ``color``'s rooks on open files and king shelter."""
    mg = eg = 0
    own_pawns = board.pawns & board.occupied_co[color]
    all_pawns = board.pawns
    for square in chess.scan_forward(board.rooks & board.occupied_co[color]):
        file_mask = _FILES[chess.square_file(square)]
        if not all_pawns & file_mask:
            mg += ROOK_OPEN_FILE[0]
            eg += ROOK_OPEN_FILE[1]
        elif not own_pawns & file_mask:
            mg += ROOK_SEMI_OPEN_FILE[0]
            eg += ROOK_SEMI_OPEN_FILE[1]
    king = board.king(color)
    if king is not None:
        file, rank = chess.square_file(king), chess.square_rank(king)
        home = rank if color == chess.WHITE else 7 - rank
        # Only a king tucked away on a wing has a shield worth keeping.
        if home <= 1 and file not in (3, 4):
            step = 1 if color == chess.WHITE else -1
            shield_ranks = chess.BB_RANKS[rank + step] | chess.BB_RANKS[rank + 2 * step]
            for f in (file - 1, file, file + 1):
                if 0 <= f < 8:
                    if not own_pawns & _FILES[f] & shield_ranks:
                        mg += KING_SHIELD_MISSING
                    if not own_pawns & _FILES[f]:
                        mg += KING_OPEN_FILE
    return mg, eg


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

    for color in chess.COLORS:
        extra_mg, extra_eg = _rooks_and_king(board, color)
        mg[color] += extra_mg
        eg[color] += extra_eg

    phase = min(phase, MAX_PHASE)
    pawns_mg, pawns_eg = _pawn_structure(
        board.pawns & board.occupied_co[chess.WHITE], board.pawns & board.occupied_co[chess.BLACK]
    )
    mg_score = mg[chess.WHITE] - mg[chess.BLACK] + pawns_mg
    eg_score = eg[chess.WHITE] - eg[chess.BLACK] + pawns_eg
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
