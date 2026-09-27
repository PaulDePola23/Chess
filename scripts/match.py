"""Play two engines against each other and tell whether one is really stronger.

Each engine runs as a UCI process, exactly as a chess GUI would run it, so any
two versions of this engine can meet, or this engine and Stockfish. Openings
come from the opening book (balanced lines 8 to 10 plies deep), each played
twice with the colours swapped. Games are adjudicated when both engines agree
one side is winning by 8 pawns, or that the game is dead level after move 40.

The result is an Elo difference with its 95% error bars, the likelihood of
superiority, and an SPRT that stops as soon as the evidence is clear (see
chessbot/sprt.py).

    # Is my working tree better than the last commit? (the usual question)
    python scripts/match.py

    # Against an older version, at a time control instead of a node count
    python scripts/match.py --base v1.2 --tc 10+0.1

    # Against Stockfish held to 1900, just for an Elo estimate
    python scripts/match.py --base stockfish:1900 --no-sprt --games 200

An engine is a directory holding this project, a git revision (exported to a
temporary directory), or "stockfish[:ELO]".
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import pathlib
import random
import shutil
import subprocess
import sys
import tempfile
import time
from multiprocessing import Pool

import chess
import chess.engine
import chess.pgn

from chessbot.book import book_moves
from chessbot.sprt import MatchStats, sprt_bounds

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAX_PLIES = 400
RESIGN_SCORE, RESIGN_MOVES = 800, 3  # both engines agree for this many moves each
DRAW_SCORE, DRAW_MOVES, DRAW_AFTER = 10, 4, 80  # ...and after this many plies


# ---------------------------------------------------------------- engines


def resolve_engine(spec: str, workdir: pathlib.Path) -> dict:
    """Turn "stockfish:1900", a directory or a git revision into how to start that engine."""
    if spec.startswith("stockfish"):
        path = shutil.which("stockfish") or "/usr/games/stockfish"
        options = {}
        if ":" in spec:
            options = {"UCI_LimitStrength": True, "UCI_Elo": int(spec.split(":", 1)[1])}
        return {"name": spec, "command": [path], "cwd": None, "options": options}
    path = pathlib.Path(spec)
    if (path / "chessbot" / "__init__.py").exists():
        tree = path.resolve()
        name = "working tree" if tree == ROOT else str(tree)
    else:
        # A git revision: export it so it can run next to the working tree.
        sha = subprocess.run(
            ["git", "rev-parse", "--short", spec], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout.strip()
        tree = workdir / sha
        if not tree.exists():
            tree.mkdir(parents=True)
            archive = subprocess.run(["git", "archive", sha], cwd=ROOT, check=True, capture_output=True).stdout
            subprocess.run(["tar", "-x", "-C", str(tree)], input=archive, check=True)
        name = f"{spec} ({sha})" if spec != sha else sha
    return {"name": name, "command": [sys.executable, "-m", "chessbot", "uci"], "cwd": str(tree), "options": {}}


def start_engine(spec: dict) -> chess.engine.SimpleEngine:
    env = dict(os.environ)
    if spec["cwd"]:
        env["PYTHONPATH"] = spec["cwd"]  # this tree's chessbot, not the installed one
    engine = chess.engine.SimpleEngine.popen_uci(spec["command"], cwd=spec["cwd"], env=env)
    if spec["options"]:
        engine.configure(spec["options"])
    return engine


# ---------------------------------------------------------------- openings


def book_openings(count: int, seed: int) -> list[list[str]]:
    """Distinct balanced openings: weighted walks through the opening book, 8 to 10 plies."""
    rng = random.Random(seed)
    seen: set[str] = set()
    openings: list[list[str]] = []
    for _ in range(count * 50):
        if len(openings) >= count:
            break
        board = chess.Board()
        target = rng.randint(8, 10)
        last_score = 0
        while len(board.move_stack) < target:
            moves = book_moves(board)
            if not moves:
                break
            # Flatter than the book's own weights, so side lines come up too.
            move, _, last_score = rng.choices(moves, weights=[math.sqrt(w) for _, w, _ in moves])[0]
            board.push(move)
        if len(board.move_stack) < 8 or abs(last_score) > 80 or board.epd() in seen:
            continue
        seen.add(board.epd())
        openings.append([move.uci() for move in board.move_stack])
    return openings


def file_openings(path: str) -> list[list[str]]:
    """Openings from a file of FENs/EPDs (one per line), as ["fen:<fen>"] entries."""
    openings = []
    for line in pathlib.Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            try:
                board = chess.Board(line)
            except ValueError:
                board, _ = chess.Board.from_epd(line)
            openings.append([f"fen:{board.fen()}"])
    return openings


def opening_board(opening: list[str]) -> chess.Board:
    if opening and opening[0].startswith("fen:"):
        return chess.Board(opening[0][4:])
    board = chess.Board()
    for uci in opening:
        board.push_uci(uci)
    return board


# ---------------------------------------------------------------- games


def make_limit(args: dict, clocks: dict) -> chess.engine.Limit:
    if args["nodes"]:
        return chess.engine.Limit(nodes=args["nodes"])
    if args["movetime"]:
        return chess.engine.Limit(time=args["movetime"] / 1000)
    return chess.engine.Limit(
        white_clock=clocks[chess.WHITE], black_clock=clocks[chess.BLACK], white_inc=args["inc"], black_inc=args["inc"]
    )


def play_game(engines: dict, opening: list[str], args: dict, game_id: object) -> tuple[str, str, chess.Board]:
    """Play one game; returns (result, how it ended, final board)."""
    board = opening_board(opening)
    clocks = {chess.WHITE: args["base"], chess.BLACK: args["base"]}
    history = {chess.WHITE: [], chess.BLACK: []}  # each engine's scores, from White's side
    while True:
        outcome = board.outcome(claim_draw=True)
        if outcome:
            return outcome.result(), outcome.termination.name.lower(), board
        if len(board.move_stack) >= MAX_PLIES:
            return "1/2-1/2", "move limit", board
        mover = board.turn
        start = time.perf_counter()
        played = engines[mover].play(board, make_limit(args, clocks), info=chess.engine.INFO_SCORE, game=game_id)
        if not (args["nodes"] or args["movetime"]):
            clocks[mover] -= time.perf_counter() - start
            if clocks[mover] < 0:
                return ("0-1" if mover == chess.WHITE else "1-0"), "time forfeit", board
            clocks[mover] += args["inc"]
        if played.move is None or played.move not in board.legal_moves:
            return ("0-1" if mover == chess.WHITE else "1-0"), "illegal move", board
        score = played.info.get("score")
        board.push(played.move)
        if score is None or not args["adjudicate"]:
            continue
        history[mover].append(score.white().score(mate_score=100_000))
        both = history[chess.WHITE][-RESIGN_MOVES:] + history[chess.BLACK][-RESIGN_MOVES:]
        if len(both) == 2 * RESIGN_MOVES:
            if all(s >= RESIGN_SCORE for s in both):
                return "1-0", "adjudicated win", board
            if all(s <= -RESIGN_SCORE for s in both):
                return "0-1", "adjudicated win", board
        level = history[chess.WHITE][-DRAW_MOVES:] + history[chess.BLACK][-DRAW_MOVES:]
        dead_level = len(level) == 2 * DRAW_MOVES and all(abs(s) <= DRAW_SCORE for s in level)
        if dead_level and len(board.move_stack) >= DRAW_AFTER:
            return "1/2-1/2", "adjudicated draw", board


def pgn_text(board: chess.Board, opening_plies: int, players: tuple, result: str, reason: str, round_: str) -> str:
    game = chess.pgn.Game.from_board(board)
    game.headers.update({"Event": "scripts/match.py", "White": players[0], "Black": players[1], "Round": round_})
    game.headers.update({"Result": result, "Termination": reason, "OpeningPlies": str(opening_plies)})
    return str(game)


def play_pair(job: tuple) -> dict:
    """Play an opening twice, the engine under test taking each colour once."""
    index, opening, new, base, args = job
    engines = {"new": start_engine(new), "base": start_engine(base)}
    try:
        scores, pgns, endings = [], [], []
        for game_number, new_color in enumerate((chess.WHITE, chess.BLACK)):
            seats = {new_color: engines["new"], not new_color: engines["base"]}
            result, reason, board = play_game(seats, opening, args, game_id=(index, game_number))
            white_score = {"1-0": 1.0, "0-1": 0.0}.get(result, 0.5)
            scores.append(white_score if new_color == chess.WHITE else 1 - white_score)
            endings.append(reason)
            if args["pgn"]:
                names = (new["name"], base["name"]) if new_color == chess.WHITE else (base["name"], new["name"])
                pgns.append(pgn_text(board, len(opening), names, result, reason, f"{index + 1}.{game_number + 1}"))
        return {"index": index, "scores": scores, "endings": endings, "pgns": pgns}
    finally:
        for engine in engines.values():
            engine.quit()


# ---------------------------------------------------------------- reporting


def status_line(stats: MatchStats, sprt: tuple | None) -> str:
    elo, margin = stats.elo()
    line = (
        f"Games {stats.games:5d}: +{stats.wins} ={stats.draws} -{stats.losses}  "
        f"Elo {elo:+6.1f} ± {margin:5.1f}  LOS {stats.los():6.1%}  pairs {stats.pairs}"
    )
    if sprt:
        lower, upper = sprt_bounds(sprt[2], sprt[3])
        line += f"  LLR {stats.llr(sprt[0], sprt[1]):+.2f} ({lower:+.2f}, {upper:+.2f})"
    return line


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--new", default=str(ROOT), help="engine under test (default: the working tree)")
    parser.add_argument("--base", default="HEAD", help="engine to compare with (default: HEAD)")
    limit = parser.add_mutually_exclusive_group()
    limit.add_argument("--nodes", type=int, help="nodes per move (default: 10000)")
    limit.add_argument("--movetime", type=int, help="milliseconds per move")
    limit.add_argument("--tc", help="clock per game as SECONDS+INCREMENT, like 10+0.1")
    parser.add_argument("--games", type=int, default=2000, help="most games to play (default: 2000)")
    parser.add_argument("--concurrency", type=int, default=os.cpu_count() or 1, help="games at once (default: CPUs)")
    parser.add_argument(
        "--sprt",
        nargs=2,
        type=float,
        default=[0.0, 10.0],
        metavar=("ELO0", "ELO1"),
        help="SPRT hypotheses (default: 0 10)",
    )
    parser.add_argument("--no-sprt", action="store_true", help="play all the games; just estimate the Elo")
    parser.add_argument("--alpha", type=float, default=0.05)
    parser.add_argument("--beta", type=float, default=0.05)
    parser.add_argument("--openings", help="file of FEN/EPD start positions (default: the opening book)")
    parser.add_argument("--seed", type=int, default=1, help="which book openings to use")
    parser.add_argument("--no-adjudicate", action="store_true", help="play every game to the end")
    parser.add_argument("--pgn", help="save the games to this PGN file")
    parser.add_argument("--json", help="save the result to this JSON file")
    args = parser.parse_args()

    if not (args.nodes or args.movetime or args.tc):
        args.nodes = 10_000
    base_time, inc = 0.0, 0.0
    if args.tc:
        base_time, _, inc_text = args.tc.partition("+")
        base_time, inc = float(base_time), float(inc_text or 0)
    sprt = None if args.no_sprt else (args.sprt[0], args.sprt[1], args.alpha, args.beta)

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="chessbot-match-"))
    try:
        new = resolve_engine(args.new, workdir)
        base = resolve_engine(args.base, workdir)
        pairs_needed = (args.games + 1) // 2
        openings = file_openings(args.openings) if args.openings else book_openings(pairs_needed, args.seed)
        if not openings:
            parser.error("no openings")
        limit_text = f"{args.nodes} nodes" if args.nodes else f"{args.movetime} ms" if args.movetime else f"{args.tc} s"
        print(
            f"{new['name']} vs {base['name']}, {limit_text} a move, {len(openings)} openings, "
            f"{args.concurrency} at a time" + (f", SPRT [{sprt[0]:g}, {sprt[1]:g}]" if sprt else ""),
            flush=True,
        )

        game_args = {
            "nodes": args.nodes,
            "movetime": args.movetime,
            "base": base_time,
            "inc": inc,
            "adjudicate": not args.no_adjudicate,
            "pgn": bool(args.pgn),
        }
        jobs = ((i, openings[i % len(openings)], new, base, game_args) for i in range(pairs_needed))
        stats = MatchStats()
        endings: dict[str, int] = {}
        verdict = "no decision: game limit reached"
        started = time.time()
        pgn_file = open(args.pgn, "w") if args.pgn else io.StringIO()
        with pgn_file, Pool(args.concurrency) as pool:
            for done, pair in enumerate(pool.imap_unordered(play_pair, jobs), 1):
                stats.add_pair(*pair["scores"])
                for ending in pair["endings"]:
                    endings[ending] = endings.get(ending, 0) + 1
                for pgn in pair["pgns"]:
                    print(pgn, end="\n\n", file=pgn_file)
                if done % 10 == 0 or done == pairs_needed:
                    print(status_line(stats, sprt), flush=True)
                if sprt:
                    llr = stats.llr(sprt[0], sprt[1])
                    lower, upper = sprt_bounds(sprt[2], sprt[3])
                    if llr >= upper:
                        verdict = f"PASSED (H1 accepted): {new['name']} is stronger"
                        break
                    if llr <= lower:
                        verdict = f"FAILED (H0 accepted): {new['name']} is not {sprt[1]:g} Elo stronger"
                        break
            pool.terminate()
        if not sprt:
            verdict = "done"

        elo, margin = stats.elo()
        print(status_line(stats, sprt))
        print(f"Endings: {', '.join(f'{k} {v}' for k, v in sorted(endings.items(), key=lambda kv: -kv[1]))}")
        print(f"{verdict}  ({time.time() - started:.0f} s)")
        summary = {
            "new": new["name"],
            "base": base["name"],
            "limit": limit_text,
            "games": stats.games,
            "wins": stats.wins,
            "draws": stats.draws,
            "losses": stats.losses,
            "pentanomial": stats.pairs,
            "elo": round(elo, 1),
            "elo_95": round(margin, 1),
            "los": round(stats.los(), 4),
            "sprt": sprt and {"elo0": sprt[0], "elo1": sprt[1], "llr": round(stats.llr(sprt[0], sprt[1]), 3)},
            "verdict": verdict,
            "endings": endings,
        }
        if args.json:
            pathlib.Path(args.json).write_text(json.dumps(summary, indent=2) + "\n")
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
                out.write(f"### {new['name']} vs {base['name']} ({limit_text} a move)\n\n")
                out.write(f"**{verdict}**\n\n| Games | W–D–L | Elo | LOS | Pairs (0–2 pts) |\n|---|---|---|---|---|\n")
                out.write(
                    f"| {stats.games} | {stats.wins}–{stats.draws}–{stats.losses} | {elo:+.1f} ± {margin:.1f} "
                    f"| {stats.los():.1%} | {stats.pairs} |\n"
                )
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
