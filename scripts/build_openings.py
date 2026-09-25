"""Build chessbot/openings.json from the Lichess chess-openings data set.

The data set (https://github.com/lichess-org/chess-openings) is in the
public domain (CC0). Each opening line is played out with python-chess and
stored under the EPD of its final position, so that transpositions into a
known opening are recognised too.

    python scripts/build_openings.py
"""

import csv
import io
import json
import pathlib
import urllib.request

import chess
import chess.pgn

SOURCE = "https://raw.githubusercontent.com/lichess-org/chess-openings/master/{}.tsv"
OUT = pathlib.Path(__file__).resolve().parent.parent / "chessbot" / "openings.json"


def main() -> None:
    table: dict[str, list[str]] = {}
    for volume in "abcde":
        text = urllib.request.urlopen(SOURCE.format(volume)).read().decode()
        for row in csv.DictReader(io.StringIO(text), delimiter="\t"):
            game = chess.pgn.read_game(io.StringIO(row["pgn"]))
            board = game.end().board()
            # Keep the first (usually most general) name for a position.
            table.setdefault(board.epd(), [row["eco"], row["name"]])
    OUT.write_text(json.dumps(table, separators=(",", ":"), sort_keys=True) + "\n")
    print(f"{len(table)} positions written to {OUT}")


if __name__ == "__main__":
    main()
