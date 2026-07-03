"""In-process example engine: returns the first legal-looking move it can find.

This exists to prove the `EnginePort` is not htttx-shaped. It imports no HTTP,
no htttx, no HeXO. It takes a `GameState` (core domain) and returns a `Move`
(core domain). A second adapter that is not htttx slots in cleanly.

It does not validate legality (the server is the referee); it just picks two
empty cells adjacent to existing stones, or near the origin if the board is
nearly empty. It is deliberately trivial.
"""

from __future__ import annotations

from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move, Side


class InProcessFirstMoveEngine:
    """A trivial in-process engine. Not htttx, not HTTP, not HeXO.

    Picks the first two empty cells it finds in a spiral around the origin.
    Good enough to prove the port works end to end; not a real engine.
    """

    def __init__(self, side: Side | str | None = None) -> None:
        self._side = Side(side) if side is not None else None

    async def get_move(self, state: GameState) -> Move:
        side = self._side or state.side
        board = state.to_board()
        pieces = _pick_two_empty(board, side)
        return Move(side=side, pieces=pieces)


def _pick_two_empty(board, side: Side) -> tuple[Coord, Coord]:
    """Pick two empty cells near the origin, spiral outwards."""
    picked: list[Coord] = []
    radius = 0
    while len(picked) < 2:
        for q in range(-radius, radius + 1):
            for r in range(-radius, radius + 1):
                if abs(q + r) > radius:
                    continue
                c = Coord(q, r)
                if not board.occupied(c) and c not in picked:
                    picked.append(c)
                    if len(picked) == 2:
                        return (picked[0], picked[1])
        radius += 1
        if radius > 20:
            break
    if len(picked) < 2:
        picked = [Coord(0, 0), Coord(1, 0)]
    return (picked[0], picked[1])
