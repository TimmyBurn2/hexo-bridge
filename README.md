# hexo-bridge

A thin adapter that sits between a HeXO bot server and your bot engine. The
bridge opens the HeXO global event stream, and on each `gameStart` dials the
engine session the server hands you (`gameStart.engine.socketUrl` plus a
short-lived per-game token) and plays the game over the htttx engine protocol.
The HeXO server is the referee: it owns legality, the clock, rating, and the
illegal-move forfeit. There is no HeXO move POST and no per-game HeXO stream.

Ports and adapters: the core is pure and does no I/O. Adapters under
`hexo_bridge.adapters` implement the ports for concrete protocols. The default
stack (HeXO plus htttx) is batteries-included and runs as-is; the plugin path is
the way to extend it.

## Working config

```toml
# config.toml
[platform]
name = "hexo"
base_url = "https://hexo.did.science"

[engine]
name = "in_process_first_move"
[engine.options]
side = "o"

[engine_session]
name = "htttx_websocket"

[bridge]
engine_timeout_seconds = 5.0
reconnect_backoff_seconds = 1.0
reconnect_max_seconds = 30.0
```

## Quickstart

```sh
git clone https://github.com/TimmyBurn2/hexo_bridge hexo-bridge
cd hexo-bridge
uv sync
export HEXO_BRIDGE_TOKEN=hxo_your_bot_play_token
uv run hexo-bridge config.toml
```

The bridge opens the global event stream, advertises itself as open for
challenges, and on each `gameStart` dials the engine session and plays. Logs go
to stderr at INFO. Get a `bot:play` token from your operator, or register an
instance yourself with a `bot:register` token (`examples/config.register.toml`).

The token is read from `HEXO_BRIDGE_TOKEN` (preferred) or
`[platform.options] token` in the file. Keep it in the env for production.
The token requirement belongs to the HeXO adapter: a non-HeXO platform (like
the offline loopback below) needs no token at all.

## Run offline ("just htttx")

No HeXO account, no token, no network:

```sh
uv run hexo-bridge examples/config.loopback.toml
```

The loopback platform synthesizes `gameStart` and `gameFinish` and stands up a
local scripted htttx endpoint, so the bridge's real `htttx_websocket` session
adapter plays a real websocket game entirely on this machine, then the bridge
exits. It is a test harness, not a referee: no legality, no win detection, no
clocks. Use it to smoke-test an engine end to end before pointing at a real
HeXO server.

## The two ports and the plugin path

The bridge is built on two ports plus an engine session channel:

- `EnginePort` (`hexo_bridge.ports.engine`): return a move for a game state.
  Ships `in_process_first_move` (a trivial picker, good for a smoke test) and
  `htttx_stateless` (an HTTP client to a bot-hosted stateless `/turn` endpoint).
- `PlatformPort` (`hexo_bridge.ports.platform`): the HeXO lifecycle surface
  (events, play, challenges, account, directory, register). HeXO is the one
  adapter implementing all sub-ports.
- `EngineSessionPort` (`hexo_bridge.ports.engine_session`): the per-game
  gameplay channel. You almost never implement this; the bundled
  `htttx_websocket` adapter speaks the htttx basic_websocket protocol over the
  socket the HeXO server hands you.

Write an adapter that implements one port and register it under the matching
entry-point group, then point a config at it:

```toml
[project.entry-points."hexo_bridge.engines"]
my_engine = "my_pkg.my_engine:MyEngine"
```

```toml
[engine]
name = "my_engine"
```

For local dev you can skip the entry point and use a dotted path:
`name = "my_pkg.my_engine:MyEngine"`. See `docs/write-your-own-adapter.md` for
the full walkthrough and `examples/` for the full set of configs (in-process,
stateless HTTP, websocket session, register/retire, and a worked custom adapter
reached both ways).

## What the bridge does not do

- No HeXO move POST. The server is the referee.
- No resign after a rejected move. A genuine engine move the server rejects ends
  the game with `finishReason: illegal-move`, server-side.
- No CAS `ply`. Retry safety lives in htttx answer-matching on the engine
  session: when the server assigns a `request_id` the adapter echoes it
  unchanged and drops a stale (interrupted) or mismatched (reordered) answer so
  a resent move cannot double-apply; when the server does not assign ids, the
  adapter correlates positionally (one request outstanding), exactly as open as
  the htttx spec.

The bridge is server-neutral: it builds the board from the `setup` packet the
server delivers (not a baked-in origin), takes the side to move from
`move_request.side` (not ply parity), and plays a positional-only conformant
server (no `request_id`) as well as one that assigns ids. See
`docs/data-flow.md` for who owns what across the bridge.

## Spec provenance

The bridge does not vendor either spec. The hand-written models in
`adapters/platforms/hexo_models.py`, `adapters/engine_sessions/htttx_models.py`,
and `adapters/engines/htttx_stateless_models.py` model the slice the bridge
branches on, with tolerate-unknown so additive spec changes do not break it. The
exact spec commits the models were built against are pinned in `pyproject.toml`
under `[tool.hexo_bridge.specs]`:

- HeXO Bot API: `github.com/TimmyBurn2/Hexo-Bot-Api`
- htttx bot API: `github.com/hex-tic-tac-toe/htttx-bot-api`

The contract test (`tests/test_spec_contract.py`) fetches each spec at its
pinned commit, parses every spec example against the matching hand-written
model, and asserts the discriminator enums the bridge branches on still match.
Run it with `RUN_CONTRACT_TESTS=1 uv run pytest` (it is skipped offline by
default). Bump the pin and the models together; the contract test enforces the
match.

## Open questions

`OPEN-QUESTIONS.md` tracks what is still unresolved. The package name
(`hexo_bridge` / `hexo-bridge`) is the user's to finalize.
