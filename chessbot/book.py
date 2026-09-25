"""Opening book: well-known opening moves, vetted by Stockfish.

``chessbot/book.json`` maps the EPD of a position to the book moves there,
as [[move, weight, centipawns], ...]; ``scripts/build_book.py`` regenerates
it. The weight is how many named opening lines play the move, so main lines
come up more often than sidelines.
"""

from __future__ import annotations

import json
import random
from functools import lru_cache
from importlib import resources

import chess


@lru_cache(maxsize=1)
def _table() -> dict[str, list[list]]:
    try:
        return json.loads(resources.files("chessbot").joinpath("book.json").read_text())
    except FileNotFoundError:
        return {}


def book_moves(board: chess.Board) -> list[tuple[chess.Move, int, int]]:
    """The book moves in ``board`` as (move, weight, centipawns for the mover), best known first."""
    found = []
    for uci, weight, score in _table().get(board.epd(), []):
        move = chess.Move.from_uci(uci)
        if board.is_legal(move):
            found.append((move, weight, score))
    return found


def book_move(board: chess.Board, rng: random.Random | None = None) -> tuple[chess.Move, int] | None:
    """A book move for ``board``, picked at random by weight, with its score; None when out of book."""
    moves = book_moves(board)
    if not moves:
        return None
    move, _, score = (rng or random).choices(moves, weights=[weight for _, weight, _ in moves])[0]
    return move, score
