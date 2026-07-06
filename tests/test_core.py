"""Tests at the port boundary: core replay from the setup seed.

Covers:
  - Board.replay: builds the board from the delivered setup seed plus the moves,
    applied in order. No baked-in origin.
  - Board.from_cells: the seed is whatever the server delivered.
  - moves_since: slicing the tail for htttx `previous`.
"""

from __future__ import annotations

import pytest

from hexo_bridge.core.board import Board, GameState
from hexo_bridge.core.move import Coord, Move, Side


def test_replay_from_standard_setup_seed():
    """The standard server delivers one cross at the origin in setup; replay
    applies the moves on top of it."""
    board = Board.replay([(0, 0, "x")], [])
    assert board.occupied(Coord(0, 0))
    assert board.side_at(Coord(0, 0)) is Side.X
    assert len(board.cells) == 1


def test_replay_from_non_standard_setup_seed():
    """A conformant server under free_setup may deliver a different starting
    position. The bridge consumes it as delivered; it does not fall back to a
    baked-in origin."""
    board = Board.replay([(3, -1, "x"), (2, 2, "o")], [])
    assert board.occupied(Coord(3, -1))
    assert board.side_at(Coord(3, -1)) is Side.X
    assert board.occupied(Coord(2, 2))
    assert board.side_at(Coord(2, 2)) is Side.O
    assert not board.occupied(Coord(0, 0)), "no origin is baked in"


def test_replay_applies_moves_on_top_of_seed():
    m1 = Move(Side.O, (Coord(1, 0), Coord(-1, 1)))
    m2 = Move(Side.X, (Coord(2, 0), Coord(-2, 2)))
    board = Board.replay([(0, 0, "x")], [m1, m2])
    assert board.side_at(Coord(0, 0)) is Side.X
    assert board.side_at(Coord(1, 0)) is Side.O
    assert board.side_at(Coord(-1, 1)) is Side.O
    assert board.side_at(Coord(2, 0)) is Side.X
    assert board.side_at(Coord(-2, 2)) is Side.X
    assert len(board.cells) == 5


def test_replay_with_empty_seed_is_empty_board():
    """No setup packet (e.g. validate offline dry-run) -> empty board, no
    baked-in origin."""
    board = Board.replay([], [])
    assert board.cells == {}


def test_from_cells_consumes_delivered_side():
    board = Board.from_cells([(5, 5, "o")])
    assert board.side_at(Coord(5, 5)) is Side.O


def test_moves_since_slices_tail():
    m1 = Move(Side.O, (Coord(1, 0), Coord(2, 0)))
    m2 = Move(Side.X, (Coord(3, 0), Coord(4, 0)))
    m3 = Move(Side.O, (Coord(5, 0), Coord(6, 0)))
    all_moves = [m1, m2, m3]
    assert Board.moves_since(all_moves, 0) == [m1, m2, m3]
    assert Board.moves_since(all_moves, 1) == [m2, m3]
    assert Board.moves_since(all_moves, 2) == [m3]
    assert Board.moves_since(all_moves, 3) == []


def test_moves_since_negative_raises():
    with pytest.raises(ValueError):
        Board.moves_since([], -1)


def test_moves_since_shrink_returns_all():
    m1 = Move(Side.O, (Coord(1, 0), Coord(2, 0)))
    all_moves = [m1]
    assert Board.moves_since(all_moves, 5) == [m1]


def test_game_state_to_board_replays_seed_plus_moves():
    m1 = Move(Side.O, (Coord(1, 0), Coord(-1, 1)))
    state = GameState(side=Side.O, setup_cells=[(0, 0, "x")], moves=[m1])
    board = state.to_board()
    assert board.occupied(Coord(0, 0))
    assert board.side_at(Coord(1, 0)) is Side.O


def test_game_state_default_setup_cells_is_empty():
    state = GameState(side=Side.O)
    assert state.setup_cells == []
    assert state.to_board().cells == {}


def test_move_accepts_one_or_two_pieces():
    Move(Side.X, (Coord(0, 1),))
    Move(Side.X, (Coord(0, 1), Coord(0, 2)))
    with pytest.raises(ValueError):
        Move(Side.X, ())
    with pytest.raises(ValueError):
        Move(Side.X, (Coord(0, 1), Coord(0, 2), Coord(0, 3)))


def test_move_rejects_duplicate_pieces():
    with pytest.raises(ValueError):
        Move(Side.X, (Coord(1, 1), Coord(1, 1)))


def test_normalize_move_pads_single_piece():
    from hexo_bridge.core.move import normalize_move

    board = Board.replay([(0, 0, "x")], [])
    one = Move(Side.O, (Coord(1, 0),))
    padded = normalize_move(one, board)
    assert len(padded.pieces) == 2
    assert padded.pieces[0] == Coord(1, 0)
    assert padded.pieces[1] != Coord(1, 0)
    # A two-piece move passes through unchanged.
    two = Move(Side.O, (Coord(1, 0), Coord(-1, 1)))
    assert normalize_move(two, board) is two


def test_side_other():
    assert Side.X.other is Side.O
    assert Side.O.other is Side.X
