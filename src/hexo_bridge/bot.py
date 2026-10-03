"""The bot's loop: declare, hold the event stream, run a session per game and one for analysis."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Mapping
from typing import Any

from hexo_bridge.client import ApiError, Client
from hexo_bridge.engine import Engine, Game, Level
from hexo_bridge.sessions import AnalysisSession, EngineFactory, GameSession

log = logging.getLogger("hexo_bridge")

REDIAL_FIRST_SECONDS = 1
REDIAL_CAP_SECONDS = 8
# The server lets a bot open its stream once every 10 s past a short burst.
REOPEN_SECONDS = 10
# A stream that stayed open this long was healthy, so the next drop redials from the first wait.
HEALTHY_STREAM_SECONDS = 10

LEVEL_ID = re.compile(r"^[a-z0-9-]{1,16}$")
LEVEL_LABEL = re.compile(r"^[!-~](?:[ -~]{0,22}[!-~])?$")
SCALE_MAX = 1_000_000
# Cuts are drops on the scaled range, which runs from -1 to 1.
CUT_MAX = 2
CUTS = ("inaccuracy", "mistake", "blunder")
# Deprecated in Bot API 0.12.0, still accepted: the bot's owner sets its text and link on its page.
DEPRECATED_KEYS = ("about", "repoUrl")


def check_declaration(declaration: Mapping[str, Any]) -> None:
    """Refuse a declaration the server would refuse, before it is sent.

    Checked here so a 400 from the server can only mean it predates a key, never a typo.
    """
    if "accepts" not in declaration:
        raise ValueError(
            "the declaration needs accepts: a bot that never declared them plays no clock"
        )
    levels = declaration.get("levels")
    if levels is not None:
        entries = levels.get("list") or []
        ids = [entry.get("id") for entry in entries]
        if not 2 <= len(entries) <= 8:
            raise ValueError("levels.list holds 2 to 8 levels")
        if any(not isinstance(i, str) or not LEVEL_ID.match(i) for i in ids) or len(
            set(ids)
        ) != len(ids):
            raise ValueError(
                "each level id is unique, lowercase letters, digits, and hyphens, 16 at most"
            )
        if any(not LEVEL_LABEL.match(str(entry.get("label", ""))) for entry in entries):
            raise ValueError("each level label is 1 to 24 printable ASCII characters")
        if levels.get("default") not in ids:
            raise ValueError("levels.default names one of the levels")
    analyzer = declaration.get("analyzer")
    if analyzer is not None:
        if analyzer.get("lines") not in (1, 2, 3):
            raise ValueError("analyzer.lines is 1, 2, or 3")
        if analyzer.get("maxSeconds", 2) not in range(1, 11):
            raise ValueError("analyzer.maxSeconds is 1 to 10")
        if "values" in analyzer:
            _check_values(analyzer["values"])


def _check_values(values: Any) -> None:
    """Refuse an analyzer's `values` the server would refuse, naming the field at fault."""
    if not isinstance(values, Mapping):
        raise ValueError("analyzer.values is a table of scale, meaning, and cuts")
    unknown = sorted(set(values) - {"scale", "meaning", "cuts"})
    if unknown:
        raise ValueError(
            f"analyzer.values takes scale, meaning, and cuts, not {', '.join(map(str, unknown))}"
        )
    if not _within(values.get("scale", 1), SCALE_MAX):
        raise ValueError(f"analyzer.values.scale is a number above 0 and at most {SCALE_MAX}")
    if values.get("meaning", "raw") not in ("expected", "raw"):
        raise ValueError('analyzer.values.meaning is "expected" or "raw"')
    if "cuts" not in values:
        return
    cuts = values["cuts"]
    if not isinstance(cuts, Mapping) or set(cuts) != set(CUTS):
        raise ValueError("analyzer.values.cuts holds inaccuracy, mistake, and blunder, and no more")
    for name in CUTS:
        if not _within(cuts[name], CUT_MAX):
            raise ValueError(
                f"analyzer.values.cuts.{name} is a number above 0 and at most {CUT_MAX}"
            )
    if not cuts["inaccuracy"] < cuts["mistake"] < cuts["blunder"]:
        raise ValueError("analyzer.values.cuts rise: inaccuracy below mistake below blunder")


def _within(value: Any, most: float) -> bool:
    # A bool is an int to Python but not a number to JSON; NaN fails both comparisons.
    return isinstance(value, int | float) and not isinstance(value, bool) and 0 < value <= most


