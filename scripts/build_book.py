"""Build chessbot/book.json, the opening book the stronger play levels use.

Candidate moves come from the named opening lines in the Lichess
chess-openings data set (public domain): every position within the first
BOOK_PLIES plies of a named line, and the moves those lines continue with.
Stockfish then vets each candidate, keeping only moves within MARGIN
centipawns of its best move, so gambits that simply lose material drop out.
A move's weight is how many named lines continue with it, so main lines are
played more often than sidelines.

book.json maps a position's EPD to [[move, weight, centipawns], ...], the
centipawns being Stockfish's score for the move from the mover's side.

    python scripts/build_book.py                  # downloads the data set
    python scripts/build_book.py --source DIR     # or reads DIR/a.tsv ... e.tsv
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import pathlib
import shutil
import urllib.request
from collections import defaultdict

import chess
import chess.engine
import chess.pgn

SOURCE = "https://raw.githubusercontent.com/lichess-org/chess-openings/master/{}.tsv"
OUT = pathlib.Path(__file__).resolve().parent.parent / "chessbot" / "book.json"
BOOK_PLIES = 12
MARGIN = 35
MATE = 100_000
CAP = 2_000


def read_volume(volume: str, source: str | None) -> str:
    if source:
        return (pathlib.Path(source) / f"{volume}.tsv").read_text()
    return urllib.request.urlopen(SOURCE.format(volume)).read().decode()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stockfish", default=shutil.which("stockfish") or "/usr/games/stockfish")
    parser.add_argument("--depth", type=int, default=16)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--source", help="directory holding a.tsv ... e.tsv (default: download them)")
    args = parser.parse_args()

    # Position (EPD) -> move -> number of named lines that play it there.
    candidates: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    boards: dict[str, chess.Board] = {}
    for volume in "abcde":
        for row in csv.DictReader(io.StringIO(read_volume(volume, args.source)), delimiter="\t"):
            game = chess.pgn.read_game(io.StringIO(row["pgn"]))
            board = game.board()
            for move in game.mainline_moves():
                if len(board.move_stack) >= BOOK_PLIES:
                    break
                epd = board.epd()
                boards.setdefault(epd, board.copy())
                candidates[epd][move.uci()] += 1
                board.push(move)

    engine = chess.engine.SimpleEngine.popen_uci(args.stockfish)
    engine.configure({"Threads": args.threads, "Hash": 256})
    limit = chess.engine.Limit(depth=args.depth)
    book: dict[str, list[list]] = {}
    try:
        for index, (epd, moves) in enumerate(candidates.items(), 1):
            board = boards[epd]
            best = engine.analyse(board, limit)["score"].pov(board.turn).score(mate_score=MATE)
            # One more search restricted to the candidates scores each of them.
            root_moves = [chess.Move.from_uci(uci) for uci in moves]
            infos = engine.analyse(board, limit, multipv=len(root_moves), root_moves=root_moves)
            scores = {info["pv"][0].uci(): info["score"].pov(board.turn).score(mate_score=MATE) for info in infos}
            best = max(best, *scores.values())
            # A few named lines are traps that end in a won position or a mate;
            # cap those scores so they read as a big advantage, not a mate score.
            kept = [
                [uci, weight, max(-CAP, min(CAP, scores[uci]))]
                for uci, weight in moves.items()
                if scores[uci] >= best - MARGIN
            ]
            if kept:
                book[epd] = sorted(kept, key=lambda item: (-item[1], item[0]))
            if index % 250 == 0:
                print(f"{index}/{len(candidates)} positions, {len(book)} in the book", flush=True)
    finally:
        engine.quit()
    OUT.write_text(json.dumps(book, separators=(",", ":"), sort_keys=True) + "\n")
    print(f"{len(book)} book positions, {sum(map(len, book.values()))} moves, written to {OUT}")


if __name__ == "__main__":
    main()
