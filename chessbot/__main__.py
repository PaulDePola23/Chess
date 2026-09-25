"""Command line entry point.

chessbot                      speak UCI on stdin/stdout (what chess GUIs expect)
chessbot play [--black]       play a game in the terminal
chessbot serve [--open]       play in the browser at http://localhost:8000
chessbot analyse FEN          print the best move for a position
"""

from __future__ import annotations

import argparse
import sys

import chess

from . import __version__
from .play import describe, play
from .search import Searcher
from .uci import format_score
from .uci import main as uci_main


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chessbot", description="A small alpha-beta chess engine.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("uci", help="run the UCI protocol on stdin/stdout (the default)")

    play_parser = commands.add_parser("play", help="play against the engine in the terminal")
    play_parser.add_argument("--black", action="store_true", help="play the black pieces")
    play_parser.add_argument("--time", type=float, default=2.0, help="engine thinking time per move in seconds")
    play_parser.add_argument("--depth", type=int, help="limit the engine's search depth (makes it weaker)")
    play_parser.add_argument("--fen", default=chess.STARTING_FEN, help="start from this position")
    play_parser.add_argument("--ascii", action="store_true", help="draw pieces as letters instead of symbols")

    serve_parser = commands.add_parser("serve", help="play in your web browser")
    serve_parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: this computer only)")
    serve_parser.add_argument("--port", type=int, default=8000, help="port to listen on (default: 8000)")
    serve_parser.add_argument("--open", action="store_true", help="open the page in your browser")

    analyse_parser = commands.add_parser("analyse", aliases=["analyze"], help="print the best move for a position")
    analyse_parser.add_argument("fen", nargs="?", default=chess.STARTING_FEN, help="position in FEN")
    analyse_parser.add_argument("--time", type=float, default=5.0, help="seconds to think")
    analyse_parser.add_argument("--depth", type=int, help="maximum search depth")
    return parser


def analyse(fen: str, time_limit: float, depth: int | None) -> int:
    try:
        board = chess.Board(fen)
    except ValueError as error:
        print(f"Invalid FEN: {error}", file=sys.stderr)
        return 2

    def report(result) -> None:
        pv = board.variation_san(result.pv)
        print(f"depth {result.depth:2d}  score {format_score(result):>9}  nodes {result.nodes:>8}  {pv}")

    result = Searcher().search(board, time_limit=time_limit, depth=depth, on_iteration=report)
    if result.best_move is None:
        print("No legal moves: " + ("checkmate." if board.is_checkmate() else "stalemate."))
        return 0
    print(f"Best move: {describe(board, result.best_move, result)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in (None, "uci"):
        uci_main()
        return 0
    if args.command == "play":
        try:
            play(
                human=chess.BLACK if args.black else chess.WHITE,
                think_time=args.time,
                depth=args.depth,
                fen=args.fen,
                unicode=not args.ascii,
            )
        except KeyboardInterrupt:
            print()
        return 0
    if args.command == "serve":
        from .server import serve

        serve(args.host, args.port, args.open)
        return 0
    return analyse(args.fen, args.time, args.depth)


if __name__ == "__main__":
    sys.exit(main())
