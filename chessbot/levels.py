"""Play levels: weaker versions of the engine, each with an estimated rating.

The four lower levels score the reasonable moves with a shallow search and
pick one at random, favouring better moves: the higher the ``temperature``
(in centipawns), the more often a weaker move gets picked. On top of that
the lowest levels sometimes play a random legal move (``blunder_rate``),
which is how they end up hanging pieces the way beginners do.

The three upper levels use the full search, capped by a node count rather
than a time limit so that they play just as well in a slow browser as natively.

The Elo figures come from matches against Stockfish 16 with
``UCI_LimitStrength`` and between neighbouring levels (see
``scripts/calibrate_levels.py``), 20 games per pairing, rounded to 50:

    level      measured    games that pinned it down
    Rookie      306 +- 88  4.5/20 vs Novice
    Novice      500 +- 73  1/20 vs Casual
    Casual      895 +- 68  3/20 vs Club, 1.5/20 vs SF1320
    Club       1137 +- 61  5/20 vs SF1320
    Skilled    1385 +- 52  18.5/20 vs Club, 11/20 vs SF1320, 2/20 vs Strong
    Strong     1647 +- 52  15/20 vs SF1320, 14/20 vs SF1600
    Expert     1910 +- 53  15.5/20 vs SF1600, 11/20 vs SF1900

Treat them as rough: Stockfish's scale is not the same as any online site's,
and the levels below Stockfish's minimum of 1320 are extrapolated.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

import chess

from .search import Searcher, SearchResult


@dataclass(frozen=True)
class Level:
    number: int
    name: str
    elo: int
    rank_depth: int | None = None  # lower levels: depth for rank_moves
    temperature: float = 0.0  # lower levels: centipawns of randomness
    blunder_rate: float = 0.0  # lower levels: chance of a random legal move
    nodes: int | None = None  # upper levels: search node budget

    def as_dict(self) -> dict:
        return {"level": self.number, "name": self.name, "elo": self.elo}


LEVELS = [
    Level(1, "Rookie", 300, rank_depth=1, temperature=150, blunder_rate=0.2),
    Level(2, "Novice", 500, rank_depth=1, temperature=80, blunder_rate=0.08),
    Level(3, "Casual", 900, rank_depth=2, temperature=50, blunder_rate=0.03),
    Level(4, "Club", 1150, rank_depth=2, temperature=20),
    Level(5, "Skilled", 1400, nodes=1_500),
    Level(6, "Strong", 1650, nodes=12_000),
    Level(7, "Expert", 1900, nodes=40_000),
]
DEFAULT_LEVEL = 3


def get_level(number: int) -> Level:
    for level in LEVELS:
        if level.number == number:
            return level
    raise ValueError(f"no level {number!r}; choose 1 to {len(LEVELS)}")


def choose_move(
    board: chess.Board,
    level: Level,
    searcher: Searcher,
    rng: random.Random | None = None,
    on_iteration=None,
) -> SearchResult:
    """Pick a move for ``board`` at ``level``. The result's score is the chosen move's."""
    if level.nodes is not None:
        return searcher.search(board, nodes=level.nodes, on_iteration=on_iteration)

    rng = rng or random.Random()
    start = time.monotonic()
    if level.blunder_rate and rng.random() < level.blunder_rate:
        move = rng.choice(list(board.legal_moves))
        return SearchResult(move, 0, 0, 0, time.monotonic() - start, [move])
    # Moves far worse than the best are almost never picked (five
    # "temperatures" is under 1%), so don't spend time scoring them exactly.
    ranked = searcher.rank_moves(board, level.rank_depth, margin=int(min(5 * level.temperature, 400)))
    if not ranked:
        return searcher.search(board, depth=1)
    best_score = ranked[0][1]
    weights = [math.exp((score - best_score) / level.temperature) for _, score in ranked]
    move, score = rng.choices(ranked, weights=weights)[0]
    result = SearchResult(move, score, level.rank_depth, searcher.nodes, time.monotonic() - start, [move])
    if on_iteration:
        on_iteration(result)
    return result
