"""Universal Chess Interface (UCI) front end.

UCI is the text protocol chess GUIs (Arena, Cute Chess, Banksia, ...) and
tools such as lichess-bot use to talk to engines. The GUI writes commands to
the engine's stdin and reads replies from its stdout. The search runs on a
background thread so that ``stop`` and ``isready`` are answered while it
thinks.
"""

from __future__ import annotations

import sys
import threading
from typing import TextIO

import chess

from . import __version__
from .search import Searcher, SearchResult, time_budget

ENGINE_NAME = f"ChessBot {__version__}"
ENGINE_AUTHOR = "the ChessBot developers"


def format_score(result: SearchResult) -> str:
    mate_in = result.mate_in
    return f"mate {mate_in}" if mate_in is not None else f"cp {result.score}"


class UCIEngine:
    def __init__(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout):
        self.stdin = stdin
        self.stdout = stdout
        self.board = chess.Board()
        self.searcher = Searcher()
        self.move_overhead = 0.05
        self._output_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._search_thread: threading.Thread | None = None
        self._search_bounded = False

    def send(self, line: str) -> None:
        with self._output_lock:
            self.stdout.write(line + "\n")
            self.stdout.flush()

    def run(self) -> None:
        """Process commands until ``quit`` or end of input."""
        for line in self.stdin:
            if not self.handle(line):
                self.stop_search()
                return
        # Input ended (e.g. commands piped in from a file): let a search with a
        # depth, time or node limit finish so its bestmove is printed.
        if self._search_thread is not None and self._search_bounded:
            self._search_thread.join()
        self.stop_search()

    def handle(self, line: str) -> bool:
        """Handle one command line. Returns False when the engine should exit."""
        tokens = line.split()
        if not tokens:
            return True
        command, args = tokens[0], tokens[1:]

        if command == "uci":
            self.send(f"id name {ENGINE_NAME}")
            self.send(f"id author {ENGINE_AUTHOR}")
            self.send("option name Hash type spin default 64 min 1 max 1024")
            self.send("option name Move Overhead type spin default 50 min 0 max 5000")
            self.send("uciok")
        elif command == "isready":
            self.send("readyok")
        elif command == "ucinewgame":
            self.stop_search()
            self.board = chess.Board()
            self.searcher.new_game()
        elif command == "setoption":
            self.set_option(args)
        elif command == "position":
            self.stop_search()
            self.set_position(args)
        elif command == "go":
            self.stop_search()
            self.go(args)
        elif command == "stop":
            self.stop_search()
        elif command == "quit":
            return False
        elif command == "d":
            # Not part of UCI, but handy when poking at the engine by hand.
            self.send(str(self.board))
            self.send(f"Fen: {self.board.fen()}")
        # Unknown commands are ignored, as the protocol asks.
        return True

    def set_option(self, args: list[str]) -> None:
        if "name" not in args:
            return
        if "value" in args:
            name = " ".join(args[args.index("name") + 1 : args.index("value")]).lower()
            value = " ".join(args[args.index("value") + 1 :])
        else:
            name, value = " ".join(args[args.index("name") + 1 :]).lower(), ""
        try:
            if name == "hash":
                # Roughly 200 bytes per entry in a Python dict.
                self.searcher.hash_entries = max(1, int(value)) * 1024 * 1024 // 200
            elif name == "move overhead":
                self.move_overhead = max(0, int(value)) / 1000
        except ValueError:
            pass

    def set_position(self, args: list[str]) -> None:
        if not args:
            return
        if "moves" in args:
            moves_at = args.index("moves")
            setup, moves = args[:moves_at], args[moves_at + 1 :]
        else:
            setup, moves = args, []
        try:
            if setup[0] == "startpos":
                board = chess.Board()
            elif setup[0] == "fen":
                board = chess.Board(" ".join(setup[1:]))
            else:
                return
            for move in moves:
                board.push_uci(move)
        except ValueError as error:
            self.send(f"info string invalid position: {error}")
            return
        self.board = board

    def go(self, args: list[str]) -> None:
        params: dict[str, int] = {}
        infinite = False
        i = 0
        while i < len(args):
            name = args[i]
            if name == "infinite":
                infinite = True
            elif name in ("wtime", "btime", "winc", "binc", "movestogo", "depth", "nodes", "movetime", "mate"):
                if i + 1 < len(args):
                    try:
                        params[name] = int(args[i + 1])
                    except ValueError:
                        pass
                    i += 1
            i += 1

        depth = params.get("depth")
        if "mate" in params:
            depth = depth or 2 * params["mate"]
        time_limit = None
        if not infinite:
            if "movetime" in params:
                time_limit = max(params["movetime"] / 1000 - self.move_overhead, 0.01)
            else:
                clock, increment = ("wtime", "winc") if self.board.turn == chess.WHITE else ("btime", "binc")
                if clock in params:
                    time_limit = time_budget(
                        params[clock] / 1000,
                        params.get(increment, 0) / 1000,
                        params.get("movestogo"),
                        self.move_overhead,
                    )

        board = self.board.copy()
        self._search_bounded = any(limit is not None for limit in (depth, time_limit, params.get("nodes")))
        self._stop_event = threading.Event()
        self._search_thread = threading.Thread(
            target=self._search,
            args=(board, depth, time_limit, params.get("nodes")),
            daemon=True,
        )
        self._search_thread.start()

    def _search(self, board: chess.Board, depth, time_limit, nodes) -> None:
        def report(result: SearchResult) -> None:
            nps = int(result.nodes / result.elapsed) if result.elapsed > 0 else 0
            self.send(
                f"info depth {result.depth} score {format_score(result)} nodes {result.nodes} "
                f"nps {nps} time {int(result.elapsed * 1000)} pv {' '.join(m.uci() for m in result.pv)}"
            )

        try:
            result = self.searcher.search(
                board,
                depth=depth,
                time_limit=time_limit,
                nodes=nodes,
                stop_event=self._stop_event,
                on_iteration=report,
            )
            best = result.best_move.uci() if result.best_move else "0000"
        except Exception as error:  # Never leave the GUI waiting for a bestmove.
            self.send(f"info string search failed: {error!r}")
            best = next((m.uci() for m in board.legal_moves), "0000")
        self.send(f"bestmove {best}")

    def stop_search(self) -> None:
        if self._search_thread is not None:
            self._stop_event.set()
            self._search_thread.join()
            self._search_thread = None


def main() -> None:
    UCIEngine().run()
