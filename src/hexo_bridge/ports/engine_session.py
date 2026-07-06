"""Engine session port: the per-game gameplay channel.

This is the channel over which moves are exchanged for one game. Per the HeXO
spec, `gameStart` carries an `engine` dial bootstrap (`socketUrl` + a short-lived
per-game `token`); the adapter dials that URL and plays the game over it. The
HeXO spec stops at the session boundary; the move exchange runs over the htttx
engine protocol on this session, not over the HeXO HTTP API.

Role (grounded in the htttx basic_websocket spec,
`definitions/basic_websocket/bws-v1-alpha.yaml`): the adapter plays the htttx
**bot** role. The htttx spec fixes the role by packet direction: a
`MoveRequestPacket` is "sent from the client, requesting the bot to evaluate";
a `MoveResponsePacket` is "sent by the bot in response to a move request". The
HeXO server is the referee and must request moves, so it plays the htttx
**client** role. The ws-layer dial direction is inverted from the canonical
htttx deployment (the adapter dials `socketUrl` rather than hosting `/game`),
but the spec defines roles by packet direction, not dial direction, so the
adapter receives `move_request` and sends `move_response`. See OPEN-QUESTIONS
item 2 for the full reasoning.

Retry safety lives in htttx answer-matching
(`MoveRequestPacket.request_id`): when the bot declares the
`basic_websocket.v1-alpha.request_id` capability and the server assigns a
per-request id, the bot echoes it unchanged on the answering `move_response`,
and the client discards any response whose id is not the outstanding one. The
outstanding request is invalidated by the next move request, an `interrupt`, or
a game setup packet; a late answer is matched as out-of-id and discarded. When
the server does not assign ids (a conformant positional-only server, or a bot
that does not declare the capability), the adapter correlates positionally:
at most one move request is outstanding at a time, so any answer while a
request is outstanding is the answer. The session adapter tracks the
outstanding request and drops a stale or mismatched answer locally rather than
sending it, so a resent or reordered response cannot double-apply. See
`HtttxWebsocketSession`.

The board the bot plays on arrives in the `setup` packet's `board.cells` and
is forwarded to the bridge, which replays the cumulative moves on top of it.
The bridge does not bake in an origin; it plays whatever the server delivers.

The bridge loop drives a session: `recv()` yields packets, and on a
`move_request` the bridge calls the `EnginePort` to compute a move and sends it
back via `send_move_response`.

This port imports only core domain types. The concrete `HtttxWebsocketSession`
adapter under `hexo_bridge.adapters.engine_sessions` implements it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from hexo_bridge.core.move import Move, Side


@dataclass(frozen=True)
class SetupPacket:
    """Initial board setup. The server sends this before any move request.

    `board_cells` is the initial board as reported by the server, as a list of
    `(q, r, side)` tuples. The bridge consumes whatever is delivered and
    replays the cumulative moves on top of it; it does not require any
    particular seed (not the single origin cross, not any other). Under
    `free_setup` a conformant server may deliver a different starting position,
    and the bridge plays it as delivered. The adapter does not validate this;
    the server is the referee.
    """

    board_cells: list[tuple[int, int, str]]


@dataclass(frozen=True)
class MoveRequestPacket:
    """The server asks the adapter to make a move.

    `side` is the engine side to move (x or o). The server states which side to
    play; the bridge does not derive it from ply parity or an origin convention.
    `previous` is the ordered list of moves made since the last request (the
    opponent's moves, or empty if this is the first request). `time_limit_seconds`
    and `request_id` are optional. When `request_id` is None, the session
    correlates positionally (one request outstanding).
    """

    side: Side
    previous: list[tuple[Side, tuple[tuple[int, int], tuple[int, int]]]]
    time_limit_seconds: float | None = None
    request_id: int | None = None


@dataclass(frozen=True)
class HeartbeatPacket:
    """Keepalive. `waiting` indicates whether the server is waiting for a move."""

    waiting: bool


@dataclass(frozen=True)
class SessionClosed:
    """The session ended (server closed the socket, game finished)."""

    reason: str | None = None


SessionPacket = SetupPacket | MoveRequestPacket | HeartbeatPacket | SessionClosed


@runtime_checkable
class EngineSessionPort(Protocol):
    """The per-game gameplay channel over the htttx engine session.

    A session is opened with `connect`, driven by `recv` / `send_move_response`,
    and closed with `close` (or by leaving the async context manager).
    """

    async def __aenter__(self) -> EngineSessionPort: ...

    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    async def connect(self, socket_url: str, token: str) -> None:
        """Dial the engine session websocket and present the per-game token."""
        ...

    async def recv(self) -> SessionPacket:
        """Receive the next packet, or `SessionClosed` when the socket ends."""
        ...

    async def send_move_response(self, move: Move, request_id: int | None = None) -> None:
        """Respond to a move_request with the adapter's move."""
        ...

    async def close(self) -> None:
        """Close the session."""
        ...
