"""Leak test: core domain imports no HTTP, no htttx, no HeXO.

This is the architectural invariant. If a HeXO or htttx type appears in core,
the boundary is wrong.
"""

from __future__ import annotations

import inspect

import hexo_bridge.core
import hexo_bridge.core.board
import hexo_bridge.core.move
import hexo_bridge.ports.engine
import hexo_bridge.ports.engine_session


def _module_source(mod) -> str:
    try:
        return inspect.getsource(mod)
    except (TypeError, OSError):
        return ""


def test_core_imports_no_http():
    for mod in [hexo_bridge.core, hexo_bridge.core.board, hexo_bridge.core.move]:
        src = _module_source(mod)
        assert "import httpx" not in src, f"{mod.__name__} imports httpx"
        assert "import requests" not in src, f"{mod.__name__} imports requests"


def test_core_imports_no_htttx_or_hexo_adapters():
    for mod in [hexo_bridge.core, hexo_bridge.core.board, hexo_bridge.core.move]:
        src = _module_source(mod)
        # Check import statements, not docstring mentions.
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if stripped.startswith(("import ", "from ")):
                assert "hexo_bridge.adapters" not in stripped, (
                    f"{mod.__name__} imports an adapter: {stripped}"
                )
                assert "hexo_bridge.adapters.platforms.hexo_models" not in stripped, (
                    f"{mod.__name__} imports spec models: {stripped}"
                )
                assert "htttx" not in stripped.lower(), f"{mod.__name__} imports htttx: {stripped}"
                assert "httpx" not in stripped, f"{mod.__name__} imports httpx: {stripped}"


def test_engine_port_imports_no_http_or_adapters():
    src = _module_source(hexo_bridge.ports.engine)
    assert "httpx" not in src
    assert "hexo_bridge.adapters" not in src


def test_engine_session_port_imports_no_http_or_adapters():
    src = _module_source(hexo_bridge.ports.engine_session)
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped.startswith(("import ", "from ")):
            assert "httpx" not in stripped, f"engine_session port imports httpx: {stripped}"
            assert "hexo_bridge.adapters" not in stripped, (
                f"engine_session port imports an adapter: {stripped}"
            )
            assert "htttx" not in stripped.lower(), f"engine_session port imports htttx: {stripped}"


def test_bridge_imports_engine_translation_error_from_port_not_adapter():
    """The bridge must catch EngineTranslationError from the port module, not the
    concrete htttx adapter. Otherwise a non-htttx engine adapter's translation
    errors fall through to the generic except and lose the 'do not score as
    engine loss' distinction.
    """
    import hexo_bridge.bridge as bridge_mod

    src = _module_source(bridge_mod)
    for line in src.splitlines():
        stripped = line.strip()
        if "EngineTranslationError" in stripped and stripped.startswith("from "):
            assert "ports.engine" in stripped, (
                f"bridge imports EngineTranslationError from adapter: {stripped}"
            )
            assert "adapters" not in stripped, (
                f"bridge imports EngineTranslationError from adapter: {stripped}"
            )
