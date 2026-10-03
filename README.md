# hexo-bridge

Run a HeXO bot around your engine, on any server that speaks the
[HeXO Bot API](https://github.com/TimmyBurn2/Hexo-Bot-Api) 0.10.0.
You write the engine; the bridge holds the event stream, accepts challenges, plays each
game on its websocket, passes the level a player picked, and reads positions as an analyzer.

An engine is a Python class, or a process in any language that speaks JSON lines.

## Install

```sh
pip install git+https://github.com/TimmyBurn2/hexo-bridge
```

Python 3.11 or newer; the one dependency is `websockets`.

## A bot in Python

```python
import os

from hexo_bridge import Engine, Evaluation, Line, run


class MyEngine(Engine):
    def move(self, position, request):
        return position.fallback_turn()

    def analyze(self, position, request):
        return Line(position.fallback_turn(), Evaluation(heuristic=0.0))


run(
    MyEngine,
    url="https://<domain>",
    token=os.environ["HEXO_BOT_TOKEN"],
    declaration={
        "accepts": {"turnMs": [5000, 300000], "match": True, "unlimited": True},
        "analyzer": {"lines": 1, "maxSeconds": 2},
    },
)
```

`declaration` is sent to `PATCH /api/bot/account` as written, so it takes every field the
API defines: `accepts` (required), `about`, `version`, `repoUrl`, `levels`, and `analyzer`.
`levels` and `analyzer` are checked against the API's bounds before anything is sent, and
when left out they are sent as null, which clears them on the server.
The declaration is sent again on every stream open; a server older than a key refuses it,
and gets the declaration without it.

The bridge makes one engine per game, as `MyEngine(game)`, and one for the analysis session,
as `MyEngine(None)`; any callable taking that one argument works, such as a
`functools.partial`.
`self.game` carries the game's id, side, opponent, clock, and `level`, the id and budget of
the level the player picked, or the default's.

- `move(position, request)` returns the turn: two `(q, r)` cells, a `Line` with an
  evaluation, a list of `Line`s best first (the first is played, the next two are published
  with the game), or `Resign()`.
  `request.time_limit` is the seconds the engine has for this move, the server's limit less
  `reserve_seconds`, or None without a clock.
- `analyze(position, request)` returns up to `request.lines` lines, best first, each with an
  evaluation, within `request.seconds`.
  Only needed when the declaration has an `analyzer`.
- `request.stop` is set when the answer is no longer wanted: past the clock, or when a reading
  is called off.
  A long search should check it.
- `close()` releases what the engine holds; `kill()` stops a call that outlived its request.

An evaluation is htttx's: `heuristic` is any number, read on a -1..1 scale, positive for x;
`win_in` is the turns to a forced win, the turn of the side then to move counted first,
positive when x wins.
Coordinates are axial `(q, r)`: +q right, +r top-right.

`Position` holds the stones in the order they were placed and the side to move, with the
rules an engine needs: `is_legal(turn)`, `reachable(cell)`, `completes_six(cell, side)`,
`play(turn)`, `threats(side)` (the windows a side fills with its next turn),
`winning_turn()`, `free_cell()`, and `fallback_turn()`.

`run` blocks until interrupted; `Bot(...)` is the same with `run()` and `stop()` for a bot
that runs beside other code.
`Client(url, token)` makes the calls the loop does not: `account()`, `challenge(name, body)`
to challenge another bot, and `decline(challenge_id)`.

## An engine in any language

```sh
HEXO_BOT_TOKEN=hxo_... hexo-bridge bot.toml
```

[`examples/bot.toml`](examples/bot.toml) names the server, the engine's command, and the
declaration; [`examples/random_engine.py`](examples/random_engine.py) is a whole engine.

The bridge starts the command once per game and once for the analysis session, writes one
JSON request per line to its stdin, and reads one JSON answer per line from its stdout.
A stdout line that is not a JSON object is logged and skipped; logs belong on stderr all the
same.
It kills the process when an answer is no longer wanted, or is not a valid answer, and starts
it again for the next request, so the process keeps no state the bridge relies on.

A move:

```json
{"type": "move", "board": {"to_move": "o", "cells": [{"q": 0, "r": 0, "p": "x"}]},
 "time_limit": 4.0, "game": {"id": "g_1", "side": "o", "opponent": "rival", "rated": false},
 "level": {"id": "normal", "budget": {"timeMs": 1000}}}
```

A reading:

```json
{"type": "analyze", "board": {"to_move": "x", "cells": [...]}, "time_limit": 2, "lines": 3}
```

Both are answered with an htttx move response, `considerations` optional, and an evaluation
on the move required for a reading:

```json
{"move": {"pieces": [{"q": 1, "r": 0}, {"q": 0, "r": 1}], "evaluation": {"heuristic": 0.1}},
 "considerations": []}
```

A move may instead be answered `{"resign": true}`.
`board.cells` lists the stones in the order they were placed; `time_limit` is null without
a clock.

## What the bridge takes care of

- The event stream, redialed after 1 s, doubling to 8 s, or after a longer `Retry-After`.
- Challenges, accepted as they arrive; the server already holds them to `accepts`.
- Each game's websocket: `setup`, every turn in `previous`, `request_id`, and redialing a
  dropped session or one the server waits on while the bot has nothing to answer.
  A game token opens its session for 60 s, so a later redial reopens the stream, which
  replays the game with a fresh token.
- The analysis session: one position at a time, an `interrupt` that calls the engine off, and
  a failed dial retried while its token lasts.
- Every answer, checked before it is sent: an illegal turn becomes `fallback_turn()`, and an
  evaluation that contradicts the board is brought in line with it, as the server fails a
  reading that does.
  A reading must also take a six the side to move can complete; a game move stays the
  engine's own, so a weak level can still miss one.
- The clock: `reserve_seconds` (1 s by default) is kept back from each move for the network,
  and an engine still searching past that plays `fallback_turn()` rather than lose on time.
  Engines are started off the clock, and a reading's time runs from the server's request.

## Versions

Every request and websocket handshake carries `User-Agent: hexo-bridge/<version>`.

## Development

```sh
uv sync
uv run pytest
uv run ruff check && uv run ruff format --check
```

## License

[MIT](LICENSE).
