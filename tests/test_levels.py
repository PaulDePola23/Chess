import random

import chess
import pytest

from chessbot.levels import DEFAULT_LEVEL, LEVELS, choose_move, get_level
from chessbot.search import Searcher
from chessbot.webapi import engine_reply

MIDDLEGAME = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
# The levels this engine plays; the top ones are Stockfish, which the web page runs.
OWN_LEVELS = [level for level in LEVELS if level.stockfish_elo is None]


def test_levels_get_stronger():
    elos = [level.elo for level in LEVELS]
    assert elos == sorted(elos)
    assert [level.number for level in LEVELS] == list(range(1, len(LEVELS) + 1))
    assert get_level(DEFAULT_LEVEL).number == DEFAULT_LEVEL


def test_unknown_level():
    with pytest.raises(ValueError):
        get_level(99)


@pytest.mark.parametrize("level", OWN_LEVELS, ids=lambda level: level.name)
def test_every_level_plays_a_legal_move(level):
    board = chess.Board(MIDDLEGAME)
    result = choose_move(board, level, Searcher(), random.Random(1))
    assert result.best_move in board.legal_moves


@pytest.mark.parametrize("level", OWN_LEVELS, ids=lambda level: level.name)
def test_every_level_takes_a_free_queen_most_of_the_time(level):
    # Nxh4 wins Black's queen. Only the random blunders of the lowest levels may miss it.
    board = chess.Board("rnb1kbnr/pppp1ppp/8/4p3/4P2q/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 1")
    rng = random.Random(7)
    searcher = Searcher()
    trials = 1 if level.nodes else 20  # the full-search levels always play the same move
    hits = sum(choose_move(board, level, searcher, rng).best_move.uci() == "f3h4" for _ in range(trials))
    assert hits >= trials * (1 - level.blunder_rate) - (4 if trials > 1 else 0)


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


def test_the_top_levels_are_named_after_pauls_animals():
    assert [(level.name, level.elo) for level in LEVELS[-3:]] == [("Summer", 2000), ("Titan", 2500), ("Pinky", 2700)]
    summer, titan, pinky = LEVELS[-3:]
    assert summer.stockfish_elo is None and summer.nodes > get_level(7).nodes
    assert (titan.stockfish_elo, pinky.stockfish_elo) == (2500, 2700)
    assert titan.as_dict()["stockfish"] == 2500 and "stockfish" not in summer.as_dict()


def test_stockfish_levels_are_left_to_the_page():
    with pytest.raises(ValueError, match="Stockfish"):
        choose_move(chess.Board(), get_level(9), Searcher())
    with pytest.raises(ValueError, match="Stockfish"):
        engine_reply([], None, Searcher(), level=10)
