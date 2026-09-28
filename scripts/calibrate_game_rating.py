"""Measure how accurately rated human players play, as the post-game review scores them.

The review gives every move an accuracy (lichess's formula) and a game the
average of its player's moves'. This takes real rated games from the lichess
database, reviews one player's moves in each exactly as the website does
(webapi.review_move at REVIEW_DEPTH), and reports the average accuracy of the
players in each 100-point rating band: the points of the "game rating" scale in
chessbot/game_rating.py. It runs on GitHub (.github/workflows/game-rating.yml),
in three steps:

    curl -sL https://database.lichess.org/standard/lichess_db_standard_rated_2025-06.pgn.zst \\
      | zstdcat | python scripts/calibrate_game_rating.py sample > sides.jsonl
    python scripts/calibrate_game_rating.py review --shard 0/8 < sides.jsonl > reviewed-0.jsonl
    python scripts/calibrate_game_rating.py report reviewed-*.jsonl

``sample`` keeps rapid and classical games (at least ten minutes each, as
lichess estimates a game's length) that ended on the board or on time, between
people (no bots), and takes one side of a game for a band until the band has
enough, each player at most once.
"""

from __future__ import annotations

import argparse
import io
import json
import random
import statistics
import sys
from multiprocessing import Pool

import chess
import chess.pgn

from chessbot.search import Searcher
from chessbot.webapi import review_move

BAND = 100
MIN_MOVES = 8  # the website shows no game rating for fewer reviewed moves
MIN_SECONDS = 600  # base + 40 x increment, lichess's estimate of a game's length


def band_of(rating: int) -> int:
    return rating // BAND * BAND


def estimated_seconds(time_control: str) -> int:
    """Lichess's estimate of a game's length from its TimeControl header ("600+5"); 0 for none."""
    base, _, increment = time_control.partition("+")
    try:
        return int(base) + 40 * int(increment or 0)
    except ValueError:
        return 0


def pgn_games(stream: io.TextIOBase):
    """(headers, movetext) for each game of a lichess PGN file, whose movetext is on one line."""
    headers: dict[str, str] = {}
    for line in stream:
        if line.startswith("["):
            key, _, value = line[1:].partition(" ")
            headers[key] = value.strip().rstrip("]").strip('"')
        elif line.strip():
            yield headers, line
            headers = {}


def sample(args: argparse.Namespace) -> None:
    stream = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    wanted = range(args.low, args.high + 1, BAND)
    taken = {band: 0 for band in wanted}
    players: set[str] = set()
    rng = random.Random(0)
    scanned = 0
    for headers, movetext in pgn_games(stream):
        scanned += 1
        if scanned % 500_000 == 0:
            print(f"{scanned} games scanned, {sum(taken.values())} sides taken", file=sys.stderr)
        if scanned >= args.max_games or all(count >= args.per_band for count in taken.values()):
            break
        if (
            estimated_seconds(headers.get("TimeControl", "-")) < MIN_SECONDS
            or headers.get("Termination") not in ("Normal", "Time forfeit")
            or "BOT" in (headers.get("WhiteTitle"), headers.get("BlackTitle"))
        ):
            continue
        try:
            ratings = {"white": int(headers["WhiteElo"]), "black": int(headers["BlackElo"])}
        except (KeyError, ValueError):
            continue
        if abs(ratings["white"] - ratings["black"]) > args.max_gap:
            continue
        sides = [
            color
            for color in ("white", "black")
            if taken.get(band_of(ratings[color]), args.per_band) < args.per_band
            and headers.get(color.title(), "?").lower() not in players
        ]
        if not sides:
            continue
        game = chess.pgn.read_game(io.StringIO(movetext))
        moves = [move.uci() for move in game.mainline_moves()] if game and not game.errors else []
        color = rng.choice(sides)
        if len(moves[0 if color == "white" else 1 :: 2]) < MIN_MOVES:
            continue
        taken[band_of(ratings[color])] += 1
        players.add(headers.get(color.title(), "?").lower())
        side = {"game": headers.get("Site", ""), "color": color, "rating": ratings[color], "moves": moves}
        print(json.dumps(side), flush=True)
    print(f"{scanned} games scanned; sides per band: {taken}", file=sys.stderr)


