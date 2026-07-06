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

## 4. Setup packet board is consumed as delivered

**Status: resolved. The bridge plays on whatever the server delivers.**

The bridge no longer assumes the setup packet's board is the standard opening.
`Board.replay(setup_cells, moves)` seeds from the delivered `setup.board.cells`
and replays the cumulative moves on top of it. The standard server delivers one
cross at the origin there; a conformant server may deliver a different starting
position under `free_setup`, and the bridge plays it as delivered. No origin is
baked in. The bridge's `run_game` captures the `setup` packet into
`GameContext.setup_cells` and re-syncs on any re-setup. Documented in
`core/board.py` and `adapters/engine_sessions/htttx_websocket.py`; covered by
`test_bridge_consumes_non_origin_setup_board`.

## 6. Server follow-up: infhex requires request_id though the spec does not

**Status: server follow-up, not a bridge or spec change.**

The open htttx spec makes `request_id` optional (capability-gated); the open
HeXO spec, after the reframe in this pass, requires only the safety property
(echo when present, positional correlation when absent). The reference server
(`infhex-tic-tac-toe`, `spike/bot-api`) is currently stricter than the open
spec: it assigns `request_id` on every `move_request` and expects it echoed.
That is a server convention the bridge honours in practice (the adapter echoes
whatever id arrives), but it is not part of the open wire contract, so the
bridge also plays a positional-only conformant server to completion. Recorded
as a server follow-up: the server may relax to the open spec's positional mode
without any bridge change. Not fixed here (the server repo is not edited in
this pass).

## 5. Package name not finalized

**Status: open, user's to finalize.**

The package is `hexo_bridge` (distribution `hexo-bridge`). The dispatcher
flagged it as the user's to finalize. No PyPI claim has been checked.
