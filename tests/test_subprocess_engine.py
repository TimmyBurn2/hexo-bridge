"""Tests for the SubprocessEngine base: stderr surfacing, restart, lifecycle.

These drive the base with a tiny Python shim that speaks JSON lines, so no
external engine is needed. They cover the fixes the base introduces:
  - a crashing child surfaces its stderr (not an opaque error),
  - a broken pipe triggers a restart on the next call,
  - a one-piece move is returned and the bridge normalizes (tested via the
    SealBot-shaped shim returning one stone),
  - close() shuts the child down cleanly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hexo_bridge.adapters.engines.stdio import StdioLineEngine
from hexo_bridge.adapters.engines.subprocess import SubprocessEngine
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move, Side, normalize_move
from hexo_bridge.ports.engine import SubprocessEngineError

_ECHO_SHIM = r"""
import sys, json
sys.stderr.write("echo shim ready\n"); sys.stderr.flush()
for line in sys.stdin:
    line = line.strip()
    if not line or line == "quit":
        break
    req = json.loads(line)
    sys.stdout.write(json.dumps({"echo": req, "pid": __import__("os").getpid()}) + "\n")
    sys.stdout.flush()
"""

_CRASH_ON_FIRST_SHIM = r"""
import sys
sys.stderr.write("crash shim: about to raise\n"); sys.stderr.flush()
raise SystemExit("deliberate crash before reading any input")
"""

_CRASH_AFTER_ONE_SHIM = r"""
import sys, json
sys.stderr.write("crash-after shim ready\n"); sys.stderr.flush()
line = sys.stdin.readline()
req = json.loads(line)
sys.stdout.write(json.dumps({"moves": [[1, 0], [-1, 1]]}) + "\n"); sys.stdout.flush()
raise SystemExit("deliberate crash after one reply")
"""


class _EchoEngine(SubprocessEngine):
    def __init__(self, *, python: str = sys.executable) -> None:
        super().__init__(command=[python, "-c", _ECHO_SHIM], restart=True)

    def build_request(self, state: GameState) -> dict:
        return {"n": len(state.moves)}

    def parse_response(self, obj: dict, state: GameState) -> Move:
        return Move(side=state.side, pieces=(Coord(1, 0), Coord(-1, 1)))


def _empty_state() -> GameState:
    return GameState(side=Side.O, moves=[], moves_to_apply=[], time_limit_seconds=None)


async def test_subprocess_surfaces_stderr_on_crash():
    """A child that crashes before replying must surface its stderr, not an
    opaque 'no response' error."""

    class CrashEngine(SubprocessEngine):
        def __init__(self) -> None:
            super().__init__(command=[sys.executable, "-c", _CRASH_ON_FIRST_SHIM], restart=True)

        def build_request(self, state: GameState) -> dict:
            return {}

        def parse_response(self, obj: dict, state: GameState) -> Move:
            raise AssertionError("should not reach parse")

    eng = CrashEngine()
    with pytest.raises(SubprocessEngineError) as exc:
        await eng.get_move(_empty_state())
    assert "no response" in str(exc.value).lower()
    assert exc.value.stderr is not None
    assert "deliberate crash" in exc.value.stderr
    await eng.close()


async def test_subprocess_restarts_after_crash():
    """After a child crashes, the next call spawns a fresh child."""

    class CrashAfterOne(SubprocessEngine):
        def __init__(self) -> None:
            super().__init__(command=[sys.executable, "-c", _CRASH_AFTER_ONE_SHIM], restart=True)

        def build_request(self, state: GameState) -> dict:
            return {}

        def parse_response(self, obj: dict, state: GameState) -> Move:
            pieces = obj["moves"]
            return Move(side=state.side, pieces=(Coord(*pieces[0]), Coord(*pieces[1])))

    eng = CrashAfterOne()
    # First call succeeds.
    mv = await eng.get_move(_empty_state())
    assert len(mv.pieces) == 2
    # Second call: the child crashed after replying; the pipe is broken. The
    # base raises, then nulls the proc so the next call respawns.
    with pytest.raises(SubprocessEngineError):
        await eng.get_move(_empty_state())
    # Third call: a fresh child has spawned and replied.
    mv = await eng.get_move(_empty_state())
    assert len(mv.pieces) == 2
    await eng.close()


async def test_subprocess_command_not_found():
    """A missing executable surfaces a clear spawn error, not a traceback."""

    class MissingEngine(SubprocessEngine):
        def __init__(self) -> None:
            super().__init__(command=["this-binary-does-not-exist-12345"], restart=True)

        def build_request(self, state: GameState) -> dict:
            return {}

        def parse_response(self, obj: dict, state: GameState) -> Move:
            raise AssertionError

    eng = MissingEngine()
    with pytest.raises(SubprocessEngineError) as exc:
        await eng.get_move(_empty_state())
    assert "cannot spawn" in str(exc.value).lower()
    await eng.close()


async def test_one_piece_move_is_normalized_by_bridge_helper():
    """An engine returning one stone (first-cross win) produces a one-piece
    Move; the bridge's normalize_move pads it to two."""

    class OneStoneEngine(SubprocessEngine):
        def __init__(self) -> None:
            super().__init__(command=[sys.executable, "-c", _ECHO_SHIM], restart=True)

        def build_request(self, state: GameState) -> dict:
            return {}

        def parse_response(self, obj: dict, state: GameState) -> Move:
            return Move(side=state.side, pieces=(Coord(1, 0),))

    eng = OneStoneEngine()
    mv = await eng.get_move(_empty_state())
    assert len(mv.pieces) == 1
    padded = normalize_move(mv, _empty_state().to_board())
    assert len(padded.pieces) == 2
    await eng.close()


