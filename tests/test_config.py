"""Tests for config loading from TOML.

Covers:
  - Basic config parsing.
  - Token from environment takes precedence over file.
  - Token from file works when env is unset.
  - Defaults for bridge timing.
"""

from __future__ import annotations

import os
from pathlib import Path

from hexo_bridge.registry.config import load_config


def _write_config(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(content)
    return p


def test_basic_config(tmp_path):
    cfg_path = _write_config(
        tmp_path,
        """
[platform]
name = "hexo"
base_url = "https://hexo.did.science"
[platform.options]
token = "hxo_from_file"

[engine]
name = "in_process_first_move"

[engine_session]
name = "htttx_websocket"
""",
    )
    os.environ.pop("HEXO_BRIDGE_TOKEN", None)
    cfg = load_config(cfg_path)
    assert cfg.platform.name == "hexo"
    assert cfg.platform.options["token"] == "hxo_from_file"
    assert cfg.engine.name == "in_process_first_move"
    assert cfg.engine_session.name == "htttx_websocket"


def test_token_from_env_takes_precedence(tmp_path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        """
[platform]
name = "hexo"
base_url = "https://hexo.did.science"
[platform.options]
token = "hxo_from_file"
""",
    )
    monkeypatch.setenv("HEXO_BRIDGE_TOKEN", "hxo_from_env")
    cfg = load_config(cfg_path)
    assert cfg.platform.options["token"] == "hxo_from_env"


def test_token_from_file_when_env_unset(tmp_path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        """
[platform]
name = "hexo"
[platform.options]
token = "hxo_from_file"
""",
    )
    monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    cfg = load_config(cfg_path)
    assert cfg.platform.options["token"] == "hxo_from_file"


def test_no_token_anywhere(tmp_path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        """
[platform]
name = "hexo"
base_url = "https://hexo.did.science"
""",
    )
    monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    cfg = load_config(cfg_path)
    assert "token" not in cfg.platform.options


def test_bridge_defaults(tmp_path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        """
[platform]
name = "hexo"
""",
    )
    monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    cfg = load_config(cfg_path)
    assert cfg.engine_timeout_seconds == 5.0
    assert cfg.reconnect_backoff_seconds == 1.0
    assert cfg.reconnect_max_seconds == 30.0


def test_bridge_overrides(tmp_path, monkeypatch):
    cfg_path = _write_config(
        tmp_path,
        """
[platform]
name = "hexo"
[bridge]
engine_timeout_seconds = 2.5
reconnect_max_seconds = 60.0
""",
    )
    monkeypatch.delenv("HEXO_BRIDGE_TOKEN", raising=False)
    cfg = load_config(cfg_path)
    assert cfg.engine_timeout_seconds == 2.5
    assert cfg.reconnect_max_seconds == 60.0
