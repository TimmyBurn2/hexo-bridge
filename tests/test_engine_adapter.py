"""Tests for the htttx stateless engine adapter: translation error vs engine error.

Covers:
  - A valid engine response produces a core Move.
  - A malformed response (no move, wrong piece count) raises EngineTranslationError.
  - A non-200 response raises EngineTranslationError.
  - EngineTranslationError is NOT a Move, so it is never submitted as an engine
    loss. This is the translation-error-masquerading-as-illegal guard.
"""

from __future__ import annotations

import json

import httpx
import pytest

from hexo_bridge.adapters.engines.htttx_stateless import HtttxStatelessEngine
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Side
from hexo_bridge.ports.engine import EngineTranslationError


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, status: int, body: dict | str | None = None) -> None:
        self.status = status
        self.body = body

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if isinstance(self.body, str):
            content = self.body.encode("utf-8")
        else:
            content = json.dumps(self.body).encode("utf-8")
        return httpx.Response(
            self.status,
            content=content,
            headers={"content-type": "application/json"},
            request=request,
        )


async def _make_engine(status: int, body: dict | str | None = None):
    transport = _MockTransport(status, body)
    client = httpx.AsyncClient(transport=transport, base_url="https://engine")
    return HtttxStatelessEngine("https://engine", timeout=5.0, client=client), client


async def test_valid_response_produces_move():
    body = {
        "move": {
            "pieces": [{"q": 1, "r": 0}, {"q": -1, "r": 1}],
        }
    }
    engine, client = await _make_engine(200, body)
    state = GameState(side=Side.O)
    move = await engine.get_move(state)
    assert move.side is Side.O
    assert move.pieces[0] == Coord(1, 0)
    assert move.pieces[1] == Coord(-1, 1)
    await client.aclose()


async def test_malformed_response_raises_translation_error():
    body = {"move": {"pieces": [{"q": 1, "r": 0}]}}
    engine, client = await _make_engine(200, body)
    state = GameState(side=Side.O)
    with pytest.raises(EngineTranslationError):
        await engine.get_move(state)
    await client.aclose()


async def test_missing_move_raises_translation_error():
    body = {}
    engine, client = await _make_engine(200, body)
    state = GameState(side=Side.O)
    with pytest.raises(EngineTranslationError):
        await engine.get_move(state)
    await client.aclose()


async def test_non_200_raises_translation_error():
    engine, client = await _make_engine(500, "internal error")
    state = GameState(side=Side.O)
    with pytest.raises(EngineTranslationError):
        await engine.get_move(state)
    await client.aclose()


async def test_duplicate_pieces_raise_translation_error():
    body = {
        "move": {
            "pieces": [{"q": 1, "r": 0}, {"q": 1, "r": 0}],
        }
    }
    engine, client = await _make_engine(200, body)
    state = GameState(side=Side.O)
    with pytest.raises(EngineTranslationError):
        await engine.get_move(state)
    await client.aclose()


async def test_translation_error_is_not_move():
    """A translation error must not be catchable as a Move.

    This is the guard against a bridge bug being scored as an engine loss:
    EngineTranslationError is a distinct exception type, not a Move, so the
    per-game loop never submits it.
    """
    body = {"move": {"pieces": []}}
    engine, client = await _make_engine(200, body)
    state = GameState(side=Side.O)
    with pytest.raises(EngineTranslationError):
        await engine.get_move(state)
    await client.aclose()
