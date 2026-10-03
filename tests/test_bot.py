import contextlib
import json
import re
import threading
import time
import tomllib
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from websockets.exceptions import InvalidStatus
from websockets.sync.server import serve

from hexo_bridge import Bot, Engine, Evaluation, Game, Level, Line, bot, sessions
from hexo_bridge.client import ApiError, Client
from hexo_bridge.position import Position
from hexo_bridge.sessions import AnalysisSession, GameSession

ACCEPTS = {"turnMs": [5000, 300000], "match": True, "unlimited": True}
DECLARATION = {"accepts": ACCEPTS, "analyzer": {"lines": 1, "maxSeconds": 2}}
CUTS = {"inaccuracy": 0.1, "mistake": 0.2, "blunder": 0.3}
VALUES = {"scale": 1, "meaning": "expected", "cuts": CUTS}
# Hexo-Bot-Api's examples/stream.ndjson at tag v0.12.0, verbatim.
FIXTURE = Path(__file__).parent / "fixtures" / "stream.ndjson"
PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"
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
        # A server older than a declaration key refuses a body that holds it.
        self.refuses = lambda body: False
        # Headers on every HTTP answer, as a server that deprecates a route sends them.
        self.headers = {}
        self.agents = []
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
                server.agents.append(("PATCH", self.headers["User-Agent"]))
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if server.refuses(body):
                    self.reply({"error": "the declaration fails validation"}, 400)
                    return
                server.declarations.append((self.headers["Authorization"], body))
                self.reply(body)

            def do_POST(self):
                server.agents.append(("POST", self.headers["User-Agent"]))
                server.accepted.append(self.path)
                self.reply({"ok": True})

            def do_GET(self):
                server.agents.append(("GET", self.headers["User-Agent"]))
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

            def reply(self, body, status=200):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for name, value in server.headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(data)

        return Handler

    def check_token(self, connection, request):
        kind = "game" if request.path.startswith("/game") else "analysis"
        self.agents.append((kind, request.headers.get("User-Agent")))
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
def server():
    server = FakeServer()
    yield server
    server.close()


