import random

import chess

from chessbot.book import _table, book_move, book_moves
from chessbot.levels import LEVELS, choose_move, get_level
from chessbot.search import Searcher
from chessbot.webapi import engine_reply


def test_book_covers_the_main_openings():
    start = {move.uci() for move, _, _ in book_moves(chess.Board())}
    assert {"e2e4", "d2d4"} <= start
    after_e4 = chess.Board()
    after_e4.push_uci("e2e4")
    assert {"e7e5", "c7c5"} <= {move.uci() for move, _, _ in book_moves(after_e4)}


def test_every_book_move_is_legal_and_sound():
    table = _table()
    assert len(table) > 1000
    for epd, entries in table.items():
        board = chess.Board()
        board.set_epd(epd)
        assert entries
        for uci, weight, score in entries:
            assert board.is_legal(chess.Move.from_uci(uci)), (epd, uci)
            assert weight >= 1
            assert -2000 <= score <= 2000  # a big advantage at most, never a mate score


def test_book_move_is_weighted_and_out_of_book_is_none():
    rng = random.Random(1)
    picks = [book_move(chess.Board(), rng)[0].uci() for _ in range(300)]
    weights = {move.uci(): weight for move, weight, _ in book_moves(chess.Board())}
    most_common = max(weights, key=weights.get)
    assert picks.count(most_common) > picks.count(min(weights, key=weights.get))
    odd = chess.Board("8/8/4k3/8/8/4K3/4P3/8 w - - 0 1")
    assert book_move(odd, rng) is None


def test_stronger_levels_open_from_the_book():
    booked = [level for level in LEVELS if level.book]
    assert booked and all(level.number > 3 for level in booked)
    for level in booked:
        result = choose_move(chess.Board(), level, Searcher(), random.Random(0))
        assert result.book and result.best_move in chess.Board().legal_moves
    result = choose_move(chess.Board(), get_level(1), Searcher(), random.Random(0))
    assert not result.book


def test_engine_reply_marks_book_moves():
    data = engine_reply([], None, Searcher(), level=get_level(7).number)
    assert data["reply"]["book"] is True
    assert data["reply"]["depth"] == 0
    assert data["reply"]["move"] in {move.uci() for move, _, _ in book_moves(chess.Board())}
