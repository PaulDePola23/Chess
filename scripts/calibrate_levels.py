"""Estimate the Elo of each play level (chessbot.levels) by playing matches.

Needs Stockfish on the PATH (or --stockfish). Neighbouring levels play each
other, and levels play Stockfish 16 with UCI_LimitStrength at fixed UCI_Elo
settings. Ratings are then fitted to every result at once, with the Stockfish
settings as fixed anchors, so the numbers are on Stockfish's rating scale.

    python scripts/calibrate_levels.py --games 20 --workers 4

Expect roughly +-100 Elo of noise with 20 games per pairing.

Players are named L<level> (a play level), SF<elo> (Stockfish at that
UCI_Elo) or N<nodes> (the full search with that node budget, for trying out
a new level). To place one new player among levels that are already
measured, give its pairings and pin the others:

    python scripts/calibrate_levels.py --pairings N3000:L4,N3000:SF1320 --fixed L4=1137
"""

from __future__ import annotations

import argparse
import itertools
import math
import random
import shutil
import sys
from collections import defaultdict
from multiprocessing import Pool

import chess
import chess.engine

from chessbot.levels import LEVELS, Level, choose_move, get_level
from chessbot.search import Searcher

PAIRINGS = [
    ("L1", "L2"),
    ("L2", "L3"),
    ("L3", "L4"),
    ("L4", "L5"),
    ("L5", "L6"),
    ("L6", "L7"),
    ("L3", "SF1320"),
    ("L4", "SF1320"),
    ("L5", "SF1320"),
    ("L6", "SF1320"),
    ("L6", "SF1600"),
    ("L7", "SF1600"),
    ("L7", "SF1900"),
]

# Short, common openings so that deterministic players don't repeat one game.
OPENINGS = [
    [],
    ["e2e4", "e7e5"],
    ["d2d4", "d7d5"],
    ["e2e4", "c7c5"],
    ["d2d4", "g8f6"],
    ["c2c4", "e7e5"],
    ["e2e4", "e7e6"],
    ["g1f3", "d7d5"],
    ["e2e4", "c7c6"],
    ["d2d4", "g8f6", "c2c4", "e7e6"],
]
MAX_PLIES = 240
STOCKFISH_MOVE_TIME = 0.05


class Player:
    def __init__(self, name: str, stockfish: str, seed: int):
        self.name = name
        if name.startswith("SF"):
            self.engine = chess.engine.SimpleEngine.popen_uci(stockfish)
            self.engine.configure({"UCI_LimitStrength": True, "UCI_Elo": int(name[2:]), "Threads": 1})
        else:
            self.engine = None
            if name.startswith("N"):
                self.level = Level(0, name, 0, nodes=int(name[1:]))
            else:
                self.level = get_level(int(name[1:]))
            self.searcher = Searcher()
            self.rng = random.Random(seed)

    def move(self, board: chess.Board) -> chess.Move:
        if self.engine:
            return self.engine.play(board, chess.engine.Limit(time=STOCKFISH_MOVE_TIME)).move
        return choose_move(board, self.level, self.searcher, self.rng).best_move

    def close(self) -> None:
        if self.engine:
            self.engine.quit()


def play_game(job) -> tuple[str, str, float]:
    """Play one game; returns (white, black, white's score)."""
    white, black, index, stockfish = job
    board = chess.Board()
    for uci in OPENINGS[(index // 2) % len(OPENINGS)]:
        board.push_uci(uci)
    players = {chess.WHITE: Player(white, stockfish, index * 2), chess.BLACK: Player(black, stockfish, index * 2 + 1)}
    try:
        while not board.is_game_over(claim_draw=True) and len(board.move_stack) < MAX_PLIES:
            board.push(players[board.turn].move(board))
    finally:
        for player in players.values():
            player.close()
    outcome = board.outcome(claim_draw=True)
    if outcome is None or outcome.winner is None:
        return white, black, 0.5
    return white, black, 1.0 if outcome.winner == chess.WHITE else 0.0


def expected(a: float, b: float) -> float:
    return 1 / (1 + 10 ** ((b - a) / 400))


def fit_ratings(games: list[tuple[str, str, float]], anchors: dict[str, float], prior: dict[str, float]):
    """Maximum-likelihood ratings (with a weak prior, sd 400) keeping the anchors fixed."""
    ratings = {**prior, **anchors}
    free = [name for name in ratings if name not in anchors]
    for _ in range(5000):
        gradient = {name: (prior[name] - ratings[name]) / 400**2 for name in free}
        information = {name: 1 / 400**2 for name in free}
        for white, black, score in games:
            e = expected(ratings[white], ratings[black])
            k = math.log(10) / 400
            for name, s, ev in ((white, score, e), (black, 1 - score, 1 - e)):
                if name in gradient:
                    gradient[name] += k * (s - ev)
                    information[name] += k * k * ev * (1 - ev)
        step = max(abs(gradient[name] / information[name]) for name in free)
        for name in free:
            ratings[name] += gradient[name] / information[name]
        if step < 0.01:
            break
    errors = {name: 1 / math.sqrt(information[name]) for name in free}
    return ratings, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--games", type=int, default=20, help="games per pairing (default 20)")
    parser.add_argument("--workers", type=int, default=4, help="games played in parallel (default 4)")
    parser.add_argument("--stockfish", default=shutil.which("stockfish") or "/usr/games/stockfish")
    parser.add_argument("--pairings", help="comma-separated A:B pairs to play instead of the default set")
    parser.add_argument("--fixed", help="comma-separated NAME=ELO ratings to hold fixed, like SF anchors")
    args = parser.parse_args()
    pairings = [tuple(pair.split(":")) for pair in args.pairings.split(",")] if args.pairings else PAIRINGS
    fixed = {k: float(v) for k, v in (item.split("=") for item in args.fixed.split(","))} if args.fixed else {}

    jobs = []
    for (a, b), index in itertools.product(pairings, range(args.games)):
        white, black = (a, b) if index % 2 == 0 else (b, a)
        jobs.append((white, black, index, args.stockfish))
    with Pool(args.workers) as pool:
        games = pool.map(play_game, jobs, chunksize=1)

    totals = defaultdict(lambda: [0.0, 0])
    for white, black, score in games:
        for key, s in (((white, black), score), ((black, white), 1 - score)):
            totals[key][0] += s
            totals[key][1] += 1
    print("Match results:")
    for a, b in pairings:
        points, count = totals[(a, b)]
        print(f"  {a:>6} vs {b:<6} {points:4.1f} / {count}")

    players = sorted({name for pair in pairings for name in pair})
    anchors = {name: float(name[2:]) for name in players if name.startswith("SF")} | fixed
    elos = {f"L{level.number}": float(level.elo) for level in LEVELS}
    prior = {name: elos.get(name, 1500.0) for name in players if name not in anchors}
    ratings, errors = fit_ratings(games, anchors, prior)
    print("\nEstimated ratings (Stockfish UCI_Elo scale):")
    for name in sorted(prior, key=lambda n: ratings[n]):
        label = f"Level {name[1:]} {get_level(int(name[1:])).name}" if name.startswith("L") else name
        was = f"   (was {int(elos[name])})" if name in elos else ""
        print(f"  {label:<18} {ratings[name]:6.0f}  +- {errors[name]:3.0f}{was}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
