"""Tests at the port boundary: core replay and turn detection.

Covers:
  - Board.replay: opening seeded at origin, moves applied in order.
  - Turn detection: ply 0 = opening (no move), ply 1 = o, ply 2 = x, even=x, odd=o.
  - Opening off-by-one: ply 0 is one stone, ply >= 1 is two stones per turn.
  - moves_since: slicing the tail for htttx `previous`.
"""

from __future__ import annotations

from hexo_bridge.core.board import Board, GameState
from hexo_bridge.core.move import Coord, Move, Side


def test_replay_seeds_opening_at_origin():
    board = Board.replay([])
    assert board.occupied(Coord(0, 0))
    assert board.side_at(Coord(0, 0)) is Side.X
    assert len(board.cells) == 1


def test_replay_applies_moves_in_order():
    m1 = Move(Side.O, (Coord(1, 0), Coord(-1, 1)))
    m2 = Move(Side.X, (Coord(2, 0), Coord(-2, 2)))
    board = Board.replay([m1, m2])
    assert board.occupied(Coord(0, 0))
    assert board.side_at(Coord(1, 0)) is Side.O
    assert board.side_at(Coord(-1, 1)) is Side.O
    assert board.side_at(Coord(2, 0)) is Side.X
    assert board.side_at(Coord(-2, 2)) is Side.X
    assert len(board.cells) == 5


def test_turn_detection_opening():
    """At ply 0 (opening placed, no moves submitted), it is O's turn."""
    turn = Board.turn_for([])
    assert turn.ply == 0
    assert turn.is_opening
    assert turn.side is Side.O


def test_turn_detection_ply1_is_x():
    """After one submitted turn (O's), it is X's turn."""
    turn = Board.turn_for([Move(Side.O, (Coord(1, 0), Coord(2, 0)))])
    assert turn.ply == 1
    assert turn.side is Side.X


def test_turn_detection_ply2_is_o():
    """After two submitted turns (O's then X's), it is O's turn."""
    m1 = Move(Side.O, (Coord(1, 0), Coord(2, 0)))
    m2 = Move(Side.X, (Coord(3, 0), Coord(4, 0)))
    turn = Board.turn_for([m1, m2])
    assert turn.ply == 2
    assert turn.side is Side.O


def test_turn_detection_even_is_o_odd_is_x():
    """After the opening (X), O moves on even ply, X on odd ply.

    The opening is X at ply 0. Submitted turns alternate: O (ply 1), X (ply 2),
    O (ply 3), etc. So even len(moves) -> O to move, odd len(moves) -> X to move.
    """
    for ply in range(10):
        moves = [Move(Side.O, (Coord(i, 0), Coord(i + 1, 0))) for i in range(ply)]
        turn = Board.turn_for(moves)
        assert turn.ply == ply
        if ply % 2 == 0:
            assert turn.side is Side.O
        else:
            assert turn.side is Side.X


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
    import pytest

    with pytest.raises(ValueError):
        Board.moves_since([], -1)


def test_moves_since_shrink_returns_all():
    m1 = Move(Side.O, (Coord(1, 0), Coord(2, 0)))
    all_moves = [m1]
    assert Board.moves_since(all_moves, 5) == [m1]


def test_game_state_to_board_replay():
    m1 = Move(Side.O, (Coord(1, 0), Coord(-1, 1)))
    state = GameState(side=Side.O, moves=[m1])
    board = state.to_board()
    assert board.occupied(Coord(0, 0))
    assert board.side_at(Coord(1, 0)) is Side.O


def test_move_rejects_wrong_piece_count():
    import pytest

    with pytest.raises(ValueError):
        Move(Side.X, (Coord(0, 1),))


def test_move_rejects_duplicate_pieces():
    import pytest

    with pytest.raises(ValueError):
        Move(Side.X, (Coord(1, 1), Coord(1, 1)))


def test_side_other():
    assert Side.X.other is Side.O
    assert Side.O.other is Side.X
