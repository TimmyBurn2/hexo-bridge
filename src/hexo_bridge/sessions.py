"""A game's websocket and the analyzer's: htttx basic_websocket v1-alpha, the bot answering."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from websockets.exceptions import InvalidStatus, WebSocketException
from websockets.sync.client import ClientConnection, connect

from hexo_bridge.answers import Evaluation, Line, Resign, as_lines, settle
from hexo_bridge.client import USER_AGENT, ApiError, Client
from hexo_bridge.engine import AnalysisRequest, Engine, Game, MoveRequest
from hexo_bridge.position import Cell, Position, Side

log = logging.getLogger("hexo_bridge")

EngineFactory = Callable[[Game | None], Engine]

REDIAL_FIRST_SECONDS = 1
REDIAL_CAP_SECONDS = 8
# A game answer carries its move and the two considerations the server publishes with the game.
GAME_LINES = 3
# A heartbeat saying the server waits, read while the bot is idle, means a request went
# missing; one read this soon after an answer was only queued behind the search.
STALE_HEARTBEAT_SECONDS = 1.0
# The server fails a reading answered more than 3 s past its limit.
ANALYSIS_GRACE_SECONDS = 1.5
# A session token opens its session for 60 s after the line that carried it.
TOKEN_SECONDS = 55
# Long enough for a reopened stream, held to one open in 10 s, to replay the game.
FRESH_TOKEN_SECONDS = 30


def start(call: Callable[..., Any], *args: Any) -> concurrent.futures.Future[Any]:
    """Run a call on its own thread, so a session can stop waiting for it."""
    future: concurrent.futures.Future[Any] = concurrent.futures.Future()

    def target() -> None:
        try:
            future.set_result(call(*args))
        except BaseException as error:
            future.set_exception(error)

    threading.Thread(target=target, daemon=True).start()
    return future


def close_when_done(future: concurrent.futures.Future[Any]) -> None:
    """Close the engine a future makes, once it has made it."""

    def done(made: concurrent.futures.Future[Any]) -> None:
        if made.exception() is None and made.result() is not None:
            made.result().close()

    future.add_done_callback(done)


def retire(engine: Engine, call: concurrent.futures.Future[Any]) -> None:
    """Stop a call that outlived its request; its engine is closed once the call returns."""
    engine.kill()
    call.add_done_callback(lambda _: engine.close())


def seconds_left(deadline: float | None) -> float | None:
    return None if deadline is None else max(0.0, deadline - time.monotonic())


def read_stones(cells: list[dict[str, Any]]) -> dict[Cell, Side]:
    return {(cell["q"], cell["r"]): cell["p"] for cell in cells}


def response(lines: list[Line], packet: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"type": "move_response", "move": lines[0].wire()}
    if lines[1:]:
        out["considerations"] = [line.wire() for line in lines[1:]]
    if "request_id" in packet:
        out["request_id"] = packet["request_id"]
    return out


class GameSession(threading.Thread):
    """One game's engine session: answers every move request, and redials a dropped connection.

    A game token opens the session only for 60 s after its `gameStart`; past that, a redial
    asks the bot to reopen its stream, which replays the game with a fresh token.
    """

    def __init__(
        self,
        client: Client,
        game: Game,
        token: str,
        url: str,
        factory: EngineFactory,
        reserve_seconds: float,
        reopen: Callable[[], None],
    ) -> None:
        super().__init__(daemon=True, name=f"game {game.id}")
        self.client = client
        self.game = game
        self.token = token
        self.url = url
        self.factory = factory
        self.reserve_seconds = reserve_seconds
        self.reopen = reopen
        self.closed = threading.Event()
        self.fresh = threading.Event()
        self.socket: ClientConnection | None = None
        self.wait = REDIAL_FIRST_SECONDS
        # The engine is made on its own thread, so making it never runs down a move's clock.
        self.warming = start(factory, game)

    def close(self) -> None:
        self.closed.set()
        self.fresh.set()
        socket = self.socket
        if socket is not None:
            socket.close()

    def refresh(self, token: str, url: str) -> None:
        """Take the fresh token a replayed `gameStart` carries."""
        self.token, self.url = token, url
        self.fresh.set()

    def run(self) -> None:
        try:
            while not self.closed.is_set() and self._play():
                if self.closed.wait(self.wait):
                    break
                self.wait = min(self.wait * 2, REDIAL_CAP_SECONDS)
        finally:
            close_when_done(self.warming)

    def _play(self) -> bool:
        """One connection; True when it should be dialed again."""
        try:
            with connect(self.url, open_timeout=10, user_agent_header=USER_AGENT) as socket:
                self.socket = socket
                self.wait = REDIAL_FIRST_SECONDS
                stones: dict[Cell, Side] = {}
                answered = time.monotonic()
                for message in socket:
                    packet = json.loads(message)
                    kind = packet.get("type")
                    if kind == "setup":
                        stones = read_stones(packet["board"]["cells"])
                    elif kind == "move_request":
                        for turn in packet.get("previous") or []:
                            played = [{**piece, "p": turn["side"]} for piece in turn["pieces"]]
                            stones.update(read_stones(played))
                        answer = self._answer(Position(dict(stones), packet["side"]), packet)
                        if answer is None:
                            self._resign()
                            return False
                        socket.send(json.dumps(answer))
                        answered = time.monotonic()
                    elif (
                        kind == "heartbeat"
                        and packet.get("waiting")
                        and time.monotonic() - answered > STALE_HEARTBEAT_SECONDS
                    ):
                        log.warning("game %s: the server waits on a move never seen", self.game.id)
                        return True
            # A clean close: the game ended, or a newer connection took the seat.
            return False
        except InvalidStatus as error:
            status = error.response.status_code
            if status in (429, 503):
                self.wait = max(self.wait, _retry_after(error))
                return True
            if status in (401, 404):
                # The token expired, or the game is over and no fresh token will come.
                return self._await_fresh()
            log.warning("game %s: the engine session answered %s", self.game.id, status)
            return False
        except (WebSocketException, OSError, TimeoutError) as error:
            if self.closed.is_set():
                return False
            log.info("game %s: the engine session dropped (%s); redialing", self.game.id, error)
            return True
        finally:
            self.socket = None

    def _await_fresh(self) -> bool:
        """Reopen the stream for a fresh token; True once one came."""
        self.fresh.clear()
        self.reopen()
        return self.fresh.wait(FRESH_TOKEN_SECONDS) and not self.closed.is_set()

    def _resign(self) -> None:
        for attempt in range(2):
            try:
                self.client.resign(self.game.id, self.token)
                log.info("game %s: resigned", self.game.id)
                return
            except ApiError as error:
                if error.status in (401, 404) and attempt == 0 and self._await_fresh():
                    continue
                log.warning("game %s: could not resign: %s", self.game.id, error)
                return
            except OSError as error:
                log.warning("game %s: could not resign: %s", self.game.id, error)
                return

    def _answer(self, position: Position, packet: dict[str, Any]) -> dict[str, Any] | None:
        """The move response, or None to resign."""
        limit = packet.get("move_time_limit")
        # The engine is told the time it has: the server's limit less the bridge's reserve.
        budget = None if limit is None else max(0.0, limit - self.reserve_seconds)
        deadline = None if budget is None else time.monotonic() + max(0.05, budget)
        answer = self._ask(position, MoveRequest(self.game, budget), deadline)
        if isinstance(answer, Resign):
            return None
        try:
            lines = as_lines(answer) if answer is not None else [Line(position.fallback_turn())]
            lines, notes = settle(position, lines, GAME_LINES, reading=False)
        except Exception:
            log.exception("game %s: the engine's answer is unusable", self.game.id)
            lines, notes = [Line(position.fallback_turn())], []
        for note in notes:
            log.warning("game %s: %s", self.game.id, note)
        log.debug("game %s: %s", self.game.id, lines[0])
        return response(lines, packet)

    def _ask(self, position: Position, request: MoveRequest, deadline: float | None) -> Any:
        """The engine's answer, or None when it has none in time and the fallback turn plays."""
        try:
            engine = self.warming.result(timeout=seconds_left(deadline))
        except TimeoutError:
            log.warning("game %s: the engine is still starting; fallback turn", self.game.id)
            return None
        except Exception:
            log.exception("game %s: the engine failed to start; fallback turn", self.game.id)
            self.warming = start(self.factory, self.game)
            return None
        call = start(engine.move, position, request)
        try:
            return call.result(timeout=seconds_left(deadline))
        except TimeoutError:
            log.warning("game %s: no move within the clock; fallback turn", self.game.id)
            request.stop.set()
            retire(engine, call)
            self.warming = start(self.factory, self.game)
        except Exception:
            log.exception("game %s: the engine failed; fallback turn", self.game.id)
        return None


