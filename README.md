# ChessBot

A small chess engine written in Python. [python-chess](https://python-chess.readthedocs.io/)
handles the rules (move generation, check, castling, en passant); ChessBot
decides which move to play.

You can play it in the terminal or in your browser, ask it to analyse a
position, or plug it into any chess GUI or bot framework that speaks the UCI
protocol.

## Quick start

Requires Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

chessbot play            # you play White
chessbot play --black    # you play Black
chessbot play --time 5   # give the bot 5 seconds a move
chessbot play --depth 3  # a weaker, faster bot
```

Enter moves as SAN (`e4`, `Nf3`, `O-O`, `exd8=Q`) or UCI (`e2e4`, `g1f3`,
`e1g1`, `e7d8q`). During a game you can also type `undo`, `hint`, `fen`,
`help` or `quit`. Use `--ascii` if your terminal doesn't draw the chess
symbols well.

### In the browser

```bash
chessbot serve --open    # or open http://localhost:8000 yourself
```

The page shows a board you can click or drag on, the engine's evaluation
and search depth as it thinks, a move list, and buttons to take back moves,
flip the board, change the thinking time and copy the game as PGN. You can
also type moves in the box next to the board. The engine runs on your
machine, in Python. The server uses only the standard library and listens
on `127.0.0.1`, so only your computer can reach it (use `--host 0.0.0.0` to
play from another device on your network).

### Online, with no install

`chessbot build-site` builds a static version of the same page in which the
engine runs in the visitor's browser: [Pyodide](https://pyodide.org) (Python
compiled to WebAssembly) loads the real `chessbot` and `python-chess` code.
The first visit downloads about 13 MB, and it searches roughly 3–4 times
slower than native Python.

The **Website** workflow (`.github/workflows/pages.yml`) builds this site and
publishes it with GitHub Pages on every push to the default branch, at
`https://<user>.github.io/<repo>/`. To switch it on once, open the repo's
**Settings → Pages** and set **Source** to **GitHub Actions** (Pages on a
private repo needs a paid GitHub plan), then re-run the workflow.

### Analysing a position

To get the best move in a position:

```bash
chessbot analyse "r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4"
# depth  1  score    mate 1  nodes       52  4. Qxf7#
# Best move: Qxf7#  (mate in 1 for White, depth 1, 0.0s)
```

## Using it with a chess GUI

Run `chessbot` with no arguments (or `python -m chessbot`) and it speaks
[UCI](https://backscattering.de/chess/uci/) on stdin/stdout. Add it as a UCI
engine in [Cute Chess](https://cutechess.com/), [Arena](http://www.playwitharena.de/),
[En Croissant](https://encroissant.org/) or similar, using the `chessbot`
executable in your virtualenv (`.venv/bin/chessbot`) as the command. The same
command works with [lichess-bot](https://github.com/lichess-bot-devs/lichess-bot)
to put it online.

It supports `go` with `depth`, `nodes`, `movetime`, `wtime`/`btime`/`winc`/`binc`/`movestogo`
and `infinite`, plus `stop`. The options are `Hash` (MB) and `Move Overhead` (ms).

## How strong is it?

We ran a short match at 0.3 seconds a move against Stockfish 16 with
`UCI_LimitStrength` on. There were 6 games per level, so treat the numbers as
rough:

| Stockfish `UCI_Elo` | ChessBot score |
| ------------------- | -------------- |
| 1320                | 5.5 / 6        |
| 1600                | 3.5 / 6        |
| 1900                | 1.5 / 6        |
| 2200                | 1.5 / 6        |

That puts it at roughly 1600 on Stockfish's scale. It searches about
25–30k positions a second, which is 7–8 plies deep in a couple of seconds
from the opening.

## How it works

| File | What it does |
| ---- | ------------ |
| [`chessbot/evaluation.py`](chessbot/evaluation.py) | Scores a position: material plus piece-square tables, blended between middlegame and endgame by how much material is left. It also knows a bishop-pair bonus, which material counts are dead draws, and how to push a lone king to the edge to mate it. |
| [`chessbot/search.py`](chessbot/search.py) | Chooses the move. Iterative-deepening alpha-beta (negamax with PVS), a transposition table, quiescence search, MVV-LVA / killer / history move ordering, null-move pruning, late-move reductions, check extensions, mate-distance scoring, and repetition and fifty-move draw detection. |
| [`chessbot/uci.py`](chessbot/uci.py) | The UCI protocol. The search runs on its own thread so `stop` and `isready` get answered while it is thinking. |
| [`chessbot/play.py`](chessbot/play.py) | The terminal game. |
| [`chessbot/webapi.py`](chessbot/webapi.py), [`chessbot/server.py`](chessbot/server.py), [`chessbot/web/`](chessbot/web) | The browser game. The page keeps the game as a list of moves and asks a backend for legal moves, notation and the engine's reply, so all chess logic stays in Python. |
| [`chessbot/site.py`](chessbot/site.py), [`chessbot/web/pyodide-backend.js`](chessbot/web/pyodide-backend.js) | The static site: the same page, answered by the engine running in a Web Worker with Pyodide instead of by the server. |
| [`chessbot/__main__.py`](chessbot/__main__.py) | The `chessbot` command. |

From Python:

```python
import chess
from chessbot import Searcher

result = Searcher().search(chess.Board(), time_limit=2.0)
print(result.best_move, result.score, result.depth, result.pv)
```

## Development

```bash
pytest               # tests: mates, tactics, stalemate avoidance, perpetual check, UCI, CLI, web API
ruff check .         # lint
ruff format .        # format
```

CI runs the same checks on Python 3.10 to 3.13 for every push and pull request.
`chessbot build-site _site` followed by `python -m http.server -d _site` lets you
try the static site locally.

Ideas for making it stronger: an opening book, static exchange evaluation
(SEE) to prune bad captures, king safety and pawn-structure terms in the
evaluation, aspiration windows, and tuning the evaluation weights with
self-play.
