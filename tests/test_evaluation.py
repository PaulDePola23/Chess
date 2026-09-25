import chess
import pytest

from chessbot.evaluation import evaluate

POSITIONS = [
    chess.STARTING_FEN,
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "8/8/8/4k3/8/8/8/R3K3 b - - 0 1",
    "6k1/5ppp/8/8/8/8/5PPP/3Q2K1 w - - 0 1",
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
