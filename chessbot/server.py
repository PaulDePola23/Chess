"""A small local web server for playing ChessBot in the browser.

    chessbot serve            then open http://127.0.0.1:8000

Uses only the standard library. The page lives in ``chessbot/web``; the
engine runs here in Python and the page talks to it through JSON endpoints:

    POST /api/state   {"moves": [...], "fen"?}                  -> game state
    POST /api/move    {"moves": [...], "fen"?, "level"?}        -> engine reply + new state
    POST /api/review  {"moves": [...], "fen"?, "ply": n}        -> review of moves[n]
    POST /api/hint    {"moves": [...], "fen"?}                  -> a good move for the side to move
    POST /api/replay  {"moves": [...], "fen"?}                  -> every position of a game

Player stats go to the Supabase project named by the CHESSBOT_STATS_URL and
CHESSBOT_STATS_KEY environment variables, or stay in the browser without them.
"""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources

import chess.svg

from . import __version__
from .levels import DEFAULT_LEVEL, LEVELS
from .search import Searcher
from .webapi import engine_reply, game_state, replay_game, review_move, suggest_move

STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/lessons.json": ("lessons.json", "application/json"),
    "/showcase.json": ("showcase.json", "application/json"),
    "/puzzles.json": ("puzzles.json", "application/json"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
}
ICON_TYPES = {".png": "image/png", ".svg": "image/svg+xml"}
STOCKFISH_TYPES = {".js": "text/javascript; charset=utf-8", ".wasm": "application/wasm"}
MAX_BODY = 1_000_000


def pieces_js() -> str:
    """A script defining ``window.CHESSBOT_PIECES``: SVG artwork keyed by piece letter ("K", "k", ...).

    The drawings are Colin M.L. Burnett's pieces as shipped with python-chess.
    """
    pieces = {symbol: chess.svg.piece(chess.Piece.from_symbol(symbol)) for symbol in "PNBRQKpnbrqk"}
    return "window.CHESSBOT_PIECES = " + json.dumps(pieces) + ";\n"


def config_js(stats_url: str | None = None, stats_key: str | None = None, offline: bool = False) -> str:
    """A script defining ``window.CHESSBOT_CONFIG``, the page's settings.

    It lists the play levels, and says where player stats are kept:
    ``stats_url`` and ``stats_key`` are a Supabase project URL and its public
    (anon or publishable) key. Without them the page keeps stats in the
    browser. They default to the CHESSBOT_STATS_URL and CHESSBOT_STATS_KEY
    environment variables.
    """
    stats_url = stats_url or os.environ.get("CHESSBOT_STATS_URL") or None
    stats_key = stats_key or os.environ.get("CHESSBOT_STATS_KEY") or None
    config = {
        "levels": [level.as_dict() for level in LEVELS],
        "defaultLevel": DEFAULT_LEVEL,
        "stats": {"url": stats_url.rstrip("/"), "key": stats_key} if stats_url and stats_key else None,
        # Only the static site registers the service worker (see site.py).
        "offline": offline,
    }
    return "window.CHESSBOT_CONFIG = " + json.dumps(config) + ";\n"


def read_static(name: str) -> bytes:
    return resources.files("chessbot").joinpath("web", name).read_bytes()


class ChessBotServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address):
        super().__init__(address, ChessBotHandler)
        self.searcher = Searcher()
        # One search at a time: the engine's hash table is not thread safe,
        # and the machine only has so many cores anyway.
        self.engine_lock = threading.Lock()


class ChessBotHandler(BaseHTTPRequestHandler):
    server: ChessBotServer
    server_version = f"ChessBot/{__version__}"

    def log_request(self, code="-", size="-") -> None:
        # Keep the terminal quiet apart from failed requests.
        if int(code) >= 400:
            super().log_request(code, size)

    def send_body(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status: int, payload: dict) -> None:
        self.send_body(status, json.dumps(payload).encode(), "application/json")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/pieces.js":
            self.send_body(HTTPStatus.OK, pieces_js().encode(), "text/javascript; charset=utf-8")
        elif path == "/config.js":
            self.send_body(HTTPStatus.OK, config_js().encode(), "text/javascript; charset=utf-8")
        elif path in STATIC_FILES:
            name, content_type = STATIC_FILES[path]
            self.send_body(HTTPStatus.OK, read_static(name), content_type)
        elif path.startswith("/icons/") and "/" not in path[7:] and path[path.rfind(".") :] in ICON_TYPES:
            try:
                body = read_static("icons/" + path[7:])
            except FileNotFoundError:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": f"no such icon: {path}"})
                return
            self.send_body(HTTPStatus.OK, body, ICON_TYPES[path[path.rfind(".") :]])
        elif path.startswith("/stockfish/") and "/" not in path[11:] and path[path.rfind(".") :] in STOCKFISH_TYPES:
            try:
                body = read_static("stockfish/" + path[11:])
            except FileNotFoundError:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": f"no such file: {path}"})
                return
            self.send_body(HTTPStatus.OK, body, STOCKFISH_TYPES[path[path.rfind(".") :]])
        else:
            self.send_json(HTTPStatus.NOT_FOUND, {"error": f"no such page: {path}"})

    def do_POST(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 <= length <= MAX_BODY:
                self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "request too large"})
                return
            request = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(request, dict):
                raise ValueError("expected a JSON object")
            moves = request.get("moves", [])
            fen = request.get("fen")
            if self.path == "/api/state":
                self.send_json(HTTPStatus.OK, game_state(moves, fen))
            elif self.path == "/api/replay":
                self.send_json(HTTPStatus.OK, replay_game(moves, fen))
            elif self.path == "/api/move":
                with self.server.engine_lock:
                    reply = engine_reply(
                        moves, request.get("think_time"), self.server.searcher, fen, level=request.get("level")
                    )
                self.send_json(HTTPStatus.OK, reply)
            elif self.path == "/api/review":
                with self.server.engine_lock:
                    review = review_move(moves, request.get("ply"), self.server.searcher, fen)
                self.send_json(HTTPStatus.OK, review)
            elif self.path == "/api/hint":
                with self.server.engine_lock:
                    hint = suggest_move(moves, self.server.searcher, fen)
                self.send_json(HTTPStatus.OK, hint)
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": f"no such endpoint: {self.path}"})
        except (ValueError, TypeError) as error:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(error)})


def serve(host: str = "127.0.0.1", port: int = 8000, open_browser: bool = False) -> None:
    server = ChessBotServer((host, port))
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0', '') else host}:{server.server_address[1]}/"
    print(f"ChessBot is ready at {url}  (Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
