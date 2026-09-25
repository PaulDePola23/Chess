import threading
import time

import chess
import pytest

from chessbot.search import MATE_SCORE, Searcher, time_budget


def forced_mate(board: chess.Board, moves: int) -> bool:
    """Brute force: can the side to move force checkmate within ``moves`` moves?"""
    for move in list(board.legal_moves):
        board.push(move)
        try:
            if board.is_checkmate():
                return True
            if moves > 1 and not board.is_game_over():
                if all(_mates_after(board, reply, moves - 1) for reply in list(board.legal_moves)):
                    return True
        finally:
            board.pop()
    return False


def _mates_after(board: chess.Board, reply: chess.Move, moves: int) -> bool:
    board.push(reply)
    try:
        return forced_mate(board, moves)
    finally:
        board.pop()


@pytest.mark.parametrize(
    "fen",
    [
        "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1",  # back rank
        "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5Q2/PPPP1PPP/RNB1KBNR w KQkq - 0 1",  # scholar's mate
        "r5k1/8/8/8/8/8/5PPP/6K1 b - - 0 1",  # back rank, Black to move
    ],
)
def test_finds_mate_in_one(fen):
    board = chess.Board(fen)
    result = Searcher().search(board, depth=3)
    board.push(result.best_move)
    assert board.is_checkmate()
    assert result.mate_in == 1
    assert result.score == MATE_SCORE - 1


@pytest.mark.parametrize(
    "fen",
    [
        "k7/8/2K5/8/8/8/8/7R w - - 0 1",
        "r1bq2r1/b4pk1/p1pp1p2/1p2pP2/1P2P1PB/3P4/1PPQ2P1/R3K2R w - - 0 1",
        "6k1/pp4p1/2p5/2bp4/8/P5Pb/1P3rrP/2BRRN1K b - - 0 1",
    ],
)
def test_finds_mate_in_two(fen):
    board = chess.Board(fen)
    result = Searcher().search(board, depth=5)
    assert result.mate_in == 2
    board.push(result.best_move)
    # Every defence still loses to a mate in one.
    assert all(_mates_after(board, reply, 1) for reply in list(board.legal_moves))


def test_reports_being_mated():
    # Whatever White plays (a4 or Kg1), Black mates with ...Rb1#.
    board = chess.Board("1r6/8/8/8/8/P5k1/8/7K w - - 0 1")
    assert all(_mates_after(board, move, 1) for move in list(board.legal_moves))
    result = Searcher().search(board, depth=4)
    assert result.mate_in == -1
    assert result.score == -MATE_SCORE + 2


def test_wins_a_hanging_queen():
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/4P2q/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1")
    result = Searcher().search(board, depth=4)
    assert result.best_move == chess.Move.from_uci("f3h4")
    assert result.score > 700


def test_does_not_blunder_into_stalemate():
    # Almost any quiet king move stalemates Black; the engine must find a way to keep playing.
    board = chess.Board("7k/8/6Q1/8/8/8/8/K7 w - - 0 1")
    result = Searcher().search(board, depth=4)
    board.push(result.best_move)
    assert not board.is_stalemate()
    assert result.score > 500


def test_takes_a_draw_by_repetition_when_losing():
    # A queen and two rooks down and facing ...Rb1#, White saves the game with
    # perpetual check: Qe8+ Kh7 Qh5+ Kg8 Qe8+ and so on.
    board = chess.Board("6k1/6p1/8/7Q/8/1q6/rr3PPP/6K1 w - - 0 1")
    result = Searcher().search(board, depth=6)
    assert result.score == 0
    assert board.gives_check(result.best_move)


def test_principal_variation_is_legal():
    board = chess.Board("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1")
    result = Searcher().search(board, depth=3)
    assert result.pv and result.pv[0] == result.best_move
    for move in result.pv:
        assert move in board.legal_moves
        board.push(move)


def test_no_legal_moves():
    checkmated = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/6Pq/5P2/PPPPP2P/RNBQKBNR w KQkq - 1 3")
    result = Searcher().search(checkmated, depth=3)
    assert result.best_move is None
    assert result.score == -MATE_SCORE

    stalemated = chess.Board("7k/5Q2/6K1/8/8/8/8/8 b - - 0 1")
    result = Searcher().search(stalemated, depth=3)
    assert result.best_move is None
    assert result.score == 0


def test_does_not_modify_the_callers_board():
    board = chess.Board()
    board.push_uci("e2e4")
    before = (board.fen(), list(board.move_stack))
    Searcher().search(board, depth=3)
    assert (board.fen(), list(board.move_stack)) == before


def test_respects_the_time_limit():
    board = chess.Board("r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1")
    start = time.monotonic()
    result = Searcher().search(board, time_limit=0.3)
    assert time.monotonic() - start < 1.0
    assert result.best_move in board.legal_moves


def test_can_be_stopped_from_another_thread():
    stop = threading.Event()
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    start = time.monotonic()
    result = Searcher().search(chess.Board(), stop_event=stop)
    timer.cancel()
    assert time.monotonic() - start < 1.0
    assert result.best_move in chess.Board().legal_moves


def test_node_limit():
    result = Searcher().search(chess.Board(), nodes=2000)
    assert result.best_move in chess.Board().legal_moves
    assert result.nodes < 2000 + 2048


def test_reports_each_iteration():
    depths = []
    Searcher().search(chess.Board(), depth=4, on_iteration=lambda r: depths.append(r.depth))
    assert depths == [1, 2, 3, 4]


def test_only_move_is_played_instantly():
    board = chess.Board("7k/8/8/8/8/8/6q1/7K w - - 0 1")  # Kxg2 is the only legal move
    result = Searcher().search(board, time_limit=10)
    assert result.best_move == chess.Move.from_uci("h1g2")
    assert result.elapsed < 0.5


@pytest.mark.parametrize(
    ("time_left", "increment", "moves_to_go", "low", "high"),
    [
        (60.0, 0.0, None, 1.5, 2.5),
        (60.0, 1.0, None, 2.5, 3.0),
        (10.0, 0.0, 1, 4.0, 5.0),
        (0.05, 0.0, None, 0.0, 0.05),
    ],
)
def test_time_budget(time_left, increment, moves_to_go, low, high):
    assert low <= time_budget(time_left, increment, moves_to_go) <= high
