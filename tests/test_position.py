from hexo_bridge.position import AXES, Position


def position(x, o=(), to_move="o"):
    stones = {cell: "x" for cell in x}
    stones.update({cell: "o" for cell in o})
    return Position(stones, to_move)


def row(length, start=0, r=0):
    return [(q, r) for q in range(start, start + length)]


def test_a_stone_goes_on_an_empty_cell_within_eight_of_a_stone():
    board = position([(0, 0)])
    assert board.reachable((8, 0))
    assert board.reachable((-4, -4))
    assert not board.reachable((9, 0))
    assert not board.reachable((0, 0))


def test_a_turn_is_two_distinct_empty_cells_the_second_may_reach_from_the_first():
    board = position([(0, 0)])
    assert board.is_legal([(1, 0), (2, 0)])
    assert board.is_legal([(8, 0), (16, 0)])
    assert not board.is_legal([(1, 0), (1, 0)])
    assert not board.is_legal([(0, 0), (1, 0)])
    assert not board.is_legal([(9, 0), (1, 0)])
    assert not board.is_legal([(1, 0)])


def test_six_in_a_line_wins_on_every_axis_and_so_does_an_overline():
    for dq, dr in AXES:
        five = position([(k * dq, k * dr) for k in range(5)], to_move="x")
        four = position([(k * dq, k * dr) for k in range(4)], to_move="x")
        assert five.completes_six((5 * dq, 5 * dr), "x")
        assert not four.completes_six((4 * dq, 4 * dr), "x")
    split = position([(-3, 0), (-2, 0), (-1, 0), (1, 0), (2, 0), (3, 0)], to_move="x")
    assert split.completes_six((0, 0), "x")
    assert not split.completes_six((0, 0), "o")


def test_a_first_stone_that_completes_six_ends_the_turn():
    board = position(row(5), [(0, 3), (1, 3), (2, 3), (3, 3)], to_move="x")
    assert board.is_legal([(5, 0), (40, 40)])
    after, won = board.play([(5, 0), (6, 6)])
    assert won
    assert (6, 6) not in after.stones


def test_threats_are_the_windows_one_turn_from_six_that_the_other_side_has_not_blocked():
    open_four = position(row(4), to_move="o")
    assert sorted(map(sorted, open_four.threats("x"))) == [
        [(-2, 0), (-1, 0)],
        [(-1, 0), (4, 0)],
        [(4, 0), (5, 0)],
    ]
    blocked = position(row(4), [(-1, 0), (4, 0)], to_move="x")
    assert blocked.threats("x") == []
    assert open_four.threats("o") == []


def test_the_winning_turn_completes_six():
    board = position(row(4), [(0, 3), (2, 3), (4, 3)], to_move="x")
    turn = board.winning_turn()
    assert turn is not None
    assert board.play(turn)[1]
    assert position([(0, 0)]).winning_turn() is None


def test_the_fallback_turn_wins_when_it_can_and_blocks_when_it_must():
    winning = position([(0, 5), (3, 5)], row(4, r=0), to_move="o")
    assert winning.play(winning.fallback_turn())[1]
    defending = position(row(4), [(0, 5), (3, 5)], to_move="o")
    after, won = defending.play(defending.fallback_turn())
    assert not won
    assert after.threats("x") == []


def test_the_fallback_turn_is_legal_on_a_quiet_board():
    board = position([(0, 0)])
    assert board.is_legal(board.fallback_turn())


def test_a_six_into_an_enclosed_cell_still_makes_a_whole_turn():
    # Every neighbour of the winning cell (2, 0) is taken; the turn's second stone goes elsewhere.
    x = [(0, 0), (1, 0), (3, 0), (4, 0), (5, 0)]
    o = [(2, 1), (1, 1), (2, -1), (3, -1)]
    board = position(x, o, to_move="x")
    turn = board.winning_turn()
    assert turn is not None
    assert board.is_legal(turn)
    assert board.play(turn)[1]
