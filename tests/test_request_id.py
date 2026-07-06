"""Tests for htttx request_id answer-matching in the websocket session.

Covers the retry-safety property that used to live in CAS `ply` and now lives in
htttx `request_id` answer-matching (see OPEN-QUESTIONS item 1, obviated):

  - The server-assigned `request_id` is echoed unchanged on `move_response`.
  - An `interrupt` for the outstanding request drops the pending answer.
  - An interrupt for a non-outstanding request_id is ignored.
  - A mismatched `request_id` (reordered answer) is dropped, not sent.
  - Under `require_request_id`, a move_request without an id is dropped.
  - After a normal send, the outstanding id is cleared.

These tests drive the session against an in-memory fake websocket so no real
socket is needed. They exercise the same `_parse_packet` / `send_move_response`
/ `_handle_interrupt` code paths the live socket uses.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from hexo_bridge.adapters.engine_sessions.htttx_websocket import HtttxWebsocketSession
from hexo_bridge.core.move import Coord, Move, Side
from hexo_bridge.ports.engine_session import MoveRequestPacket, SessionClosed


class _FakeRecv:
    """A fake websocket exposing only `recv` and `send` with a scripted inbox.

    `inbox` is seeded with raw payloads (str). More can be appended at runtime
    (`push`); the reader wakes when a new payload arrives. Sent payloads are
    collected on `sent`.
    """

    def __init__(self, inbox: list[str]) -> None:
        self._q: asyncio.Queue[str] = asyncio.Queue()
        for item in inbox:
            self._q.put_nowait(item)
        self.sent: list[str] = []

    def push(self, payload: str) -> None:
        self._q.put_nowait(payload)

    async def recv(self) -> str:
        return await self._q.get()

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        pass


def _move_request(rid: int | None, previous: list | None = None, side: str = "o") -> str:
    return json.dumps(
        {
            "type": "move_request",
            "side": side,
            "previous": previous or [],
            **({"request_id": rid} if rid is not None else {}),
        }
    )


def _interrupt(rid: int | None = None) -> str:
    payload: dict[str, Any] = {"type": "interrupt"}
    if rid is not None:
        payload["request_id"] = rid
    return json.dumps(payload)


def _setup() -> str:
    return json.dumps(
        {"type": "setup", "board": {"to_move": "o", "cells": [{"q": 0, "r": 0, "p": "x"}]}}
    )


async def _start_session(inbox: list[str], **opts) -> tuple[HtttxWebsocketSession, _FakeRecv]:
    fake = _FakeRecv(inbox)
    session = HtttxWebsocketSession(**opts)
    session._ws = fake
    session._connected = True
    session._queue = asyncio.Queue()
    session._reader_task = asyncio.create_task(session._reader_loop())
    return session, fake


async def _recv_move_request(session: HtttxWebsocketSession) -> MoveRequestPacket:
    """Drain non-move_request packets until a move_request arrives."""
    while True:
        pkt = await session.recv()
        if isinstance(pkt, MoveRequestPacket):
            return pkt
        if isinstance(pkt, SessionClosed):
            raise AssertionError(f"session closed before move_request: {pkt.reason}")


def _move() -> Move:
    return Move(Side.O, (Coord(1, 0), Coord(-1, 1)))


async def test_request_id_echoed_unchanged():
    session, fake = await _start_session([_setup(), _move_request(7)])
    pkt = await _recv_move_request(session)
    assert pkt.request_id == 7
    await session.send_move_response(_move(), pkt.request_id)
    assert len(fake.sent) == 1
    resp = json.loads(fake.sent[0])
    assert resp["type"] == "move_response"
    assert resp["request_id"] == 7
    await session.close()


async def test_no_request_id_when_not_required_sends_without_id():
    session, fake = await _start_session([_move_request(None)])
    pkt = await session.recv()
    assert pkt.request_id is None
    await session.send_move_response(_move(), None)
    assert len(fake.sent) == 1
    resp = json.loads(fake.sent[0])
    assert "request_id" not in resp  # exclude_none drops a None id
    await session.close()


async def test_interrupt_drops_pending_answer():
    # move_request arrives, then an interrupt for the same id invalidates it.
    session, fake = await _start_session([_move_request(5), _interrupt(5)])
    pkt = await session.recv()  # consumes the move_request
    # Give the reader a tick to process the interrupt behind the move_request.
    await asyncio.sleep(0.01)
    assert session._invalidated is True
    await session.send_move_response(_move(), pkt.request_id)
    assert fake.sent == [], "answer for an interrupted request must be dropped"
    await session.close()


async def test_interrupt_for_non_outstanding_id_is_ignored():
    session, fake = await _start_session([_move_request(5), _interrupt(99)])
    pkt = await session.recv()
    await asyncio.sleep(0.01)
    assert session._invalidated is False
    await session.send_move_response(_move(), pkt.request_id)
    assert len(fake.sent) == 1
    await session.close()


async def test_interrupt_without_id_invalidates_outstanding():
    session, fake = await _start_session([_move_request(5), _interrupt(None)])
    pkt = await session.recv()
    await asyncio.sleep(0.01)
    assert session._invalidated is True
    await session.send_move_response(_move(), pkt.request_id)
    assert fake.sent == []
    await session.close()


async def test_mismatched_request_id_dropped():
    session, fake = await _start_session([_move_request(5)])
    await _recv_move_request(session)
    # Engine returns, but the bridge somehow passes a stale id (e.g. reordered).
    await session.send_move_response(_move(), request_id=4)
    assert fake.sent == [], "mismatched request_id must be dropped"
    await session.close()


async def test_outstanding_cleared_after_send():
    session, _fake = await _start_session([_move_request(5)])
    pkt = await _recv_move_request(session)
    await session.send_move_response(_move(), pkt.request_id)
    assert session._outstanding_request_id is None
    assert session._invalidated is False
    await session.close()


async def test_require_request_id_drops_request_without_id():
    session, fake = await _start_session([_move_request(None)], require_request_id=True)
    pkt = await session.recv()
    assert pkt.request_id is None
    await asyncio.sleep(0.01)
    assert session._invalidated is True
    await session.send_move_response(_move(), None)
    assert fake.sent == []
    await session.close()


async def test_require_request_id_accepts_request_with_id():
    session, fake = await _start_session([_move_request(3)], require_request_id=True)
    pkt = await session.recv()
    assert pkt.request_id == 3
    await session.send_move_response(_move(), 3)
    assert len(fake.sent) == 1
    await session.close()


async def test_second_move_request_after_send_sets_new_outstanding():
    # Per the htttx spec, the server sends the next move_request only after the
    # current is answered. We model that by appending the second request after
    # the first response is sent.
    session, fake = await _start_session([_move_request(5)])
    pkt1 = await _recv_move_request(session)
    await session.send_move_response(_move(), pkt1.request_id)
    fake.push(_move_request(6))
    pkt2 = await _recv_move_request(session)
    assert pkt2.request_id == 6
    assert session._outstanding_request_id == 6
    await session.send_move_response(_move(), 6)
    assert len(fake.sent) == 2
    await session.close()


async def test_send_when_not_connected_is_noop():
    session = HtttxWebsocketSession()
    # No connect() called; _ws is None.
    await session.send_move_response(_move(), 1)  # must not raise


async def test_recv_returns_session_closed_on_bad_json():
    session, _fake = await _start_session(["not json"])
    pkt = await session.recv()
    assert isinstance(pkt, SessionClosed)
    await session.close()


async def test_setup_packet_invalidates_outstanding_request():
    """Per the htttx spec, a game setup packet invalidates the outstanding
    request just like an interrupt or the next move_request. The adapter must
    drop the pending answer rather than send it."""
    session, fake = await _start_session([_move_request(5), _setup()])
    pkt = await _recv_move_request(session)
    # Let the reader process the setup packet behind the move_request.
    await asyncio.sleep(0.01)
    assert session._invalidated is True
    await session.send_move_response(_move(), pkt.request_id)
    assert fake.sent == [], "answer after a setup packet must be dropped"
    await session.close()


# --- Positional-only server (no request_id anywhere) -----------------------
# A conformant server that never assigns request_id must still be playable to
# completion: the adapter correlates positionally (one request outstanding).


def _setup_non_origin() -> str:
    """A setup packet delivering a non-[0,0] board, to prove the bridge consumes
    whatever the server delivers rather than falling back to a baked-in origin."""
    return json.dumps(
        {
            "type": "setup",
            "board": {
                "to_move": "o",
                "cells": [{"q": 3, "r": -2, "p": "x"}],
            },
        }
    )


async def test_positional_only_server_plays_to_completion():
    """The real openness test: a server that sends no request_id on any
    move_request, and a setup packet that does NOT place the origin cross, must
    still be playable. The adapter correlates positionally (one outstanding) and
    the bridge builds the board from the delivered setup, not a baked-in origin.
    """
    inbox = [
        _setup_non_origin(),
        _move_request(None),  # first request, no id, previous empty
        # The second request arrives only after the first is answered; pushed
        # below.
    ]
    session, fake = await _start_session(inbox)
    pkt = await _recv_move_request(session)
    assert pkt.request_id is None
    assert session._outstanding is True
    # Answer positionally: no id to echo.
    await session.send_move_response(_move(), None)
    assert len(fake.sent) == 1, "positional answer must be sent"
    resp = json.loads(fake.sent[0])
    assert "request_id" not in resp, "no id was assigned; none must be echoed"

    # Second request: still no id. The adapter must accept it and answer again.
    fake.push(_move_request(None))
    pkt2 = await _recv_move_request(session)
    assert pkt2.request_id is None
    await session.send_move_response(_move(), None)
    assert len(fake.sent) == 2
    await session.close()


async def test_positional_interrupt_without_id_drops_outstanding():
    """In positional mode (no ids), an interrupt carrying no request_id (the
    spec only attaches one when request_id is in use) invalidates the single
    outstanding request."""
    session, fake = await _start_session([_move_request(None), _interrupt(None)])
    pkt = await _recv_move_request(session)
    assert pkt.request_id is None
    await asyncio.sleep(0.01)
    assert session._invalidated is True, "positional interrupt must invalidate"
    await session.send_move_response(_move(), None)
    assert fake.sent == [], "answer after a positional interrupt must be dropped"
    await session.close()


async def test_send_with_no_request_outstanding_is_dropped():
    """An answer with no request outstanding (e.g. a stray late answer) is
    dropped, not sent."""
    session, fake = await _start_session([])
    await session.send_move_response(_move(), None)
    assert fake.sent == [], "answer with no outstanding request must be dropped"
    await session.close()


async def test_setup_with_non_origin_board_is_forwarded_unchanged():
    """The adapter forwards whatever board the setup packet delivered; it does
    not warn on or rewrite a non-[0,0] board."""
    session, _fake = await _start_session([_setup_non_origin()])
    pkt = await session.recv()
    from hexo_bridge.ports.engine_session import SetupPacket

    assert isinstance(pkt, SetupPacket)
    assert pkt.board_cells == [(3, -2, "x")]
    await session.close()
