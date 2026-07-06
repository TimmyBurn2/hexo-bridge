"""Bridge engine adapter for SealBot-perf (the optimized `minimax_cpp` build).

This is a worked example of wiring a real, third-party engine into the bridge
as a `SubprocessEngine`: two methods (`build_request`, `parse_response`) on top
of the base, which owns process spawn, JSON-line framing, stderr capture, and
restart-on-death. It takes core-domain types only (`GameState` in, `Move` out);
it imports no htttx, HTTP, or HeXO types.

SealBot-perf is a C++ pybind engine (`minimax_cpp`) built for CPython 3.14, while
the bridge itself may run on a different CPython. To avoid an ABI mismatch, the
engine runs in its own interpreter as a subprocess. The shim replays the board
the server delivered in the `setup` packet (the seed), then each completed
turn's two stones, into SealBot's own `HexGame`, so this adapter never
reimplements the rules, then returns the two stones of the next turn.

If the subprocess fails to import `minimax_cpp` or crashes, the base surfaces the
captured stderr in a `SubprocessEngineError` instead of an opaque translation
error.

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

from hexo_bridge.adapters.engines.subprocess import SubprocessEngine
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move
from hexo_bridge.ports.engine import SubprocessEngineError

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
    # Apply the starting board the server delivered (the setup packet's cells),
    # in order, as SealBot's own turn model would play them. The standard
    # server delivers one cross at the origin here; the bridge passes whatever
    # was delivered, so this does not bake in the origin.
    for q, r in req.get("setup", []):
        game.make_move(q, r)
    for q, r in req["moves"]:
        game.make_move(q, r)
    bot.time_limit = req.get("time_limit", time_limit)
    result = bot.get_move(game)
    sys.stdout.write(json.dumps({"moves": [list(m) for m in result]}) + "\n")
    sys.stdout.flush()
"""


class SealPerfEngine(SubprocessEngine):
    """Drives SealBot-perf as a subprocess via the SubprocessEngine base."""

    def __init__(
        self,
        *,
        python: str = "python3",
        bot_dir: str,
        root_dir: str,
        time_limit: float = 0.3,
    ) -> None:
        super().__init__(
            command=[python, "-c", _SUBPROCESS_SHIM, bot_dir, root_dir, str(time_limit)],
            restart=True,
        )
        self._time_limit = time_limit

    def build_request(self, state: GameState) -> dict:
        # The board the bot plays on is whatever the server delivered in the
        # `setup` packet (`state.setup_cells`), plus the cumulative completed
        # turns (`state.moves`). The bridge does not bake in an origin; SealBot
        # replays the delivered seed then each completed turn's two stones into
        # its own `HexGame`, so this adapter never reimplements the rules, then
        # returns the two stones of the next turn (us).
        setup: list[list[int]] = [[q, r] for q, r, _ in state.setup_cells]
        placements: list[list[int]] = []
        for move in state.moves:
            for piece in move.pieces:
                placements.append([piece.q, piece.r])
        # Think for the configured per-turn budget, but never longer than the
        # clock the server left for this turn. The bridge's wait_for is the hard
        # bound; this is the suggested budget passed to the engine.
        think_time = self._time_limit
        if state.time_limit_seconds is not None:
            think_time = min(think_time, state.time_limit_seconds)
        return {"setup": setup, "moves": placements, "time_limit": think_time}

    def parse_response(self, obj: dict, state: GameState) -> Move:
        try:
            pieces = obj["moves"]
        except KeyError as exc:
            raise SubprocessEngineError(f"SealBot-perf response missing 'moves': {obj!r}") from exc
        coords = [Coord(int(q), int(r)) for q, r in pieces]
        if not coords:
            raise SubprocessEngineError("SealBot-perf returned no move")
        if len(coords) == 1:
            # The first stone won; return a one-piece move and let the bridge
            # normalize to a two-piece transport shape.
            return Move(side=state.side, pieces=(coords[0],))
        return Move(side=state.side, pieces=(coords[0], coords[1]))
