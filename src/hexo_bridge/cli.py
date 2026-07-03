"""CLI entry point: `hexo-bridge` command.

Usage:
  hexo-bridge config.toml
  HEXO_BRIDGE_TOKEN=hxo_... hexo-bridge config.toml
"""

from __future__ import annotations

import asyncio
import logging
import sys

from hexo_bridge.bridge import run_bridge
from hexo_bridge.registry.config import load_config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if len(sys.argv) < 2:
        print("usage: hexo-bridge <config.toml>", file=sys.stderr)
        sys.exit(2)
    config = load_config(sys.argv[1])
    try:
        asyncio.run(run_bridge(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
