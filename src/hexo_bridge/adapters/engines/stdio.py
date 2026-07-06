"""Generic stdio line adapter: a language-agnostic engine boundary.

The clean path for an existing engine. An engine with a `reset` / `setup` /
`place` / `best_move` loop in any language plugs in with a small shim that
reads JSON lines from stdin and writes JSON lines to stdout, speaking the
contract below. No Python in hexo-bridge is needed for the engine itself.

This is one of the bridge's two language-agnostic engine boundaries. The full
contract is documented as a versioned, language-agnostic spec in
`docs/stdio-protocol.md`: an author in Rust, C++, or Go can implement it
without reading any Python. This docstring is the short form.

Line protocol (one JSON object per line, UTF-8, newline-terminated). The
adapter is the client; the engine is the server. One request yields one reply,
in order.

Protocol version 1.

Requests (adapter -> engine):

  {"op": "reset"}
      Engine clears to an empty board. Reply: {"ok": true, "v": 1}. The `v`
      field is the protocol version the engine speaks; the adapter accepts
      `v: 1` and rejects any other value as a version mismatch. This is the
      version handshake; it runs on every re-sync (the adapter re-syncs from
      scratch on every `get_move` call).

  {"op": "setup", "cells": [[q, r, side], ...]}
      Apply the starting board the server delivered in the htttx `setup`
      packet. `side` is "x" or "o". The bridge consumes whatever the server
      delivers (the standard server sends one cross at the origin here; a
      conformant server may send a different starting position under
      `free_setup`). Sent once per game before any `place`/`best_move`, and
      only when `state.setup_cells` is non-empty. Omitted for an empty-board
      dry run (validate). Reply: {"ok": true}.

  {"op": "place", "q": <int>, "r": <int>, "side": "x"|"o"}
      Apply one placement. `side` is included so the engine does not have to
      infer turn. Reply: {"ok": true}.

  {"op": "best_move", "time_ms": <int>}
      Ask the engine for the next move for the side to move. `time_ms` is a
      suggested per-move budget (a hint; the bridge's hard clamp is separate).
      Reply: {"move": [[q, r], ...]} with 1 or 2 coord pairs. 1 pair means the
      first stone won; the bridge pads to a two-piece transport shape. An empty
      list means the engine concedes (no move), which the adapter raises as a
      SubprocessEngineError.

  {"op": "quit"}
      No reply expected; the engine exits cleanly. Sent by the base on close.

A malformed reply, a missing field, or a version mismatch is a
`SubprocessEngineError` carrying the captured child stderr.

The adapter re-syncs from scratch on every `get_move` call: it sends `reset`,
then `setup` (when the server delivered a non-empty board), then replays the
cumulative move list via `place`, then `best_move`. This is simple and correct
(the cost is microseconds for a real engine) and means a crashed-and-restarted
child is recovered transparently on the next call, with no incremental-state
bookkeeping to get wrong. The board the engine sees is exactly the board the
server delivered, replayed with the moves the server reported.
"""

from __future__ import annotations

import json
from typing import Any

from hexo_bridge.adapters.engines.subprocess import SubprocessEngine
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move
from hexo_bridge.ports.engine import SubprocessEngineError

_PROTOCOL_VERSION = 1


class StdioLineEngine(SubprocessEngine):
    """Drive an engine speaking the reset/setup/place/best_move stdio line
    protocol (version 1).

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
        """Rebuild the engine's board from scratch on every call.

        Sends `reset` (version handshake, clears to empty), then `setup` with
        the board the server delivered in the `setup` packet (when non-empty),
        then replays every placement in `state.moves` (the completed turns the
        server reported, excluding the setup seed). Full replay every call is
        simple and correct; a crashed child is recovered transparently because
        the next call resets and replays unconditionally.
        """
        await self._send_reset()
        if state.setup_cells:
            await self._send(
                {
                    "op": "setup",
                    "cells": [[q, r, s] for q, r, s in state.setup_cells],
                }
            )
        for mv in state.moves:
            for piece in mv.pieces:
                await self._send(
                    {"op": "place", "q": piece.q, "r": piece.r, "side": mv.side.value}
                )

    async def _send_reset(self) -> None:
        line = await self._send({"op": "reset"})
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SubprocessEngineError(
                f"malformed reset reply: {line!r}", stderr=await self._drain_stderr()
            ) from exc
        if not obj.get("ok"):
            raise SubprocessEngineError(
                f"reset failed: {obj!r}", stderr=await self._drain_stderr()
            )
        v = obj.get("v")
        if v != _PROTOCOL_VERSION:
            raise SubprocessEngineError(
                f"stdio protocol version mismatch: engine speaks v={v!r}, "
                f"adapter requires v={_PROTOCOL_VERSION}",
                stderr=await self._drain_stderr(),
            )