# --- Stdio line adapter ----------------------------------------------------

_STDIO_SHIM = r"""
import sys, json
board = {(0,0): 'x'}  # opening seeded by reset
def reset():
    global board
    board = {(0,0): 'x'}
for line in sys.stdin:
    line = line.strip()
    if not line or line == 'quit':
        break
    req = json.loads(line)
    op = req.get('op')
    if op == 'reset':
        reset()
        sys.stdout.write(json.dumps({'ok': True}) + '\n'); sys.stdout.flush()
    elif op == 'place':
        board[(req['q'], req['r'])] = req['side']
        sys.stdout.write(json.dumps({'ok': True}) + '\n'); sys.stdout.flush()
    elif op == 'best_move':
        # Return two empty neighbours of the origin.
        sys.stdout.write(json.dumps({'move': [[1, 0], [-1, 1]]}) + '\n'); sys.stdout.flush()
"""

_STDIO_ONE_STONE_SHIM = _STDIO_SHIM.replace(
    "json.dumps({'move': [[1, 0], [-1, 1]]})",
    "json.dumps({'move': [[1, 0]]})",
)


async def test_stdio_adapter_returns_two_piece_move():
    eng = StdioLineEngine(command=[sys.executable, "-c", _STDIO_SHIM])
    mv = await eng.get_move(_empty_state())
    assert mv.side is Side.O
    assert len(mv.pieces) == 2
    await eng.close()


async def test_stdio_adapter_one_stone_is_normalized():
    eng = StdioLineEngine(command=[sys.executable, "-c", _STDIO_ONE_STONE_SHIM])
    mv = await eng.get_move(_empty_state())
    assert len(mv.pieces) == 1
    padded = normalize_move(mv, _empty_state().to_board())
    assert len(padded.pieces) == 2
    await eng.close()


async def test_stdio_adapter_surfaces_shim_crash():
    eng = StdioLineEngine(command=[sys.executable, "-c", _CRASH_ON_FIRST_SHIM])
    with pytest.raises(SubprocessEngineError) as exc:
        await eng.get_move(_empty_state())
    assert exc.value.stderr is not None
    await eng.close()


def test_validate_cli_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The `hexo-bridge validate` CLI runs end to end against a real engine
    config and exits 0 on success."""
    from hexo_bridge.cli import main

    cfg = tmp_path / "cfg.toml"
    cfg.write_text(
        "[platform]\nname='loopback'\n"
        "[engine]\nname='in_process_first_move'\n"
        "[engine.options]\nside='o'\n"
        "[engine_session]\nname='htttx_websocket'\n"
        "[bridge]\nengine_timeout_seconds=5.0\n"
    )
    monkeypatch.setattr(sys, "argv", ["hexo-bridge", "validate", str(cfg)])
    try:
        main()
    except SystemExit as exc:
        assert exc.code == 0
    else:
        pytest.fail("validate did not exit")
