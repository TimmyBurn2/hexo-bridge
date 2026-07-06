"""Bridge engine adapter for SealBot-perf (the optimized `minimax_cpp` build).

This is a worked example of wiring a real, third-party engine into the bridge as
an `EnginePort`: the one method `get_move(state) -> Move`. It takes core-domain
types only (`GameState` in, `Move` out); it imports no htttx, HTTP, or HeXO types.

SealBot-perf is a C++ pybind engine (`minimax_cpp`) built for CPython 3.14, while
the bridge itself may run on a different CPython. To avoid an ABI mismatch, the
engine runs in its own interpreter as a subprocess and this adapter talks to it
over a tiny JSON-lines protocol: send the ordered list of every placement so far
(the origin first, then each completed turn's two stones), get back the two stones
of the next turn. The subprocess replays those placements into SealBot's own
`HexGame`, so this adapter never reimplements the rules.

Config (dotted-path form, no entry point needed):

    [engine]
    name = "hexo_bridge_examples.seal_perf_engine:SealPerfEngine"
    [engine.options]
    python    = "python3"                       # a CPython 3.14 that can import minimax_cpp
    bot_dir   = "/home/you/Work/Hexo/SealBot/current"
    root_dir  = "/home/you/Work/Hexo/SealBot"
    time_limit = 0.3
"""

from __future__ import annotations

import asyncio
import json

from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move
from hexo_bridge.ports.engine import EngineTranslationError

_SUBPROCESS_SHIM = r"""
import sys, json
bot_dir, root_dir, time_limit = sys.argv[1], sys.argv[2], float(sys.argv[3])
sys.path.insert(0, bot_dir)
from minimax_cpp import MinimaxBot
sys.path.insert(0, root_dir)
from game import HexGame

bot = MinimaxBot(time_limit)
sys.stderr.write("seal_perf ready\n")
sys.stderr.flush()

for line in sys.stdin:
    line = line.strip()
    if not line or line == "quit":
        break
    req = json.loads(line)
    game = HexGame(win_length=6)
    game.reset()
    for q, r in req["moves"]:
        game.make_move(q, r)
    bot.time_limit = req.get("time_limit", time_limit)
    result = bot.get_move(game)
    sys.stdout.write(json.dumps({"moves": [list(m) for m in result]}) + "\n")
    sys.stdout.flush()
"""


class SealPerfEngine:
    """Drives SealBot-perf as a subprocess and answers full turns."""

    def __init__(
        self,
        *,
        python: str = "python3",
        bot_dir: str,
        root_dir: str,
        time_limit: float = 0.3,
    ) -> None:
        self._python = python
        self._bot_dir = bot_dir
        self._root_dir = root_dir
        self._time_limit = time_limit
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    async def _ensure_proc(self) -> asyncio.subprocess.Process:
        if self._proc is not None and self._proc.returncode is None:
            return self._proc

        self._proc = await asyncio.create_subprocess_exec(
            self._python,
            "-c",
            _SUBPROCESS_SHIM,
            self._bot_dir,
            self._root_dir,
            str(self._time_limit),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        return self._proc

    async def get_move(self, state: GameState) -> Move:
        # Every placement so far, in play order: the server-placed origin first,
        # then each completed turn's two stones. SealBot replays these to rebuild
        # the position, then returns the two stones of the side to move (us).
        placements: list[list[int]] = [[0, 0]]
        for move in state.moves:
            for piece in move.pieces:
                placements.append([piece.q, piece.r])

        # Think for the configured per-turn budget, but never longer than the
        # clock the server left for this turn.
        think_time = self._time_limit
        if state.time_limit_seconds is not None:
            think_time = min(think_time, state.time_limit_seconds)

        request = {
            "moves": placements,
            "time_limit": think_time,
        }

        async with self._lock:
            proc = await self._ensure_proc()
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write((json.dumps(request) + "\n").encode())
            await proc.stdin.drain()
            line = await proc.stdout.readline()

        if not line:
            raise EngineTranslationError("SealBot-perf subprocess produced no response")

        try:
            pieces = json.loads(line)["moves"]
        except (json.JSONDecodeError, KeyError) as error:
            raise EngineTranslationError(f"SealBot-perf response was not valid: {line!r}") from error

        coords = [Coord(int(q), int(r)) for q, r in pieces]
        if not coords:
            raise EngineTranslationError("SealBot-perf returned no move")

        # A turn is two placements. SealBot returns one only when the first stone
        # already wins; pad with a distinct neighbour so the transport has a legal
        # shape (the server ends the game on the winning stone and ignores the rest).
        if len(coords) == 1:
            filler = Coord(coords[0].q + 1, coords[0].r)
            if filler == coords[0]:
                filler = Coord(coords[0].q, coords[0].r + 1)
            coords.append(filler)

        return Move(side=state.side, pieces=(coords[0], coords[1]))

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.returncode is not None:
            return

        try:
            if proc.stdin is not None:
                proc.stdin.write(b"quit\n")
                await proc.stdin.drain()
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except (TimeoutError, ProcessLookupError, BrokenPipeError):
            proc.kill()
