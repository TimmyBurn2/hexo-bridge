"""Positions and the HeXO rules a bot needs: legal cells, sixes, and the windows to fill next."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

Side = Literal["x", "o"]
Cell = tuple[int, int]
Turn = tuple[Cell, Cell]

RADIUS = 8
SIX = 6
# A window of six holding this many of one side's stones and none of the
# other's is filled by that side's next turn of two stones.
OPEN = SIX - 2
AXES: tuple[Cell, ...] = ((1, 0), (0, 1), (1, -1))
NEIGHBOURS: tuple[Cell, ...] = ((1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1))


def other(side: Side) -> Side:
    return "o" if side == "x" else "x"


def distance(a: Cell, b: Cell) -> int:
    dq, dr = a[0] - b[0], a[1] - b[1]
    return (abs(dq) + abs(dr) + abs(dq + dr)) // 2


@dataclass(frozen=True)
class Position:
    """The stones on the board, in the order they were placed, and the side to move.

    Coordinates are htttx axial `(q, r)`: +q right, +r top-right.
    """

    stones: Mapping[Cell, Side]
    to_move: Side

    def reachable(self, cell: Cell, extra: Iterable[Cell] = ()) -> bool:
        """Whether a stone may go on `cell`: empty and within 8 of a stone."""
        if cell in self.stones:
            return False
        return any(distance(cell, stone) <= RADIUS for stone in (*self.stones, *extra))

    def is_legal(self, turn: Iterable[Cell]) -> bool:
        """Whether two cells are a legal turn for the side to move."""
        cells = list(turn)
        if len(cells) != 2 or cells[0] == cells[1]:
            return False
        first, second = cells
        if not self.reachable(first):
            return False
        # A first stone that completes six ends the turn; the second is never placed.
        if self.completes_six(first, self.to_move):
            return second not in self.stones
        return self.reachable(second, (first,))

    def completes_six(self, cell: Cell, side: Side) -> bool:
        """Whether a stone of `side` on `cell` makes six or more in a line."""
        for dq, dr in AXES:
            run = 1
            for step in (1, -1):
                q, r = cell[0] + step * dq, cell[1] + step * dr
                while self.stones.get((q, r)) == side:
                    run += 1
                    q, r = q + step * dq, r + step * dr
            if run >= SIX:
                return True
        return False

    def play(self, turn: Iterable[Cell]) -> tuple[Position, bool]:
        """The position after the side to move plays `turn`, and whether it completed six."""
        stones = dict(self.stones)
        for cell in turn:
            after = Position(stones, self.to_move)
            won = after.completes_six(cell, self.to_move)
            stones[cell] = self.to_move
            if won:
                return Position(stones, other(self.to_move)), True
        return Position(stones, other(self.to_move)), False

    def threats(self, side: Side) -> list[tuple[Cell, ...]]:
        """The empty cells of every window of six that `side` fills with its next turn."""
        found: dict[tuple[Cell, Cell], tuple[Cell, ...]] = {}
        for (q, r), owner in self.stones.items():
            if owner != side:
                continue
            for dq, dr in AXES:
                for back in range(SIX):
                    start = (q - back * dq, r - back * dr)
                    if (start, (dq, dr)) in found:
                        continue
                    cells = [(start[0] + i * dq, start[1] + i * dr) for i in range(SIX)]
                    owners = [self.stones.get(cell) for cell in cells]
                    if other(side) in owners:
                        continue
                    empty = tuple(
                        cell for cell, owner in zip(cells, owners, strict=True) if owner is None
                    )
                    if SIX - len(empty) >= OPEN:
                        found[(start, (dq, dr))] = empty
        return list(found.values())

    def winning_turn(self) -> Turn | None:
        """A turn that completes six for the side to move, if one exists."""
        window = min(self.threats(self.to_move), key=len, default=None)
        if window is None:
            return None
        if len(window) == 2:
            return (window[0], window[1])
        # The first stone ends the game, so the second need only be an empty cell.
        return (window[0], self.free_cell((window[0],)))

    def fallback_turn(self) -> Turn:
        """A legal turn from the rules alone: win, else block the other side, else play close."""
        win = self.winning_turn()
        if win is not None:
            return win
        chosen: list[Cell] = []
        windows = [set(window) for window in self.threats(other(self.to_move))]
        while windows and len(chosen) < 2:
            counts: dict[Cell, int] = {}
            for window in windows:
                for cell in window:
                    counts[cell] = counts.get(cell, 0) + 1
            block = max(sorted(counts), key=lambda cell: counts[cell])
            chosen.append(block)
            windows = [window for window in windows if block not in window]
        while len(chosen) < 2:
            chosen.append(self.free_cell(chosen))
        return (chosen[0], chosen[1])

    def free_cell(self, taken: Iterable[Cell] = ()) -> Cell:
        """The empty cell beside the most stones, other than `taken`: legal, and near play."""
        taken = set(taken)
        counts: dict[Cell, int] = {}
        for q, r in self.stones:
            for dq, dr in NEIGHBOURS:
                cell = (q + dq, r + dr)
                if cell not in self.stones and cell not in taken:
                    counts[cell] = counts.get(cell, 0) + 1
        if not counts:
            raise ValueError("no stones to play beside")
        return max(sorted(counts), key=lambda cell: counts[cell])
