import io
import time

import chess

from chessbot.uci import UCIEngine


def run_uci(commands: str) -> list[str]:
    output = io.StringIO()
    UCIEngine(stdin=io.StringIO(commands), stdout=output).run()
    return output.getvalue().splitlines()


def bestmove(lines: list[str]) -> str:
    moves = [line.split()[1] for line in lines if line.startswith("bestmove")]
    assert len(moves) == 1, lines
    return moves[0]


def test_handshake():
    lines = run_uci("uci\nisready\nquit\n")
    assert lines[0].startswith("id name ChessBot")
    assert "uciok" in lines
    assert lines[-1] == "readyok"


def test_go_depth_from_startpos():
    lines = run_uci("position startpos\ngo depth 3\n")
    assert chess.Move.from_uci(bestmove(lines)) in chess.Board().legal_moves
    infos = [line for line in lines if line.startswith("info depth")]
    assert [int(line.split()[2]) for line in infos] == [1, 2, 3]
    assert all(" score cp " in line and " pv " in line for line in infos)


def test_position_with_moves():
    lines = run_uci("position startpos moves e2e4 e7e5 g1f3\ngo depth 2\n")
    board = chess.Board()
    for uci in ["e2e4", "e7e5", "g1f3"]:
        board.push_uci(uci)
    assert chess.Move.from_uci(bestmove(lines)) in board.legal_moves


def test_position_fen_and_mate_score():
    lines = run_uci("position fen 6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1\ngo depth 3\n")
    assert bestmove(lines) == "a1a8"
    assert any("score mate 1" in line for line in lines)


def test_fen_followed_by_moves():
    fen = "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1"
    lines = run_uci(f"position fen {fen} moves g1f1 g8f8\nd\n")
    expected = chess.Board(fen)
    expected.push_uci("g1f1")
    expected.push_uci("g8f8")
    assert f"Fen: {expected.fen()}" in lines


def test_go_with_clock():
    start = time.monotonic()
    lines = run_uci("position startpos\ngo wtime 3000 btime 3000 winc 0 binc 0\n")
    assert time.monotonic() - start < 2.0
    assert chess.Move.from_uci(bestmove(lines)) in chess.Board().legal_moves


def test_go_movetime():
    start = time.monotonic()
    lines = run_uci("position startpos\ngo movetime 300\n")
    assert time.monotonic() - start < 1.0
    bestmove(lines)


def test_stop_ends_infinite_search():
    reader, writer = io.StringIO(), io.StringIO()
    engine = UCIEngine(stdin=reader, stdout=writer)
    engine.handle("position startpos")
    engine.handle("go infinite")
    time.sleep(0.2)
    engine.handle("isready")  # Answered while the search is running.
    assert "readyok" in writer.getvalue()
    engine.handle("stop")
    assert chess.Move.from_uci(bestmove(writer.getvalue().splitlines())) in chess.Board().legal_moves


def test_quit_stops_a_running_search():
    engine = UCIEngine(stdin=io.StringIO(), stdout=io.StringIO())
    engine.handle("go infinite")
    time.sleep(0.1)
    start = time.monotonic()
    assert engine.handle("quit") is False
    engine.stop_search()
    assert time.monotonic() - start < 1.0
    assert "bestmove" in engine.stdout.getvalue()


def test_no_legal_moves():
    lines = run_uci("position fen 7k/5Q2/6K1/8/8/8/8/8 b - - 0 1\ngo depth 3\n")
    assert bestmove(lines) == "0000"


def test_invalid_input_is_ignored():
    lines = run_uci("position fen not a fen\nfoo bar\ngo nonsense\nsetoption name Hash value lots\nisready\n")
    assert "readyok" in lines
    assert any(line.startswith("info string invalid position") for line in lines)


def test_setoption():
    engine = UCIEngine(stdin=io.StringIO(), stdout=io.StringIO())
    engine.handle("setoption name Hash value 16")
    engine.handle("setoption name Move Overhead value 200")
    assert engine.searcher.hash_entries == 16 * 1024 * 1024 // 200
    assert engine.move_overhead == 0.2


def test_ucinewgame_resets_position():
    engine = UCIEngine(stdin=io.StringIO(), stdout=io.StringIO())
    engine.handle("position startpos moves e2e4")
    engine.handle("ucinewgame")
    assert engine.board == chess.Board()
