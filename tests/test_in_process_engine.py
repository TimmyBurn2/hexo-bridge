"""Tests for the in-process engine: proves the port is not htttx-shaped.

The InProcessFirstMoveEngine imports no HTTP, no htttx, no HeXO. It takes a
core GameState and returns a core Move. This is the leak test: if the port were
htttx-shaped, a non-htttx adapter could not slot in.
"""

from __future__ import annotations

import inspect

from hexo_bridge.adapters.engines.in_process import InProcessFirstMoveEngine
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Move, Side


async def test_in_process_returns_valid_move():
    engine = InProcessFirstMoveEngine(Side.O)
    state = GameState(side=Side.O)
    move = await engine.get_move(state)
    assert isinstance(move, Move)
    assert move.side is Side.O
    assert len(move.pieces) == 2
    assert move.pieces[0] != move.pieces[1]


async def test_in_process_move_avoids_occupied():
    engine = InProcessFirstMoveEngine(Side.O)
    state = GameState(side=Side.O)
    move = await engine.get_move(state)
    board = state.to_board()
    for piece in move.pieces:
        assert not board.occupied(piece)


async def test_in_process_does_not_import_httpx_or_htttx():
    """The in-process engine module must not import httpx, htttx, or hexo platform."""
    import hexo_bridge.adapters.engines.in_process as mod

    source = inspect.getsource(mod)
    assert "import httpx" not in source
    assert "import htttx" not in source
    assert "hexo_bridge.adapters.platforms" not in source
    assert "hexo_bridge.adapters.engine_sessions" not in source


async def test_in_process_coerces_string_side_from_config():
    """Config `[engine.options] side = "o"` arrives as the string "o", not a
    Side. The engine must coerce it so the returned Move.side is a Side, not a
    plain string (the type contract and Move.__repr__ depend on it)."""
    engine = InProcessFirstMoveEngine(side="o")
    assert engine._side is Side.O
    state = GameState(side=Side.O)
    move = await engine.get_move(state)
    assert move.side is Side.O
    # repr must not raise (regression: a plain-string side blew up Move.__repr__).
    assert "o" in repr(move)


async def test_in_process_accepts_side_enum():
    engine = InProcessFirstMoveEngine(side=Side.X)
    assert engine._side is Side.X
