import random

import chess
import pytest

from chessbot.levels import DEFAULT_LEVEL, LEVELS, choose_move, get_level
from chessbot.search import Searcher
from chessbot.webapi import engine_reply

MIDDLEGAME = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"


def test_levels_get_stronger():
    elos = [level.elo for level in LEVELS]
    assert elos == sorted(elos)
    assert [level.number for level in LEVELS] == list(range(1, len(LEVELS) + 1))
    assert get_level(DEFAULT_LEVEL).number == DEFAULT_LEVEL


def test_unknown_level():
    with pytest.raises(ValueError):
        get_level(99)


@pytest.mark.parametrize("level", LEVELS, ids=lambda level: level.name)
def test_every_level_plays_a_legal_move(level):
    board = chess.Board(MIDDLEGAME)
    result = choose_move(board, level, Searcher(), random.Random(1))
    assert result.best_move in board.legal_moves


@pytest.mark.parametrize("level", LEVELS, ids=lambda level: level.name)
def test_every_level_takes_a_free_queen_most_of_the_time(level):
    # Nxh4 wins Black's queen. Only the random blunders of the lowest levels may miss it.
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/4P2q/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1")
    rng = random.Random(7)
    searcher = Searcher()
    hits = sum(choose_move(board, level, searcher, rng).best_move.uci() == "f3h4" for _ in range(20))
    assert hits >= 20 * (1 - level.blunder_rate) - 4


def test_low_levels_vary_their_moves():
    board = chess.Board()
    searcher = Searcher()
    rng = random.Random(3)
    moves = {choose_move(board, get_level(1), searcher, rng).best_move for _ in range(15)}
    assert len(moves) >= 3


def test_rank_moves_orders_moves_and_respects_the_margin():
    board = chess.Board(MIDDLEGAME)
    ranked = Searcher().rank_moves(board, 2, margin=100)
    scores = [score for _, score in ranked]
    assert scores == sorted(scores, reverse=True)
    assert all(scores[0] - score < 100 for score in scores)
    assert len({move for move, _ in ranked}) == len(ranked)


def test_engine_reply_at_a_level():
    result = engine_reply(["e2e4"], None, Searcher(), level=2)
    board = chess.Board()
    board.push_uci("e2e4")
    assert chess.Move.from_uci(result["reply"]["move"]) in board.legal_moves


@pytest.mark.parametrize("level", ["3", 1.5, 0])
def test_engine_reply_rejects_bad_levels(level):
    with pytest.raises(ValueError):
        engine_reply([], None, Searcher(), level=level)
