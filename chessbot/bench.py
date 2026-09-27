"""A fixed search benchmark: the engine's speed, and a signature of its search.

Searching the same positions to the same depth visits exactly the same nodes
every time, so the total node count is a fingerprint of the search. A change
that shouldn't alter how the engine plays (a speed-up, a refactor) must leave
the signature as it was; anything that changes play changes it. The positions
and depth are fixed; only the time depends on the machine.

    chessbot bench              # depth 5
    chessbot bench --depth 6
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import chess

from .search import Searcher

DEFAULT_DEPTH = 5

# Openings, middlegames, tactics and endgames. Each is searched with a fresh
# Searcher, so the order doesn't matter and nothing carries over.
BENCH_POSITIONS = [
    chess.STARTING_FEN,
    "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4",
    "r3k2r/p1ppqpb1/bn2pnp1/3PN3/1p2P3/2N2Q1p/PPPBBPPP/R3K2R w KQkq - 0 1",
    "8/2p5/3p4/KP5r/1R3p1k/8/4P1P1/8 w - - 0 1",
    "r3k2r/Pppp1ppp/1b3nbN/nP6/BBP1P3/q4N2/Pp1P2PP/R2Q1RK1 w kq - 0 1",
    "rnbq1k1r/pp1Pbppp/2p5/8/2B5/8/PPP1NnPP/RNBQK2R w KQ - 1 8",
    "r4rk1/1pp1qppp/p1np1n2/2b1p1B1/2B1P1b1/P1NP1N2/1PP1QPPP/R4RK1 w - - 0 10",
    "r1bq1rk1/pp2bppp/2n1pn2/2pp4/2PP4/2N1PN2/PP1B1PPP/R2QKB1R w KQ - 0 8",
    "r1b1kb1r/pp2pppp/1qnp1n2/8/3NP3/2N5/PPP2PPP/R1BQKB1R w KQkq - 4 6",
    "rnbq1rk1/ppp1ppbp/3p1np1/8/2PPP3/2N2N2/PP2BPPP/R1BQK2R b KQ - 3 6",
    "2r2rk1/pp3ppp/2n1b3/3pP3/3P4/P1N5/1P3PPP/R2R2K1 w - - 1 18",
    "r2q1rk1/1b2bppp/p2p1n2/1p2p3/3NP3/1BN1B3/PPP2PPP/R2Q1RK1 w - - 0 12",
    "6k1/5ppp/8/8/8/8/5PPP/3Q2K1 w - - 0 1",
    "8/8/1p1k4/1P6/2K5/8/8/8 w - - 0 1",
    "1K1k4/1P6/8/8/8/8/r7/2R5 w - - 0 1",
    "8/5pk1/6p1/7p/7P/6P1/5PK1/3R4 w - - 0 1",
]


@dataclass
class BenchResult:
    nodes: int  # the signature
    seconds: float
    depth: int

    @property
    def nps(self) -> int:
        return int(self.nodes / self.seconds) if self.seconds > 0 else 0


def run_bench(
    depth: int = DEFAULT_DEPTH,
    positions: list[str] | None = None,
    report: Callable[[int, str, int, float], None] | None = None,
) -> BenchResult:
    """Search each position to ``depth``; ``report(index, fen, nodes, seconds)`` hears about each one."""
    total_nodes = 0
    total_time = 0.0
    for index, fen in enumerate(positions or BENCH_POSITIONS, 1):
        board = chess.Board(fen)
        start = time.perf_counter()
        result = Searcher().search(board, depth=depth)
        elapsed = time.perf_counter() - start
        total_nodes += result.nodes
        total_time += elapsed
        if report:
            report(index, fen, result.nodes, elapsed)
    return BenchResult(total_nodes, total_time, depth)
