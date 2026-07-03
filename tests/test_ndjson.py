"""Tests for NDJSON line parsing, including skipping blank keepalive lines.

Covers:
  - Valid JSON lines are parsed into Event models.
  - Blank lines (keepalives) are skipped, not errors.
  - Malformed JSON lines are skipped, not fatal.
  - The Event union discriminates on `type`.
"""

from __future__ import annotations

import httpx
import pytest

from hexo_bridge.adapters.platforms.hexo import HeXOEvents

NDJSON_LINES = [
    "",
    '{"type":"challenge","challenge":{"id":"abc","challenger":{"id":"a","name":"A"},"destUser":{"id":"b","name":"B"},"variant":"httt6","rated":true,"timeControl":{"mode":"unlimited"},"status":"created"}}',
    "   ",
    "",
    '{"type":"gameStart","game":{"id":"g1","side":"p1","opponent":{"id":"b","name":"B"},"variant":"httt6","rated":true},"engine":{"socketUrl":"wss://x/engine/g1","token":"egs_t"}}',
    "not json at all",
    '{"type":"opponentGone","gameId":"g1","gone":true,"finishesInSeconds":30}',
    '{"type":"gameFinish","game":{"id":"g1","side":"p1","opponent":{"id":"b","name":"B"},"variant":"httt6","rated":true,"status":"finished","finishReason":"six-in-a-row","winner":"p1"}}',
    "",
]


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = "\n".join(self._lines)
        return httpx.Response(
            200,
            content=body.encode("utf-8"),
            headers={"content-type": "application/x-ndjson"},
            request=request,
        )


async def _collect_events(lines: list[str], read_timeout: float = 45.0) -> list:
    transport = _MockTransport(lines)
    client = httpx.AsyncClient(transport=transport, base_url="https://test")
    events = HeXOEvents(client, "https://test", read_timeout=read_timeout)
    result = []
    async for event in events.stream_events():
        result.append(event)
    await client.aclose()
    return result


async def test_blank_lines_skipped():
    events = await _collect_events(NDJSON_LINES)
    # 4 valid lines: challenge, gameStart, opponentGone, gameFinish
    assert len(events) == 4


async def test_challenge_event_parsed():
    events = await _collect_events(NDJSON_LINES)
    challenge = events[0]
    assert challenge.root.type == "challenge"
    assert challenge.root.challenge.id == "abc"


async def test_game_start_event_parsed():
    events = await _collect_events(NDJSON_LINES)
    gs = events[1]
    assert gs.root.type == "gameStart"
    assert gs.root.game.id == "g1"
    assert str(gs.root.engine.socketUrl) == "wss://x/engine/g1"
    assert gs.root.engine.token == "egs_t"


async def test_opponent_gone_event_parsed():
    events = await _collect_events(NDJSON_LINES)
    og = events[2]
    assert og.root.type == "opponentGone"
    assert og.root.gameId == "g1"
    assert og.root.finishesInSeconds == 30


async def test_game_finish_event_parsed():
    events = await _collect_events(NDJSON_LINES)
    gf = events[3]
    assert gf.root.type == "gameFinish"
    assert gf.root.game.finishReason == "six-in-a-row"
    assert str(gf.root.game.winner.root) == "p1"


async def test_malformed_json_skipped():
    lines = ["not json", '{"type":"unknown"}', ""]
    events = await _collect_events(lines)
    assert len(events) == 0


async def test_empty_stream():
    events = await _collect_events([])
    assert len(events) == 0


async def test_only_keepalives():
    events = await _collect_events(["", "", "   ", ""])
    assert len(events) == 0


async def test_stream_409_raises():
    class _ErrTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, content=b"{}", request=request)

    transport = _ErrTransport()
    client = httpx.AsyncClient(transport=transport, base_url="https://test")
    events = HeXOEvents(client, "https://test", read_timeout=45.0)
    from hexo_bridge.adapters.platforms.hexo import HeXOApiError

    with pytest.raises(HeXOApiError):
        async for _ in events.stream_events():
            pass
    await client.aclose()
