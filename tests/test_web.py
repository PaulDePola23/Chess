import json
import threading
import urllib.error
import urllib.request

import chess
import pytest

from chessbot.levels import LEVELS
from chessbot.search import Searcher
from chessbot.server import ChessBotServer, config_js, pieces_js
from chessbot.webapi import (
    engine_reply,
    game_state,
    move_accuracy,
    replay_game,
    review_move,
    suggest_move,
    winning_chances,
)


def test_initial_state():
    state = game_state([])
    assert state["fen"] == chess.STARTING_FEN
    assert state["turn"] == "white"
    assert len(state["legal"]) == 20
    assert "Nf3" in state["legal_san"]
    assert state["legal"][state["legal_san"].index("Nf3")] == "g1f3"
    assert state["san"] == []
    assert state["last"] is None
    assert state["check"] is None
    assert not state["over"]


def test_state_after_moves():
    state = game_state(["e2e4", "e7e5", "d1h5"])
    assert state["turn"] == "black"
    assert state["san"] == ["e4", "e5", "Qh5"]
    assert state["last"] == "d1h5"
    assert "1. e4 e5 2. Qh5" in state["pgn"]


def test_checkmate_state():
    state = game_state(["f2f3", "e7e5", "g2g4", "d8h4"])
    assert state["over"]
    assert state["result"] == "0-1"
    assert state["reason"] == "checkmate"
    assert state["check"] == "e1"
    assert state["legal"] == []
    assert state["san"][-1] == "Qh4#"


def test_promotion_moves_are_listed():
    state = game_state([], fen="8/P7/8/8/8/8/8/k6K w - - 0 1")
    assert {"a7a8q", "a7a8r", "a7a8b", "a7a8n"} <= set(state["legal"])
    assert "a8=Q+" in state["legal_san"]


@pytest.mark.parametrize("moves", [["e2e5"], ["zz"], "e2e4", [1, 2]])
def test_rejects_bad_moves(moves):
    with pytest.raises(ValueError):
        game_state(moves)


def test_engine_reply():
    progress = []
    result = engine_reply(["f2f3", "e7e5", "g2g4"], 1.0, Searcher(), on_progress=progress.append)
    reply = result["reply"]
    assert reply["move"] == "d8h4"
    assert reply["san"] == "Qh4#"
    # Scores are from White's point of view: Black mating means a negative mate.
    assert reply["mate"] == -1
    assert reply["score"] is None
    assert result["state"]["over"]
    assert progress and progress[0]["depth"] == 1


def test_engine_reply_refuses_finished_games():
    with pytest.raises(ValueError):
        engine_reply(["f2f3", "e7e5", "g2g4", "d8h4"], 0.1, Searcher())


def test_pieces_js_has_all_pieces():
    script = pieces_js()
    pieces = json.loads(script.split("=", 1)[1].strip().rstrip(";"))
    assert set(pieces) == set("PNBRQKpnbrqk")
    assert all(svg.startswith("<svg") for svg in pieces.values())


