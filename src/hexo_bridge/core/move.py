"""Engine-protocol primitives for htttx hexagonal tic-tac-toe.

These mirror the htttx `Coord`, `Move`, and side enum shapes verbatim, because
the htttx engine protocol is the gameplay layer and these names are its, not
HeXO's. They live in core as the shared alphabet an `EnginePort` speaks; a HeXO
adapter translates between HeXO's `p1`/`p2` and these `x`/`o` at the boundary.

Nothing here does I/O or knows about HTTP, websockets, or HeXO endpoints.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import Enum


class Side(str, Enum):
    """Engine-side alphabet (htttx). `x` opens; `o` replies.

    Core speaks the engine alphabet, not the platform's `p1`/`p2`. The adapter
    maps platform side -> engine side at the boundary.
    """

    X = "x"
    O = "o"

    @property
    def other(self) -> Side:
        return Side.O if self is Side.X else Side.X


class Coord:
    """Axial hex coordinate. +q right, +r top-right. Unbounded integers.

    Hashable and immutable enough to key a dict. Two coords are equal iff their
    q and r are equal.
    """

    __slots__ = ("q", "r")

    def __init__(self, q: int, r: int) -> None:
        self.q = q
        self.r = r

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Coord) and self.q == other.q and self.r == other.r

    def __hash__(self) -> int:
        return hash((self.q, self.r))

    def __iter__(self) -> Iterator[int]:
        yield self.q
        yield self.r

    def __repr__(self) -> str:
        return f"Coord(q={self.q}, r={self.r})"

    def __str__(self) -> str:
        return f"({self.q},{self.r})"

    @classmethod
    def from_pair(cls, pair: tuple[int, int]) -> Coord:
        return cls(pair[0], pair[1])


class Move:
    """A single complete move of two placements, played by one side.

    Per the htttx spec a move is exactly two placements (`Move.pieces` has
    minItems=2 maxItems=2). The opening ply 0 is the lone exception: the server
    places one cross at the origin, and a bot never submits it. That exception is
    modelled at the board level (the opening is seeded into the board state), not
    by allowing a one-stone `Move`.
    """

    __slots__ = ("pieces", "side")

    def __init__(self, side: Side, pieces: tuple[Coord, Coord]) -> None:
        if len(pieces) != 2:
            raise ValueError(f"a move has exactly two pieces, got {len(pieces)}")
        if pieces[0] == pieces[1]:
            raise ValueError("a move's two pieces must be distinct cells")
        self.side = side
        self.pieces = pieces

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Move) and self.side is other.side and self.pieces == other.pieces

    def __hash__(self) -> int:
        return hash((self.side, self.pieces))

    def __repr__(self) -> str:
        q1, r1 = self.pieces[0]
        q2, r2 = self.pieces[1]
        return f"Move(side={self.side.value}, pieces=[{q1},{r1}, {q2},{r2}])"
