"""Every lesson puzzle must be legal, and its answer must really be the best move."""

import json
from importlib import resources

import chess
import pytest

from chessbot.search import Searcher

LESSONS = json.loads(resources.files("chessbot").joinpath("web", "lessons.json").read_text())
PUZZLES = [lesson for lesson in LESSONS if "puzzle" in lesson]


def test_lessons_have_the_expected_shape():
    ids = [lesson["id"] for lesson in LESSONS]
    assert len(ids) == len(set(ids))
    for lesson in LESSONS:
        assert lesson["title"] and lesson["summary"] and lesson["body"]
        if "puzzle" in lesson:
            puzzle = lesson["puzzle"]
            assert puzzle["goal"] and puzzle["hint"] and puzzle["explain"]
            assert ("solution" in puzzle) != ("accept" in puzzle)


@pytest.mark.parametrize("lesson", PUZZLES, ids=lambda lesson: lesson["id"])
def test_puzzle(lesson):
    puzzle = lesson["puzzle"]
    board = chess.Board(puzzle["fen"])
    assert board.is_valid(), board.status()
    searcher = Searcher()

    if "accept" in puzzle:
        # Several answers are right: all must be close to the engine's best,
        # and the engine's own choice must be one of them.
        accepted = [chess.Move.from_uci(uci) for uci in puzzle["accept"]]
        ranked = dict(searcher.rank_moves(board, 3, margin=10_000, max_nodes=10**9))
        best_move = max(ranked, key=ranked.get)
        assert best_move in accepted, board.san(best_move)
        for move in accepted:
            assert ranked[move] >= ranked[best_move] - 80, board.san(move)
        return

    solution = [chess.Move.from_uci(uci) for uci in puzzle["solution"]]
    for index, move in enumerate(solution):
        assert move in board.legal_moves, f"{move} is illegal in {board.fen()}"
        if index % 2 == 0 and not puzzle.get("mate"):
            # The player's move must be clearly better than every alternative.
            ranked = searcher.rank_moves(board, 3, margin=10_000, max_nodes=10**9)
            assert ranked[0][0] == move, f"engine prefers {board.san(ranked[0][0])} to {board.san(move)}"
            assert ranked[0][1] - ranked[1][1] >= 100, "the puzzle has more than one good answer"
        board.push(move)

    if puzzle.get("mate"):
        assert board.is_checkmate()
    else:
        # The line must win material, or leave a clearly winning position,
        # and not end in a drawn ending.
        assert not board.is_insufficient_material()
        side = chess.Board(puzzle["fen"]).turn
        gained = material(board, side) - material(chess.Board(puzzle["fen"]), side)
        result = searcher.search(board, depth=4)
        score = result.score if board.turn == side else -result.score
        assert gained >= 2 or score > 150


def material(board: chess.Board, color: chess.Color) -> int:
    """Material balance in pawns (1/3/3/5/9) from ``color``'s side."""
    values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
    return sum(
        value * (len(board.pieces(piece, color)) - len(board.pieces(piece, not color)))
        for piece, value in values.items()
    )
