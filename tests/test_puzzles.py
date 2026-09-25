"""The generated puzzle set (chessbot/web/puzzles.json) must be sound."""

import json
from importlib import resources

import chess
import pytest

PUZZLES = json.loads(resources.files("chessbot").joinpath("web", "puzzles.json").read_text())


def test_there_are_puzzles_with_unique_ids():
    assert len(PUZZLES) >= 10
    ids = [p["id"] for p in PUZZLES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("puzzle", PUZZLES, ids=lambda p: p["id"])
def test_puzzle_is_legal_and_ends_on_the_solvers_move(puzzle):
    board = chess.Board(puzzle["fen"])
    assert board.is_valid()
    # The opponent's mistake, then the solver's moves and the replies to them.
    assert len(puzzle["moves"]) >= 2 and len(puzzle["moves"]) % 2 == 0
    for uci in puzzle["moves"]:
        move = chess.Move.from_uci(uci)
        assert move in board.legal_moves, f"{uci} is illegal in {board.fen()}"
        board.push(move)
    if puzzle["theme"].startswith("Mate in"):
        assert board.is_checkmate()
        assert puzzle["theme"] == f"Mate in {len(puzzle['moves']) // 2}"
    assert 300 <= puzzle["rating"] <= 3000
