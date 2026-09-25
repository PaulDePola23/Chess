import io

import chess
import pytest

from chessbot.__main__ import main
from chessbot.play import parse_move, play, render


def test_parse_move_accepts_san_and_uci():
    board = chess.Board()
    assert parse_move(board, "Nf3") == chess.Move.from_uci("g1f3")
    assert parse_move(board, "e2e4") == chess.Move.from_uci("e2e4")
    assert parse_move(board, "E2E4") == chess.Move.from_uci("e2e4")
    assert parse_move(board, "e2e5") is None
    assert parse_move(board, "Qh5") is None
    assert parse_move(board, "hello") is None


def test_render_shows_coordinates_and_last_move():
    board = chess.Board()
    board.push_uci("e2e4")
    text = render(board, unicode=False)
    lines = text.splitlines()
    assert lines[0].startswith("8  r  n  b  q  k  b  n  r")
    assert lines[-1].split() == list("abcdefgh")
    assert "[P]" in lines[4]  # e4
    flipped = render(board, chess.BLACK, unicode=False).splitlines()
    assert flipped[0].startswith("1 ") and flipped[-1].split() == list("hgfedcba")


def test_play_a_short_game():
    # After 1. f3 e5 the human walks into fool's mate; the engine should find Qh4#.
    fen = "rnbqkbnr/pppp1ppp/8/4p3/8/5P2/PPPPP1PP/RNBQKBNR w KQkq - 0 2"
    stdin = io.StringIO("help\nnonsense\ng4\n")
    stdout = io.StringIO()
    result = play(human=chess.WHITE, depth=2, fen=fen, stdin=stdin, stdout=stdout, unicode=False)
    out = stdout.getvalue()
    assert result == "0-1"
    assert "ChessBot plays Qh4#" in out
    assert "Illegal or unrecognised move: 'nonsense'" in out
    assert "Black wins by checkmate" in out


def test_play_undo_and_quit():
    stdin = io.StringIO("e4\nundo\nfen\nquit\n")
    stdout = io.StringIO()
    result = play(human=chess.WHITE, depth=1, stdin=stdin, stdout=stdout, unicode=False)
    assert result == "*"
    assert chess.STARTING_FEN in stdout.getvalue()


def test_engine_moves_first_when_human_is_black():
    stdout = io.StringIO()
    play(human=chess.BLACK, depth=1, stdin=io.StringIO(""), stdout=stdout, unicode=False)
    assert "ChessBot plays" in stdout.getvalue()


def test_analyse_command(capsys):
    assert main(["analyse", "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1", "--depth", "3"]) == 0
    out = capsys.readouterr().out
    assert "Best move: Ra8#" in out
    assert "mate in 1 for White" in out


def test_analyse_rejects_bad_fen(capsys):
    assert main(["analyse", "not-a-fen"]) == 2


def test_version(capsys):
    with pytest.raises(SystemExit):
        main(["--version"])
    assert "chessbot" in capsys.readouterr().out
