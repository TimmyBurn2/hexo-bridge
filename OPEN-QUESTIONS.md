# Open questions

Design questions surfaced during the build, not buried. Each is grounded in
the spec or server source where possible, and labelled with a status.

## 1. Obviated: CAS-ply move-submit / 422-then-resign forfeit

**Status: obviated. The spec has no move-submit endpoint, so there is nothing
to guard. Not returning.**

The dispatcher described a HeXO move-submit endpoint with CAS `ply`, a per-game
NDJSON stream, and a `422`-then-resign forfeit flow. The actual HeXO openapi.yaml
has none of these: there is one global NDJSON stream (`GET /api/stream/event`),
and per-game play runs over a websocket engine session (`gameStart.engine.socketUrl`
plus a short-lived per-game token). The spec stops at the session boundary:
"Gameplay (move exchange, answer-matching) runs over that session, not over this
API" (`EngineSession`). The server is the sole referee: on an illegal move it
ends the game with `finishReason: illegal-move` and the opponent as winner
(`GameEventInfo.finishReason`). There is no move POST to `422`, so there is no
`422`-then-resign path for the bridge to implement. CAS `ply` has no endpoint to
guard. This is closed, not deferred: if the spec ever adds a move-submit
endpoint it will be a new design, not a revival of this one.

## 2. Engine session role: pinned from the htttx spec

**Status: resolved, grounded in the htttx basic_websocket spec.**

The htttx basic_websocket spec (`definitions/basic_websocket/bws-v1-alpha.yaml`)
fixes the role by packet direction, not by who dials:

- `MoveRequestPacket`: "A packet sent from the **client**, requesting the
    **bot** to evaluate the position, and make a move."
- `MoveResponsePacket`: "A packet sent by the **bot** in response to a move
    request."
- `info.description`: "Api for bots to implement."

So the **client** sends `move_request` (and `setup`, `heartbeat`, `interrupt`,
`eval_request`); the **bot** sends `move_response` (and `eval_response`). The
HeXO `EngineSession` (`openapi.yaml`) hands the adapter a `socketUrl` to dial,
server-issued and read-only. The HeXO server is the referee (it owns pairing,
clocks, ratings, and the illegal-move forfeit), so it must be the party that
requests moves. The adapter computes moves, so it must be the party that
responds. Therefore: the HeXO server plays the htttx **client** role, the
adapter plays the htttx **bot** role.

The ws-layer dial direction is inverted from the canonical htttx deployment
(where the bot hosts `/game` and the client dials it): in HeXO the server hosts
the `socketUrl` and the adapter dials it. The htttx spec defines roles by
packet direction, not dial direction, so the inversion is a transport concern
only. The adapter dials `socketUrl` as the ws-layer client but plays the htttx
bot role at the application layer: it receives `move_request` and sends
`move_response`. This is the only semantically consistent reading (the server
cannot compute the bot's move). Cited in
`hexo_bridge/adapters/engine_sessions/htttx_websocket.py`.

## 3. htttx drift check uses a hand-curated combined file

**Status: removed. The curated file, the codegen, and the drift gate are gone.**

The bridge no longer vendors either spec or generates models from them. The
hand-written models in `adapters/platforms/hexo_models.py`,
`adapters/engine_sessions/htttx_models.py`, and
`adapters/engines/htttx_stateless_models.py` model the slice the bridge branches
on, with `extra="ignore"` so additive spec changes do not break parsing. The
contract test (`tests/test_spec_contract.py`) fetches each spec at the commit
pinned in `pyproject.toml` under `[tool.hexo_bridge.specs]`, parses every spec
example against the matching model, and asserts the discriminator enums the bridge
branches on still match. That replaces the drift gate.

## 4. Setup packet board is assumed standard

**Status: assumption, documented in `engine_sessions/htttx_websocket.py`.**

The bridge assumes the setup packet's board is the standard opening (one cross
at origin). If the server sends a non-standard setup (the `free_setup`
capability), the bridge logs a warning but still plays on the standard board.
A full fix honors `SetupPacket.board_cells` when non-empty, but this requires
the bridge to support arbitrary starting positions, which the core `Board.replay`
does not currently model (it always seeds `with_opening()`).

## 5. Package name not finalized

**Status: open, user's to finalize.**

The package is `hexo_bridge` (distribution `hexo-bridge`). The dispatcher
flagged it as the user's to finalize. No PyPI claim has been checked.
