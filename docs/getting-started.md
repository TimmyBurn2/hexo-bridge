# Getting started

This walks a bot author from zero to a running bridge. Lead with a working
config, then the steps around it.

## 1. Install

```sh
git clone https://github.com/TimmyBurn2/hexo_bridge hexo-bridge
cd hexo-bridge
uv sync
```

The `hexo-bridge` command comes from `[project.scripts]` in `pyproject.toml`.

## 2. Get a bot:play token

HeXO is the platform. An operator registers a bot instance with a `bot:register`
token; each registered instance gets its own `bot:play` token. The bridge plays
games with the `bot:play` token. Ask your operator for one, or register an
instance yourself if you hold a `bot:register` token (see
`examples/config.register.toml`).

Keep the token in the environment, not the file:

```sh
export HEXO_BRIDGE_TOKEN=hxo_your_play_token
```

## 3. Point at an engine

The engine is the compute backend. It returns a move for a board state. The
bridge ships three:

- `in_process_first_move`: a trivial in-process picker. No external process.
  Good for a smoke test.
- `htttx_stateless`: an HTTP client to a bot-hosted stateless `/turn` endpoint
  (the htttx stateless v1-alpha). Use this if your engine is an HTTP service.
- `my_custom_engine`: the worked example for "write your own" (see
  `docs/write-your-own-adapter.md`).

If your engine is something else, write an adapter (next doc) and select it by
entry point or dotted path.

## 4. Fill a config

Copy the simplest one and edit:

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
```

This is `examples/config.in-process.toml`. The `side` option (`x` or `o`) is the
side your engine plays; the bridge also maps the platform's `p1`/`p2` to `x`/`o`
at the boundary.

Token from env vs file: `HEXO_BRIDGE_TOKEN` takes precedence over
`[platform.options] token`. Keep it in the env for production, in the file only
for local dev.

## 5. Run

```sh
uv run hexo-bridge config.toml
```

The bridge opens the global event stream, advertises itself as open for
challenges, and on each `gameStart` dials the engine session the server hands
you and plays the game. Logs go to stderr at INFO.

## What you see

- `game g...: started (side=p1)` when a game begins.
- `game g...: finished (reason=..., winner=...)` when it ends. The server is the
  referee: it owns legality, the clock, rating, and the illegal-move forfeit
  (`finishReason: illegal-move`, opponent as winner). The bridge does not
  resign after a rejected move, there is no move POST to reject.

## Next

- `docs/data-flow.md` for who owns what across the bridge.
- `docs/write-your-own-adapter.md` to ship your own engine or platform.
- `examples/` for the full set of configs.
- `OPEN-QUESTIONS.md` for what is still open.
