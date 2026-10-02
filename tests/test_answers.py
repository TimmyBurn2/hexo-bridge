import json
import math

import pytest

from hexo_bridge.answers import Evaluation, Line, as_lines, settle
from hexo_bridge.position import Position


def position(x, o=(), to_move="o"):
    stones = {cell: "x" for cell in x}
    stones.update({cell: "o" for cell in o})
    return Position(stones, to_move)


QUIET = position([(0, 0), (1, 1), (2, 2)], [(1, 0), (-1, 0)], to_move="o")


def best(board, line, need=True):
    lines, notes = settle(board, [line], 3, need)
    return lines[0], notes


def test_a_line_that_completes_six_is_valued_for_its_mover():
    board = position([(q, 0) for q in range(5)], [(0, 3), (2, 3), (4, 3), (6, 3)], to_move="x")
    line, notes = best(board, Line(((5, 0), (9, 9)), Evaluation(heuristic=-0.5)))
    assert line.evaluation == Evaluation(win_in=1)
    assert notes


def test_a_best_line_that_misses_a_six_the_mover_completes_is_replaced_by_it():
    board = position([(q, 0) for q in range(5)], [(0, 3), (2, 3), (4, 3), (6, 3)], to_move="x")
    line, _ = best(board, Line(((0, 1), (1, 1)), Evaluation(heuristic=0.2)))
    assert board.play(line.turn)[1]
    assert line.evaluation == Evaluation(win_in=1)


def test_a_six_the_other_side_completes_next_turn_is_valued_for_that_side():
    two_fours = position(
        [(q, 0) for q in range(4)] + [(q, 3) for q in range(4)], [(0, 7), (5, 7)], to_move="o"
    )
    line, notes = best(two_fours, Line(((-1, 0), (4, 0)), Evaluation(heuristic=-0.9)))
    assert line.evaluation == Evaluation(win_in=1)
    assert notes


def test_win_in_must_fall_on_the_winners_turn_and_a_win_in_one_needs_a_six_on_the_board():
    line = Line(((0, 3), (3, 0)))
    # After o's line x is to move: odd counts are x's turns, even counts o's.
    for win_in, kept in ((-2, True), (3, True), (2, False), (-3, False), (1, False)):
        settled, _ = best(QUIET, Line(line.turn, Evaluation(win_in=win_in)))
        assert (settled.evaluation.win_in == win_in) is kept, win_in


def test_a_reading_always_carries_an_evaluation_and_a_game_move_need_not():
    line, _ = best(QUIET, Line(((0, 3), (3, 0))), need=True)
    assert line.evaluation == Evaluation(heuristic=0.0)
    line, _ = best(QUIET, Line(((0, 3), (3, 0))), need=False)
    assert line.evaluation is None


def test_values_past_the_servers_bounds_are_clamped_or_dropped():
    assert best(QUIET, Line(((0, 3), (3, 0)), Evaluation(heuristic=2e6)))[
        0
    ].evaluation == Evaluation(heuristic=1e6)
    assert best(QUIET, Line(((0, 3), (3, 0)), Evaluation(heuristic=math.inf)))[
        0
    ].evaluation == Evaluation(heuristic=0.0)
    assert best(QUIET, Line(((0, 3), (3, 0)), Evaluation(win_in=0)))[0].evaluation == Evaluation(
        heuristic=0.0
    )


def test_an_illegal_best_line_becomes_the_fallback_turn():
    line, notes = best(QUIET, Line(((0, 0), (5, 5)), Evaluation(heuristic=0.1)))
    assert QUIET.is_legal(line.turn)
    assert notes


def test_illegal_repeated_or_unvalued_considerations_drop_and_the_rest_stop_at_the_limit():
    lines = [
        Line(((0, 3), (3, 0)), Evaluation(heuristic=0.1)),
        Line(((3, 0), (0, 3)), Evaluation(heuristic=0.1)),
        Line(((0, 0), (0, 4)), Evaluation(heuristic=0.1)),
        Line(((0, 4), (4, 0))),
        Line(((0, 5), (5, 0)), Evaluation(heuristic=0.2)),
        Line(((0, 6), (6, 0)), Evaluation(heuristic=0.3)),
    ]
    settled, _ = settle(QUIET, lines, 2, True)
    assert [line.turn for line in settled] == [((0, 3), (3, 0)), ((0, 5), (5, 0))]


def test_an_answer_may_be_a_bare_turn_a_line_or_lines():
    assert as_lines([(1, 0), (2, 0)]) == [Line(((1, 0), (2, 0)))]
    assert as_lines(Line(((1, 0), (2, 0)))) == [Line(((1, 0), (2, 0)))]
    with pytest.raises(TypeError):
        as_lines([(1, 0)])


def test_a_game_move_that_misses_a_six_stays_the_engines_own():
    board = position([(q, 0) for q in range(5)], [(0, 3), (2, 3), (4, 3), (6, 3)], to_move="x")
    line, _ = best(board, Line(((0, 1), (1, 1))), need=False)
    assert line.turn == ((0, 1), (1, 1))


def test_engine_numbers_of_any_numeric_type_come_out_as_plain_json():
    from decimal import Decimal
    from fractions import Fraction

    line, _ = best(
        QUIET, Line(((Fraction(0), 3.0), (3, Fraction(0))), Evaluation(heuristic=Decimal("0.5")))
    )
    assert json.loads(json.dumps(line.wire())) == {
        "pieces": [{"q": 0, "r": 3}, {"q": 3, "r": 0}],
        "evaluation": {"heuristic": 0.5},
    }
    half_cell, _ = best(QUIET, Line(((0.5, 3), (3, 0)), Evaluation(heuristic=0.1)))
    assert QUIET.is_legal(half_cell.turn)
