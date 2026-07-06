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


async def test_bridge_plays_positional_only_server_to_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real openness test: a conformant positional-only server (no
    request_id on any move_request) must be playable to completion. The bridge
    correlates positionally (one request outstanding); it does not require the
    server to assign ids. This is exactly as open as the htttx spec."""
    monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    cfg = load_config(EXAMPLES / "config.loopback.toml")
    platform = LoopbackPlatform(
        move_requests_per_game=2,
        game_timeout_seconds=10.0,
        send_request_id=False,
    )
    await asyncio.wait_for(run_bridge(cfg, platform=platform), timeout=15)

    responses = platform.received_move_responses["loopback-1"]
    assert len(responses) == 2, f"positional server should have received 2 answers: {responses}"
    for r in responses:
        assert r["type"] == "move_response"
        assert "request_id" not in r, "no id was assigned; none must be echoed"
        assert len(r["move"]["pieces"]) == 2
    assert platform.events.exhausted is True


async def test_bridge_consumes_non_origin_setup_board(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bridge builds the opening from the setup packet the server delivered,
    not from a baked-in [0,0] origin. A server that delivers a different
    starting position must be playable, and the engine's move must be a legal
    two-stone shape (the in-process picker finds empty cells near the delivered
    board, not near a baked-in origin)."""
    monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    cfg = load_config(EXAMPLES / "config.loopback.toml")
    platform = LoopbackPlatform(
        move_requests_per_game=2,
        game_timeout_seconds=10.0,
        setup_cells=[(4, -2, "x"), (3, 1, "o")],
    )
    await asyncio.wait_for(run_bridge(cfg, platform=platform), timeout=15)

    responses = platform.received_move_responses["loopback-1"]
    assert len(responses) == 2, f"non-origin setup should have produced 2 answers: {responses}"
    for r in responses:
        pieces = r["move"]["pieces"]
        assert len(pieces) == 2
        # The engine must not have landed on either delivered cell: those are
        # occupied by the setup. This proves the board was built from the
        # delivered setup, not from a baked-in origin that would leave (4,-2)
        # and (3,1) empty.
        placed = {(p["q"], p["r"]) for p in pieces}
        assert (4, -2) not in placed, "engine played on a setup-occupied cell"
        assert (3, 1) not in placed, "engine played on a setup-occupied cell"
    # The OPENING move (first response) is played on a board that is exactly
    # the delivered setup (no moves yet). The in-process engine anchors on the
    # centroid of occupied cells, so its first piece must land near the setup
    # centroid, not near a baked-in (0,0) origin. An empty-board fallback would
    # anchor at (0,0) and pick (0,0); the setup centroid is (3, ~0), so a
    # first piece near there locks in that the board was built from the setup.
    first_pieces = responses[0]["move"]["pieces"]
    first = first_pieces[0]
    assert abs(first["q"] - 3) + abs(first["r"]) <= 2, (
        f"engine anchored near a baked-in origin, not the setup centroid: {first}"
    )
