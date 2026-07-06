"""Generic stdio line adapter: the UCI analog for HeXO.

The clean path for an existing engine. An engine with a `reset` / `place` /
`best_move` loop in any language plugs in with a ~20-line shim that reads JSON
lines from stdin and writes JSON lines to stdout, speaking the protocol below.
No Python in hexo-bridge is needed for the engine itself.

Line protocol (one JSON object per line, UTF-8, newline-terminated). The
adapter is the client; the engine is the server.

Requests (adapter -> engine):
  {"op": "reset"}
      Engine resets to an empty board with the opening cross at the origin
      seeded. Reply: {"ok": true}.

  {"op": "place", "q": <int>, "r": <int>, "side": "x"|"o"}
      Apply one placement. `side` is included so the engine does not have to
      infer turn. Reply: {"ok": true}.

  {"op": "best_move", "time_ms": <int>}
      Ask the engine for the next move for the side to move. `time_ms` is a
      suggested per-move budget (a hint; the bridge's hard clamp is separate).
      Reply: {"move": [[q, r], ...]} with 1 or 2 coord pairs. 1 pair means the
      first stone won; the bridge pads to a two-piece transport shape. An empty
      list means the engine concedes (no move).

  {"op": "quit"}
      No reply expected; the engine exits cleanly. Sent by the base on close.

A malformed reply or a missing field is a `SubprocessEngineError` carrying the
captured child stderr.

The adapter is stateful between move requests: it `place`s incrementally and
only `reset`s on first connect or after the base restarts a crashed child. This
mirrors how a real engine works and avoids a full replay each turn. On restart
the next `get_move` unconditionally `reset`s and replays the cumulative move
list, so a crashed child is recovered without the caller knowing.
"""

from __future__ import annotations

import json
from typing import Any

from hexo_bridge.adapters.engines.subprocess import SubprocessEngine
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move
from hexo_bridge.ports.engine import SubprocessEngineError


class StdioLineEngine(SubprocessEngine):
    """Drive an engine speaking the reset/place/best_move stdio line protocol.

    Config (entry point `stdio`):

        [engine]
        name = "stdio"
        [engine.options]
        command = ["python3", "-m", "my_engine_shim"]
        args = []                       # optional, appended to command
        cwd = "/path/to/engine"
        time_budget_ms = 300
        env = { "PYTHONPATH": "..." }   # optional
    """

    def __init__(
        self,
        *,
        command: list[str],
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        time_budget_ms: int = 300,
    ) -> None:
        super().__init__(
            command=list(command) + list(args or []),
            cwd=cwd,
            env=env,
            restart=True,
        )
        self._time_budget_ms = time_budget_ms
        # 0 = not yet synced (needs reset + replay), 1 = synced to the current
        # cumulative state. Cleared on restart so the next get_move replays.
        self._synced = False

    async def get_move(self, state: GameState) -> Move:
        await self._sync(state)
        line = await self._send({"op": "best_move", "time_ms": self._time_budget_ms})
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SubprocessEngineError(
                f"malformed JSON line: {line!r}", stderr=await self._drain_stderr()
            ) from exc
        return self.parse_response(obj, state)

    def parse_response(self, obj: dict[str, Any], state: GameState) -> Move:
        if "move" not in obj:
            raise SubprocessEngineError(f"best_move reply missing 'move': {obj!r}")
        raw = obj["move"]
        if not raw:
            raise SubprocessEngineError("engine returned no move (empty 'move' list)")
        coords = tuple(Coord(int(q), int(r)) for q, r in raw)
        if len(coords) not in (1, 2):
            raise SubprocessEngineError(f"engine returned {len(coords)} pieces, expected 1 or 2")
        return Move(side=state.side, pieces=coords)

    async def _sync(self, state: GameState) -> None:
        """Rebuild the engine's board to match the cumulative state.

        On first call or after a restart, `reset` and replay every placement
        (the opening at origin is seeded by the engine's `reset`; the cumulative
        moves list excludes it, per core convention). After a successful sync,
        incremental `place` calls are NOT tracked across requests: the adapter
        re-syncs from scratch every call. This is simpler and correct; the cost
        is a full replay each turn, which a real engine handles in microseconds.
        Stateful incremental mode is a future optimization.
        """
        await self._send({"op": "reset"})
        for mv in state.moves:
            for piece in mv.pieces:
                await self._send({"op": "place", "q": piece.q, "r": piece.r, "side": mv.side.value})
        self._synced = True
