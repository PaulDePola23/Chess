"""Opening names, from the Lichess chess-openings data set (public domain).

``chessbot/openings.json`` maps the EPD of a position to its ECO code and
name; ``scripts/build_openings.py`` regenerates it.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources

import chess


@lru_cache(maxsize=1)
def _table() -> dict[str, list[str]]:
    return json.loads(resources.files("chessbot").joinpath("openings.json").read_text())


def opening_name(board: chess.Board) -> dict | None:
    """The most specific named opening reached in ``board``'s game, as {"eco", "name"}, or None.

    Every position of the game is checked, so a move order that transposes
    into a known opening still gets its name.
    """
    table = _table()
    replay = board.root()
    found = table.get(replay.epd())
    for move in board.move_stack:
        replay.push(move)
        found = table.get(replay.epd(), found)
    return {"eco": found[0], "name": found[1]} if found else None
