import chess
import pytest

from chessbot.evaluation import _pawn_structure, _rooks_and_king, evaluate

POSITIONS = [
    chess.STARTING_FEN,
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "8/8/8/4k3/8/8/8/R3K3 b - - 0 1",
    "6k1/5ppp/8/8/8/8/5PPP/3Q2K1 w - - 0 1",
    # Doubled, isolated and passed pawns, rooks on open files, a broken king shield.
    "2r3k1/p4p1p/1p4p1/3P4/8/P1P3P1/P4P1P/4R1K1 b - - 0 1",
    "r4rk1/pp3ppp/8/8/8/6P1/PPP2P1P/2KR3R w - - 0 1",
]


def test_starting_position_is_level():
    assert evaluate(chess.Board()) == 0


@pytest.mark.parametrize("fen", POSITIONS)
def test_evaluation_is_colour_symmetric(fen):
    # mirror() swaps the colours and flips the board, including the side to
    # move, so the side to move should see exactly the same score.
    board = chess.Board(fen)
    assert evaluate(board) == evaluate(board.mirror())


def test_score_is_from_side_to_move():
    white_to_move = chess.Board("4k3/8/8/8/8/8/8/3QK3 w - - 0 1")
    black_to_move = chess.Board("4k3/8/8/8/8/8/8/3QK3 b - - 0 1")
    assert evaluate(white_to_move) > 800
    assert evaluate(black_to_move) < -800


def test_extra_material_is_worth_more():
    level = chess.Board("4k3/pppppppp/8/8/8/8/PPPPPPPP/4K3 w - - 0 1")
    pawn_up = chess.Board("4k3/ppppppp1/8/8/8/8/PPPPPPPP/4K3 w - - 0 1")
    assert evaluate(pawn_up) > evaluate(level) + 50


def test_advanced_pawns_are_worth_more_in_the_endgame():
    on_second = chess.Board("4k3/8/8/8/8/8/P7/4K3 w - - 0 1")
    on_seventh = chess.Board("4k3/P7/8/8/8/8/8/4K3 w - - 0 1")
    assert evaluate(on_seventh) > evaluate(on_second) + 50


@pytest.mark.parametrize(
    "fen",
    [
        "8/8/4k3/8/8/3K4/8/8 w - - 0 1",  # K v K
        "8/8/4k3/8/8/3K4/8/6N1 w - - 0 1",  # K+N v K
        "8/8/4k3/8/8/3K4/8/6B1 b - - 0 1",  # K+B v K
        "8/8/4k3/2n5/8/3K4/8/6B1 w - - 0 1",  # K+B v K+N
    ],
)
def test_insufficient_material_is_a_draw(fen):
    assert evaluate(chess.Board(fen)) == 0


def test_lone_king_is_pushed_to_the_edge():
    centre = chess.Board("8/8/8/4k3/8/8/8/R3K3 w - - 0 1")
    corner = chess.Board("7k/8/8/8/8/8/8/R3K3 w - - 0 1")
    assert evaluate(corner) > evaluate(centre)


def test_passed_pawns_are_worth_more_the_further_they_go():
    on_e4 = _pawn_structure(chess.BB_E4, 0)
    on_e6 = _pawn_structure(chess.BB_E6, 0)
    assert on_e6[0] > on_e4[0] and on_e6[1] > on_e4[1]
    # A black pawn in front of it or on a neighbouring file ahead stops it being passed.
    blocked = _pawn_structure(chess.BB_E4, chess.BB_D6)
    free = _pawn_structure(chess.BB_E4, chess.BB_B6)
    assert blocked[1] < free[1]


def test_doubled_and_isolated_pawns_are_weaknesses():
    connected = _pawn_structure(chess.BB_A2 | chess.BB_B2, 0)
    isolated = _pawn_structure(chess.BB_A2 | chess.BB_C2, 0)
    assert isolated[0] < connected[0] and isolated[1] < connected[1]
    side_by_side = _pawn_structure(chess.BB_D2 | chess.BB_E2 | chess.BB_F3, 0)
    doubled = _pawn_structure(chess.BB_D2 | chess.BB_E2 | chess.BB_E3, 0)
    assert doubled[1] < side_by_side[1]


def test_rooks_like_open_files():
    open_file = chess.Board("4k3/pppp1ppp/8/8/8/8/PPPP1PPP/4R1K1 w - - 0 1")
    closed_file = chess.Board("4k3/pppp1ppp/8/8/8/8/PPPP1PPP/3R2K1 w - - 0 1")
    semi_open = chess.Board("4k3/pppppppp/8/8/8/8/PPPP1PPP/4R1K1 w - - 0 1")
    scores = [_rooks_and_king(board, chess.WHITE)[0] for board in (open_file, semi_open, closed_file)]
    assert scores == sorted(scores, reverse=True) and scores[0] > scores[2]


def test_a_castled_king_wants_its_pawn_shield():
    sheltered = chess.Board("6k1/5ppp/8/8/8/8/5PPP/6K1 w - - 0 1")
    broken = chess.Board("6k1/5ppp/8/8/6P1/8/5P1P/6K1 w - - 0 1")  # g-pawn pushed past the shield
    gone = chess.Board("6k1/5ppp/8/8/8/8/5P1P/6K1 w - - 0 1")
    shelter = [_rooks_and_king(board, chess.WHITE)[0] for board in (sheltered, broken, gone)]
    assert shelter[0] == 0
    assert shelter[0] > shelter[1] > shelter[2]
