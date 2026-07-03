"""Tests for bridge fixes from the red-team: per-game isolation, orphan cleanup,
no double-count on move tracking, and EngineTranslationError at the port level.

These lock in the red-team fixes so regressions are caught.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from hexo_bridge.core.move import Coord, Move, Side
from hexo_bridge.ports.engine import EngineTranslationError
from hexo_bridge.ports.engine_session import (
    MoveRequestPacket,
)


def test_engine_translation_error_lives_on_port():
    """EngineTranslationError must be importable from the port, not just the adapter.

    This is the M3 fix: the bridge catches the port-level type so a non-htttx
    engine adapter's translation errors are recognized.
    """
    from hexo_bridge.ports.engine import EngineTranslationError as PortError

    assert PortError is EngineTranslationError
    assert issubclass(EngineTranslationError, Exception)


def test_bridge_imports_engine_translation_error_from_port():
    """The bridge module must import EngineTranslationError from ports.engine."""
    import inspect

    import hexo_bridge.bridge as bridge_mod

    src = inspect.getsource(bridge_mod)
    found = False
    for line in src.splitlines():
        stripped = line.strip()
        if "EngineTranslationError" in stripped and stripped.startswith("from "):
            assert "ports.engine" in stripped, f"bridge imports from wrong place: {stripped}"
            assert "adapters" not in stripped, f"bridge imports from adapter: {stripped}"
            found = True
    assert found, "bridge does not import EngineTranslationError"


async def test_previous_does_not_double_count():
    """The bridge must not append the bot's own move on send (H4 fix).

    The bot's move comes back in the next move_request's `previous` field.
    Appending it on send would double-count it on the next request.
    """
    from hexo_bridge.bridge import GameContext, _handle_move_request

    @dataclass
    class FakeSession:
        sent: list = field(default_factory=list)

        async def send_move_response(self, move, request_id=None):
            self.sent.append((move, request_id))

    @dataclass
    class FakeEngine:
        async def get_move(self, state):
            return Move(Side.O, (Coord(5, 0), Coord(6, 0)))

    session = FakeSession()
    engine = FakeEngine()
    ctx = GameContext(
        game_id="g1",
        side=Side.O,
        session=session,
        engine=engine,
    )

    packet = MoveRequestPacket(
        side=Side.O,
        previous=[(Side.X, ((1, 0), (2, 0)))],
        time_limit_seconds=None,
        request_id=1,
    )
    await _handle_move_request(ctx, packet, engine_timeout=10.0)

    assert len(ctx.cumulative_moves) == 1, "previous should add 1 move"
    assert len(session.sent) == 1, "one move_response should be sent"

    packet2 = MoveRequestPacket(
        side=Side.O,
        previous=[
            (Side.O, ((5, 0), (6, 0))),
            (Side.X, ((7, 0), (8, 0))),
        ],
        time_limit_seconds=None,
        request_id=2,
    )
    await _handle_move_request(ctx, packet2, engine_timeout=10.0)

    assert len(ctx.cumulative_moves) == 3, (
        "after second request: 1 (first previous) + 2 (second previous) = 3"
    )
