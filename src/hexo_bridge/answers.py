"""What an engine answers, and the checks the server applies before it takes an answer."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from hexo_bridge.position import Position, Side, Turn, other

# The server's bounds on an evaluation.
HEURISTIC_LIMIT = 1e6
WIN_IN_LIMIT = 1000


@dataclass(frozen=True)
class Evaluation:
    """htttx's evaluation of the position after a line; x counts positive, o negative.

    `heuristic` is any real number; the website divides it by the analyzer's declared
    `values.scale`, 1 by default, and reads the result on a -1..1 scale.
    `win_in` is the turns to a forced win, the turn of the side then to move counted first:
    positive when x wins, negative when o does.
    """

    heuristic: float | None = None
    win_in: int | None = None

    def wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.heuristic is not None:
            out["heuristic"] = self.heuristic
        if self.win_in is not None:
            out["win_in"] = self.win_in
        return out

    @classmethod
    def from_wire(cls, data: Any) -> Evaluation | None:
        if not isinstance(data, dict):
            return None
        heuristic, win_in = data.get("heuristic"), data.get("win_in")
        if heuristic is None and win_in is None:
            return None
        return cls(
            None if heuristic is None else float(heuristic), None if win_in is None else int(win_in)
        )


@dataclass(frozen=True)
class Line:
    """A turn, and optionally the engine's evaluation of the position after it."""

    turn: Turn
    evaluation: Evaluation | None = None

    def wire(self) -> dict[str, Any]:
        out: dict[str, Any] = {"pieces": [{"q": q, "r": r} for q, r in self.turn]}
        if self.evaluation is not None:
            out["evaluation"] = self.evaluation.wire()
        return out

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> Line:
        pieces = data["pieces"]
        if len(pieces) != 2:
            raise ValueError(f"a turn is two stones, got {len(pieces)}")
        turn = (
            (int(pieces[0]["q"]), int(pieces[0]["r"])),
            (int(pieces[1]["q"]), int(pieces[1]["r"])),
        )
        return cls(turn, Evaluation.from_wire(data.get("evaluation")))


class Resign:
    """Return an instance from `Engine.move` to resign the game."""


def as_lines(answer: Any) -> list[Line]:
    """An engine's answer as lines, best first: a Line, a list of Lines, or a bare turn."""
    if isinstance(answer, Line):
        return [answer]
    if isinstance(answer, Sequence) and answer and all(isinstance(item, Line) for item in answer):
        return list(answer)
    cells = [tuple(cell) for cell in answer]
    if len(cells) != 2 or any(len(cell) != 2 for cell in cells):
        raise TypeError(
            f"an answer is a Line, a list of Lines, or two (q, r) cells, not {answer!r}"
        )
    return [Line((cells[0], cells[1]))]  # type: ignore[arg-type]


def sign(side: Side) -> int:
    return 1 if side == "x" else -1


def settle(
    position: Position, lines: list[Line], limit: int, reading: bool
) -> tuple[list[Line], list[str]]:
    """The lines as the server will take them, and a note for everything that had to change.

    The best line is never dropped: an illegal one becomes the fallback turn. A consideration
    that is illegal, repeats a line, or carries no evaluation is dropped, as the server would
    drop it. Every evaluation is brought in line with the board's own facts, since the server
    fails a reading whose evaluation contradicts them. A reading also needs an evaluation on
    every line, and a best line that takes the six the side to move can complete; a game move
    stays the engine's own, even a weak one.
    """
    notes: list[str] = []
    win = position.winning_turn() if reading else None
    kept: list[Line] = []
    for rank, line in enumerate(lines):
        turn = _turn(line.turn)
        evaluation = line.evaluation
        if turn is None or not position.is_legal(turn):
            if rank:
                notes.append(f"dropped an illegal consideration {line.turn}")
                continue
            notes.append(f"illegal turn {line.turn}; playing the fallback turn")
            turn, evaluation = position.fallback_turn(), None
        elif rank == 0 and win is not None and not position.play(turn)[1]:
            notes.append(f"{turn} misses the six the side to move completes; playing it")
            turn, evaluation = win, None
        if rank and evaluation is None:
            continue
        if any(set(turn) == set(other_line.turn) for other_line in kept):
            notes.append(f"dropped a repeated line {turn}")
            continue
        fitted, note = _fit(position, turn, evaluation, reading or evaluation is not None)
        if note:
            notes.append(f"{turn}: {note}")
        kept.append(Line(turn, fitted))
        if len(kept) == limit:
            break
    return kept, notes


def _whole(value: Any) -> int:
    # Engines hand over numpy numbers and floats; the wire and the checks want plain ints.
    number = float(value)
    if not number.is_integer():
        raise ValueError(f"{value!r} is not a whole number")
    return int(number)


def _turn(raw: Any) -> Turn | None:
    try:
        cells = [(_whole(cell[0]), _whole(cell[1])) for cell in raw]
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return (cells[0], cells[1]) if len(cells) == 2 else None


def _favours(evaluation: Evaluation, side: Side) -> bool:
    if evaluation.win_in is not None:
        return (evaluation.win_in > 0) == (side == "x")
    return (evaluation.heuristic or 0) * sign(side) > 0


def _in_bounds(evaluation: Evaluation | None) -> Evaluation | None:
    if evaluation is None:
        return None
    heuristic: float | None
    win_in: int | None
    try:
        heuristic = None if evaluation.heuristic is None else float(evaluation.heuristic)
    except (TypeError, ValueError):
        heuristic = None
    try:
        win_in = None if evaluation.win_in is None else _whole(evaluation.win_in)
    except (TypeError, ValueError, OverflowError):
        win_in = None
    if heuristic is not None:
        heuristic = (
            max(-HEURISTIC_LIMIT, min(HEURISTIC_LIMIT, heuristic))
            if math.isfinite(heuristic)
            else None
        )
    if win_in is not None and (win_in == 0 or abs(win_in) > WIN_IN_LIMIT):
        win_in = None
    if heuristic is None and win_in is None:
        return None
    return Evaluation(heuristic, win_in)


def _fit(
    position: Position, turn: Turn, evaluation: Evaluation | None, required: bool
) -> tuple[Evaluation | None, str | None]:
    mover = position.to_move
    opponent = other(mover)
    given = evaluation
    evaluation = _in_bounds(evaluation)
    after, won = position.play(turn)
    # Filling in a missing evaluation is no correction, so only a given one earns a note.
    if won:
        if evaluation is not None and _favours(evaluation, mover):
            return evaluation, None
        fact = Evaluation(win_in=sign(mover)) if required else None
        return fact, "it completes six" if given else None
    if after.threats(opponent):
        if evaluation is not None and evaluation.win_in == sign(opponent):
            return evaluation, None
        fact = Evaluation(win_in=sign(opponent)) if required else None
        return fact, "the other side completes six next turn" if given else None
    note = None if evaluation == given else "evaluation out of bounds"
    if evaluation is not None and evaluation.win_in is not None:
        win_in = evaluation.win_in
        winner = "x" if win_in > 0 else "o"
        # An odd count ends on the turn of the side then to move,
        # and a win in 1 needs a six already on the board.
        if (abs(win_in) % 2 == 1) != (winner == opponent) or win_in == sign(opponent):
            note = f"win_in {win_in} contradicts the board"
            evaluation = (
                Evaluation(heuristic=evaluation.heuristic)
                if evaluation.heuristic is not None
                else None
            )
    if evaluation is None and required:
        return Evaluation(heuristic=0.0), note
    return evaluation, note