@pytest.fixture(scope="module")
def played():
    """The fake server after a bot played its game and answered its readings, then stopped."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(bot, "REOPEN_SECONDS", 0.5)
        server = FakeServer()
        runner = Bot(SlowReader, url=server.url, token="hxo_test", declaration=DECLARATION)
        thread = threading.Thread(target=runner.run, daemon=True)
        thread.start()
        assert server.game_done.wait(15)
        assert server.analysis_done.wait(10)
        runner.stop()
        server.finished.set()
        thread.join(5)
        assert not thread.is_alive()
        yield server
        server.close()


def cells(pieces):
    return tuple((piece["q"], piece["r"]) for piece in pieces)


def test_a_bot_plays_on_through_an_expired_token_and_answers_only_the_reading_not_called_off(
    played,
):
    server = played
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


def spec_events():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


@pytest.mark.parametrize("move_requests", [True, False], ids=["sent", "no longer sent"])
def test_every_event_in_the_spec_example_is_handled_whether_or_not_move_requests_come(
    monkeypatch, move_requests
):
    started = []
    accepted = []
    monkeypatch.setattr(GameSession, "start", lambda self: started.append(self))
    monkeypatch.setattr(AnalysisSession, "start", lambda self: started.append(self))
    monkeypatch.setattr(Client, "accept", lambda self, challenge_id: accepted.append(challenge_id))
    runner = Bot(SlowReader, url="http://127.0.0.1:9", token="hxo_test", declaration=DECLARATION)
    events = spec_events()
    assert any(event["type"] == "moveRequest" for event in events)
    if not move_requests:
        events = [event for event in events if event["type"] != "moveRequest"]
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


def test_every_request_and_handshake_names_the_bridge_and_its_version(played):
    version = tomllib.loads(PYPROJECT.read_text())["project"]["version"]
    assert {kind for kind, _ in played.agents} == {"PATCH", "GET", "POST", "game", "analysis"}
    assert {agent for _, agent in played.agents} == {f"hexo-bridge/{version}"}


@pytest.mark.parametrize(
    "values",
    [
        VALUES,
        {},
        {"meaning": "raw"},
        {"scale": 1_000_000, "cuts": {"inaccuracy": 0.5, "mistake": 1, "blunder": 2}},
    ],
)
def test_values_within_the_apis_bounds_pass_the_check_before_sending(values):
    bot.check_declaration({"accepts": ACCEPTS, "analyzer": {"lines": 1, "values": values}})


@pytest.mark.parametrize(
    ("values", "field"),
    [
        ("expected", "analyzer.values"),
        ({"scales": 1}, "analyzer.values"),
        ({"scale": 0}, "analyzer.values.scale"),
        ({"scale": 1_000_001}, "analyzer.values.scale"),
        ({"scale": True}, "analyzer.values.scale"),
        ({"scale": None}, "analyzer.values.scale"),
        ({"meaning": "calibrated"}, "analyzer.values.meaning"),
        ({"cuts": None}, "analyzer.values.cuts"),
        ({"cuts": {"inaccuracy": 0.1, "mistake": 0.2}}, "analyzer.values.cuts"),
        ({"cuts": {**CUTS, "swindle": 0.4}}, "analyzer.values.cuts"),
        ({"cuts": {**CUTS, "inaccuracy": 0}}, "analyzer.values.cuts.inaccuracy"),
        ({"cuts": {**CUTS, "mistake": float("nan")}}, "analyzer.values.cuts.mistake"),
        ({"cuts": {**CUTS, "blunder": 2.5}}, "analyzer.values.cuts.blunder"),
        ({"cuts": {"inaccuracy": 0.3, "mistake": 0.2, "blunder": 0.1}}, "analyzer.values.cuts"),
        ({"cuts": {"inaccuracy": 0.2, "mistake": 0.2, "blunder": 0.3}}, "analyzer.values.cuts"),
    ],
)
def test_values_out_of_the_apis_bounds_are_refused_naming_the_field(values, field):
    declaration = {"accepts": ACCEPTS, "analyzer": {"lines": 1, "values": values}}
    with pytest.raises(ValueError, match=f"^{re.escape(field)} "):
        bot.check_declaration(declaration)


def test_an_older_server_gets_the_declaration_without_the_keys_it_predates_newest_first(server):
    analyzer = {**DECLARATION["analyzer"], "values": VALUES}
    declaration = {"accepts": ACCEPTS, "levels": None, "analyzer": analyzer}
    client = Client(server.url, "hxo_test")

    # Bot API 0.10.0 takes an analyzer, but not its values.
    server.refuses = lambda body: "values" in (body.get("analyzer") or {})
    client.declare(declaration)
    assert server.declarations[-1][1] == {**declaration, "analyzer": DECLARATION["analyzer"]}

    # Bot API 0.9.0 takes no analyzer at all.
    server.refuses = lambda body: "analyzer" in body
    client.declare(declaration)
    assert server.declarations[-1][1] == {"accepts": ACCEPTS, "levels": None}

    # The caller's declaration is left whole, so the next stream open sends it all again.
    assert analyzer["values"] == VALUES


def test_a_challenge_from_a_bot_of_the_same_owner_is_accepted_and_played_unrated(monkeypatch):
    started = []
    accepted = []
    monkeypatch.setattr(GameSession, "start", lambda self: started.append(self))
    monkeypatch.setattr(Client, "accept", lambda self, challenge_id: accepted.append(challenge_id))
    runner = Bot(SlowReader, url="http://127.0.0.1:9", token="hxo_test", declaration=DECLARATION)
    events = spec_events()
    challenge = next(event for event in events if event["type"] == "challenge")
    sibling = {**challenge["challenge"]["destUser"], "name": "alice-bot-2"}
    runner.handle({**challenge, "challenge": {**challenge["challenge"], "challenger": sibling}})
    deadline = time.monotonic() + 2
    while not accepted and time.monotonic() < deadline:
        time.sleep(0.01)
    assert accepted == [challenge["challenge"]["challengeId"]]

    start = next(event for event in events if event["type"] == "gameStart")
    runner.handle({**start, "opponent": sibling, "rated": False})
    (session,) = started
    assert (session.game.opponent, session.game.rated) == ("alice-bot-2", False)
    answer = session._answer(
        START, {"type": "move_request", "side": "o", "move_time_limit": 5, "request_id": 0}
    )
    assert answer["request_id"] == 0 and START.is_legal(cells(answer["move"]["pieces"]))


def test_a_declared_about_or_repo_url_is_still_sent_with_one_warning_per_run(
    server, monkeypatch, caplog
):
    declaration = {**DECLARATION, "about": "Plays beside the stones.", "repoUrl": ""}
    runner = Bot(SlowReader, url=server.url, token="hxo_test", declaration=declaration)
    opens = []

    def stream(self, open_for_challenges=True, stop=None):
        opens.append(open_for_challenges)
        if len(opens) == 2:
            runner.stop()
        return iter(())

    monkeypatch.setattr(Client, "stream", stream)
    monkeypatch.setattr(bot, "REDIAL_FIRST_SECONDS", 0.01)
    runner.run()

    assert [body for _, body in server.declarations] == [{"levels": None, **declaration}] * 2
    warnings = [record.getMessage() for record in caplog.records if "0.12.0" in record.getMessage()]
    assert len(warnings) == 1 and "about and repoUrl" in warnings[0]
    assert "bot page" in warnings[0]


def notes(caplog):
    return [
        record.getMessage() for record in caplog.records if " deprecated: " in record.getMessage()
    ]


def test_a_deprecation_header_is_logged_once_per_route_and_value_by_its_pattern(
    server, monkeypatch, caplog
):
    monkeypatch.setattr("hexo_bridge.client._noted", set())
    server.headers = {"Deprecation": "@1798761600", "Sunset": "Fri, 01 Jan 2027 00:00:00 GMT"}
    client = Client(server.url, "hxo_secret")
    client.accept("c_secret1")
    client.accept("c_secret2")
    client.resign("g_secret", "hgs_secret")
    server.headers = {"Deprecation": "@1801440000"}
    client.accept("c_secret3")
    server.refuses = lambda body: True
    with pytest.raises(ApiError):
        client.declare({"accepts": ACCEPTS})

    sunset = ", Sunset Fri, 01 Jan 2027 00:00:00 GMT"
    assert notes(caplog) == [
        f"the server marks POST /api/bot/challenge/{{challengeId}}/accept deprecated: "
        f"Deprecation @1798761600{sunset}",
        f"the server marks POST /api/bot/game/{{gameId}}/resign deprecated: "
        f"Deprecation @1798761600{sunset}",
        "the server marks POST /api/bot/challenge/{challengeId}/accept deprecated: "
        "Deprecation @1801440000",
        "the server marks PATCH /api/bot/account deprecated: Deprecation @1801440000",
    ]
    assert server.accepted[:2] == [
        "/api/bot/challenge/c_secret1/accept",
        "/api/bot/challenge/c_secret2/accept",
    ]
    assert not any(
        "secret" in record.getMessage() or "127.0.0.1" in record.getMessage()
        for record in caplog.records
    )


def test_a_deprecation_on_a_session_handshake_is_logged_by_its_route_without_the_token(
    monkeypatch, caplog
):
    monkeypatch.setattr("hexo_bridge.client._noted", set())

    def refuse(connection, request):
        if request.path.endswith("has_gone"):
            return connection.respond(401, "the token expired\n")
        return None

    def mark(connection, request, response):
        response.headers["Deprecation"] = "@1798761600"

    with serve(
        lambda ws: None, "127.0.0.1", 0, process_request=refuse, process_response=mark
    ) as ws:
        threading.Thread(target=ws.serve_forever, daemon=True).start()
        origin = f"ws://127.0.0.1:{ws.socket.getsockname()[1]}"
        for _ in range(2):
            with sessions.dial(
                f"{origin}/api/bot/game/g_1/socket?token=hgs_secret", sessions.GAME_ROUTE
            ):
                pass
        with pytest.raises(InvalidStatus):
            sessions.dial(
                f"{origin}/api/bot/analysis/socket?token=has_gone", sessions.ANALYSIS_ROUTE
            )
        ws.shutdown()

    assert notes(caplog) == [
        "the server marks GET /api/bot/game/{gameId}/socket deprecated: Deprecation @1798761600",
        "the server marks GET /api/bot/analysis/socket deprecated: Deprecation @1798761600",
    ]
    assert not any("hgs_secret" in record.getMessage() for record in caplog.records)
