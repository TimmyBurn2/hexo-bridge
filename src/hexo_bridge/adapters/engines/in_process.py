"""In-process example engine: returns the first legal-looking move it can find.

This exists to prove the `EnginePort` is not htttx-shaped. It imports no HTTP,
no htttx, no HeXO. It takes a `GameState` (core domain) and returns a `Move`
(core domain). A second adapter that is not htttx slots in cleanly.

It does not validate legality (the server is the referee); it just picks two
empty cells near the existing stones, or near the centre of the delivered board.
It is deliberately trivial.
"""

from __future__ import annotations

from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move, Side


class InProcessFirstMoveEngine:
    """A trivial in-process engine. Not htttx, not HTTP, not HeXO.

    Picks the first two empty cells it finds in a spiral around the centre of
    the board the server delivered (the setup seed). Good enough to prove the
    port works end to end; not a real engine.
    """

    def __init__(self, side: Side | str | None = None) -> None:
        self._side = Side(side) if side is not None else None

    async def get_move(self, state: GameState) -> Move:
        side = self._side or state.side
        board = state.to_board()
        pieces = _pick_two_empty(board, side)
        return Move(side=side, pieces=pieces)


def _pick_two_empty(board, side: Side) -> tuple[Coord, Coord]:
    """Pick two empty cells, spiral outwards from the centre of the board.

    The starting point is the average of the occupied cells (the setup seed),
    or (0, 0) only when the board is empty (the offline `validate` dry-run),
    in which case any cell is empty and the spiral finds one immediately. This
    does not bake in an origin convention for live play; it is just a search
    anchor when nothing is on the board.
    """
    picked: list[Coord] = []
    if board.cells:
        qs = [c.q for c in board.cells]
        rs = [c.r for c in board.cells]
        anchor = Coord(sum(qs) // len(qs), sum(rs) // len(rs))
    else:
        anchor = Coord(0, 0)
    radius = 0
    while len(picked) < 2:
        for dq in range(-radius, radius + 1):
            for dr in range(-radius, radius + 1):
                if abs(dq + dr) > radius:
                    continue
                c = Coord(anchor.q + dq, anchor.r + dr)
                if not board.occupied(c) and c not in picked:
                    picked.append(c)
                    if len(picked) == 2:
                        return (picked[0], picked[1])
        radius += 1
        if radius > 20:
            break
    if len(picked) < 2:
        # Unreachable on an infinite board (the spiral always finds empty
        # cells). Kept as a defensive fallback so the function always returns.
        picked = [Coord(anchor.q, anchor.r), Coord(anchor.q + 1, anchor.r)]
    return (picked[0], picked[1])