@dataclass
class Reading:
    position: Position
    packet: dict[str, Any]
    deadline: float
    request: AnalysisRequest
    engine: Engine | None = None
    call: concurrent.futures.Future[Any] = field(default_factory=concurrent.futures.Future)


class AnalysisSession(threading.Thread):
    """The analyzer's session: one position at a time, each read on its own thread so an
    `interrupt` can call it off."""

    def __init__(
        self,
        url: str,
        factory: EngineFactory,
        lines: int,
        reopen: Callable[[], None],
        previous: AnalysisSession | None,
    ) -> None:
        super().__init__(daemon=True, name="analysis")
        self.url = url
        self.factory = factory
        self.lines = lines
        self.reopen = reopen
        # Closed only once this session is open, so the server hands the seat over with no
        # reading lost as a disconnect.
        self.previous = previous
        self.offered = time.monotonic()
        self.lock = threading.Lock()
        self.making = threading.Lock()
        self.closed = threading.Event()
        self.socket: ClientConnection | None = None
        self.engine: Engine | None = None
        self.current: Reading | None = None

    def close(self) -> None:
        self.closed.set()
        socket = self.socket
        if socket is not None:
            socket.close()

    def run(self) -> None:
        wait = REDIAL_FIRST_SECONDS
        try:
            while not self.closed.is_set():
                outcome = self._serve()
                if outcome in ("served", "gone"):
                    # The server offers a fresh session 5 s after an open one closes.
                    return
                if outcome == "refused" or time.monotonic() - self.offered + wait > TOKEN_SECONDS:
                    # The token will not open a session: a reopened stream offers a new one.
                    self.reopen()
                    return
                if self.closed.wait(wait):
                    return
                wait = min(wait * 2, REDIAL_CAP_SECONDS)
        finally:
            self.closed.set()
            self._drop()
            with self.lock:
                engine, self.engine = self.engine, None
            if engine is not None:
                engine.close()

    def _serve(self) -> str:
        """One connection: `served` once it opened, else `retry`, `refused`, or `gone`."""
        try:
            with connect(self.url, open_timeout=10, user_agent_header=USER_AGENT) as socket:
                self.socket = socket
                if self.previous is not None:
                    self.previous.close()
                    self.previous = None
                log.info("analysis session open")
                stones: dict[Cell, Side] = {}
                for message in socket:
                    packet = json.loads(message)
                    kind = packet.get("type")
                    if kind == "setup":
                        stones = read_stones(packet["board"]["cells"])
                    elif kind == "move_request":
                        self._read(Position(dict(stones), packet["side"]), packet)
                    elif kind == "interrupt":
                        self._drop()
            log.info("analysis session closed")
            return "served"
        except InvalidStatus as error:
            status = error.response.status_code
            log.warning("analysis session refused: %s", status)
            if status in (429, 503):
                return "retry"
            # 401: the token expired or was replaced; 404: the bot declares no analyzer.
            return "refused" if status == 401 else "gone"
        except (WebSocketException, OSError, TimeoutError) as error:
            if self.socket is not None:
                log.info("analysis session ended: %s", error)
                return "served"
            log.info("analysis session dial failed: %s", error)
            return "retry"
        finally:
            self.socket = None

    def _engine(self) -> Engine:
        # Made under its own lock, so a slow engine never holds up an interrupt.
        with self.making:
            with self.lock:
                engine = self.engine
            if engine is None:
                engine = self.factory(None)
                with self.lock:
                    stale = self.closed.is_set()
                    if not stale:
                        self.engine = engine
                if stale:
                    engine.close()
            return engine

    def _read(self, position: Position, packet: dict[str, Any]) -> None:
        self._drop()
        seconds = float(packet.get("move_time_limit") or 2)
        # The server's clock started when it sent the request, so the deadline does too.
        deadline = time.monotonic() + seconds + ANALYSIS_GRACE_SECONDS
        reading = Reading(position, packet, deadline, AnalysisRequest(seconds, self.lines))

        def read() -> Any:
            reading.engine = self._engine()
            if reading.request.stop.is_set():
                return None
            return reading.engine.analyze(position, reading.request)

        reading.call = start(read)
        with self.lock:
            self.current = reading
        threading.Thread(target=self._finish, args=(reading,), daemon=True).start()

    def _finish(self, reading: Reading) -> None:
        answer = None
        try:
            answer = reading.call.result(timeout=seconds_left(reading.deadline))
        except TimeoutError:
            if reading.request.stop.is_set():
                return
            log.warning(
                "analysis: no reading in %s s; answering from the rules", reading.request.seconds
            )
            self._retire(reading)
        except Exception:
            if reading.request.stop.is_set():
                return
            log.exception("analysis: the engine failed; answering from the rules")
        position = reading.position
        try:
            lines = as_lines(answer) if answer is not None else []
            lines, notes = settle(
                position, lines or [self._ruled(position)], self.lines, reading=True
            )
        except Exception:
            log.exception("analysis: the engine's reading is unusable; answering from the rules")
            lines, notes = settle(position, [self._ruled(position)], self.lines, reading=True)
        for note in notes:
            log.warning("analysis: %s", note)
        with self.lock:
            if self.current is not reading or self.socket is None:
                return
            self.current = None
            try:
                self.socket.send(json.dumps(response(lines, reading.packet)))
            except WebSocketException:
                return
        log.debug(
            "analysis: %d stones, %s to move: %s", len(position.stones), position.to_move, lines
        )

    @staticmethod
    def _ruled(position: Position) -> Line:
        return Line(position.fallback_turn(), Evaluation(heuristic=0.0))

    def _drop(self) -> None:
        with self.lock:
            reading, self.current = self.current, None
        if reading is not None and not reading.call.done():
            self._retire(reading)

    def _retire(self, reading: Reading) -> None:
        reading.request.stop.set()
        engine = reading.engine
        if engine is None:
            # Still being made: the read returns at once when it sees the stop.
            return
        with self.lock:
            if self.engine is engine:
                self.engine = None
        retire(engine, reading.call)
        # The replacement is made now, off any reading's clock.
        start(self._engine)


def _retry_after(error: InvalidStatus) -> int:
    try:
        return max(1, int(error.response.headers.get("Retry-After", "1")))
    except ValueError:
        return 1
