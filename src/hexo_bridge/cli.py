"""`hexo-bridge bot.toml`: run a bot whose engine is a process speaking JSON lines."""

from __future__ import annotations

import argparse
import functools
import logging
import os
import sys
import tomllib
from pathlib import Path
from typing import Any

from hexo_bridge.bot import run
from hexo_bridge.client import ApiError
from hexo_bridge.engine import StdioEngine


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="hexo-bridge", description=__doc__)
    parser.add_argument("config", type=Path, help="the bot's TOML file")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every move and reading")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Only the bridge's own debug lines: websockets logs each dial with its token in the URL.
    if args.verbose:
        logging.getLogger("hexo_bridge").setLevel(logging.DEBUG)
    try:
        with args.config.open("rb") as handle:
            config = tomllib.load(handle)
        server, engine, declaration = (
            _table(config, "server"),
            _table(config, "engine"),
            _table(config, "declaration"),
        )
        url = _value(server, "url", "server", str)
        token_env = server.get("token_env", "HEXO_BOT_TOKEN")
        token = os.environ.get(token_env)
        if not token:
            raise ValueError(f"set {token_env} to the bot's token")
        command = _value(engine, "command", "engine", list)
        # The command and its working directory are read relative to the config file.
        cwd = (args.config.resolve().parent / engine.get("cwd", ".")).resolve()
        factory = functools.partial(
            StdioEngine, command=[str(part) for part in command], cwd=str(cwd)
        )
        reserve = float(engine.get("reserve_seconds", 1.0))
        run(
            factory,
            url=url,
            token=token,
            declaration=declaration,
            open_for_challenges=bool(server.get("open", True)),
            reserve_seconds=reserve,
        )
    except (OSError, ValueError, TypeError, tomllib.TOMLDecodeError) as error:
        sys.exit(f"hexo-bridge: {error}")
    except ApiError as error:
        sys.exit(f"hexo-bridge: the server refused the bot: {error}")


def _table(config: dict[str, Any], name: str) -> dict[str, Any]:
    table = config.get(name)
    if not isinstance(table, dict):
        raise ValueError(f"the config needs a [{name}] table")
    return table


def _value(table: dict[str, Any], key: str, where: str, kind: type) -> Any:
    value = table.get(key)
    if not isinstance(value, kind) or not value:
        raise ValueError(f"[{where}] needs {key}")
    return value
