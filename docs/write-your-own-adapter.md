# Write your own adapter

The bridge is ports and adapters. The core is pure and does no I/O. You write an
adapter that implements one port, register it, and point a config at it. This
doc covers the engine port (the common case) and the platform port.

## The engine port

`EnginePort` (`hexo_bridge.ports.engine`): one async method, `get_move(state)
-> Move`. Core types only (`GameState`, `Move`); no HTTP, no htttx, no HeXO.
`get_move` may return one or two pieces: one when the first stone already wins
(the server ends the game on it and ignores the rest); the bridge pads a
one-piece move to two before sending. Raise `EngineTranslationError` (or the
`SubprocessEngineError` subclass) for a bridge-side translation failure; the
bridge never scores it as an engine loss.

Three tiers, cleanest path first for an existing engine.

## Tier 1: the stdio adapter (zero Python for your engine)

You have an engine with a `reset` / `place` / `best_move` API in any language.
Write a ~20-line shim that reads JSON lines from stdin and writes JSON lines to
stdout, speaking this protocol:

```
adapter -> engine:  {"op": "reset"}
engine -> adapter:  {"ok": true}

adapter -> engine:  {"op": "place", "q": 1, "r": 0, "side": "o"}
engine -> adapter:  {"ok": true}

adapter -> engine:  {"op": "best_move", "time_ms": 300}
engine -> adapter:  {"move": [[1, 0], [-1, 1]]}      # 1 or 2 coord pairs

adapter -> engine:  {"op": "quit"}                    # engine exits
```

`reset` seeds the opening cross at the origin (the server auto-plays it; the
engine starts every game from that position). `place` applies one placement
(`side` is given, no turn inference). `best_move` returns 1 or 2 coord pairs;
one pair means the first stone won. The bridge pads a one-pair reply to a
two-stone transport shape. A `time_ms` of 0 or omitted means "no budget"; the
bridge's hard clamp is separate (see Think-time below).

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

The adapter is stateful: it `place`s incrementally and only `reset`s on first
connect or after the base restarts a crashed child. If your engine is
stateless-by-replay (it rebuilds the board from a move list each call), use
tier 2 instead.

The full protocol is documented in `src/hexo_bridge/adapters/engines/stdio.py`.

## Tier 2: SubprocessEngine subclass (native or foreign, custom protocol)

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

Pass a `time_limit` field in the request as a suggested per-move budget; the
bridge's hard clamp is separate (see Think-time below).

## Tier 3: in-process pure Python

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

## Tier 4: htttx stateless (network server)

When the engine is a separate HTTP service speaking the htttx stateless
`/turn` protocol, use the shipped `htttx_stateless` adapter. This is for an
engine hosted as a network server, not a local process. See
`src/hexo_bridge/adapters/engines/htttx_stateless.py`.

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

Dry-run the configured engine once against an empty board, no server, no token:

```sh
hexo-bridge validate config.toml
```

It resolves the engine, spawns it, calls `get_move` once, prints the move and
timing, and exits non-zero on failure (spawn error, ABI/import error, malformed
response, timeout). Catches the failures that bite on first run in seconds.

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
