# Paul's Chess

Play against Paul's Chess Bot, a small chess engine written in Python.
[python-chess](https://python-chess.readthedocs.io/) handles the rules (move
generation, check, castling, en passant); the bot decides which move to play.
The Python package and command are called `chessbot`.

You can play it in the terminal or in your browser, ask it to analyse a
position, or plug it into any chess GUI or bot framework that speaks the UCI
protocol.

**Play it online: https://pauldepola23.github.io/Chess/**

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

The page has six tabs, plus a light/dark switch (it follows your device's
setting until you choose):

- **Home**: a title page with a replay of Morphy's Opera Game, how to play,
  the ten levels, how it works, and credits.
- **Play**: a board you can click or drag on (or type moves), ten opponent
  levels from Rookie (about 200) to Pinky (2700), the engine's
  evaluation as it thinks, the name of the opening, and buttons to take back
  a move, resign, flip the board and copy the game as PGN. **Coach mode**
  rings pieces that can be taken for free (yours in red, the bot's in green),
  and there are three hints per game; games with hints or take-backs are
  unrated. Moves slide into place, with optional sounds. When a game ends, a review lists your
  biggest mistakes with the move you should have played; click one to see
  it on the board.
- **Puzzles**: tactics taken from real games, picked to match your puzzle
  rating, which goes up when you solve one on the first try and down when you
  miss, use a hint or look at the solution.
- **Friend**: play another person. Create a game link and send it to a
  friend; they open it, add their name and you're playing live, with draw
  offers and resigning. Anyone else with the link can watch. Or play on one
  device, taking turns.
- **Learn**: eleven short lessons for newer players (piece values, opening
  principles, checks-captures-threats, forks, pins, skewers, discovered
  attacks and basic mates), each with a puzzle to solve on the board.
- **Stats**: a leaderboard of everyone who entered a name, ranked by an
  estimated rating. Each player's page has a rating-over-time chart, badges
  to collect, results by level, and their recent games, which can be replayed
  move by move and reviewed.

The engine runs on your machine, in Python. The server uses only the
standard library and listens on `127.0.0.1`, so only your computer can reach
it (use `--host 0.0.0.0` to play from another device on your network).

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

### Levels and ratings

| Level | Rating | How it plays |
| ----- | ------ | ------------ |
| Rookie | ~200 | 1-ply search, lots of randomness, a random move 12% of the time |
| Novice | ~500 | 1-ply search, a little randomness, a random move 4% of the time |
| Casual | ~800 | 2-ply search, some randomness, a random move 3% of the time |
| Club | ~1150 | 2-ply search, a little randomness |
| Skilled | ~1450 | full search, 1,500 positions a move |
| Strong | ~1750 | full search, 12,000 positions a move |
| Expert | ~1950 | full search, 40,000 positions a move |
| Summer | ~2100 | full search, 60,000 positions a move (about 8 seconds in the browser) |
| Titan | 2500 | Stockfish with `UCI_Elo` 2500, a second a move |
| Pinky | 2700 | Stockfish with `UCI_Elo` 2700, a second a move |

The top three are named after Summer the dog, Mikayla and Paul's dog Titan,
and Pinky the cat. Titan and Pinky are Stockfish itself, which is
far beyond what a Python engine can reach: the page runs
[Stockfish.js](https://github.com/nmrugg/stockfish.js) (GPL-3.0, the "lite"
single-threaded build, about 7 MB, in `chessbot/web/stockfish/`) in a Web
Worker, loaded the first time one of them plays, and holds it to their rating with
Stockfish's own `UCI_LimitStrength`. Club and up open from the opening book.

The lower levels score the reasonable moves with a shallow search and pick
one at random, favouring the better ones. The upper levels use the full
search with a fixed node budget, so they play equally well in the browser
and natively. The ratings were measured with `scripts/calibrate_levels.py`,
which plays the levels against each other and against Stockfish 16 at fixed
`UCI_Elo` settings (20 to 70 games per pairing; the full results are in
`chessbot/levels.py`). They're rough, on Stockfish's rating scale (which
doesn't match any online site exactly), and the ones below Stockfish's
minimum of 1320 are extrapolated from games between levels.

A player's rating is an Elo, like the puzzle rating: everyone starts at
1000 and each rated game moves it up or down depending on the level they
faced, most when the result is a surprise. Games with take-backs or hints
don't count, and games from before ratings began stay in the records
without moving anyone's rating.

### Shared stats

Supabase pauses free projects after about a week without requests. The
**Keep the site awake and check it** workflow (`.github/workflows/keepalive.yml`)
reads a row from each table every three days so that never happens, and
fetches the live site's main files and engines; if the project gets
paused anyway, the run fails and GitHub emails you, and you can restore it
from the Supabase dashboard. GitHub switches off schedules in repositories
without commits for 60 days, so the workflow switches itself back on each
time it runs.

If your tables were created from an older `games.sql`, also run the upgrades:
[`supabase/upgrade-1.sql`](supabase/upgrade-1.sql) (hints and replays; until
then games still save, just without those details) and
[`supabase/upgrade-2.sql`](supabase/upgrade-2.sql) (puzzle ratings; until then
they stay in each browser) and [`supabase/upgrade-3.sql`](supabase/upgrade-3.sql)
(online games on the Friend tab).

### Playing a friend online

A game between two people lives in the `live_games` table, and both
browsers check it every second and a half. Anyone can read a game, but only
its two players can change it: each seat gets a random token that stays in
that player's browser, and every change (joining, moving, offering a draw,
resigning) goes through a database function that checks the token, whose
turn it is and that exactly one move was added. The tokens are in a table
the public key can't read. Both browsers check with python-chess that the
moves are legal. Games against friends don't count on the Stats page.

### Installing and offline play

The public site can be installed as an app (Chrome and Edge offer an
**Install the app** button on the Home tab; on iPhone use Share → Add to
Home Screen). A service worker (`chessbot/web/sw.js`, filled in by
`build-site` with the file list and a version hash) caches the site and the
Python engine after the first visit, so games, puzzles and lessons work
offline. Games finished offline are saved when the connection returns.

### Puzzles

`scripts/generate_puzzles.py` builds `chessbot/web/puzzles.json` the way
Lichess builds its puzzle database, on a smaller scale: Stockfish plays itself
at club strength, full-strength Stockfish finds the positions where one side
has just blundered and exactly one move punishes it, and the solution runs on
while there's still exactly one winning move. Each puzzle is rated by the
weakest ChessBot search that finds the answer. `tests/test_puzzles.py` checks
every puzzle is legal.

The public site keeps everyone's games in a Supabase project, configured in
`.github/workflows/pages.yml`. Without one (for example in a fork, or with
`chessbot serve`), stats are kept in each visitor's browser. To use your own
free [Supabase](https://supabase.com) project:

1. Create a project, open **SQL Editor**, and run [`supabase/games.sql`](supabase/games.sql).
   It creates a `games` table that anyone can read and add to, but not change.
2. From the project's **API** settings, copy the **Project URL** and the public
   **anon** (or **publishable**) key. Both are meant to be public; never use the
   secret or `service_role` key.
3. Put them in `.github/workflows/pages.yml`, or add repository variables named
   `CHESSBOT_STATS_URL` and `CHESSBOT_STATS_KEY` under **Settings → Secrets and
   variables → Actions → Variables** (these take precedence), then re-run the
   **Website** workflow.

For `chessbot serve` or `chessbot build-site`, set the same two environment
variables (or pass `--stats-url` and `--stats-key` to `build-site`). Names
and results are public, and because the site has no logins, anyone could
submit made-up results.

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

The full engine's strength depends on how long it thinks. Measured against
Stockfish 16 with `UCI_LimitStrength` (the calibration behind the levels
table above, 20–24 games per pairing):

| Positions a move | Time natively | Rating (Stockfish's scale) |
| ---------------- | ------------- | -------------------------- |
| 1,500            | 0.06 s        | ~1450 (Skilled)            |
| 12,000           | 0.5 s         | ~1750 (Strong)             |
| 40,000           | 1.6 s         | ~1950 (Expert)             |
| 60,000           | 2.4 s         | ~2100 (Summer)             |
| 80,000           | 3.2 s         | ~2230                      |

It searches about 25,000 positions a second in Python, three to four times
slower in the browser. The pawn-structure, rook and king-shelter terms in the
evaluation were worth about 50 Elo on their own (58 wins, 22 draws and 40
losses against the previous evaluation at the same node count).

## How it works

| File | What it does |
| ---- | ------------ |
| [`chessbot/evaluation.py`](chessbot/evaluation.py) | Scores a position: material plus piece-square tables, blended between middlegame and endgame by how much material is left, with pawn structure (doubled, isolated and passed pawns), rooks on open files and the pawn shield in front of the king. It also knows a bishop-pair bonus, which material counts are dead draws, and how to push a lone king to the edge to mate it. |
| [`chessbot/search.py`](chessbot/search.py) | Chooses the move. Iterative-deepening alpha-beta (negamax with PVS), a transposition table, quiescence search, MVV-LVA / killer / history move ordering, null-move pruning, late-move reductions, check extensions, mate-distance scoring, and repetition and fifty-move draw detection. |
| [`chessbot/uci.py`](chessbot/uci.py) | The UCI protocol. The search runs on its own thread so `stop` and `isready` get answered while it is thinking. |
| [`chessbot/openings.py`](chessbot/openings.py) | Opening names, from the public-domain [Lichess chess-openings](https://github.com/lichess-org/chess-openings) data set (`scripts/build_openings.py` rebuilds `openings.json`). |
| [`chessbot/book.py`](chessbot/book.py) | The opening book the Club level and up play from: the moves of the named Lichess opening lines, kept only where Stockfish rates them within a third of a pawn of its best move (`scripts/build_book.py` rebuilds `book.json`). Main lines come up more often than sidelines. |
| [`chessbot/levels.py`](chessbot/levels.py) | The ten play levels, their measured ratings, and how the weaker ones choose their moves. |
| [`chessbot/play.py`](chessbot/play.py) | The terminal game. |
| [`chessbot/webapi.py`](chessbot/webapi.py), [`chessbot/server.py`](chessbot/server.py), [`chessbot/web/`](chessbot/web) | The browser game. The page keeps the game as a list of moves and asks a backend for legal moves, notation, the engine's reply and the post-game review, so all chess logic stays in Python. Lessons live in `chessbot/web/lessons.json`; `tests/test_lessons.py` checks every puzzle with the engine. |
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
pytest               # tests: mates, tactics, perpetual check, levels, review, lesson puzzles, UCI, CLI, web API
ruff check .         # lint
ruff format .        # format
```

CI runs the same checks on Python 3.10 to 3.13 for every push and pull request.
`chessbot build-site _site` followed by `python -m http.server -d _site` lets you
try the static site locally.

Ideas for making it stronger: static exchange evaluation (SEE) to prune bad
captures, aspiration windows, mobility in the evaluation, and tuning the
evaluation weights with self-play.

## License

ChessBot is free software under the [GNU General Public License v3.0 or later](LICENSE),
the same license as [python-chess](https://github.com/niklasf/python-chess), which it
builds on. The chess piece images are Colin M.L. Burnett's, as shipped with python-chess.
