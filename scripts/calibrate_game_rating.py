"""Measure the accuracy each play level scores in the post-game review.

The review gives every move an accuracy (lichess's formula) and the game the
average of its moves'. This plays each level against itself from book
openings, reviews every move exactly as the website does
(webapi.review_move at REVIEW_DEPTH), and prints each level's average game
accuracy: the points of the "game rating" scale in chessbot/game_rating.py,
which turns a game's accuracy into the rating of the level that plays that
accurately.

    python scripts/calibrate_game_rating.py --players L1,L2,SF1320,SF1600 --games 16

Players are L<level> (a play level) or SF<elo> (Stockfish held to that
UCI_Elo, as the level ratings were measured against; needs stockfish on the
PATH).
"""

from __future__ import annotations

import argparse
import random
import shutil
import statistics
from multiprocessing import Pool

import chess
import chess.engine

from chessbot.book import book_moves
from chessbot.levels import LEVELS, choose_move, get_level
from chessbot.search import Searcher
from chessbot.webapi import review_move

MAX_PLIES = 200
STOCKFISH_MOVE_TIME = 0.05  # as in scripts/calibrate_levels.py, whose anchors the level ratings are on


def opening(rng: random.Random) -> chess.Board:
    """A few plies down a random line of the opening book."""
    board = chess.Board()
    for _ in range(rng.randint(2, 8)):
        moves = book_moves(board)
        if not moves:
            break
        board.push(rng.choices([m for m, _, _ in moves], weights=[w for _, w, _ in moves])[0])
    return board


def rating(player: str) -> int:
    return int(player[2:]) if player.startswith("SF") else get_level(int(player[1:])).elo


def play_and_review(job: tuple[str, int, str]) -> tuple[str, list[float]]:
    """One game of a player against itself; the accuracy of each side."""
    player, seed, stockfish = job
    rng = random.Random(seed)
    board = opening(rng)
    start = len(board.move_stack)
    engine = level = None
    stockfish_elo = int(player[2:]) if player.startswith("SF") else get_level(int(player[1:])).stockfish_elo
    if stockfish_elo:
        engine = chess.engine.SimpleEngine.popen_uci(stockfish)
        engine.configure({"UCI_LimitStrength": True, "UCI_Elo": stockfish_elo})
    else:
        level = get_level(int(player[1:]))
    try:
        searcher = Searcher()
        while not board.is_game_over(claim_draw=True) and len(board.move_stack) < MAX_PLIES:
            if engine:
                move = engine.play(board, chess.engine.Limit(time=STOCKFISH_MOVE_TIME)).move
            else:
                move = choose_move(board, level, searcher, rng).best_move
            board.push(move)
    finally:
        if engine:
            engine.quit()
    moves = [m.uci() for m in board.move_stack]
    reviewer = Searcher()
    by_side: dict[bool, list[float]] = {chess.WHITE: [], chess.BLACK: []}
    for ply in range(start, len(moves)):
        item = review_move(moves, ply, reviewer)
        by_side[ply % 2 == 0].append(item["accuracy"])
    return player, [statistics.mean(a) for a in by_side.values() if len(a) >= 10]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--games", type=int, default=16, help="games per level (default 16)")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--players", help="comma-separated players (default: every level)")
    parser.add_argument("--stockfish", default=shutil.which("stockfish") or "/usr/games/stockfish")
    args = parser.parse_args()
    players = args.players.split(",") if args.players else [f"L{level.number}" for level in LEVELS]
    jobs = [(player, 1000 * index + game, args.stockfish) for index, player in enumerate(players)
            for game in range(args.games)]  # fmt: skip
    results: dict[str, list[float]] = {player: [] for player in players}
    with Pool(args.workers) as pool:
        for player, accuracies in pool.imap_unordered(play_and_review, jobs):
            results[player].extend(accuracies)
    print("player     rating   accuracy (mean +- sd, sides)")
    for player in sorted(players, key=rating):
        values = results[player]
        print(
            f"{player:8} {rating(player):6}   {statistics.mean(values):5.1f} +- {statistics.stdev(values):4.1f}"
            f"   ({len(values)})"
        )


if __name__ == "__main__":
    main()
