# stdio line protocol (v1)

The bridge's local, language-agnostic engine boundary. This document is the
contract: an author in Rust, C++, Go, or any language that reads lines from
stdin and writes lines to stdout can implement an engine against it without
reading any Python. The bridge's `stdio` adapter is the reference client.

This is one of two language-agnostic engine boundaries. The other,
htttx-stateless over HTTP, is portable across the whole ecosystem (it speaks
BSoD's ecosystem engine spec, not a bridge-private protocol). An engine written
only to this stdio protocol is coupled to this bridge; an htttx-stateless engine
is not. See `docs/write-your-own-adapter.md` for the portability trade-off.

## Framing

One UTF-8 JSON object per line, newline-terminated (`\n`), on stdin and stdout.
The bridge is the client; the engine is the server. One request yields exactly
one reply, in order. The engine writes diagnostics to stderr (captured by the
bridge and surfaced in the error when a call fails).

The bridge re-syncs the engine from scratch on every move request: it sends
`reset`, then `setup` (when the server delivered a non-empty board), then one
`place` per completed turn, then `best_move`. The engine need not persist state
across requests; a crashed-and-restarted child is recovered transparently on the
next call.

## Version

Protocol version 1. The version handshake is the `reset` reply: the engine
returns `{"ok": true, "v": 1}`. The bridge accepts `v: 1` and rejects any other
value as a version mismatch (a `SubprocessEngineError` with the captured stderr).
A future v2 would negotiate here; the bridge would error rather than guess.

## Requests (bridge -> engine)

### `reset` - clear to an empty board

```json
{"op": "reset"}
```

Reply:

```json
{"ok": true, "v": 1}
```

Clear the board to empty. The version handshake runs here. Sent first on every
re-sync.

### `setup` - apply the starting board the server delivered

```json
{"op": "setup", "cells": [[q, r, side], ...]}
```

Reply:

```json
{"ok": true}
```

`side` is `"x"` or `"o"`. This is the board the server delivered in the htttx
`setup` packet, forwarded unchanged. The standard server sends one cross at the
origin here; a conformant server may send a different starting position (under
`free_setup`), and the bridge forwards whatever was delivered. The engine
applies it as the seed; it does not bake in an origin. Sent once per game before
any `place` or `best_move`, and only when the server delivered a non-empty
board. Omitted for an empty-board dry run (`validate`).

### `place` - apply one placement

```json
{"op": "place", "q": <int>, "r": <int>, "side": "x"|"o"}
```

Reply:

```json
{"ok": true}
```

Apply one placement. `side` is given so the engine does not have to infer turn.
Sent once per stone of each completed turn the server reported in `previous`,
in play order.

### `best_move` - ask for the next move

```json
{"op": "best_move", "time_ms": <int>}
```

Reply:

```json
{"move": [[q, r], ...]}
```

Ask the engine for the next move for the side to move. `time_ms` is a suggested
per-move budget in milliseconds (a hint; the bridge's hard clamp on the engine
call is separate and authoritative). The reply `move` is a list of 1 or 2 coord
pairs:

- 2 pairs: a normal two-stone turn.
- 1 pair: the first stone already wins (the server ends the game on it); the
  bridge pads to a two-stone transport shape before sending it on the wire.
- empty list: the engine concedes (no move). The bridge raises a
  `SubprocessEngineError` and does not submit a move.

### `quit` - clean shutdown

```json
{"op": "quit"}
```

No reply expected. The engine exits cleanly. Sent by the bridge on shutdown.

## Errors and lifecycle

- A malformed JSON reply, a missing field, or a version mismatch is a
  `SubprocessEngineError` carrying the captured child stderr, so an import, ABI,
  or logic failure is debuggable instead of opaque.
- If the child crashes or closes stdout mid-call, the bridge raises a
  `SubprocessEngineError` with the return code and stderr, then respawns a fresh
  child on the next call (the re-sync replays from scratch, so no state is
  lost).
- The bridge clamps the `best_move` call to `min(engine_timeout, clock)` via a
  hard timeout; `time_ms` is only a hint the engine is free to ignore.

## Minimal engine shape (any language)

A complete engine is a loop that reads a line, dispatches on `op`, writes one
reply line, and exits on `quit`. Pseudocode:

```
read line
while line not empty and line != "quit":
    req = parse_json(line)
    switch req.op:
        case "reset":    board = {}; reply({"ok": true, "v": 1})
        case "setup":    for c in req.cells: board[(c.q,c.r)] = c.side; reply({"ok": true})
        case "place":    board[(req.q,req.r)] = req.side; reply({"ok": true})
        case "best_move": reply({"move": pick_two_empty(board)})
    read line
exit
```

`reply(obj)` is `print(to_json(obj))` followed by a flush. That is the whole
contract.

## Think-time: clock vs budget

The `time_ms` field on `best_move` is a suggested budget, not the turn clock.
The real clock is enforced by the server and clamped by the bridge's hard
timeout on the engine call (see `docs/write-your-own-adapter.md`). An engine
that ignores `time_ms` entirely still plays legally; it just may not use the
full budget. Do not treat `time_ms` as a hard deadline you must self-terminate
against; the bridge will kill the call at the clock bound.

## Reference client

The bridge's `stdio` adapter (`src/hexo_bridge/adapters/engines/stdio.py`) is
the reference client for this protocol. A worked engine shim driving a real
third-party engine (SealBot-perf, a C++ pybind engine) lives at
`src/hexo_bridge_examples/seal_perf_engine.py`.
