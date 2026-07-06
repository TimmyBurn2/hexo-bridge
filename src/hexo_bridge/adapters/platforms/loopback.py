"""Loopback platform adapter: the offline, token-free test harness.

Harness-only. This is NOT a HeXO server and NOT a referee; it exists so the
bridge can run end to end with no HeXO account, no token, and no network (the
"just htttx" mode). It synthesizes the platform lifecycle and stands up a
small local websocket endpoint that plays the htttx client role with a
scripted packet sequence, so the bridge's real engine session adapter
(`htttx_websocket`) is exercised over a real socket: real dial, real Bearer
token presentation, real JSON wire shapes, real `request_id` echo.

What it deliberately does not do: legality checking, win detection, clocks,
ratings. Those are server-owned and are not reimplemented here. The scripted
opponent moves are canned, and the bot's own move is echoed back verbatim as
the first element of the next `move_request.previous` (transcription, not
refereeing). Every game ends administratively (`finishReason: "terminated"`)
when the script runs out; the loopback never computes a result.

Lifecycle per game: emit `gameStart` whose engine bootstrap points at the
loopback's own local socket, run the script (setup, then N move_requests,
reading a move_response after each), close the socket normally, emit
`gameFinish`. After the last game the events sub-port marks itself
`exhausted` and the bridge shuts down instead of reconnecting.

Sub-ports a loopback has no use for (challenges, account, directory,
register) are stubs that raise on any use.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import AsyncIterator
from typing import Any

from websockets.asyncio.server import serve

from hexo_bridge.adapters.platforms.hexo_models import (
    EngineSession,
    Event,
    GameEventInfo,
    GameFinishEvent,
    GameStartEvent,
    Player,
    Side,
    Variant,
)
from hexo_bridge.ports.platform import EventsPort, PlayPort

logger = logging.getLogger("hexo_bridge.platform.loopback")


class LoopbackEvents(EventsPort):
    """A finite event supply: gameStart then gameFinish per game, then done.

    Sets `exhausted` after the last gameFinish so the bridge shuts down
    instead of reconnecting (see the EventsPort docstring).
    """

    def __init__(self, platform: LoopbackPlatform) -> None:
        self._platform = platform
        self.exhausted = False

    async def stream_events(self) -> AsyncIterator[Event]:
        # The harness runs one deterministic pass and never reconnects: the
        # supply is exhausted whether the pass completed or died on an internal
        # error, so the bridge terminates instead of replaying games.
        p = self._platform
        try:
            await p._ensure_server()
            for n in range(1, p._games + 1):
                game_id = f"loopback-{n}"
                yield p._game_start_event(game_id)
                try:
                    await asyncio.wait_for(p._done_events[game_id].wait(), timeout=p._game_timeout)
                except TimeoutError:
                    logger.warning(
                        "loopback: game %s did not finish within %.0fs; finishing anyway",
                        game_id,
                        p._game_timeout,
                    )
                yield p._game_finish_event(game_id)
        finally:
            self.exhausted = True


class LoopbackPlay(PlayPort):
    """Play stub: nothing to resign, no roster, status is accepted as-is."""

    async def resign_game(self, game_id: str) -> bool:
        return False

    async def list_games(self) -> list:
        return []

    async def set_bot_status(self, open_for_challenge: bool) -> bool:
        return open_for_challenge


class _UnsupportedPort:
    """Stub for sub-ports the loopback harness does not provide."""

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(
            f"loopback platform does not support '{name}': it is an offline "
            "test harness, not a HeXO server"
        )


class LoopbackPlatform:
    """PlatformPort adapter for the offline loopback harness. No token.

    Options (all optional, `[platform.options]`):
      - games: how many scripted games to run before exhausting (default 1).
      - move_requests_per_game: script length per game (default 2).
      - game_timeout_seconds: give up waiting for a game's script and finish
        it anyway, so a broken run terminates instead of hanging (default 30).
      - stream_read_timeout: accepted and ignored; the bridge threads this
        bridge-level option into every platform constructor.

    Test-visible state:
      - received_move_responses: raw move_response payloads per game id, as
        received over the wire from the real session adapter.
      - auth_failures: game ids whose dial presented a wrong or missing token.
      - closed: True once close() ran.
    """

    def __init__(
        self,
        *,
        games: int = 1,
        move_requests_per_game: int = 2,
        game_timeout_seconds: float = 30.0,
        stream_read_timeout: float = 45.0,
        send_request_id: bool = True,
        setup_cells: list[tuple[int, int, str]] | None = None,
    ) -> None:
        self._games = games
        self._move_requests = move_requests_per_game
        self._game_timeout = game_timeout_seconds
        # When False, the loopback plays a positional-only server: it sends no
        # request_id on any move_request and accepts the next move_response as
        # the answer (no id matching). This is the real openness test: the
        # bridge must play a conformant positional-only server to completion,
        # not just one that assigns ids.
        self._send_request_id = send_request_id
        # The board the loopback delivers in the setup packet. Defaults to the
        # standard opening (one cross at the origin). Override to prove the
        # bridge consumes whatever the server delivers (e.g. a non-[0,0] board)
        # rather than falling back to a baked-in origin.
        self._setup_cells = (
            list(setup_cells) if setup_cells is not None else [(0, 0, "x")]
        )
        self._server: Any = None
        self._port: int | None = None
        self._expected_tokens: dict[str, str] = {}
        self._done_events: dict[str, asyncio.Event] = {}
        self.received_move_responses: dict[str, list[dict]] = {}
        self.auth_failures: list[str] = []
        self.closed = False
        self._events = LoopbackEvents(self)
        self._play = LoopbackPlay()
        self._unsupported = _UnsupportedPort()

    # --- PlatformPort surface ------------------------------------------------

    @property
    def events(self) -> LoopbackEvents:
        return self._events

    @property
    def play(self) -> LoopbackPlay:
        return self._play

    @property
    def challenges(self) -> Any:
        return self._unsupported

    @property
    def account(self) -> Any:
        return self._unsupported

    @property
    def directory(self) -> Any:
        return self._unsupported

    @property
    def register(self) -> Any:
        return self._unsupported

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self.closed = True

    # --- Local scripted htttx endpoint ---------------------------------------

    async def _ensure_server(self) -> None:
        if self._server is None:
            self._server = await serve(self._handler, "127.0.0.1", 0)
            self._port = self._server.sockets[0].getsockname()[1]
            logger.info("loopback: local htttx endpoint on 127.0.0.1:%d", self._port)

    async def _handler(self, connection: Any) -> None:
        """Serve one game session: check the per-game token, run the script."""
        game_id = connection.request.path.rsplit("/", 1)[-1]
        done = self._done_events.get(game_id)
        expected = self._expected_tokens.get(game_id)
        auth = connection.request.headers.get("Authorization")
        if expected is None or auth != f"Bearer {expected}":
            logger.warning("loopback: bad token for game %s", game_id)
            self.auth_failures.append(game_id)
            await connection.close(1008, "bad token")
            if done is not None:
                done.set()
            return
        try:
            await self._run_script(connection, game_id)
        except Exception as exc:
            logger.warning("loopback: game %s script aborted: %s", game_id, exc)
        finally:
            if done is not None:
                done.set()

    async def _run_script(self, connection: Any, game_id: str) -> None:
        """Play the htttx client role: setup, then N move_requests.

        The bot is O (platform side p2), which moves first after the server
        opening, so the script needs no leading opponent move. Each
        move_request's `previous` echoes the bot's own last move verbatim
        (first, per the htttx spec) plus one canned opponent move placed far
        from the setup. No legality is checked; echoing is not refereeing.

        `send_request_id=False` plays a positional-only server: no request_id
        on any move_request, and the next move_response is accepted as the
        answer (no id matching). `setup_cells` overrides the board the setup
        packet delivers, so a test can prove the bridge consumes a non-[0,0]
        board rather than falling back to a baked-in origin.
        """
        await connection.send(
            json.dumps(
                {
                    "type": "setup",
                    "board": {
                        "cells": [
                            {"q": q, "r": r, "p": s} for q, r, s in self._setup_cells
                        ],
                    },
                }
            )
        )
        previous: list[dict] = []
        for n in range(1, self._move_requests + 1):
            request: dict = {
                "type": "move_request",
                "side": "o",
                "previous": previous,
                "move_time_limit": 5.0,
            }
            if self._send_request_id:
                request["request_id"] = n
            await connection.send(json.dumps(request))
            payload = json.loads(await connection.recv())
            self.received_move_responses[game_id].append(payload)
            if payload.get("type") != "move_response":
                logger.warning(
                    "loopback: game %s: unexpected packet %s, ending script", game_id, payload
                )
                break
            if self._send_request_id and payload.get("request_id") != n:
                logger.warning(
                    "loopback: game %s: request_id mismatch (got %s, want %s), ending script",
                    game_id,
                    payload.get("request_id"),
                    n,
                )
                break
            previous = [
                {"side": "o", "pieces": payload["move"]["pieces"]},
                {
                    "side": "x",
                    "pieces": [{"q": 10 + 2 * n, "r": 0}, {"q": 11 + 2 * n, "r": 0}],
                },
            ]
        await connection.close(1000, "script complete")

    # --- Synthesized lifecycle events ----------------------------------------

    def _game_start_event(self, game_id: str) -> Event:
        token = secrets.token_urlsafe(16)
        self._expected_tokens[game_id] = token
        self._done_events[game_id] = asyncio.Event()
        self.received_move_responses[game_id] = []
        return Event(
            root=GameStartEvent(
                type="gameStart",
                game=self._game_info(game_id),
                engine=EngineSession(
                    socketUrl=f"ws://127.0.0.1:{self._port}/game/{game_id}",
                    token=token,
                ),
            )
        )

    def _game_finish_event(self, game_id: str) -> Event:
        # "terminated" is the honest reason: the harness ends the game
        # administratively when the script runs out; no result was refereed.
        return Event(
            root=GameFinishEvent(
                type="gameFinish",
                game=self._game_info(game_id, finished=True),
            )
        )

    def _game_info(self, game_id: str, finished: bool = False) -> GameEventInfo:
        extra: dict = {"status": "finished", "finishReason": "terminated"} if finished else {}
        return GameEventInfo(
            id=game_id,
            side=Side(root="p2"),
            opponent=Player(id="loopback-opponent", name="Scripted Opponent"),
            variant=Variant(root="httt6"),
            rated=False,
            **extra,
        )