@pytest.fixture
def server():
    srv = ChessBotServer(("127.0.0.1", 0))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def request(url, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, response.headers.get("Content-Type"), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type"), error.read()


@pytest.mark.parametrize(
    ("path", "content_type", "snippet"),
    [
        ("/", "text/html", b'id="board"'),
        ("/app.js", "text/javascript", b"chessbotBackend"),
        ("/style.css", "text/css", b"--sq-light"),
        ("/pieces.js", "text/javascript", b"CHESSBOT_PIECES"),
        ("/showcase.json", "application/json", b"Opera Game"),
    ],
)
def test_serves_the_page(server, path, content_type, snippet):
    status, received_type, body = request(server + path)
    assert status == 200
    assert received_type.startswith(content_type)
    assert snippet in body


def test_api_state(server):
    status, _, body = request(server + "/api/state", {"moves": ["e2e4"]})
    assert status == 200
    assert json.loads(body)["san"] == ["e4"]


def test_api_move(server):
    status, _, body = request(server + "/api/move", {"moves": ["e2e4"], "think_time": 0.2})
    assert status == 200
    result = json.loads(body)
    board = chess.Board()
    board.push_uci("e2e4")
    assert chess.Move.from_uci(result["reply"]["move"]) in board.legal_moves
    assert result["state"]["turn"] == "white"


def test_api_errors(server):
    status, _, body = request(server + "/api/state", {"moves": ["e2e5"]})
    assert status == 400
    assert "illegal move" in json.loads(body)["error"]
    assert request(server + "/api/nothing", {})[0] == 404
    assert request(server + "/missing.html")[0] == 404


def test_build_site(tmp_path, capsys):
    from chessbot.__main__ import main

    assert main(["build-site", str(tmp_path / "site")]) == 0
    site = tmp_path / "site"
    assert {p.name for p in site.iterdir()} >= {
        "index.html",
        "app.js",
        "style.css",
        "pieces.js",
        "pyodide-backend.js",
        "python.json",
        "config.js",
        "lessons.json",
        "showcase.json",
        ".nojekyll",
    }
    page = (site / "index.html").read_text()
    assert page.index("pyodide-backend.js") < page.index('src="app.js"')
    assert "running in your browser with Pyodide" in page
    sources = json.loads((site / "python.json").read_text())
    assert {"chessbot/search.py", "chessbot/levels.py", "chessbot/webapi.py", "chess/__init__.py"} <= set(sources)
    assert json.loads(sources["chessbot/openings.json"])  # opening names ship with the engine
    assert "class Searcher" in sources["chessbot/search.py"]


def test_review_flags_a_blunder():
    # 3...Nf6?? allows Qxf7#.
    moves = ["e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7"]
    review = review_move(moves, 5, Searcher())
    assert review["san"] == "Nf6"
    assert review["color"] == "black"
    assert review["number"] == 3
    assert review["verdict"] == "blunder"
    assert review["best_san"] != "Nf6"
    assert review["after"]["mate"] == 1  # White mates in one after it
    assert review["accuracy"] < 20
    assert review["fen"] == game_state(moves[:5])["fen"]


def test_review_of_the_best_move_loses_nothing():
    review = review_move(["e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6", "h5f7"], 6, Searcher())
    assert review["san"] == "Qxf7#"
    assert review["loss"] == 0
    assert review["accuracy"] == 100
    assert review["verdict"] is None


@pytest.mark.parametrize("ply", [-1, 2, "0"])
def test_review_rejects_bad_plies(ply):
    with pytest.raises(ValueError):
        review_move(["e2e4", "e7e5"], ply, Searcher())


def test_winning_chances_and_accuracy():
    assert winning_chances(0) == 0
    assert winning_chances(100_000) == 1
    assert winning_chances(-100_000) == -1
    assert 0.3 < winning_chances(300) < 0.6
    assert move_accuracy(50, 50) == 100
    assert move_accuracy(0, -900) < 20


def test_state_from_a_fen():
    fen = "6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1"
    state = game_state(["a1a8"], fen=fen)
    assert state["over"] and state["reason"] == "checkmate"


def test_serves_config_and_lessons(server):
    status, _, body = request(server + "/config.js")
    assert status == 200
    config = json.loads(body.decode().split("=", 1)[1].strip().rstrip(";"))
    assert [level["level"] for level in config["levels"]] == [level.number for level in LEVELS]
    assert config["stats"] is None
    status, content_type, body = request(server + "/lessons.json")
    assert status == 200 and content_type == "application/json"
    assert json.loads(body)[0]["id"] == "pieces"


def test_config_names_the_stats_database(monkeypatch):
    monkeypatch.setenv("CHESSBOT_STATS_URL", "https://example.supabase.co/")
    monkeypatch.setenv("CHESSBOT_STATS_KEY", "sb_publishable_abc")
    config = json.loads(config_js().split("=", 1)[1].strip().rstrip(";"))
    assert config["stats"] == {"url": "https://example.supabase.co", "key": "sb_publishable_abc"}


def test_api_move_at_a_level_and_review(server):
    status, _, body = request(server + "/api/move", {"moves": ["e2e4"], "level": 1})
    assert status == 200
    reply = json.loads(body)["reply"]
    status, _, body = request(server + "/api/review", {"moves": ["e2e4", reply["move"]], "ply": 0})
    assert status == 200
    assert json.loads(body)["san"] == "e4"
    assert request(server + "/api/move", {"moves": [], "level": 42})[0] == 400


def test_hanging_pieces():
    # 1. e4 e5 2. Qh5 Nf6: the knight attacks the queen and e4; the queen attacks e5.
    state = game_state(["e2e4", "e7e5", "d1h5", "g8f6"])
    assert state["hanging"] == {"white": ["e4", "h5"], "black": ["e5"]}
    assert game_state([])["hanging"] == {"white": [], "black": []}


def test_opening_names():
    assert game_state(["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "f8c5"])["opening"] == {
        "eco": "C50",
        "name": "Italian Game: Giuoco Piano",
    }
    # A transposition still finds the name.
    nimzo = ["d2d4", "g8f6", "c2c4", "e7e6", "b1c3", "f8b4"]
    other_order = ["c2c4", "e7e6", "d2d4", "g8f6", "b1c3", "f8b4"]
    assert game_state(nimzo)["opening"] == game_state(other_order)["opening"]
    assert game_state([])["opening"] is None
    assert game_state([], fen="6k1/5ppp/8/8/8/8/8/R5K1 w - - 0 1")["opening"] is None


def test_suggest_move_finds_mate():
    hint = suggest_move(["e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6"], Searcher())
    assert hint == {"move": "h5f7", "san": "Qxf7#"}
    with pytest.raises(ValueError):
        suggest_move(["f2f3", "e7e5", "g2g4", "d8h4"], Searcher())


def test_api_hint(server):
    status, _, body = request(server + "/api/hint", {"moves": ["e2e4", "e7e5", "d1h5", "b8c6", "f1c4", "g8f6"]})
    assert status == 200
    assert json.loads(body)["san"] == "Qxf7#"


def test_replay_game():
    moves = ["f2f3", "e7e5", "g2g4", "d8h4"]
    replay = replay_game(moves)
    assert replay["san"] == ["f3", "e5", "g4", "Qh4#"]
    assert replay["fens"][0] == chess.STARTING_FEN
    assert replay["fens"][-1] == game_state(moves)["fen"]
    assert replay["checks"] == [None, None, None, None, "e1"]
    with pytest.raises(ValueError):
        replay_game(["e2e5"])


def test_api_replay(server):
    status, _, body = request(server + "/api/replay", {"moves": ["e2e4", "e7e5"]})
    assert status == 200
    assert json.loads(body)["san"] == ["e4", "e5"]
