"""Every shipped example config must load and resolve its adapters.

This is the "configs are not decorative" guard: a config that references an
adapter name that does not resolve (a typo, a missing entry point, a wrong
dotted path) is caught here, not at first run on a player's machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hexo_bridge.registry.config import load_config
from hexo_bridge.registry.resolver import (
    ENGINE_GROUP,
    ENGINE_SESSION_GROUP,
    PLATFORM_GROUP,
    resolve_adapter,
)

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

EXAMPLE_CONFIGS = sorted(p.name for p in EXAMPLES.glob("config.*.toml"))


@pytest.mark.parametrize("name", EXAMPLE_CONFIGS)
def test_example_config_loads(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    cfg = load_config(EXAMPLES / name)
    assert cfg.platform.name
    assert cfg.engine.name
    assert cfg.engine_session.name


@pytest.mark.parametrize("name", EXAMPLE_CONFIGS)
def test_example_config_adapters_resolve(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    cfg = load_config(EXAMPLES / name)
    assert resolve_adapter(cfg.platform.name, PLATFORM_GROUP)
    assert resolve_adapter(cfg.engine.name, ENGINE_GROUP)
    assert resolve_adapter(cfg.engine_session.name, ENGINE_SESSION_GROUP)


def test_at_least_one_example_config_ships() -> None:
    # Guard against the examples dir going empty by accident.
    assert len(EXAMPLE_CONFIGS) >= 4, f"expected several example configs, got {EXAMPLE_CONFIGS}"


def test_custom_engine_resolves_both_ways() -> None:
    """The worked custom engine must resolve by entry point and by dotted path,
    and both must resolve to the same class."""
    by_entry = resolve_adapter("my_custom_engine", ENGINE_GROUP)
    by_dotted = resolve_adapter(
        "hexo_bridge_examples.custom_engine:FirstLegalMoveEngine", ENGINE_GROUP
    )
    assert by_entry is by_dotted


def test_custom_engine_runs() -> None:
    """The custom engine is not just resolvable, it returns a Move."""
    import asyncio

    cls = resolve_adapter("my_custom_engine", ENGINE_GROUP)
    engine = cls(side="o")
    from hexo_bridge.core.board import GameState
    from hexo_bridge.core.move import Move, Side

    state = GameState(side=Side.O)
    move = asyncio.run(engine.get_move(state))
    assert isinstance(move, Move)
    assert move.side is Side.O
    assert len(move.pieces) == 2
