"""Play against the engine in the terminal."""

from __future__ import annotations

import sys
from typing import TextIO

import chess

from .search import Searcher, SearchResult

UNICODE_PIECES = {
    "P": "♙", "N": "♘", "B": "♗", "R": "♖", "Q": "♕", "K": "♔",
    "p": "♟", "n": "♞", "b": "♝", "r": "♜", "q": "♛", "k": "♚",
}  # fmt: skip

HELP = """Enter moves in SAN (e4, Nf3, O-O, exd8=Q) or UCI (e2e4, g1f3, e1g1, e7d8q).
Other commands: undo, hint, fen, help, quit."""


def render(board: chess.Board, orientation: chess.Color = chess.WHITE, unicode: bool = True) -> str:
    """Draw the board with coordinates, from ``orientation``'s side."""
    ranks = range(7, -1, -1) if orientation == chess.WHITE else range(8)
    files = range(8) if orientation == chess.WHITE else range(7, -1, -1)
    last = board.peek() if board.move_stack else None
    lines = []
    for rank in ranks:
        cells = []
        for file in files:
            square = chess.square(file, rank)
            piece = board.piece_at(square)
            if piece is None:
                cell = "·"
            else:
                cell = UNICODE_PIECES[piece.symbol()] if unicode else piece.symbol()
            if last and square in (last.from_square, last.to_square):
                cell = f"[{cell}]"
            else:
                cell = f" {cell} "
            cells.append(cell)
        lines.append(f"{rank + 1} {''.join(cells)}")
    lines.append("  " + "".join(f" {chess.FILE_NAMES[f]} " for f in files))
    return "\n".join(lines)


def describe(board: chess.Board, move: chess.Move, result: SearchResult) -> str:
    """One line summary of the engine's move, e.g. ``Nf3  (eval +0.35, depth 7, 1.9s)``."""
    mate_in = result.mate_in
    # Scores are from the mover's point of view; show them from White's.
    sign = 1 if board.turn == chess.WHITE else -1
    if mate_in is not None:
        evaluation = f"mate in {abs(mate_in)} for {'White' if mate_in * sign > 0 else 'Black'}"
    else:
        evaluation = f"eval {sign * result.score / 100:+.2f}"
    return f"{board.san(move)}  ({evaluation}, depth {result.depth}, {result.elapsed:.1f}s)"


def parse_move(board: chess.Board, text: str) -> chess.Move | None:
    """Parse SAN or UCI input, returning None if it is not a legal move."""
    try:
        return board.parse_san(text)
    except ValueError:
        pass
    try:
        move = chess.Move.from_uci(text.lower())
    except ValueError:
        return None
    return move if move in board.legal_moves else None


def play(
    human: chess.Color = chess.WHITE,
    think_time: float = 2.0,
    depth: int | None = None,
    fen: str = chess.STARTING_FEN,
    unicode: bool = True,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> str:
    """Run an interactive game and return the result (``1-0``, ``0-1``, ``1/2-1/2`` or ``*``)."""
    board = chess.Board(fen)
    engine = Searcher()

    def say(text: str = "") -> None:
        stdout.write(text + "\n")
        stdout.flush()

    say(f"You are playing {'White' if human == chess.WHITE else 'Black'}. {HELP}")
    while not board.is_game_over(claim_draw=True):
        say()
        say(render(board, human, unicode))
        if board.turn == human:
            stdout.write("Your move: ")
            stdout.flush()
            line = stdin.readline()
            if not line:
                say()
                return "*"
            text = line.strip()
            command = text.lower()
            if command in ("quit", "exit", "resign"):
                say("Game abandoned.")
                return "*"
            if command == "help":
                say(HELP)
            elif command == "fen":
                say(board.fen())
            elif command == "undo":
                # Take back the engine's reply and your own move.
                if len(board.move_stack) >= 2:
                    board.pop()
                    board.pop()
                else:
                    say("Nothing to undo.")
            elif command == "hint":
                result = engine.search(board, time_limit=think_time, depth=depth)
                if result.best_move:
                    say(f"Hint: {describe(board, result.best_move, result)}")
            elif text:
                move = parse_move(board, text)
                if move is None:
                    say(f"Illegal or unrecognised move: {text!r}. Type 'help' for the move format.")
                else:
                    board.push(move)
            continue

        say("Thinking...")
        result = engine.search(board, time_limit=think_time, depth=depth)
        say(f"ChessBot plays {describe(board, result.best_move, result)}")
        board.push(result.best_move)

    say()
    say(render(board, human, unicode))
    outcome = board.outcome(claim_draw=True)
    reason = outcome.termination.name.replace("_", " ").lower()
    winner = {chess.WHITE: "White wins", chess.BLACK: "Black wins", None: "Draw"}[outcome.winner]
    say(f"Game over: {winner} by {reason} ({outcome.result()}).")
    return outcome.result()
