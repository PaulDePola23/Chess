"""Tactics test: how many puzzles an engine solves at a fixed budget.

By default it uses the Puzzles tab's positions (chessbot/web/puzzles.json),
each checked with Stockfish to have exactly one winning move; any EPD test
suite with "bm" (best move) or "am" (avoid move) operations works too. It is
quicker and noisier than a match, and only measures tactics, but a change
that makes the engine stronger shouldn't make it solve fewer.

    python scripts/tactics.py                          # the working tree, 20,000 nodes a puzzle
    python scripts/tactics.py --engine HEAD~3 --nodes 5000
    python scripts/tactics.py --epd suite.epd --movetime 1000
    python scripts/tactics.py --engine stockfish:2000

Engines are given as in scripts/match.py: a directory, a git revision or
"stockfish[:ELO]".
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import tempfile
import time
from multiprocessing import Pool

import chess
import chess.engine
from match import ROOT, resolve_engine, start_engine

PUZZLES = ROOT / "chessbot" / "web" / "puzzles.json"
BANDS = [(0, 1000), (1000, 1400), (1400, 1800), (1800, 9999)]

_engine = None


def load_puzzles(limit: int | None) -> list[dict]:
    items = []
    for puzzle in json.loads(PUZZLES.read_text()):
        board = chess.Board(puzzle["fen"])
        board.push_uci(puzzle["moves"][0])  # the opponent's mistake; the solver moves next
        item = {"id": puzzle["id"], "fen": board.fen(), "best": [puzzle["moves"][1]], "avoid": []}
        items.append({**item, "rating": puzzle["rating"]})
    return items[:limit] if limit else items


def load_epd(path: str, limit: int | None) -> list[dict]:
    items = []
    for number, line in enumerate(pathlib.Path(path).read_text().splitlines(), 1):
        if not line.strip() or line.startswith("#"):
            continue
        board, ops = chess.Board.from_epd(line)
        best = [move.uci() for move in ops.get("bm", [])]
        avoid = [move.uci() for move in ops.get("am", [])]
        if best or avoid:
            items.append({"id": ops.get("id", str(number)), "fen": board.fen(), "best": best, "avoid": avoid})
    return items[:limit] if limit else items


def init_worker(spec: dict) -> None:
    global _engine
    _engine = start_engine(spec)


def solve(job: tuple) -> dict:
    item, limit = job
    board = chess.Board(item["fen"])
    played = _engine.play(board, limit, game=item["id"])  # a new game each time, so nothing carries over
    move = played.move.uci() if played.move else None
    solved = (not item["best"] or move in item["best"]) and move not in item["avoid"]
    return {**item, "played": move, "solved": solved}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default=str(ROOT), help="engine to test (default: the working tree)")
    limit = parser.add_mutually_exclusive_group()
    limit.add_argument("--nodes", type=int, help="nodes per position (default: 20000)")
    limit.add_argument("--movetime", type=int, help="milliseconds per position")
    parser.add_argument("--epd", help="EPD test suite (default: the Puzzles tab's puzzles)")
    parser.add_argument("--limit", type=int, help="only the first N positions")
    parser.add_argument("--concurrency", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--show-failures", action="store_true", help="list the positions it got wrong")
    args = parser.parse_args()

    if args.movetime:
        search_limit = chess.engine.Limit(time=args.movetime / 1000)
    else:
        search_limit = chess.engine.Limit(nodes=args.nodes or 20_000)
    items = load_epd(args.epd, args.limit) if args.epd else load_puzzles(args.limit)
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="chessbot-tactics-"))
    try:
        spec = resolve_engine(args.engine, workdir)
        limit_text = f"{args.movetime} ms" if args.movetime else f"{args.nodes or 20_000} nodes"
        print(f"{spec['name']}: {len(items)} positions, {limit_text} each", flush=True)
        started = time.time()
        with Pool(args.concurrency, initializer=init_worker, initargs=(spec,)) as pool:
            results = pool.map(solve, [(item, search_limit) for item in items], chunksize=4)
        solved = sum(r["solved"] for r in results)
        print(f"Solved {solved} of {len(results)} ({solved / len(results):.1%}) in {time.time() - started:.0f} s")
        if not args.epd:
            for low, high in BANDS:
                band = [r for r in results if low <= r["rating"] < high]
                if band:
                    label = f"{low}-{high - 1}" if high < 9999 else f"{low}+"
                    print(f"  puzzles rated {label:>9}: {sum(r['solved'] for r in band):3d} of {len(band):3d}")
        if args.show_failures:
            for r in results:
                if not r["solved"]:
                    wanted = " or ".join(r["best"]) or "not " + " or ".join(r["avoid"])
                    print(f"  {r['id']}: played {r['played']}, wanted {wanted}")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
