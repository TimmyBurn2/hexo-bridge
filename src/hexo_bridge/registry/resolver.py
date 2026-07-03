"""Registry: resolve adapters by name via entry points, with a dotted-path fallback.

Entry point groups:
  - hexo_bridge.engines          -> EnginePort implementations
  - hexo_bridge.engine_sessions  -> EngineSessionPort implementations
  - hexo_bridge.platforms        -> PlatformPort implementations

A dotted path like `my_pkg.my_mod:MyAdapter` is the local-dev fallback,
resolved without installing an entry point. No hardcoded endpoints, tokens, or
class names anywhere in the bridge.
"""

from __future__ import annotations

import importlib
from importlib.metadata import entry_points
from typing import Any, TypeVar

T = TypeVar("T")

ENGINE_GROUP = "hexo_bridge.engines"
ENGINE_SESSION_GROUP = "hexo_bridge.engine_sessions"
PLATFORM_GROUP = "hexo_bridge.platforms"


class AdapterResolutionError(Exception):
    """Raised when an adapter name cannot be resolved to a class."""


def resolve_adapter(name: str, group: str) -> type[Any]:
    """Resolve an adapter by name.

    First tries entry points in `group`. If not found, tries to interpret `name`
    as a dotted path `module.path:ClassName` (or `module.path.ClassName`).
    """
    for ep in entry_points(group=group):
        if ep.name == name:
            return ep.load()
    if ":" in name:
        module_path, _, attr = name.partition(":")
    elif "." in name:
        module_path, _, attr = name.rpartition(".")
    else:
        raise AdapterResolutionError(
            f"no adapter '{name}' in group '{group}', and it is not a dotted path"
        )
    try:
        mod = importlib.import_module(module_path)
    except ImportError as exc:
        raise AdapterResolutionError(
            f"cannot import module '{module_path}' for adapter '{name}': {exc}"
        ) from exc
    obj = getattr(mod, attr, None)
    if obj is None:
        raise AdapterResolutionError(f"module '{module_path}' has no attribute '{attr}'")
    return obj


def list_adapters(group: str) -> list[str]:
    """List registered adapter names in a group."""
    return sorted(ep.name for ep in entry_points(group=group))
