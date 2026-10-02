import contextlib
import json
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from websockets.sync.server import serve

from hexo_bridge import Bot, Engine, Evaluation, Game, Level, Line, bot, sessions
from hexo_bridge.client import Client
from hexo_bridge.position import Position
from hexo_bridge.sessions import AnalysisSession, GameSession

ACCEPTS = {"turnMs": [5000, 300000], "match": True, "unlimited": True}
DECLARATION = {"accepts": ACCEPTS, "analyzer": {"lines": 1, "maxSeconds": 2}}
FIXTURE = Path(__file__).parent / "fixtures" / "stream.ndjson"
GAME = Game("g_1", "o", "rival", False, Level(None), {"mode": "turn", "turnTimeMs": 5000}, 1)
START = Position({(0, 0): "x"}, "o")


class SlowReader(Engine):
    def move(self, position, request):
        return Line(position.fallback_turn(), Evaluation(heuristic=0.1))

    def analyze(self, position, request):
        # A search that runs until it is called off, or for half a second.
        request.stop.wait(0.5)
        return Line(position.fallback_turn(), Evaluation(heuristic=0.25))


class FakeServer:
    """The Bot API's HTTP side and its two websockets, scripted for one game and two readings.

    The game's first connection drops after one move, and its token then expires, as a
    game token does 60 s after its gameStart: the game ends only on a reopened stream's token.
    """

    def __init__(self):
        self.declarations = []
        self.accepted = []
        self.game_answers = []
        self.readings = []
        self.streams = 0
        self.expired = set()
        self.finished = threading.Event()
        self.game_done = threading.Event()
        self.analysis_done = threading.Event()
        self.ws = serve(self.socket, "127.0.0.1", 0, process_request=self.check_token)
        self.ws_url = f"ws://127.0.0.1:{self.ws.socket.getsockname()[1]}"
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), self.handler())
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        for server in (self.ws, self.http):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    def close(self):
        self.finished.set()
        self.http.shutdown()
        self.ws.shutdown()

    def game_start(self, token):
        return {
            "type": "gameStart",
            "gameId": "g_1",
            "side": "o",
            "opponent": {"name": "rival", "rating": 1500, "provisional": True},
            "timeControl": {"mode": "turn", "turnTimeMs": 5000},
            "openingPlies": 1,
            "rated": False,
            "level": None,
            "engine": {"socketUrl": f"{self.ws_url}/game", "token": token},
        }

    def events(self):
        if self.streams > 1:
            return [self.game_start("hgs_2")]
        return [
            {
                "type": "analysisSession",
                "engine": {"socketUrl": f"{self.ws_url}/analysis", "token": "has_1"},
            },
            {"type": "challenge", "challenge": {"challengeId": "c_1"}},
            self.game_start("hgs_1"),
        ]

    def handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.declarations.append((self.headers["Authorization"], body))
                self.reply(body)

            def do_POST(self):
                server.accepted.append(self.path)
                self.reply({"ok": True})

            def do_GET(self):
                server.streams += 1
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
                self.end_headers()
                for event in server.events():
                    self.wfile.write((json.dumps(event) + "\n").encode())
                    self.wfile.flush()
                while not server.finished.wait(0.2):
                    self.wfile.write(b"\n")
                    self.wfile.flush()

            def reply(self, body):
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return Handler

    def check_token(self, connection, request):
        if request.path.split("token=")[-1] in self.expired:
            return connection.respond(404, "the token expired\n")
        return None

    def socket(self, connection):
        if connection.request.path.startswith("/game"):
            self.play(connection)
        else:
            self.analyze(connection)

    def play(self, ws):
        ws.send(json.dumps({"type": "setup", "board": {"cells": [{"q": 0, "r": 0, "p": "x"}]}}))
        if ws.request.path.endswith("hgs_1"):
            ws.send(
                json.dumps(
                    {
                        "type": "move_request",
                        "side": "o",
                        "previous": [],
                        "move_time_limit": 5,
                        "request_id": 0,
                    }
                )
            )
            self.game_answers.append(json.loads(ws.recv(timeout=5)))
            self.expired.add("hgs_1")
            ws.close(code=1011)
            return
        first = self.game_answers[0]["move"]["pieces"]
        previous = [
            {"side": "o", "pieces": first},
            {"side": "x", "pieces": [{"q": -3, "r": 0}, {"q": -3, "r": 1}]},
        ]
        ws.send(
            json.dumps(
                {
                    "type": "move_request",
                    "side": "o",
                    "previous": previous,
                    "move_time_limit": 5,
                    "request_id": 1,
                }
            )
        )
        self.game_answers.append(json.loads(ws.recv(timeout=5)))
        self.game_done.set()

    def analyze(self, ws):
        cells = [{"q": 0, "r": 0, "p": "x"}, {"q": 1, "r": 0, "p": "o"}, {"q": 2, "r": 0, "p": "o"}]
        for request_id in (1, 2):
            ws.send(json.dumps({"type": "setup", "board": {"cells": cells}}))
            ws.send(
                json.dumps(
                    {
                        "type": "move_request",
                        "side": "x",
                        "previous": [],
                        "move_time_limit": 2,
                        "request_id": request_id,
                    }
                )
            )
            if request_id == 1:
                time.sleep(0.1)
                ws.send(json.dumps({"type": "interrupt"}))
        self.readings.append(json.loads(ws.recv(timeout=5)))
        with contextlib.suppress(TimeoutError):
            self.readings.append(json.loads(ws.recv(timeout=1)))
        self.analysis_done.set()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(bot, "REOPEN_SECONDS", 0.5)
    server = FakeServer()
    yield server
    server.close()


