"""TOML config loading. No YAML.

Config field names are kept minimal; operators extend via the
`[engine.options]` and `[platform.options]` tables for adapter-specific
settings. The loader is platform-neutral: it knows no tokens and reads no
environment. The HeXO adapter resolves `HEXO_BRIDGE_TOKEN` itself, so a
non-HeXO platform needs no token at all.

Example config:

    [platform]
    name = "hexo"
    base_url = "https://hexo.did.science"
    # token from env HEXO_BRIDGE_TOKEN, or inline:
    # token = "hxo_..."

    [engine]
    name = "in_process_first_move"
    # OR a dotted path for local dev:
    # name = "my_pkg.my_engine:MyEngine"
    [engine.options]
    side = "o"

    [engine_session]
    name = "htttx_websocket"

    [bridge]
    engine_timeout_seconds = 5.0
    reconnect_backoff_seconds = 1.0
    reconnect_max_seconds = 30.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass
class AdapterConfig:
    name: str
    options: dict = field(default_factory=dict)


@dataclass
class BridgeConfig:
    platform: AdapterConfig
    engine: AdapterConfig
    engine_session: AdapterConfig
    engine_timeout_seconds: float = 5.0
    reconnect_backoff_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0
    stream_read_timeout_seconds: float = 45.0


def load_config(path: str | Path) -> BridgeConfig:
    """Load a BridgeConfig from a TOML file.

    Token handling lives in the HeXO adapter, not here: `HeXOPlatform` reads
    `HEXO_BRIDGE_TOKEN` from the environment (taking precedence over an inline
    `[platform.options] token`) and fails when it has neither. This loader
    passes platform options through untouched.
    """
    with open(path, "rb") as f:
        data = tomllib.load(f)

    platform_section = data.get("platform", {})

    # Hoist top-level platform keys (base_url, etc.) into options so the adapter
    # constructor receives them. The config example places base_url at [platform]
    # level, not under [platform.options], so both must be merged.
    platform_options = dict(platform_section.get("options", {}))
    for key in ("base_url", "token", "register_token", "timeout"):
        if key in platform_section and key not in platform_options:
            platform_options[key] = platform_section[key]

    engine_section = data.get("engine", {})
    session_section = data.get("engine_session", {})
    bridge_section = data.get("bridge", {})

    return BridgeConfig(
        platform=AdapterConfig(
            name=platform_section.get("name", "hexo"),
            options=platform_options,
        ),
        engine=AdapterConfig(
            name=engine_section.get("name", "in_process_first_move"),
            options=engine_section.get("options", {}),
        ),
        engine_session=AdapterConfig(
            name=session_section.get("name", "htttx_websocket"),
            options=session_section.get("options", {}),
        ),
        engine_timeout_seconds=float(bridge_section.get("engine_timeout_seconds", 5.0)),
        reconnect_backoff_seconds=float(bridge_section.get("reconnect_backoff_seconds", 1.0)),
        reconnect_max_seconds=float(bridge_section.get("reconnect_max_seconds", 30.0)),
        stream_read_timeout_seconds=float(bridge_section.get("stream_read_timeout_seconds", 45.0)),
    )
