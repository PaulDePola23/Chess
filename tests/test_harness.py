import math
import pathlib
import subprocess
import sys

import chess
import pytest

from chessbot.__main__ import main
from chessbot.bench import BENCH_POSITIONS, run_bench
from chessbot.sprt import MatchStats, elo_from_score, expected_score, sprt_bounds

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_bench_positions_are_legal_and_varied():
    boards = [chess.Board(fen) for fen in BENCH_POSITIONS]
    assert all(board.is_valid() for board in boards)
    assert len({board.epd() for board in boards}) == len(boards)
    assert any(chess.popcount(board.occupied) <= 8 for board in boards)  # endgames too


def test_bench_signature_is_deterministic():
    first = run_bench(depth=2, positions=BENCH_POSITIONS[:4])
    second = run_bench(depth=2, positions=BENCH_POSITIONS[:4])
    assert first.nodes == second.nodes > 0


def test_bench_command(capsys):
    assert main(["bench", "--depth", "1"]) == 0
    out = capsys.readouterr().out
    assert "Bench: " in out and "nodes/s" in out


def test_elo_and_expected_score_are_inverse():
    for elo in (-400, -50, 0, 10, 300):
        assert elo_from_score(expected_score(elo)) == pytest.approx(elo)


def test_even_results_give_zero_elo():
    stats = MatchStats()
    for _ in range(20):
        stats.add_pair(1, 0)  # each engine wins with the same colour
        stats.add_pair(0.5, 0.5)
    elo, margin = stats.elo()
    assert elo == pytest.approx(0) and margin == 0
    assert (stats.wins, stats.draws, stats.losses) == (20, 40, 20)
    assert stats.pairs == [0, 0, 40, 0, 0]


def test_a_clearly_better_engine_passes_the_sprt():
    stats = MatchStats()
    for first, second, count in [(1, 1, 30), (1, 0.5, 30), (1, 0, 25), (0.5, 0, 10), (0, 0, 5)]:
        for _ in range(count):
            stats.add_pair(first, second)
    elo, margin = stats.elo()
    mean, _ = stats.mean_and_variance()
    assert mean == pytest.approx(0.675)
    assert elo == pytest.approx(elo_from_score(0.675)) and 0 < margin < elo
    assert stats.los() > 0.999
    lower, upper = sprt_bounds()
    assert stats.llr(0, 10) > upper
    assert stats.llr(0, 10) > stats.llr(200, 210)  # the evidence points below +200


def test_sprt_bounds():
    lower, upper = sprt_bounds(0.05, 0.05)
    assert lower == pytest.approx(-math.log(19)) and upper == pytest.approx(math.log(19))


def run_script(*args, timeout=180):
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=True
    ).stdout


def test_match_between_two_engine_processes():
    # The working tree against itself: every pair splits 1-1, since the engine is deterministic.
    out = run_script(
        "scripts/match.py", "--base", str(ROOT), "--nodes", "200", "--games", "2", "--concurrency", "1", "--no-sprt"
    )
    assert "Games     2:" in out and "pairs [0, 0, 1, 0, 0]" in out


def test_match_with_a_node_budget_for_each_engine():
    out = run_script(
        "scripts/match.py", "--base", str(ROOT), "--nodes", "300", "--base-nodes", "30", "--games", "2",
        "--concurrency", "1", "--no-sprt",
    )  # fmt: skip
    assert "300 nodes (base 30) a move" in out and "Games     2:" in out


def test_tactics_runner_solves_easy_puzzles():
    out = run_script("scripts/tactics.py", "--limit", "4", "--nodes", "3000", "--concurrency", "1")
    assert "Solved" in out and "of 4" in out
