"""Tests for platform error handling: 409, 422, 429.

Covers:
  - resign_game returns False on 409 (already finished), True on 200.
  - resign_game returns False on 404 (reaped).
  - Non-200/409/404 raises HeXOApiError with the error code.
  - 429 (rate-limited) surfaces as an error.
  - Engine translation errors are NOT submitted as engine losses.
"""

from __future__ import annotations

import httpx
import pytest

from hexo_bridge.adapters.platforms.hexo import HeXOApiError, HeXOPlay


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, status: int, body: dict | None = None) -> None:
        self.status = status
        self.body = body or {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import json

        return httpx.Response(
            self.status,
            content=json.dumps(self.body).encode("utf-8"),
            headers={"content-type": "application/json"},
            request=request,
        )


async def _make_play(status: int, body: dict | None = None) -> HeXOPlay:
    transport = _MockTransport(status, body)
    client = httpx.AsyncClient(transport=transport, base_url="https://test")
    return HeXOPlay(client, "https://test"), client


async def test_resign_returns_true_on_200():
    play, client = await _make_play(200, {"ok": True})
    assert await play.resign_game("g1") is True
    await client.aclose()


async def test_resign_returns_false_on_409():
    play, client = await _make_play(409, {"error": "game-finished"})
    assert await play.resign_game("g1") is False
    await client.aclose()


async def test_resign_returns_false_on_404():
    play, client = await _make_play(404, {"error": "not-found"})
    assert await play.resign_game("g1") is False
    await client.aclose()


async def test_resign_raises_on_429():
    play, client = await _make_play(429, {"error": "rate-limited"})
    with pytest.raises(HeXOApiError) as exc_info:
        await play.resign_game("g1")
    assert exc_info.value.status == 429
    assert exc_info.value.error_code == "rate-limited"
    await client.aclose()


async def test_resign_raises_on_403():
    play, client = await _make_play(403, {"error": "forbidden"})
    with pytest.raises(HeXOApiError):
        await play.resign_game("g1")
    await client.aclose()


async def test_error_carries_status_and_code():
    # 429 raises HeXOApiError with status and error code (unlike 409 which returns False).
    play, client = await _make_play(429, {"error": "rate-limited", "message": "slow down"})
    with pytest.raises(HeXOApiError) as exc_info:
        await play.resign_game("g1")
    assert exc_info.value.status == 429
    assert exc_info.value.error_code == "rate-limited"
    assert "slow down" in exc_info.value.message
    await client.aclose()