def review_side(side: dict) -> dict:
    """A side's game accuracy, as the website's review scores it."""
    moves = side["moves"]
    first = 0 if side["color"] == "white" else 1
    reviewer = Searcher()
    accuracies = [review_move(moves, ply, reviewer)["accuracy"] for ply in range(first, len(moves), 2)]
    return {
        "game": side["game"],
        "rating": side["rating"],
        "moves": len(accuracies),
        "accuracy": round(statistics.mean(accuracies), 2),
    }


def review(args: argparse.Namespace) -> None:
    index, count = (int(part) for part in args.shard.split("/"))
    sides = [json.loads(line) for number, line in enumerate(sys.stdin) if number % count == index]
    with Pool(args.workers) as pool:
        for result in pool.imap_unordered(review_side, sides):
            print(json.dumps(result), flush=True)


def monotone(values: list[float], weights: list[float]) -> list[float]:
    """The closest non-decreasing sequence (pool adjacent violators, weighted)."""
    blocks: list[list[float]] = []  # [mean, weight, length]
    for value, weight in zip(values, weights, strict=True):
        blocks.append([value, weight, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            (a, wa, na), (b, wb, nb) = blocks.pop(-2), blocks.pop()
            blocks.append([(a * wa + b * wb) / (wa + wb), wa + wb, na + nb])
    return [mean for mean, _, length in blocks for _ in range(length)]


def report(args: argparse.Namespace) -> None:
    by_band: dict[int, list[float]] = {}
    for path in args.files:
        with open(path) as file:
            for line in file:
                side = json.loads(line)
                by_band.setdefault(band_of(side["rating"]), []).append(side["accuracy"])
    bands = sorted(band for band, values in by_band.items() if len(values) >= max(args.min_sides, 2))
    means = [statistics.mean(by_band[band]) for band in bands]
    fitted = monotone(means, [len(by_band[band]) for band in bands])
    lines = [
        "| band | sides | mean | sd | quartiles | monotone |",
        "|---|---|---|---|---|---|",
    ]
    for band, mean, fit in zip(bands, means, fitted, strict=True):
        values = by_band[band]
        low, median, high = statistics.quantiles(values, n=4)
        lines.append(
            f"| {band}-{band + BAND - 1} | {len(values)} | {mean:.1f} | {statistics.stdev(values):.1f}"
            f" | {low:.1f} / {median:.1f} / {high:.1f} | {fit:.1f} |"
        )
    points = [(round(fit, 1), band + BAND // 2) for band, fit in zip(bands, fitted, strict=True)]
    lines += ["", "Points (monotone mean accuracy, band middle):", "", f"    {points}"]
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sampler = commands.add_parser("sample", help="lichess PGN on stdin -> sides to review, one JSON per line")
    sampler.add_argument("--per-band", type=int, default=150, help="sides per 100-point band (default 150)")
    sampler.add_argument("--low", type=int, default=600, help="lowest band (default 600)")
    sampler.add_argument("--high", type=int, default=2500, help="highest band (default 2500)")
    sampler.add_argument("--max-gap", type=int, default=300, help="largest rating gap between the players")
    sampler.add_argument("--max-games", type=int, default=10_000_000, help="stop after this many games")
    sampler.set_defaults(run=sample)
    reviewer = commands.add_parser("review", help="sides on stdin -> their accuracy, one JSON per line")
    reviewer.add_argument("--shard", default="0/1", help="review every Nth side from the Ith: I/N")
    reviewer.add_argument("--workers", type=int, default=4)
    reviewer.set_defaults(run=review)
    reporter = commands.add_parser("report", help="the accuracy of each rating band, as a Markdown table")
    reporter.add_argument("files", nargs="+")
    reporter.add_argument("--min-sides", type=int, default=30, help="leave out bands with fewer sides")
    reporter.set_defaults(run=report)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
