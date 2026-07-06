# Write your own adapter

The bridge is ports and adapters. The core is pure and does no I/O. You write an
adapter that implements one port, register it, and point a config at it. This
doc covers the engine port (the common case) and the platform port.

## Three engine boundaries, by portability

The bridge has three ways to reach an engine. Pick by how portable you want the
engine to be, not by language:

| Boundary | Portability | When to pick |
| --- | --- | --- |
| htttx-stateless over HTTP | ecosystem-wide | A new engine in any language that should work with any conformant client, not just this bridge. Speaks BSoD's ecosystem engine spec (`/turn`), so the same server works elsewhere. |
| stdio line protocol over a subprocess | this bridge only | Wrapping an existing local engine that already speaks lines, or one that does not want to run an HTTP server. Any language, any ABI; the subprocess boundary decouples ABI from the bridge. |
| in-process Python | pure-Python only | A quick stub or a Python-native engine. No I/O, no subprocess. |

Generality here is language-agnostic wire boundaries plus clear docs, not more
surface area. The bridge ships exactly two wire formats (the htttx-stateless
HTTP shape and the stdio line protocol) plus the Python in-process convenience.
There is no third wire format (no gRPC, protobuf, or msgpack), and no plugin
framework: an engine written to the stdio protocol is coupled to this bridge,
while an htttx-stateless engine is portable. State plainly which one you are
building.

## The engine port

`EnginePort` (`hexo_bridge.ports.engine`): one async method, `get_move(state)
-> Move`. Core types only (`GameState`, `Move`); no HTTP, no htttx, no HeXO.
`get_move` may return one or two pieces: one when the first stone already wins
(the server ends the game on it and ignores the rest); the bridge pads a
one-piece move to two before sending. Raise `EngineTranslationError` (or the
`SubprocessEngineError` subclass) for a bridge-side translation failure; the
bridge never scores it as an engine loss.

`GameState` carries: `side` (the side to move, from `move_request.side`), the
board as `setup_cells` (the cells the server delivered in the `setup` packet)
plus `moves` (the cumulative completed turns), the clock, and an optional
`request_id`. The board the engine plays on is whatever the server delivered;
the bridge does not bake in an origin.

## Tier 1: htttx-stateless over HTTP (the portable boundary)

The engine is a separate HTTP service exposing the htttx stateless
`/turn` endpoint. The bridge POSTs the board and reads back a move. This is the
boundary to pick for a new engine in any language that should work
ecosystem-wide: the same server speaks BSoD's ecosystem engine spec, so it works
with any conformant client, not just this bridge.

Use the shipped `htttx_stateless` adapter:

```toml
[engine]
name = "htttx_stateless"
[engine.options]
base_url = "http://127.0.0.1:8080"
# turn_path = "stateless/v1-alpha/turn"  # default; override via capabilities.json api_root
timeout = 5.0
```

A tiny reference server (stdlib Python, no dependencies) lives at
`examples/stateless_engine_reference.py`. Run it and dry-run the boundary:

```sh
python3 examples/stateless_engine_reference.py --port 8080
hexo-bridge validate examples/config.stateless-engine.toml
```

A non-Python author implements the same `/turn` request/response shape in any
language and host; the bridge does not care.

## Tier 2: the stdio line protocol (the bridge-coupled, any-language boundary)

You have an engine with a `reset` / `setup` / `place` / `best_move` API in any
language. Write a small shim that reads JSON lines from stdin and writes JSON
lines to stdout, speaking the versioned, language-agnostic contract in
`docs/stdio-protocol.md`. The contract is fully documented there; an author in
Rust, C++, or Go can implement it without reading any Python.

```
adapter -> engine:  {"op": "reset"}
engine -> adapter:  {"ok": true, "v": 1}

adapter -> engine:  {"op": "setup", "cells": [[0, 0, "x"]]}
engine -> adapter:  {"ok": true}

adapter -> engine:  {"op": "place", "q": 1, "r": 0, "side": "o"}
engine -> adapter:  {"ok": true}

adapter -> engine:  {"op": "best_move", "time_ms": 300}
engine -> adapter:  {"move": [[1, 0], [-1, 1]]}      # 1 or 2 coord pairs

adapter -> engine:  {"op": "quit"}                    # engine exits
```

`reset` clears to an empty board and runs the version handshake (`v: 1`).
`setup` applies the starting board the server delivered (the standard server
sends one cross at the origin here; the bridge forwards whatever was
delivered). `place` applies one placement (`side` is given, no turn inference).
`best_move` returns 1 or 2 coord pairs; one pair means the first stone won. The
bridge pads a one-pair reply to a two-stone transport shape. A `time_ms` of 0 or
omitted means "no budget"; the bridge's hard clamp is separate (see Think-time
below).

Point the config at it:

```toml
[engine]
name = "stdio"
[engine.options]
command = ["python3", "-m", "my_engine_shim"]
cwd = "/path/to/engine"
time_budget_ms = 300
# env = { "PYTHONPATH": "..." }   # optional
# args = []                       # optional, appended to command
```