class Bot:
    """A bot on one server: its declaration, its event stream, and its sessions.

    `factory` makes the engine for each game (called with its `Game`) and for the analysis
    session (called with None); an `Engine` subclass is one.
    """

    def __init__(
        self,
        factory: EngineFactory,
        *,
        url: str,
        token: str,
        declaration: Mapping[str, Any],
        open_for_challenges: bool = True,
        reserve_seconds: float = 1.0,
    ) -> None:
        check_declaration(declaration)
        # Absent keys are sent as null: the server keeps a key a declaration leaves out.
        self.declaration = {"levels": None, "analyzer": None, **declaration}
        self.factory = factory
        self.client = Client(url, token)
        self.open_for_challenges = open_for_challenges
        self.reserve_seconds = reserve_seconds
        self.stopped = threading.Event()
        self.leave = threading.Event()
        self.lock = threading.Lock()
        self.games: dict[str, GameSession] = {}
        self.analysis: AnalysisSession | None = None
        levels = self.declaration["levels"] or {}
        self.levels = {
            level["id"]: Level(level["id"], level.get("budget", {}))
            for level in levels.get("list", [])
        }
        self.default_level = levels.get("default")
        analyzer = self.declaration["analyzer"]
        self.lines = int(analyzer["lines"]) if analyzer else 0
        if analyzer:
            probe = factory(None)
            try:
                if type(probe).analyze is Engine.analyze:
                    raise TypeError(
                        "the declaration has an analyzer, but the engine has no analyze()"
                    )
            finally:
                probe.close()

    def run(self) -> None:
        """Hold the bot online until `stop()`; a refused token or declaration raises `ApiError`."""
        deprecated = [key for key in DEPRECATED_KEYS if key in self.declaration]
        if deprecated:
            log.warning(
                "the declaration holds %s, deprecated since Bot API 0.12.0 and still sent: "
                "the bot's owner sets its text and source link on the bot page",
                " and ".join(deprecated),
            )
        wait = REDIAL_FIRST_SECONDS
        opened = 0.0
        reopening = False
        try:
            while not self.stopped.is_set():
                # A reopen for fresh tokens waits out the server's stream-open rate first.
                if reopening and self.stopped.wait(
                    max(0.0, opened + REOPEN_SECONDS - time.monotonic())
                ):
                    break
                reopening = False
                self.leave.clear()
                opened = time.monotonic()
                try:
                    # Declared on every open, so a server upgraded in between takes the newer keys.
                    self.client.declare(self.declaration)
                    log.info("stream opening")
                    for event in self.client.stream(self.open_for_challenges, self.leave):
                        self.handle(event)
                except ApiError as error:
                    if error.status in (400, 401, 403):
                        raise
                    wait = max(wait, error.retry_after or 0)
                    log.warning("refused: %s", error)
                except (OSError, ValueError) as error:
                    log.warning("stream dropped: %s", error)
                if self.leave.is_set() and not self.stopped.is_set():
                    log.info("reopening the stream for fresh tokens")
                    reopening = True
                    continue
                if time.monotonic() - opened > HEALTHY_STREAM_SECONDS:
                    wait = REDIAL_FIRST_SECONDS
                if self.stopped.wait(wait):
                    break
                wait = min(wait * 2, REDIAL_CAP_SECONDS)
        finally:
            with self.lock:
                sessions = [*self.games.values(), self.analysis]
            for session in sessions:
                if session is not None:
                    session.close()

    def stop(self) -> None:
        """Leave the stream at its next line, at most 10 s away, and close every session."""
        self.stopped.set()
        self.leave.set()

    def reopen(self) -> None:
        """Leave the stream and open it again, which replays each live game with a fresh token."""
        self.leave.set()

    def handle(self, event: Mapping[str, Any]) -> None:
        kind = event.get("type")
        if kind == "gameStart":
            self._start_game(event)
        elif kind == "gameFinish":
            log.info(
                "game %s over: %s, winner %s",
                event["gameId"],
                event.get("reason"),
                event.get("winner"),
            )
            with self.lock:
                session = self.games.pop(event["gameId"], None)
            if session is not None:
                session.close()
        elif kind == "challenge":
            # Off the stream's thread, so accepting never holds up the next event.
            challenge_id = event["challenge"]["challengeId"]
            threading.Thread(target=self._accept, args=(challenge_id,), daemon=True).start()
        elif kind == "analysisSession" and self.lines:
            self._start_analysis(event)
        # moveRequest, deprecated since Bot API 0.12.0, needs nothing: the game's socket asks.

    def _accept(self, challenge_id: str) -> None:
        try:
            self.client.accept(challenge_id)
        except (ApiError, OSError) as error:
            log.warning("could not accept challenge %s: %s", challenge_id, error)

    def _start_game(self, event: Mapping[str, Any]) -> None:
        engine = event["engine"]
        url = self.client.socket_url(engine["socketUrl"], engine["token"])
        with self.lock:
            running = self.games.get(event["gameId"])
            if running is not None and running.is_alive():
                running.refresh(engine["token"], url)
                return
            level_id = event.get("level") or self.default_level
            # A level the bot no longer declares plays its default.
            level = (
                self.levels.get(level_id) or self.levels.get(self.default_level) or Level(level_id)
            )
            game = Game(
                id=event["gameId"],
                side=event["side"],
                opponent=event["opponent"]["name"],
                rated=bool(event.get("rated")),
                level=level,
                time_control=event.get("timeControl", {}),
                opening_plies=int(event.get("openingPlies", 1)),
            )
            session = GameSession(
                self.client,
                game,
                engine["token"],
                url,
                self.factory,
                self.reserve_seconds,
                self.reopen,
            )
            self.games[game.id] = session
        session.start()
        log.info(
            "game %s vs %s, playing %s at level %s", game.id, game.opponent, game.side, level.id
        )

    def _start_analysis(self, event: Mapping[str, Any]) -> None:
        engine = event["engine"]
        url = self.client.socket_url(engine["socketUrl"], engine["token"])
        with self.lock:
            session = self.analysis = AnalysisSession(
                url, self.factory, self.lines, self.reopen, self.analysis
            )
        session.start()


def run(
    factory: EngineFactory,
    *,
    url: str,
    token: str,
    declaration: Mapping[str, Any],
    open_for_challenges: bool = True,
    reserve_seconds: float = 1.0,
) -> None:
    """Run the bot until interrupted, logging to stderr unless logging is already set up.

    `reserve_seconds` is kept back from each move's clock for the network; an engine still
    searching past what is left plays a fallback turn rather than lose on time.
    """
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bot = Bot(
        factory,
        url=url,
        token=token,
        declaration=declaration,
        open_for_challenges=open_for_challenges,
        reserve_seconds=reserve_seconds,
    )
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.stop()
