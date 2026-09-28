"""Play levels: weaker versions of the engine, each with an estimated rating.

The four lower levels score the reasonable moves with a shallow search and
pick one at random, favouring better moves: the higher the ``temperature``
(in centipawns), the more often a weaker move gets picked. On top of that
the lowest levels sometimes play a random legal move (``blunder_rate``),
which is how they end up hanging pieces the way beginners do.

Skilled, Strong, Expert and Summer use the full search, capped by a node
count rather than a time limit so that they play just as well in a slow
browser as natively. Club and up open from the opening book.

Titan and Pinky are Stockfish, held to their rating with ``UCI_Elo``. The web
page runs it itself (Stockfish.js in a Web Worker), so ``choose_move``
refuses them.

The Elo figures come from matches against Stockfish 16 with
``UCI_LimitStrength`` and between levels (see ``scripts/calibrate_levels.py``),
rounded to 50:

    level      measured    games that pinned it down
    Rookie      ~180       9.5/70 vs Novice
    Novice      ~500       10.5/70 vs Casual
    Casual      ~800       9/120 vs Club, 7.5/120 vs SF1320 (three runs: 890, 782, 756)
    Club       1163 +- 55  6.5/20 vs SF1320, 2/20 vs Skilled
    Skilled    1455 +- 55  14.5/20 vs SF1320, 1.5/20 vs Strong
    Strong     1730 +- 43  18/20 vs SF1320, 12/20 vs SF1600, 3/20 vs Expert
    Expert     1949 +- 47  13/20 vs SF1600, 14/20 vs SF1900
    Summer     2096 +- 45  15.5/24 vs Expert, 16/24 vs SF1900, 12/24 vs SF2200

Treat them as rough: Stockfish's scale is not the same as any online site's,
and the levels below Stockfish's minimum of 1320 are extrapolated from games
between levels.

Those ratings were measured with an earlier search. When futility pruning,
late move pruning and aspiration windows made it stronger for the same number
of nodes, the node budgets were matched again so that each level still plays
as it did when it was measured: the new search on the new budget against the
old search on the old one, until the score was even within the error bars
(``scripts/match.py --base-nodes``):

    level      old budget   new budget   new vs old
    Skilled       1,500        1,500      +7 +- 30   (400 games)
    Strong       12,000        6,000     +15 +- 30   (400 games; 7,000: +27)
    Expert       40,000       18,000      -5 +- 39   (200 games; 22,000: +63)
    Summer       60,000       28,000     between 27,000: -14 +- 43 and 33,000: +47 +- 48 (150 games each)

The lower levels choose among a shallow search's moves and barely changed
(the same first choice in 181 to 185 of 185 test positions).
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass

import chess

from .book import book_move
from .evaluation import evaluate
from .search import MATE_SCORE, Searcher, SearchResult


@dataclass(frozen=True)
class Level:
    number: int
    name: str
    elo: int
    rank_depth: int | None = None  # lower levels: depth for rank_moves
    glance_rate: float = 0.0  # lower levels: chance of judging the moves at a glance instead (see glance())
    temperature: float = 0.0  # lower levels: centipawns of randomness
    blunder_rate: float = 0.0  # lower levels: chance of a random legal move
    nodes: int | None = None  # upper levels: search node budget
    book: bool = False  # play well-known opening moves from the opening book
    stockfish_elo: int | None = None  # the top levels: Stockfish held to this rating (played in the browser)
    description: str = ""  # how it plays, for players choosing a level

    def as_dict(self) -> dict:
        info = {"level": self.number, "name": self.name, "elo": self.elo, "description": self.description}
        if self.stockfish_elo is not None:
            info["stockfish"] = self.stockfish_elo
        return info


LEVELS = [
    Level(
        1,
        "Rookie",
        200,
        rank_depth=1,
        glance_rate=1.0,
        temperature=150,
        blunder_rate=0.3,
        description="Moves almost at random and leaves pieces hanging. For your very first games.",
    ),
    Level(
        2,
        "Novice",
        500,
        rank_depth=1,
        glance_rate=0.5,
        temperature=60,
        blunder_rate=0.1,
        description="Only sometimes checks what you can take back, so it still gives pieces away.",
    ),
    Level(
        3,
        "Casual",
        800,
        rank_depth=1,
        temperature=80,
        blunder_rate=0.12,
        description="Sees what you can take next move, but misses forks, pins and plans.",
    ),
    Level(
        4,
        "Club",
        1150,
        rank_depth=2,
        temperature=20,
        book=True,
        description="Rarely blunders. Beat it with tactics and a plan.",
    ),
    Level(5, "Skilled", 1450, nodes=1_500, book=True, description="Looks a few moves ahead and punishes loose pieces."),
    Level(6, "Strong", 1750, nodes=6_000, book=True, description="Sees most tactics. You'll need a real advantage."),
    Level(7, "Expert", 1950, nodes=18_000, book=True, description="The full engine at a brisk pace. Hard to beat."),
    # The last three are named after two dogs and a cat.
    Level(
        8,
        "Summer",
        2100,
        nodes=28_000,
        book=True,
        description="Named after Summer the dog.",
    ),
    Level(
        9,
        "Titan",
        2500,
        stockfish_elo=2500,
        description="Named after Mikayla & Paul's dog, Titan.",
    ),
    Level(
        10,
        "Pinky",
        2700,
        stockfish_elo=2700,
        description="Named after Pinky the cat. Good luck.",
    ),
]
DEFAULT_LEVEL = 3


def glance(board: chess.Board) -> list[tuple[chess.Move, int]]:
    """Score each legal move by how the board looks right after it, best first.

    Nothing looks at the reply, so a defended pawn looks free and a piece left
    where it can be taken looks safe: how beginners see the board, and how
    Rookie and Novice do.
    """
    scored = []
    for move in board.legal_moves:
        board.push(move)
        if board.is_checkmate():
            score = MATE_SCORE
        elif board.is_stalemate() or board.is_insufficient_material():
            score = 0
        else:
            score = -evaluate(board)
        board.pop()
        scored.append((move, score))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


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
    if level.stockfish_elo is not None:
        raise ValueError(f"{level.name} is played by Stockfish, which the web page runs itself")
    rng = rng or random.Random()
    if level.book:
        found = book_move(board, rng)
        if found:
            move, score = found
            return SearchResult(move, score, 0, 0, 0.0, [move], book=True)
    if level.nodes is not None:
        return searcher.search(board, nodes=level.nodes, on_iteration=on_iteration)

    start = time.monotonic()
    if level.blunder_rate and rng.random() < level.blunder_rate:
        move = rng.choice(list(board.legal_moves))
        return SearchResult(move, 0, 0, 0, time.monotonic() - start, [move])
    glanced = bool(level.glance_rate) and rng.random() < level.glance_rate
    if glanced:
        ranked = glance(board)
    else:
        # Moves far worse than the best are almost never picked (five
        # "temperatures" is under 1%), so don't spend time scoring them exactly.
        ranked = searcher.rank_moves(board, level.rank_depth, margin=int(min(5 * level.temperature, 400)))
    if not ranked:
        return searcher.search(board, depth=1)
    best_score = ranked[0][1]
    weights = [math.exp((score - best_score) / level.temperature) for _, score in ranked]
    move, score = rng.choices(ranked, weights=weights)[0]
    depth, nodes = (0, len(ranked)) if glanced else (level.rank_depth, searcher.nodes)  # a glance looks once
    result = SearchResult(move, score, depth, nodes, time.monotonic() - start, [move])
    if on_iteration:
        on_iteration(result)
    return result
