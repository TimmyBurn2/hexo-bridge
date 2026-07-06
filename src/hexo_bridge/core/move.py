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
    """A single complete move of one or two placements, played by one side.

    Per the htttx spec a completed turn is two placements (`Move.pieces` has
    minItems=2 maxItems=2). The opening position (one cross at the origin on
    the standard server) is delivered in the `setup` packet, not submitted as a
    `Move`; a bot never submits the opening. That setup seed is carried on
    `GameState.setup_cells`, not modelled as a `Move`.

    A `Move` may carry ONE placement when the engine returns a single stone that
    wins the game on the first cross of a turn (the server ends the game on that
    stone and ignores the rest). The bridge owns a normalizer (`normalize_move`)
    that pads a one-piece move to two before it is sent on the wire, so the
    transport always carries a legal two-stone shape. Adapters may return one or
    two pieces; the bridge pads.
    """

    __slots__ = ("pieces", "side")

    def __init__(self, side: Side, pieces: tuple[Coord, ...]) -> None:
        if len(pieces) not in (1, 2):
            raise ValueError(f"a move has one or two pieces, got {len(pieces)}")
        if len(pieces) == 2 and pieces[0] == pieces[1]:
            raise ValueError("a move's two pieces must be distinct cells")
        self.side = side
        self.pieces = pieces

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Move) and self.side is other.side and self.pieces == other.pieces

    def __hash__(self) -> int:
        return hash((self.side, self.pieces))

    def __repr__(self) -> str:
        if len(self.pieces) == 1:
            q1, r1 = self.pieces[0]
            return f"Move(side={self.side.value}, pieces=[{q1},{r1}])"
        q1, r1 = self.pieces[0]
        q2, r2 = self.pieces[1]
        return f"Move(side={self.side.value}, pieces=[{q1},{r1}, {q2},{r2}])"


def normalize_move(move: Move, board) -> Move:
    """Pad a one-piece move to a legal two-piece move for the transport.

    The htttx wire shape requires exactly two placements per move_response. An
    engine may return a single stone when the first stone already wins (the
    server ends the game on the winning stone and ignores the rest). This
    helper pads such a move with a distinct empty neighbour so the transport
    carries a legal shape. Two-piece moves pass through unchanged.

    Every candidate filler is checked for occupancy on the rebuilt board. If
    the engine was wrong about the first stone winning and the filler lands on
    an occupied cell, the server would forfeit the bot as illegal-move, so the
    filler MUST be empty. Falls back to a widening spiral if the three cardinal
    neighbours are all occupied; raises only if no empty cell exists.
    """
    if len(move.pieces) == 2:
        return move
    if len(move.pieces) != 1:
        raise ValueError(f"normalize_move expects 1 or 2 pieces, got {len(move.pieces)}")
    first = move.pieces[0]
    # Try the cardinal neighbours first, then a widening spiral for a nearly
    # full board. Every candidate is checked for occupancy; no fallback skips
    # the check (an occupied filler would forfeit illegal-move).
    candidates = [
        Coord(first.q + 1, first.r),
        Coord(first.q, first.r + 1),
        Coord(first.q - 1, first.r),
        Coord(first.q, first.r - 1),
        Coord(first.q + 1, first.r + 1),
        Coord(first.q - 1, first.r - 1),
        Coord(first.q + 1, first.r - 1),
        Coord(first.q - 1, first.r + 1),
    ]
    for cand in candidates:
        if cand != first and not board.occupied(cand):
            return Move(side=move.side, pieces=(first, cand))
    # Last resort: any empty cell in a spiral out to a bounded radius.
    for radius in range(2, 6):
        for dq in range(-radius, radius + 1):
            for dr in range(-radius, radius + 1):
                if abs(dq + dr) > radius:
                    continue
                cand = Coord(first.q + dq, first.r + dr)
                if cand != first and not board.occupied(cand):
                    return Move(side=move.side, pieces=(first, cand))
    raise ValueError(f"no empty neighbour to pad single-stone move at {first}")
