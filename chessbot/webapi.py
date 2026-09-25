"""Game functions for the browser UI, returning plain JSON-friendly dicts.

The web page keeps the game as a list of UCI moves and asks for two things:
the state of the game after those moves (legal moves, notation, result) and
the engine's reply. ``chessbot serve`` exposes these over HTTP; any other
front end can call them directly.
"""

from __future__ import annotations

from collections.abc import Callable

import chess
import chess.pgn

from .search import Searcher, SearchResult

MIN_THINK_TIME = 0.05
MAX_THINK_TIME = 30.0


def _board_from(moves: list[str], fen: str | None = None) -> chess.Board:
    if not isinstance(moves, list) or not all(isinstance(m, str) for m in moves):
        raise ValueError("moves must be a list of UCI strings")
    board = chess.Board(fen) if fen else chess.Board()
    for uci in moves:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            raise ValueError(f"not a UCI move: {uci!r}") from None
        if move not in board.legal_moves:
            raise ValueError(f"illegal move {uci} in position {board.fen()}")
        board.push(move)
    return board


def _pgn(board: chess.Board) -> str:
    game = chess.pgn.Game.from_board(board)
    game.headers["Event"] = "Casual game"
    game.headers["Site"] = "ChessBot web"
    for tag in ("Date", "Round", "White", "Black"):
        game.headers.pop(tag, None)
    return str(game)


def game_state(moves: list[str], fen: str | None = None) -> dict:
    """Describe the position after ``moves`` (played from ``fen``, default the start position)."""
    board = _board_from(moves, fen)
    replay = chess.Board(fen) if fen else chess.Board()
    san = []
    for move in board.move_stack:
        san.append(replay.san(move))
        replay.push(move)

    outcome = board.outcome(claim_draw=True)
    king = board.king(board.turn)
    return {
        "fen": board.fen(),
        "turn": "white" if board.turn == chess.WHITE else "black",
        "legal": [move.uci() for move in board.legal_moves],
        "legal_san": [board.san(move) for move in board.legal_moves],
        "san": san,
        "check": chess.square_name(king) if board.is_check() and king is not None else None,
        "last": board.peek().uci() if board.move_stack else None,
        "over": outcome is not None,
        "result": outcome.result() if outcome else None,
        "reason": outcome.termination.name.replace("_", " ").lower() if outcome else None,
        "pgn": _pgn(board),
    }


def _white_pov(result: SearchResult, board: chess.Board) -> tuple[int | None, int | None]:
    """(centipawns, mate-in) from White's point of view; one of them is None."""
    sign = 1 if board.turn == chess.WHITE else -1
    if result.mate_in is not None:
        return None, sign * result.mate_in
    return sign * result.score, None


def engine_reply(
    moves: list[str],
    think_time: float,
    searcher: Searcher,
    fen: str | None = None,
    on_progress: Callable[[dict], None] | None = None,
) -> dict:
    """Let the engine choose a move after ``moves`` and return it with the new game state.

    ``on_progress`` receives a small dict after each completed search depth,
    for showing the engine's thinking as it happens.
    """
    board = _board_from(moves, fen)
    if board.is_game_over(claim_draw=True):
        raise ValueError("the game is already over")
    think_time = min(max(float(think_time), MIN_THINK_TIME), MAX_THINK_TIME)

    def summary(result: SearchResult) -> dict:
        score, mate = _white_pov(result, board)
        return {
            "depth": result.depth,
            "score": score,
            "mate": mate,
            "nodes": result.nodes,
            "time": round(result.elapsed, 3),
            "pv": board.variation_san(result.pv) if result.pv else "",
        }

    callback = (lambda result: on_progress(summary(result))) if on_progress else None
    result = searcher.search(board, time_limit=think_time, on_iteration=callback)
    move = result.best_move
    reply = summary(result)
    reply["move"] = move.uci()
    reply["san"] = board.san(move)
    return {"reply": reply, "state": game_state([*moves, move.uci()], fen)}
