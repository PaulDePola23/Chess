"""Generate rated tactics puzzles for the Puzzles tab (chessbot/web/puzzles.json).

Needs Stockfish on the PATH (or --stockfish). The method follows the one
Lichess uses for its puzzle database, on a smaller scale:

1. Stockfish plays itself at club strength (UCI_LimitStrength), which
   produces realistic mistakes.
2. Every position is checked with full-strength Stockfish. A puzzle starts
   where one side has just made a mistake that the other can punish with
   exactly one move: the best move wins at least 2.5 pawns (or mates), and
   the second-best is at least 2.5 pawns worse.
3. The solution continues while the solver keeps having exactly one winning
   move, up to three moves (all the way for short mates).
4. A puzzle's rating is estimated from the weakest ChessBot level that finds
   the first move, plus a little for each extra move in the solution.

    python scripts/generate_puzzles.py --games 300 --workers 4
"""

from __future__ import annotations

import argparse
import json
import pathlib
import random
import shutil
import sys
from multiprocessing import Pool

import chess
import chess.engine

from chessbot.search import Searcher

OUT = pathlib.Path(__file__).resolve().parent.parent / "chessbot" / "web" / "puzzles.json"
WIN = 250  # centipawns: a move must win at least this much...
GAP = 250  # ...and be this much better than the second-best move.
MAX_SOLVER_MOVES = 3
ANALYSIS = chess.engine.Limit(depth=14)


def score(info: dict, pov: chess.Color) -> int:
    return info["score"].pov(pov).score(mate_score=100_000)


MATE_IN_ONE = 100_000 - 1


def only_move(infos: list[dict], pov: chess.Color):
    """(best move, its score) if the side to move has exactly one winning move, else None."""
    if not infos or "pv" not in infos[0]:
        return None
    best = score(infos[0], pov)
    second = score(infos[1], pov) if len(infos) > 1 else -100_000
    if best >= WIN and best - second >= GAP:
        return infos[0]["pv"][0], best
    return None


def estimate_rating(board: chess.Board, answer: chess.Move, solver_moves: int, mate: bool) -> int:
    """The weakest engine budget that finds the answer sets the base rating."""
    rating = 2150
    for nodes, found_at in ((200, 900), (1_500, 1200), (12_000, 1550), (40_000, 1850)):
        if Searcher().search(board, nodes=nodes).best_move == answer:
            rating = found_at
            break
    rating += 120 * (solver_moves - 1)
    return rating - (150 if mate and solver_moves == 1 else 0)


VALUABLE = (chess.KNIGHT, chess.BISHOP, chess.ROOK, chess.QUEEN, chess.KING)


def theme(board: chess.Board, answer: chess.Move) -> str:
    after = board.copy()
    after.push(answer)
    targets = [
        square
        for square in after.attacks(answer.to_square)
        if after.color_at(square) == (not board.turn) and after.piece_type_at(square) in VALUABLE
    ]
    if len(targets) >= 2 and not after.is_attacked_by(not board.turn, answer.to_square):
        return "Fork"
    if after.is_check():
        return "Check"
    if board.is_capture(answer):
        return "Capture"
    return "Quiet move"


def build_puzzle(engine, before: chess.Board, blunder: chess.Move) -> dict | None:
    """A puzzle where ``blunder`` (played from ``before``) can be punished, or None."""
    board = before.copy()
    board.push(blunder)
    solver = board.turn
    first = only_move(engine.analyse(board, ANALYSIS, multipv=2), solver)
    if not first:
        return None
    answer, first_score = first
    if before.is_capture(blunder) and answer.to_square == blunder.to_square and first_score < 99_000:
        return None  # just taking back a piece after a trade isn't much of a puzzle
    mate_line = first_score >= 99_000
    moves = [blunder]
    line = board.copy()
    move = answer
    for step in range(6 if mate_line else MAX_SOLVER_MOVES):
        if step:
            infos = engine.analyse(line, ANALYSIS, multipv=2)
            if mate_line and infos and score(infos[0], solver) == MATE_IN_ONE:
                move = infos[0]["pv"][0]  # any mate is accepted, so it needn't be unique
            else:
                found = only_move(infos, solver)
                if not found:
                    if mate_line:
                        return None
                    break
                move = found[0]
        moves.append(move)
        line.push(move)
        if line.is_game_over() or (not mate_line and step + 1 >= MAX_SOLVER_MOVES):
            break
        reply = engine.play(line, chess.engine.Limit(depth=12)).move
        moves.append(reply)
        line.push(reply)
    if len(moves) % 2 == 1:  # end on the solver's move
        moves.pop()
        line.pop()
    if mate_line and not line.is_checkmate():
        return None
    solver_moves = len(moves) // 2
    return {
        "fen": before.fen(),
        "moves": [m.uci() for m in moves],
        "rating": estimate_rating(board, answer, solver_moves, mate_line),
        "theme": f"Mate in {solver_moves}" if mate_line else theme(board, answer),
    }


def play_and_mine(job) -> list[dict]:
    seed, stockfish = job
    rng = random.Random(seed)
    players = []
    for _ in range(2):
        engine = chess.engine.SimpleEngine.popen_uci(stockfish)
        engine.configure({"UCI_LimitStrength": True, "UCI_Elo": rng.randint(1320, 1900), "Threads": 1})
        players.append(engine)
    analyst = chess.engine.SimpleEngine.popen_uci(stockfish)
    analyst.configure({"Threads": 1, "Hash": 32})
    puzzles = []
    try:
        board = chess.Board()
        # A few random opening moves so games differ.
        for _ in range(rng.randint(2, 6)):
            board.push(rng.choice(list(board.legal_moves)))
            if board.is_game_over():
                return []
        while not board.is_game_over() and len(board.move_stack) < 160:
            before = board.copy()
            move = players[len(board.move_stack) % 2].play(board, chess.engine.Limit(time=0.02)).move
            board.push(move)
            if len(board.move_stack) >= 12:
                puzzle = build_puzzle(analyst, before, move)
                if puzzle:
                    puzzles.append(puzzle)
    finally:
        for engine in [*players, analyst]:
            engine.quit()
    return puzzles


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--games", type=int, default=300)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--stockfish", default=shutil.which("stockfish") or "/usr/games/stockfish")
    args = parser.parse_args()

    with Pool(args.workers) as pool:
        batches = pool.map(play_and_mine, [(args.seed * 100_000 + i, args.stockfish) for i in range(args.games)])
    seen = set()
    puzzles = []
    for puzzle in (p for batch in batches for p in batch):
        key = puzzle["fen"].rsplit(" ", 2)[0]
        if key in seen:
            continue
        seen.add(key)
        puzzles.append(puzzle)
    puzzles.sort(key=lambda p: (p["rating"], p["fen"]))
    for index, puzzle in enumerate(puzzles, 1):
        puzzle["id"] = f"p{index:04d}"
    OUT.write_text(json.dumps(puzzles, separators=(",", ":")) + "\n")
    ratings = [p["rating"] for p in puzzles]
    print(f"{len(puzzles)} puzzles written to {OUT}")
    for low in range(600, 2400, 300):
        print(f"  {low}-{low + 299}: {sum(low <= r < low + 300 for r in ratings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
