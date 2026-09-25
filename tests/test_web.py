import json
import threading
import urllib.error
import urllib.request

import chess
import pytest

from chessbot.search import Searcher
from chessbot.server import ChessBotServer, pieces_js
from chessbot.webapi import engine_reply, game_state


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
