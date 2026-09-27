import random

import chess
import pytest

from chessbot.bench import BENCH_POSITIONS
from chessbot.evaluation import TERM_NAMES, evaluate, evaluation_terms
from chessbot.explain import explain_move
from chessbot.webapi import explain


def reasons(fen: str, uci: str) -> list[str]:
    return [r["text"] for r in explain_move(chess.Board(fen), chess.Move.from_uci(uci))["reasons"]]


def test_evaluation_parts_add_up_to_the_evaluation():
    rng = random.Random(4)
    for fen in BENCH_POSITIONS:
        board = chess.Board(fen)
        for _ in range(10):
            terms = evaluation_terms(board)
            assert set(terms) == set(TERM_NAMES)
            white = evaluate(board) * (1 if board.turn == chess.WHITE else -1)
            assert sum(terms.values()) == pytest.approx(white, abs=1)
            moves = list(board.legal_moves)
            if not moves:
                break
            board.push(rng.choice(moves))


def test_a_knight_fork_is_a_fork_that_wins_the_rook():
    assert reasons("r3k3/8/8/1N6/8/8/8/4K3 w - - 0 1", "b5c7")[:2] == ["Forks the king and rook.", "Wins a rook."]


def test_a_pin_and_a_mate_threat():
    assert "Pins the knight to the king." in reasons("4k3/8/2n5/8/8/8/8/4KB2 w - - 0 1", "f1b5")
    qh5 = reasons("r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/8/PPPP1PPP/RNBQK1NR w KQkq - 2 3", "d1h5")
    assert qh5[0] == "Threatens checkmate with Qxf7#."


def test_checkmate_needs_no_other_reason():
    board = chess.Board("r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4")
    result = explain_move(board, chess.Move.from_uci("h5f7"))
    assert result["san"] == "Qxf7#" and result["reasons"] == [{"kind": "good", "text": "Checkmate."}]


def test_a_blunder_is_called_one_without_consolations():
    result = explain_move(
        chess.Board("rnbqkbnr/ppp1pppp/8/3p4/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"), chess.Move.from_uci("d1g4")
    )
    assert [r["kind"] for r in result["reasons"]] == ["bad"]
    assert result["reasons"][0]["text"] == "Leaves the queen on g4 to be taken."
    assert result["score"] < -500  # White's side: White has lost the queen


def test_opening_moves_and_black_moves():
    assert reasons(chess.STARTING_FEN, "g1f3")[0] == "Develops the knight."
    assert "Takes space in the centre." in reasons(chess.STARTING_FEN, "e2e4")
    black = reasons("rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2", "b8c6")
    assert "Defends the pawn on e5." in black and "Develops the knight." in black


def test_a_simple_trade_is_not_called_a_blunder():
    fen = "r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
    result = explain_move(chess.Board(fen), chess.Move.from_uci("b5c6"))
    assert "Trades its bishop for the knight." in [r["text"] for r in result["reasons"]]
    assert all(r["kind"] != "bad" for r in result["reasons"])


def test_the_breakdown_reports_each_part_and_its_change():
    result = explain_move(chess.Board(), chess.Move.from_uci("g1f3"))
    terms = {t["name"]: t for t in result["terms"]}
    assert list(terms) == list(TERM_NAMES)
    assert terms["Piece activity"]["change"] > 0 and terms["Material"]["value"] == 0


def test_explain_api():
    data = explain(["e2e4", "e7e5"], "g1f3")
    assert data["san"] == "Nf3" and data["reasons"]
    with pytest.raises(ValueError):
        explain([], "e2e5")
    with pytest.raises(ValueError):
        explain([], "nonsense")
