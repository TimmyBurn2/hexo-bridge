"""htttx basic_websocket engine session adapter.

Implements `EngineSessionPort` by dialing the `gameStart.engine.socketUrl`
websocket and speaking the htttx basic_websocket v1-alpha packet shapes over
it.

Role (grounded in `definitions/basic_websocket/bws-v1-alpha.yaml`): the adapter
plays the htttx **bot** role. A `MoveRequestPacket` is "sent from the client,
requesting the bot to evaluate"; a `MoveResponsePacket` is "sent by the bot in
response to a move request". The HeXO server (the referee) hands the adapter a
`socketUrl` to dial, so the adapter is the ws-layer client but the app-layer
bot: it receives `setup`, `move_request`, and `heartbeat` from the server and
sends `move_response` back. See OPEN-QUESTIONS item 2.

Retry safety via `request_id` (htttx spec, `MoveRequestPacket.request_id`): the
client assigns a strictly-increasing per-request id when the bot declares the
`basic_websocket.v1-alpha.request_id` capability; the bot echoes it unchanged on
the answering `move_response`; the client discards any response whose id is not
the outstanding one. An `interrupt` invalidates the outstanding request and
"a late answer is matched as out-of-id and discarded". When the server does not
assign ids (a conformant positional-only server, or a bot that does not declare
the capability), the adapter correlates positionally: at most one request is
outstanding at a time, so any answer while a request is outstanding is the
answer. The adapter tracks a separate `_outstanding` flag so it can distinguish
"no request outstanding" from "a positional request outstanding (no id)". An
`interrupt` in positional mode (no id on the interrupt) invalidates the single
outstanding request. This adapter goes one step further and drops a stale or
mismatched answer locally rather than sending it, so a resent or reordered
response cannot double-apply.

The board the bot plays on comes from the delivered `setup` packet, not from a
baked-in origin. The adapter passes `setup.board.cells` through to the bridge,
which replays the cumulative moves on top of it. The standard server delivers
one cross at the origin there; a conformant server may deliver a different
starting position under `free_setup`, and the adapter forwards it unchanged.

To react to an `interrupt` that arrives while the engine is computing, the
session runs a background reader task that drains the socket continuously.
Control packets (`interrupt`, `config`, `heartbeat`, `setup`) are handled
inline; `move_request` and `SessionClosed` are queued for the bridge's `recv()`
to consume. Per the spec at most one move request is outstanding at a time, so
the queue holds at most one move request.

The packet wire shapes come from the hand-written htttx models
(`hexo_bridge.adapters.engine_sessions.htttx_models`). Translation between wire
types and core domain types happens here, at the boundary, so neither core nor
the port interface imports htttx.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import websockets
from websockets.asyncio.client import connect

from hexo_bridge.adapters.engine_sessions.htttx_models import (
    GameSetupPacket,
    HeartbeatPacket,
    MoveOption,
    MoveRequestPacket,
    MoveResponsePacket,
)
from hexo_bridge.core.move import Move, Side
from hexo_bridge.ports.engine_session import (
    HeartbeatPacket as HeartbeatP,
)
from hexo_bridge.ports.engine_session import (
    MoveRequestPacket as MoveRequestP,
)
from hexo_bridge.ports.engine_session import (
    SessionClosed,
    SessionPacket,
    SetupPacket,
)

logger = logging.getLogger("hexo_bridge.engine_session")


class HtttxWebsocketSession:
    """EngineSessionPort adapter: htttx basic_websocket over the HeXO socketUrl.

    The session is an async context manager. Inside, `recv()` yields
    `SessionPacket` values until the socket closes (yields `SessionClosed`).
    `send_move_response()` sends a `move_response` packet back, echoing the
    outstanding `request_id` unchanged and dropping a stale or mismatched
    answer.

    Args:
        require_request_id: When True, the bot declares
            `basic_websocket.v1-alpha.request_id` in its capabilities, so every
            `move_request` from the server MUST carry a `request_id`. A request
            without one is a protocol violation: the adapter logs it and drops
            the answer rather than sending an unmatched response. When False
            (default), `request_id` is optional and answers are correlated
            positionally (one outstanding) when the server sends no id, and by
            id when it does.

            Capabilities are advertised out-of-band (the bot's
            `capabilities.json`), not over the websocket. Setting this flag
            asserts that you have published `basic_websocket.v1-alpha.request_id`
            in capabilities.json; if you set it without publishing, every
            move_request arrives without an id and every answer is dropped.
    """

    def __init__(self, *, require_request_id: bool = False) -> None:
        self._ws: Any = None
        self._connected = False
        self._require_request_id = require_request_id
        self._reader_task: asyncio.Task | None = None
        self._queue: asyncio.Queue | None = None
        # The request_id the server assigned to the currently outstanding
        # move_request, or None when no request is outstanding OR the server is
        # correlating positionally (no ids in use). `_outstanding` is the
        # authoritative "a request is currently outstanding" flag; the id is
        # secondary. Cleared after the answer is sent or dropped.
        self._outstanding_request_id: int | None = None
        self._outstanding: bool = False
        # Set by an `interrupt` (or by a request_id violation under
        # require_request_id) to invalidate the outstanding request. The next
        # send_move_response for that request is dropped, not sent.
        self._invalidated: bool = False

    async def __aenter__(self) -> HtttxWebsocketSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def connect(self, socket_url: str, token: str) -> None:
        headers = {"Authorization": f"Bearer {token}"}
        self._ws = await connect(socket_url, additional_headers=headers)
        self._connected = True
        self._queue = asyncio.Queue()
        self._reader_task = asyncio.create_task(self._reader_loop(), name="htttx-ws-reader")

    async def _reader_loop(self) -> None:
        """Continuously drain the socket and route packets.

        `interrupt`, `config`, `heartbeat`, and `setup` are handled inline
        (interrupt mutates answer-matching state). `move_request` and
        `SessionClosed` are queued for `recv()`. On any socket error the loop
        queues `SessionClosed` and exits.
        """
        assert self._queue is not None
        while True:
            try:
                raw = await self._ws.recv()
            except websockets.ConnectionClosed as exc:
                self._connected = False
                await self._queue.put(SessionClosed(reason=str(exc)))
                return
            except Exception as exc:  # reader must not die silently
                self._connected = False
                logger.warning("engine session: reader error: %s", exc)
                await self._queue.put(SessionClosed(reason=str(exc)))
                return

            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            raw = raw.strip()
            if not raw:
                # Blank-line keepalive: surface as a heartbeat so the bridge
                # loop can note it (and stay consistent with the old reader).
                await self._queue.put(HeartbeatP(waiting=False))
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await self._queue.put(SessionClosed(reason=f"bad json: {raw[:100]}"))
                return

            ptype = data.get("type")
            if ptype == "interrupt":
                self._handle_interrupt(data)
                # Interrupts are not surfaced to the bridge: they affect only
                # answer-matching state. Keep the loop alive.
                continue
            await self._queue.put(self._parse_packet(data))

    def _handle_interrupt(self, data: dict) -> None:
        """Apply an interrupt to the outstanding request.

        Per the spec: "After an interrupt the bot must not answer that request;
        a late answer is matched as out-of-id and discarded." If the interrupt
        carries a request_id and it does not match the outstanding one, ignore
        it (it targets a request that is not currently outstanding, or the
        session is positional and the interrupt is not for us). Otherwise
        mark the outstanding request invalidated so the pending send is dropped.

        Positional case: when the server is not assigning ids, an interrupt
        carries no request_id (the spec only attaches one when request_id is in
        use) and applies to the single outstanding request.
        """
        if not self._outstanding:
            return
        rid = data.get("request_id")
        if (
            rid is not None
            and self._outstanding_request_id is not None
            and rid != self._outstanding_request_id
        ):
            logger.debug(
                "engine session: interrupt for non-outstanding request_id=%s "
                "(outstanding=%s), ignoring",
                rid,
                self._outstanding_request_id,
            )
            return
        logger.info(
            "engine session: interrupt invalidated outstanding request_id=%s",
            self._outstanding_request_id,
        )
        self._invalidated = True

    def _parse_packet(self, data: dict) -> SessionPacket:
        ptype = data.get("type")
        if ptype == "setup":
            GameSetupPacket.model_validate(data)
            # A setup packet invalidates the outstanding request, like an
            # interrupt or the next move_request (htttx spec: the outstanding
            # request is invalidated by "the next move request, an interrupt, or
            # a game setup packet").
            if self._outstanding:
                logger.info(
                    "engine session: setup packet invalidated outstanding request_id=%s",
                    self._outstanding_request_id,
                )
                self._invalidated = True
            # Consume whatever board the server delivered. The bridge plays on
            # it as delivered; it does not require the single origin cross and
            # does not fall back to a baked-in origin.
            cells: list[tuple[int, int, str]] = []
            board = data.get("board")
            if board and "cells" in board:
                for cell in board["cells"]:
                    cells.append((cell["q"], cell["r"], cell["p"]))
            return SetupPacket(board_cells=cells)
        elif ptype == "move_request":
            validated = MoveRequestPacket.model_validate(data)
            side = Side(validated.side)
            previous: list[tuple[Side, tuple[tuple[int, int], tuple[int, int]]]] = []
            for mv in validated.previous or []:
                # The htttx Move model enforces exactly two pieces; a malformed
                # entry would have failed model_validate above. The opening is
                # delivered in the setup packet, not in previous, so every
                # previous entry is a completed two-stone turn.
                mv_side = Side(mv.side)
                p1 = (mv.pieces[0].q, mv.pieces[0].r)
                p2 = (mv.pieces[1].q, mv.pieces[1].r)
                previous.append((mv_side, (p1, p2)))
            rid = validated.request_id
            if self._require_request_id and rid is None:
                logger.warning(
                    "engine session: move_request without request_id but "
                    "require_request_id is set; dropping answer"
                )
                self._invalidated = True
                self._outstanding = False
                self._outstanding_request_id = None
            else:
                self._outstanding = True
                self._outstanding_request_id = rid
                self._invalidated = False
            return MoveRequestP(
                side=side,
                previous=previous,
                time_limit_seconds=validated.move_time_limit,
                request_id=rid,
            )
        elif ptype == "heartbeat":
            validated = HeartbeatPacket.model_validate(data)
            return HeartbeatP(waiting=validated.waiting)
        elif ptype == "config":
            # Configuration packets are accepted and ignored (the bridge does
            # not advertise config support). Keep the connection open.
            logger.debug("engine session: received config packet, ignoring")
            return HeartbeatP(waiting=False)
        else:
            logger.warning("engine session: unknown packet type: %s", ptype)
            return SessionClosed(reason=f"unknown packet type: {ptype}")

    async def recv(self) -> SessionPacket:
        """Receive the next packet queued by the background reader.

        Returns `SessionClosed` when the socket has ended. Never raises: the
        reader catches socket errors and queues `SessionClosed`.
        """
        if self._queue is None:
            return SessionClosed(reason="not connected")
        return await self._queue.get()

    async def send_move_response(self, move: Move, request_id: int | None = None) -> None:
        """Send `move_response` echoing `request_id` unchanged when present.

        Drops the answer (does not send) when:
          - no request is currently outstanding (the bridge is not answering
            anything), or
          - the outstanding request was invalidated by an `interrupt` or a
            `require_request_id` violation, or
          - both the supplied and outstanding ids are present and differ
            (mismatched / reordered answer).

        Positional correlation: when the server is not assigning ids, the
        outstanding request is tracked by the `_outstanding` flag alone, and
        any answer while a request is outstanding is sent (one-outstanding
        correlation, exactly as open as the htttx spec).

        After sending or dropping, the outstanding request is cleared.
        """
        if self._ws is None or not self._connected:
            self._clear_outstanding()
            return
        if not self._outstanding:
            logger.debug(
                "engine session: dropping move_response with no request outstanding"
            )
            self._clear_outstanding()
            return
        if self._invalidated:
            logger.info(
                "engine session: dropping move_response for invalidated request_id=%s",
                request_id,
            )
            self._clear_outstanding()
            return
        if (
            request_id is not None
            and self._outstanding_request_id is not None
            and request_id != self._outstanding_request_id
        ):
            logger.warning(
                "engine session: dropping mismatched move_response request_id=%s (outstanding=%s)",
                request_id,
                self._outstanding_request_id,
            )
            self._clear_outstanding()
            return
        c1, c2 = move.pieces[0], move.pieces[1]
        move_opt = MoveOption(
            pieces=[
                {"q": c1.q, "r": c1.r},
                {"q": c2.q, "r": c2.r},
            ]
        )
        resp = MoveResponsePacket(type="move_response", move=move_opt, request_id=request_id)
        payload = resp.model_dump(by_alias=True, exclude_none=True)
        try:
            await self._ws.send(json.dumps(payload))
        finally:
            self._clear_outstanding()

    def _clear_outstanding(self) -> None:
        self._outstanding = False
        self._outstanding_request_id = None
        self._invalidated = False

    async def close(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        if self._ws is not None and self._connected:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
        self._connected = False
        self._clear_outstanding()
