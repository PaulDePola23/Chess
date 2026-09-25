"""Build a static version of the browser game, for hosting on GitHub Pages or any web server.

    chessbot build-site _site

The static site needs no server-side Python: the engine runs in the visitor's
browser with Pyodide (Python compiled to WebAssembly). It is the same page as
``chessbot serve`` plus ``pyodide-backend.js``, which answers the page's
requests in a Web Worker instead of over HTTP, and ``python.json``, which
holds the source of chessbot and python-chess for Pyodide to import.
"""

from __future__ import annotations

import hashlib
import json
from importlib import resources
from pathlib import Path

import chess

from .server import config_js, pieces_js

# The modules the engine needs in the browser. chess.pgn imports chess.engine
# and chess.svg, so those come along too.
CHESSBOT_MODULES = [
    "__init__.py",
    "evaluation.py",
    "search.py",
    "levels.py",
    "book.py",
    "book.json",
    "openings.py",
    "openings.json",
    "webapi.py",
]
CHESS_MODULES = ["__init__.py", "pgn.py", "engine.py", "svg.py"]
# Stockfish.js for the Titan and Pinky levels (see web/stockfish/README.md).
STOCKFISH_FILES = ["stockfish-18-lite-single.js", "stockfish-18-lite-single.wasm", "COPYING.txt"]

SERVER_FOOTER = "<p>The engine is <code>chessbot/search.py</code> and <code>chessbot/evaluation.py</code>."
STATIC_FOOTER = (
    "<p>The engine is <code>chessbot/search.py</code> and <code>chessbot/evaluation.py</code>, "
    "running in your browser with Pyodide."
)


def python_sources() -> dict[str, str]:
    """Source of the Python modules the page loads into Pyodide, keyed by path."""
    package = resources.files("chessbot")
    sources = {f"chessbot/{name}": package.joinpath(name).read_text() for name in CHESSBOT_MODULES}
    chess_dir = Path(chess.__file__).parent
    sources.update({f"chess/{name}": (chess_dir / name).read_text() for name in CHESS_MODULES})
    return sources


def build_site(out_dir: str | Path, stats_url: str | None = None, stats_key: str | None = None) -> Path:
    """Write the static site into ``out_dir`` (created if needed) and return its path.

    ``stats_url`` and ``stats_key`` point the stats page at a Supabase project
    (see ``config_js``); without them stats stay in each visitor's browser.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    web = resources.files("chessbot").joinpath("web")

    page = web.joinpath("index.html").read_text()
    app_script = '<script src="app.js"></script>'
    for old, new in [
        (app_script, '<script src="pyodide-backend.js"></script>\n' + app_script),
        (SERVER_FOOTER, STATIC_FOOTER),
    ]:
        if old not in page:
            raise RuntimeError(f"index.html no longer contains {old!r}; update chessbot/site.py")
        page = page.replace(old, new)

    (out / "index.html").write_text(page)
    for name in [
        "app.js",
        "style.css",
        "pyodide-backend.js",
        "lessons.json",
        "showcase.json",
        "puzzles.json",
        "manifest.webmanifest",
    ]:
        (out / name).write_text(web.joinpath(name).read_text())
    (out / "icons").mkdir(exist_ok=True)
    for icon in web.joinpath("icons").iterdir():
        (out / "icons" / icon.name).write_bytes(icon.read_bytes())
    (out / "stockfish").mkdir(exist_ok=True)
    for name in STOCKFISH_FILES:
        (out / "stockfish" / name).write_bytes(web.joinpath("stockfish", name).read_bytes())
    (out / "pieces.js").write_text(pieces_js())
    (out / "config.js").write_text(config_js(stats_url, stats_key, offline=True))
    (out / "python.json").write_text(json.dumps(python_sources()))
    write_service_worker(out, web.joinpath("sw.js").read_text())
    # Serve files as they are; GitHub Pages would otherwise run them through Jekyll.
    (out / ".nojekyll").write_text("")
    return out


def write_service_worker(out: Path, template: str) -> None:
    """Write sw.js with the list of files to cache and a version that changes with them."""
    files = sorted(p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file() and p.name != "sw.js")
    digest = hashlib.sha256()
    for name in files:
        digest.update(name.encode())
        digest.update((out / name).read_bytes())
    # Stockfish (7 MB) is cached when someone first plays Titan or Pinky, not up front.
    app_files = ["./", *(name for name in files if not name.startswith((".", "stockfish/")))]
    worker = template.replace("__VERSION__", digest.hexdigest()[:12]).replace("__APP_FILES__", json.dumps(app_files))
    (out / "sw.js").write_text(worker)
