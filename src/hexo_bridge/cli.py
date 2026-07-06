"""CLI entry point: the `hexo-bridge` command.

Subcommands:
  hexo-bridge run <config.toml>          run the bridge (default)
  hexo-bridge validate <config.toml>     dry-run the engine once, print move + timing
  hexo-bridge engines --list            list registered engine adapters
  hexo-bridge engines <config.toml>     resolve and report the configured engine

For back-compat, `hexo-bridge <config.toml>` (no subcommand) is treated as
`hexo-bridge run <config.toml>`.

Exit codes for `validate`:
  0  engine resolved, spawned, returned a legal move.
  1  engine failure (spawn, ABI/import, malformed response, timeout, crash).
  2  config/usage/resolution failure.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from hexo_bridge.bridge import build_engine
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Move, Side, normalize_move
from hexo_bridge.ports.engine import EngineTranslationError
from hexo_bridge.registry.config import load_config
from hexo_bridge.registry.resolver import (
    ENGINE_GROUP,
    AdapterResolutionError,
    list_adapters,
    resolve_adapter,
)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(prog="hexo-bridge", description="HeXO bot bridge.")
    sub = parser.add_subparsers(dest="command")

    sub_run = sub.add_parser("run", help="Run the bridge against a config.")
    sub_run.add_argument("config", type=Path)

    sub_validate = sub.add_parser("validate", help="Dry-run the engine once against an empty board.")
    sub_validate.add_argument("config", type=Path)

    sub_engines = sub.add_parser("engines", help="List or resolve engine adapters.")
    sub_engines.add_argument("config", type=Path, nargs="?", help="If given, resolve the configured engine.")
    sub_engines.add_argument("--list", action="store_true", help="List registered engine adapters.")

    args = parser.parse_args()

    # Back-compat: `hexo-bridge <config.toml>` -> run.
    if args.command is None:
        rest = sys.argv[1:]
        if len(rest) == 1 and not rest[0].startswith("-"):
            args = argparse.Namespace(command="run", config=Path(rest[0]))
        else:
            parser.print_help(sys.stderr)
            sys.exit(2)

    if args.command == "run":
        _run_bridge(args.config)
    elif args.command == "validate":
        sys.exit(_validate(args.config))
    elif args.command == "engines":
        sys.exit(_engines(args))


def _run_bridge(config_path: Path) -> None:
    from hexo_bridge.bridge import run_bridge

    config = load_config(config_path)
    try:
        asyncio.run(run_bridge(config))
    except KeyboardInterrupt:
        pass


def _validate(config_path: Path) -> int:
    """Dry-run the configured engine once, no server, no token, no session.

    Builds a `GameState` for an opening-ply move: the side the config selects,
    no setup board, no moves. Catches adapter resolution, spawn, ABI, import,
    shape, and think-time errors in seconds and prints the move. `validate`
    runs whichever boundary the config selects: in-process, subprocess/stdio,
    or htttx-stateless (the latter POSTs to the configured `/turn` URL, so a
    stateless engine must be running for it to succeed).
    """
    try:
        config = load_config(config_path)
    except FileNotFoundError:
        print(f"config not found: {config_path}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    engine_name = config.engine.name
    print(f"engine: {engine_name}")
    # No setup packet offline: the engine plays on an empty board. The side is
    # the configured side (or O by default); there is no server stating it.
    state = GameState(side=Side.O, moves=[], moves_to_apply=[], time_limit_seconds=None, request_id=None)
    print(f"state: side={state.side.value}, setup_cells={len(state.setup_cells)}, moves={len(state.moves)}, clock=none")

    try:
        engine = build_engine(config)
    except AdapterResolutionError as exc:
        print(f"FAILED: cannot resolve engine: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"FAILED: engine constructor error: {exc}", file=sys.stderr)
        return 1

    # Run get_move and close() in the SAME event loop so the subprocess
    # transports stay bound to a live loop. A cross-loop close() would raise
    # RuntimeError on drain() and leak the child (the failure path `validate`
    # is meant to diagnose).
    timeout = config.engine_timeout_seconds
    import time

    start = time.perf_counter()
    try:
        move = asyncio.run(_validate_run(engine, state, timeout))
    except EngineTranslationError as exc:
        elapsed = time.perf_counter() - start
        print(f"FAILED after {elapsed:.2f}s: {exc}", file=sys.stderr)
        return 1
    except TimeoutError:
        elapsed = time.perf_counter() - start
        print(f"FAILED after {elapsed:.2f}s: engine timed out (bound {timeout:.1f}s)", file=sys.stderr)
        return 1
    except Exception as exc:
        elapsed = time.perf_counter() - start
        print(f"FAILED after {elapsed:.2f}s: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    if len(move.pieces) == 1:
        move = normalize_move(move, state.to_board())

    elapsed = time.perf_counter() - start
    pieces = ", ".join(f"({p.q},{p.r})" for p in move.pieces)
    print(f"move:  side={move.side.value}, pieces=[{pieces}]")
    print(f"elapsed: {elapsed:.2f}s")
    print("OK")
    return 0


async def _validate_run(engine, state: GameState, timeout: float) -> Move:
    """Run get_move then close() in one event loop so the child is cleaned up.

    On timeout or failure `close()` still runs (the `finally`), so a hung child
    is killed via `SubprocessEngine.close()` -> `_kill`, not leaked.
    """
    try:
        return await asyncio.wait_for(engine.get_move(state), timeout=timeout)
    finally:
        close = getattr(engine, "close", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass


def _engines(args: argparse.Namespace) -> int:
    if args.list or args.config is None:
        names = list_adapters(ENGINE_GROUP)
        print("registered engine adapters:")
        for name in names:
            print(f"  {name}")
        print("(dotted paths like 'pkg.mod:Class' are also accepted)")
        return 0

    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"config not found: {args.config}", file=sys.stderr)
        return 2
    name = config.engine.name
    try:
        cls = resolve_adapter(name, ENGINE_GROUP)
    except AdapterResolutionError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 2
    source = "entry-point" if name in list_adapters(ENGINE_GROUP) else "dotted-path"
    print(f"{name} -> {cls.__module__}:{cls.__qualname__} (source: {source})")
    try:
        cls(**config.engine.options)
        print("constructor: OK")
        return 0
    except Exception as exc:
        print(f"constructor: FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    main()
