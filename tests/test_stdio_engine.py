import sys
from pathlib import Path

import pytest

from hexo_bridge.answers import Evaluation
from hexo_bridge.engine import AnalysisRequest, Game, Level, MoveRequest, StdioEngine
from hexo_bridge.position import Position

EXAMPLE = Path(__file__).parent.parent / "examples" / "random_engine.py"
GAME = Game("g_1", "o", "someone", False, Level(None), {"mode": "unlimited"}, 1)
START = Position({(0, 0): "x"}, "o")


@pytest.fixture
def engine():
    engine = StdioEngine(GAME, [sys.executable, str(EXAMPLE)])
    yield engine
    engine.close()


def test_the_example_engine_answers_a_move_and_a_reading(engine):
    move = engine.move(START, MoveRequest(GAME, None))
    assert START.is_legal(move[0].turn)
    reading = engine.analyze(START, AnalysisRequest(1, 1))
    assert reading[0].evaluation == Evaluation(heuristic=0.0)


def test_a_killed_engine_starts_again_on_the_next_request(engine):
    engine.move(START, MoveRequest(GAME, None))
    engine.kill()
    engine.process.wait()
    assert START.is_legal(engine.move(START, MoveRequest(GAME, None))[0].turn)


def test_an_engine_that_exits_without_answering_raises():
    silent = StdioEngine(GAME, [sys.executable, "-c", "import sys; sys.stdin.readline()"])
    with pytest.raises(RuntimeError):
        silent.move(START, MoveRequest(GAME, None))
    silent.close()


ANSWER = (
    "    q = max(cell['q'] for cell in json.loads(line)['board']['cells']) + 1\n"
    "    move = {'pieces': [{'q': q, 'r': 0}, {'q': q + 1, 'r': 0}]}\n"
    "    print(json.dumps({'move': move}), flush=True)\n"
)
LATER = Position({(0, 0): "x", (1, 0): "o", (2, 0): "o"}, "x")


def test_lines_that_are_not_json_objects_are_skipped_as_the_engine_talking(tmp_path):
    talker = tmp_path / "talker.py"
    talker.write_text(
        "import json, sys\nprint('starting up', flush=True)\n"
        "for line in sys.stdin:\n    print('thinking...', flush=True)\n" + ANSWER
    )
    engine = StdioEngine(GAME, [sys.executable, str(talker)])
    assert engine.move(START, MoveRequest(GAME, None))[0].turn == ((1, 0), (2, 0))
    assert engine.move(LATER, MoveRequest(GAME, None))[0].turn == ((3, 0), (4, 0))
    engine.close()


def test_after_a_malformed_answer_the_next_answer_is_for_its_own_board(tmp_path):
    stray = tmp_path / "stray.py"
    stray.write_text(
        "import json, sys\nfor count, line in enumerate(sys.stdin):\n"
        "    if count == 1:\n        print(json.dumps({'note': 'thinking'}), flush=True)\n" + ANSWER
    )
    engine = StdioEngine(GAME, [sys.executable, str(stray)])
    assert engine.move(START, MoveRequest(GAME, None))[0].turn == ((1, 0), (2, 0))
    with pytest.raises(KeyError):
        engine.move(START, MoveRequest(GAME, None))
    # Out of step, the stale answer for the second board, (1, 0) and (2, 0), would come back.
    assert engine.move(LATER, MoveRequest(GAME, None))[0].turn == ((3, 0), (4, 0))
    engine.close()
