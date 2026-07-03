"""Tests for the bridge adapter builders: build_platform and build_engine.

Covers the red-team findings:
  - `stream_read_timeout_seconds` (a `[bridge]`-level concern) is threaded into
    the platform adapter as `stream_read_timeout`, not silently ignored.
  - A missing token raises a clear ValueError, not an opaque TypeError.
  - Each shipped example config constructs the full platform/engine/session
    stack (with a stub token), not just resolves the classes. This catches
    dead config fields and type-coercion gaps that pure resolution misses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hexo_bridge.bridge import build_engine, build_platform
from hexo_bridge.registry.config import load_config

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
EXAMPLE_CONFIGS = sorted(p.name for p in EXAMPLES.glob("config.*.toml"))


async def test_build_platform_threads_stream_read_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HEXO_BRIDGE_TOKEN", "hxo_stub")
    cfg = load_config(EXAMPLES / "config.in-process.toml")
    platform = build_platform(cfg)
    assert platform.events._read_timeout == cfg.stream_read_timeout_seconds  # type: ignore[attr-defined]
    await platform.close()


async def test_build_platform_overrides_stream_read_timeout_from_options(
    monkeypatch: pytest.MonkeyPatch,
):
    """An explicit [platform.options] stream_read_timeout wins over [bridge]."""
    monkeypatch.setenv("HEXO_BRIDGE_TOKEN", "hxo_stub")
    cfg = load_config(EXAMPLES / "config.in-process.toml")
    cfg.platform.options["stream_read_timeout"] = 99.0
    platform = build_platform(cfg)
    assert platform.events._read_timeout == 99.0  # type: ignore[attr-defined]
    await platform.close()


def test_build_platform_raises_clear_error_when_no_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    cfg = load_config(EXAMPLES / "config.in-process.toml")
    cfg.platform.options.pop("token", None)
    with pytest.raises(ValueError, match="no HeXO token"):
        build_platform(cfg)


@pytest.mark.parametrize("name", EXAMPLE_CONFIGS)
async def test_example_config_constructs_full_stack(name: str, monkeypatch: pytest.MonkeyPatch):
    """Each shipped config must build the platform + engine + session class,
    with a stub token, end to end. Catches dead config fields and constructor
    signature mismatches that pure class resolution misses."""
    monkeypatch.setenv("HEXO_BRIDGE_TOKEN", "hxo_stub")
    cfg = load_config(EXAMPLES / name)
    platform = build_platform(cfg)
    engine = build_engine(cfg)
    from hexo_bridge.bridge import build_engine_session_factory

    session_cls = build_engine_session_factory(cfg)
    assert platform is not None
    assert engine is not None
    assert session_cls is not None
    await platform.close()
    if hasattr(engine, "close"):
        await engine.close()
