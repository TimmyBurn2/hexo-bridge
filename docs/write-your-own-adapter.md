# Write your own adapter

The bridge is ports and adapters. The core is pure and does no I/O. You write an
adapter that implements one port, register it, and point a config at it. This
doc covers the engine port (the common case) and the platform port, with the
minimal custom example from `src/hexo_bridge_examples/`.

## The ports

Two ports you are likely to implement:

- `EnginePort` (`hexo_bridge.ports.engine`): return a move for a game state.
  Implementations may be in-process, an HTTP client, or anything else. Imports
  only core domain types (`GameState`, `Move`); no HTTP, no htttx, no HeXO.
- `PlatformPort` (`hexo_bridge.ports.platform`): the platform lifecycle surface.
  HeXO is one adapter implementing all sub-ports (`events`, `play`,
  `challenges`, `account`, `directory`, `register`). A thinner platform may
  implement a subset.
- `EngineSessionPort` (`hexo_bridge.ports.engine_session`): the per-game
  gameplay channel. You usually do not implement this; the htttx websocket
  adapter is the one for HeXO. It is a port only because the transport is not
  assumed.

The leak test (`tests/test_leak.py`): if a concrete protocol type appears in a
port interface or in core, the boundary is wrong.

## A minimal engine adapter

This is `src/hexo_bridge_examples/custom_engine.py`, whole:

```python
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move, Side


class FirstLegalMoveEngine:
    def __init__(self, side: Side | None = None) -> None:
        self._side = Side(side) if side is not None else None

    async def get_move(self, state: GameState) -> Move:
        side = self._side or state.side
        pieces = _pick_two_empty(state)
        return Move(side=side, pieces=pieces)
```

Constructor options come straight from `[engine.options]` in the config, so
`side = "o"` in the TOML becomes `FirstLegalMoveEngine(side="o")`. Raise
`EngineTranslationError` (from `hexo_bridge.ports.engine`) for a bridge-side
translation failure; the bridge distinguishes it from a genuine engine move and
never scores it as an engine loss.

## Register it

Two ways, both already wired for the example.

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

**Dotted path** (the local-dev fallback). No entry point needed; the resolver
imports the module and reads the attribute:

```toml
[engine]
name = "my_pkg.my_engine:MyEngine"
```

The example ships both: `examples/config.custom-engine.entrypoint.toml` uses the
`my_custom_engine` entry point declared in this repo's `pyproject.toml`;
`examples/config.custom-engine.dotted-path.toml` uses
`hexo_bridge_examples.custom_engine:FirstLegalMoveEngine`. Both resolve to the
same class.

## Entry-point groups

- `hexo_bridge.engines`: `EnginePort` implementations.
- `hexo_bridge.engine_sessions`: `EngineSessionPort` implementations.
- `hexo_bridge.platforms`: `PlatformPort` implementations.

## A note on the engine session

You almost never write an `EngineSessionPort`. The HeXO server hands the bridge
a `socketUrl` on `gameStart`, the bridge dials it, and the bundled
`HtttxWebsocketSession` speaks the htttx basic_websocket protocol over it. If
you do need a different transport (a test harness, a relay), implement
`EngineSessionPort` the same way: `connect`, `recv`, `send_move_response`,
`close`, and register under `hexo_bridge.engine_sessions`.