The adapter re-syncs from scratch on every call: `reset`, then `setup` (when
the server delivered a non-empty board), then replay the full cumulative move
list, then `best_move`. This is simple, correct, and recovers a crashed child
transparently.

The full contract, framing, error and lifecycle behaviour, and a minimal engine
shape are in `docs/stdio-protocol.md`.

## Tier 3: SubprocessEngine subclass (custom line protocol)

When the stdio protocol does not fit (your engine speaks its own JSON shape, or
you want a tighter integration), subclass `SubprocessEngine`
(`hexo_bridge.adapters.engines.subprocess`). The base owns process spawn,
JSON-line framing, stderr capture, restart-on-death, and lifecycle; you
implement two methods:

```python
from hexo_bridge.adapters.engines.subprocess import SubprocessEngine
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move

class MyEngine(SubprocessEngine):
    def __init__(self, *, python: str, bot_dir: str, time_limit: float = 0.3):
        super().__init__(command=[python, "-c", _SHIM, bot_dir], restart=True)
        self._time_limit = time_limit

    def build_request(self, state: GameState) -> dict:
        # ...your engine's request shape...
        return {"moves": [...], "time_limit": self._time_limit}

    def parse_response(self, obj: dict, state: GameState) -> Move:
        pieces = [Coord(int(q), int(r)) for q, r in obj["moves"]]
        return Move(side=state.side, pieces=(pieces[0], pieces[1]))
```

The worked example is `src/hexo_bridge_examples/seal_perf_engine.py`: it drives
SealBot-perf (a C++ pybind engine built for CPython 3.14) as a subprocess, so
the bridge's own CPython does not need to match the engine's ABI. If the
subprocess fails to import or crashes, the base raises a
`SubprocessEngineError` carrying the captured stderr, so an ABI or import
failure is debuggable instead of opaque.

A subclass that invents its own line shape is coupled to this bridge (it is not
the portable boundary). If portability matters, implement the stdio line
protocol (tier 2) or htttx-stateless (tier 1) instead.

Pass a `time_limit` field in the request as a suggested per-move budget; the
bridge's hard clamp is separate (see Think-time below).

## Tier 4: in-process pure Python

For a Python-native engine or a quick stub, implement `EnginePort.get_move`
directly. No I/O, no subprocess. See
`src/hexo_bridge/adapters/engines/in_process.py`:

```python
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move, Side

class InProcessFirstMoveEngine:
    def __init__(self, side: Side | None = None) -> None:
        self._side = Side(side) if side is not None else None

    async def get_move(self, state: GameState) -> Move:
        side = self._side or state.side
        pieces = _pick_two_empty(state)
        return Move(side=side, pieces=pieces)
```

## Register it

Two ways, both wired for the example.

**Entry point** (the installed-package path). Add to your package's
`pyproject.toml`:

```toml
[project.entry-points."hexo_bridge.engines"]
my_engine = "my_pkg.my_engine:MyEngine"
```

Then select by name:

```toml
[engine]
name = "my_engine"
```

**Dotted path** (the local-dev fallback). No entry point needed:

```toml
[engine]
name = "my_pkg.my_engine:MyEngine"
```

## Think-time: clock vs budget

`state.time_limit_seconds` is **clock-remaining** for this move (the htttx
`move_time_limit` the server enforces), NOT a think budget. The bridge clamps
the engine call to `min(engine_timeout_seconds, time_limit_seconds)` via
`asyncio.wait_for`; that is the hard bound, so an engine that ignores any
suggested budget cannot blow the turn. A budget an engine sets for itself (a
`time_limit` field in a SubprocessEngine request, or `time_ms` in the stdio
`best_move` op) is a hint the engine is free to ignore; the bridge does not
double-clamp.

## Validate before going live

Dry-run the configured engine once, no server, no token:

```sh
hexo-bridge validate config.toml
```

It resolves the engine, spawns it, calls `get_move` once, prints the move and
timing, and exits non-zero on failure (spawn error, ABI/import error, malformed
response, timeout). `validate` runs whichever boundary the config selects:

- in-process: calls `get_move` directly;
- subprocess/stdio: spawns the child, runs the version handshake, calls
  `best_move`, and tears it down;
- htttx-stateless: POSTs to the configured `/turn` URL (so a stateless engine
  must be running for it to succeed).

Catches the failures that bite on first run in seconds.

List registered engine adapters:

```sh
hexo-bridge engines --list
```

## The other ports

- `PlatformPort` (`hexo_bridge.ports.platform`): the platform lifecycle surface.
  HeXO is one adapter implementing all sub-ports. A thinner platform may
  implement a subset.
- `EngineSessionPort` (`hexo_bridge.ports.engine_session`): the per-game
  gameplay channel. You usually do not implement this; the htttx websocket
  adapter is the one for HeXO.

The leak test (`tests/test_leak.py`): if a concrete protocol type appears in a
port interface or in core, the boundary is wrong.

## Entry-point groups

- `hexo_bridge.engines`: `EnginePort` implementations.
- `hexo_bridge.engine_sessions`: `EngineSessionPort` implementations.
- `hexo_bridge.platforms`: `PlatformPort` implementations.
