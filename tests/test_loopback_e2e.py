"""End-to-end: the bridge runs a full offline game against the loopback.

No HeXO server, no token, no network beyond 127.0.0.1. This drives the real
stack: run_bridge resolves the adapters from the shipped example config, the
loopback emits gameStart, the real HtttxWebsocketSession dials the loopback's
local socket with the per-game Bearer token, the engine answers scripted
move_requests over the wire, the script ends, gameFinish arrives, and the
bridge shuts down cleanly on its own (the loopback's event supply exhausts).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from hexo_bridge.adapters.platforms.loopback import LoopbackPlatform
from hexo_bridge.bridge import run_bridge
from hexo_bridge.registry.config import load_config

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("env_token", [None, "hxo_stray_env_token"])
async def test_loopback_example_config_runs_to_completion(
    env_token: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipped loopback config runs a whole game and exits by itself.

    Parametrized over the env token to prove the loopback is token-independent:
    absent or stray, HEXO_BRIDGE_TOKEN must not matter to a non-HeXO run.
    """
    if env_token is None:
        monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    else:
        monkeypatch.setenv("HEXO_BRIDGE_TOKEN", env_token)
    cfg = load_config(EXAMPLES / "config.loopback.toml")
    # Returning inside the timeout IS the clean-shutdown assertion: run_bridge
    # only returns after the stream loop breaks on exhaustion and the finally
    # block has cancelled game tasks and closed the platform.
    await asyncio.wait_for(run_bridge(cfg), timeout=15)


async def test_loopback_exercises_real_session_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine's moves demonstrably crossed the real websocket session.

    The loopback records the raw move_response payloads it received over the
    wire; they can only exist if the bridge's htttx_websocket adapter dialed,
    authenticated, parsed the scripted move_requests, called the engine, and
    echoed each request_id back unchanged.
    """
    monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    cfg = load_config(EXAMPLES / "config.loopback.toml")
    platform = LoopbackPlatform(move_requests_per_game=2, game_timeout_seconds=10.0)

    before = {t for t in asyncio.all_tasks() if not t.done()}
    await asyncio.wait_for(run_bridge(cfg, platform=platform), timeout=15)
    leaked = [
        t
        for t in asyncio.all_tasks()
        if not t.done() and t not in before and t is not asyncio.current_task()
    ]

    responses = platform.received_move_responses["loopback-1"]
    assert len(responses) == 2, f"engine should have answered 2 move_requests: {responses}"
    assert [r["request_id"] for r in responses] == [1, 2], "request_id must echo unchanged"
    for r in responses:
        assert r["type"] == "move_response"
        assert len(r["move"]["pieces"]) == 2, "a move is exactly two placements"

    assert platform.auth_failures == [], "the per-game Bearer token must have been presented"
    assert platform.events.exhausted is True
    assert platform.closed is True
    assert leaked == [], f"tasks leaked past shutdown: {leaked}"
