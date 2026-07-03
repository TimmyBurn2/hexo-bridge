"""A minimal custom `EnginePort` implementation.

This is the worked example for "write your own adapter". It imports only core
domain types (`GameState`, `Move`, `Coord`, `Side`), no HTTP, no htttx wire
types, no HeXO. It returns the first two empty cells it finds in a small ring
around the origin. It is deliberately trivial: the point is to show the port
boundary, not to play well.

Select it from a config in either of two ways:

  1. By entry point (the package registers `my_custom_engine` under
     `hexo_bridge.engines` in `pyproject.toml`):

         [engine]
         name = "my_custom_engine"
         [engine.options]
         side = "o"

  2. By dotted path (the local-dev fallback, no entry point needed as long as
     the module is importable):

         [engine]
         name = "hexo_bridge_examples.custom_engine:FirstLegalMoveEngine"
         [engine.options]
         side = "o"
"""

from __future__ import annotations

from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move, Side


class FirstLegalMoveEngine:
    """Trivial custom engine: first two empty cells near the origin.

    Constructor options come straight from `[engine.options]` in the config, so
    `side = "o"` in the TOML becomes `FirstLegalMoveEngine(side="o")`.
    """

    def __init__(self, side: Side | None = None) -> None:
        self._side = Side(side) if side is not None else None

    async def get_move(self, state: GameState) -> Move:
        side = self._side or state.side
        pieces = _pick_two_empty(state)
        return Move(side=side, pieces=pieces)


def _pick_two_empty(state: GameState) -> tuple[Coord, Coord]:
    board = state.to_board()
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
    picked = [Coord(0, 1), Coord(1, 0)]
    return (picked[0], picked[1])