def cells(pieces):
    return tuple((piece["q"], piece["r"]) for piece in pieces)


def test_a_bot_plays_on_through_an_expired_token_and_answers_only_the_reading_not_called_off(
    server,
):
    runner = Bot(SlowReader, url=server.url, token="hxo_test", declaration=DECLARATION)
    thread = threading.Thread(target=runner.run, daemon=True)
    thread.start()
    assert server.game_done.wait(15)
    assert server.analysis_done.wait(10)
    runner.stop()
    server.finished.set()
    thread.join(5)
    assert not thread.is_alive()

    # Absent keys go as null, since the server keeps whatever a declaration leaves out.
    assert server.declarations[0] == ("Bearer hxo_test", {"levels": None, **DECLARATION})
    assert server.accepted == ["/api/bot/challenge/c_1/accept"]
    assert server.streams >= 2

    first, second = server.game_answers
    assert first["request_id"] == 0 and START.is_legal(cells(first["move"]["pieces"]))
    after, _ = START.play(cells(first["move"]["pieces"]))
    after, _ = Position(after.stones, "x").play(((-3, 0), (-3, 1)))
    assert second["request_id"] == 1 and after.is_legal(cells(second["move"]["pieces"]))

    assert [reading["request_id"] for reading in server.readings] == [2]
    assert server.readings[0]["move"]["evaluation"] == {"heuristic": 0.25}


def test_a_move_past_the_clock_is_the_fallback_turn_and_the_engine_is_replaced_off_the_clock():
    made = []

    class Stuck(Engine):
        def __init__(self, game):
            super().__init__(game)
            self.killed = False
            made.append(self)

        def move(self, position, request):
            request.stop.wait(5)
            return position.fallback_turn()

        def kill(self):
            self.killed = True

    session = GameSession(None, GAME, "hgs_1", "ws://unused", Stuck, 0.5, lambda: None)
    started = time.monotonic()
    answer = session._answer(
        START, {"type": "move_request", "side": "o", "move_time_limit": 1.0, "request_id": 3}
    )
    assert time.monotonic() - started < 0.9
    assert answer["request_id"] == 3 and START.is_legal(cells(answer["move"]["pieces"]))
    assert made[0].killed
    assert session.warming.result(timeout=2) is made[1]


def test_a_reading_past_its_limit_is_answered_from_the_rules(monkeypatch):
    monkeypatch.setattr(sessions, "ANALYSIS_GRACE_SECONDS", 0.1)

    class Deaf(Engine):
        def analyze(self, position, request):
            time.sleep(2)
            return Line(position.fallback_turn(), Evaluation(heuristic=0.5))

    session = AnalysisSession("ws://unused", Deaf, 1, lambda: None, None)
    sent = []
    session.socket = types.SimpleNamespace(send=sent.append)
    board = Position({(0, 0): "x", (1, 0): "o", (2, 0): "o"}, "x")
    session._read(
        board, {"type": "move_request", "side": "x", "move_time_limit": 0.2, "request_id": 7}
    )
    deadline = time.monotonic() + 2
    while not sent and time.monotonic() < deadline:
        time.sleep(0.02)
    answer = json.loads(sent[0])
    assert answer["request_id"] == 7
    assert board.is_legal(cells(answer["move"]["pieces"]))
    assert answer["move"]["evaluation"] == {"heuristic": 0.0}


def test_every_event_in_the_spec_example_is_handled(monkeypatch):
    started = []
    accepted = []
    monkeypatch.setattr(GameSession, "start", lambda self: started.append(self))
    monkeypatch.setattr(AnalysisSession, "start", lambda self: started.append(self))
    monkeypatch.setattr(Client, "accept", lambda self, challenge_id: accepted.append(challenge_id))
    runner = Bot(SlowReader, url="http://127.0.0.1:9", token="hxo_test", declaration=DECLARATION)
    events = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]
    for event in events:
        runner.handle(event)
    deadline = time.monotonic() + 2
    while not accepted and time.monotonic() < deadline:
        time.sleep(0.01)

    start = next(event for event in events if event["type"] == "gameStart")
    games = [session.game for session in started if isinstance(session, GameSession)]
    assert [(game.id, game.side) for game in games] == [(start["gameId"], start["side"])]
    assert any(isinstance(session, AnalysisSession) for session in started)
    assert accepted == [
        next(event for event in events if event["type"] == "challenge")["challenge"]["challengeId"]
    ]
    assert runner.games == {}


def test_a_declaration_the_server_would_refuse_is_refused_before_it_is_sent():
    with pytest.raises(ValueError):
        Bot(SlowReader, url="http://127.0.0.1:9", token="hxo_test", declaration={})
    one_level = {
        "accepts": ACCEPTS,
        "levels": {"default": "a", "list": [{"id": "a", "label": "a"}]},
    }
    with pytest.raises(ValueError):
        Bot(SlowReader, url="http://127.0.0.1:9", token="hxo_test", declaration=one_level)

    class MoveOnly(Engine):
        def move(self, position, request):
            return position.fallback_turn()

    with pytest.raises(TypeError):
        Bot(MoveOnly, url="http://127.0.0.1:9", token="hxo_test", declaration=DECLARATION)
