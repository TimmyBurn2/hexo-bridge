# Data flow

Which side owns what. Read this once to know where to look when something goes
wrong.

## Two layers, one bridge

```mermaid
sequenceDiagram
    participant HeXO as HeXO server (referee)
    participant Bridge as hexo-bridge
    participant Engine as your engine

    Note over HeXO,Bridge: global NDJSON event stream<br/>(gameStart, gameFinish, challenge*, opponentGone)
    Bridge->>HeXO: GET /api/stream/event (long-lived)
    HeXO-->>Bridge: event lines + blank-line keepalive

    HeXO->>Bridge: gameStart.engine<br/>(socketUrl + per-game token)

    Note over HeXO,Bridge: htttx websocket (engine session the server hosts)<br/>server sends move_request<br/>adapter sends move_response
    Bridge->>HeXO: dial socketUrl (wss, per-game bearer)
    loop per move
        HeXO->>Bridge: move_request (side, previous, request_id?)
        Bridge->>Engine: EnginePort.get_move(state)
        Engine-->>Bridge: Move (core domain)
        Bridge->>HeXO: move_response (echo request_id)
    end

    HeXO->>Bridge: gameFinish (finishReason, winner)
```

## HeXO lifecycle vs engine session

The HeXO server owns the lifecycle: pairing, challenges, the global event
stream, game start and finish. The bridge opens one long-lived global stream
per process and dispatches `gameStart` to a per-game task.

The engine session is per-game. On `gameStart` the server hands the bridge a
`socketUrl` and a short-lived per-game token. The bridge dials it and plays that
one game over it. When the game ends the server closes the socket and emits
`gameFinish` on the global stream. The bridge does not open a second stream per
game.

## Who owns what

| Concern | Owner |
| --- | --- |
| Pairing, challenges, ratings | HeXO server |
| Clocks (turn / match time control) | HeXO server |
| Move legality | HeXO server |
| The illegal-move forfeit (`finishReason: illegal-move`) | HeXO server |
| The engine session (`socketUrl`) | HeXO server (hosted), adapter (dialed) |
| Computing a move | your engine (`EnginePort`) |
| Mapping `p1`/`p2` to `x`/`o` | the bridge, at the boundary |
| Reconnecting the global stream | the bridge, with backoff |
| Reconnecting a dropped engine session | the bridge, via a fresh `gameStart` on reconnect |

The bridge does not adjudicate. It never resigns after a rejected move (there is
no move POST to reject). On a bridge-side translation error it does not send a
move, so the server times the side out on its own terms; a genuine engine move
that the server rejects as illegal ends the game server-side.

## Retry safety

There is no HeXO move POST, so there is no CAS `ply` to guard. The "a resent
move cannot double-apply" property lives in htttx `request_id` answer-matching
on the engine session:

- The server assigns a strictly-increasing `request_id` per `move_request`.
- The adapter echoes it unchanged on the answering `move_response`.
- The server discards any response whose id is not the outstanding one.
- An `interrupt` invalidates the outstanding request; a late answer is dropped.

The `HtttxWebsocketSession` adapter goes one step further and drops a stale
(interrupted) or mismatched (reordered) answer locally rather than sending it.
If the bot declares `basic_websocket.v1-alpha.request_id` in its capabilities,
set `require_request_id = true` in `[engine_session.options]` and the adapter
will also drop any `move_request` that arrives without an id. See
`examples/config.websocket-session.toml`.

## The engine alphabet vs the platform alphabet

Core speaks the htttx engine alphabet: `x` (crosses) and `o` (circles). The
HeXO platform surface speaks play order: `p1` and `p2`. The platform adapter
maps between them at the boundary; core never sees `p1`/`p2`. Your engine
returns `x`/`o` moves; the bridge sends them over the engine session unchanged.
