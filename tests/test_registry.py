"""Tests for the registry: entry-point resolution and dotted-path fallback.

Covers:
  - Built-in adapters resolve by entry-point name.
  - Dotted-path fallback resolves `module.path:ClassName`.
  - Dotted-path fallback resolves `module.path.ClassName`.
  - Unknown names raise AdapterResolutionError.
  - list_adapters returns the registered names.
"""

from __future__ import annotations

import pytest

from hexo_bridge.adapters.engine_sessions.htttx_websocket import HtttxWebsocketSession
from hexo_bridge.adapters.engines.in_process import InProcessFirstMoveEngine
from hexo_bridge.adapters.platforms.hexo import HeXOPlatform
from hexo_bridge.registry.resolver import (
    AdapterResolutionError,
    list_adapters,
    resolve_adapter,
)


def test_resolve_engine_by_entry_point():
    cls = resolve_adapter("in_process_first_move", "hexo_bridge.engines")
    assert cls is InProcessFirstMoveEngine


def test_resolve_stateless_engine_by_entry_point():
    from hexo_bridge.adapters.engines.htttx_stateless import HtttxStatelessEngine

    cls = resolve_adapter("htttx_stateless", "hexo_bridge.engines")
    assert cls is HtttxStatelessEngine


def test_resolve_engine_session_by_entry_point():
    cls = resolve_adapter("htttx_websocket", "hexo_bridge.engine_sessions")
    assert cls is HtttxWebsocketSession


def test_resolve_platform_by_entry_point():
    cls = resolve_adapter("hexo", "hexo_bridge.platforms")
    assert cls is HeXOPlatform


def test_resolve_dotted_path_with_colon():
    cls = resolve_adapter(
        "hexo_bridge.adapters.engines.in_process:InProcessFirstMoveEngine",
        "hexo_bridge.engines",
    )
    assert cls is InProcessFirstMoveEngine


def test_resolve_dotted_path_with_dot():
    cls = resolve_adapter(
        "hexo_bridge.adapters.engines.in_process.InProcessFirstMoveEngine",
        "hexo_bridge.engines",
    )
    assert cls is InProcessFirstMoveEngine


def test_unknown_name_raises():
    with pytest.raises(AdapterResolutionError):
        resolve_adapter("nonexistent_engine", "hexo_bridge.engines")


def test_bad_module_raises():
    with pytest.raises(AdapterResolutionError):
        resolve_adapter("nonexistent_pkg.mod:Thing", "hexo_bridge.engines")


def test_missing_attribute_raises():
    with pytest.raises(AdapterResolutionError):
        resolve_adapter(
            "hexo_bridge.adapters.engines.in_process:NoSuchClass", "hexo_bridge.engines"
        )


def test_list_engines():
    names = list_adapters("hexo_bridge.engines")
    assert "in_process_first_move" in names
    assert "htttx_stateless" in names


def test_list_platforms():
    names = list_adapters("hexo_bridge.platforms")
    assert "hexo" in names


def test_list_engine_sessions():
    names = list_adapters("hexo_bridge.engine_sessions")
    assert "htttx_websocket" in names
