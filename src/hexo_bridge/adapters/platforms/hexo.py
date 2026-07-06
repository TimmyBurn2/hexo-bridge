"""HeXO platform adapter.

Implements the full `PlatformPort` surface against the HeXO Bot API
(https://hexo.did.science). Uses `httpx.AsyncClient` for HTTP and a manual
NDJSON line reader for the global event stream.

Auth: a Personal Access Token sent as `Authorization: Bearer hxo_...`. The
token requirement is this adapter's, not the bridge's: `HeXOPlatform` reads
`HEXO_BRIDGE_TOKEN` from the environment (taking precedence over the `token`
constructor argument, so secrets stay out of config files) and raises when it
has neither. A non-HeXO platform runs with no token.

Endpoints implemented (per openapi.yaml):
  - GET  /api/stream/event         -> Events (NDJSON, long-lived)
  - POST /api/bot/game/{id}/resign -> Play
  - GET  /api/bot/games            -> Play
  - POST /api/bot/status           -> Play
  - POST /api/challenge/{handle}   -> Challenges
  - GET  /api/challenges           -> Challenges
  - GET  /api/challenge/{id}/show -> Challenges
  - POST /api/challenge/{id}/accept   -> Challenges
  - POST /api/challenge/{id}/decline  -> Challenges
  - POST /api/challenge/{id}/cancel   -> Challenges
  - GET  /api/account              -> Account
  - DELETE /api/token              -> Account
  - GET  /api/bots                 -> Directory
  - POST /api/bot/register         -> Register (bot:register)
  - POST /api/bot/{handle}/retire  -> Register (bot:register)
  - POST /api/bulk-pairing         -> Challenges (bot:organize, optional)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from hexo_bridge.adapters.platforms.hexo_models import (
    BotInstance,
    BotListing,
    Challenge,
    DeclineReason,
    Event,
    GameEventInfo,
    RegisteredInstance,
    TimeControl,
    Variant,
)
from hexo_bridge.ports.platform import (
    AccountPort,
    ChallengesPort,
    DirectoryPort,
    EventsPort,
    PlatformPort,
    PlayPort,
    RegisterPort,
)

logger = logging.getLogger("hexo_bridge.platform")


class HeXOApiError(Exception):
    """A non-2xx response from the HeXO API. Carries the status and error code."""

    def __init__(self, status: int, error_code: str | None, message: str) -> None:
        self.status = status
        self.error_code = error_code
        self.message = message
        super().__init__(f"HeXO {status} {error_code}: {message}")


class HeXOEvents(EventsPort):
    """The global NDJSON event stream.

    One stream per process. Opening a new stream closes any previous one for the
    same token (event-stream supersede). Blank-line keepalives are skipped. On
    a dropped stream, the iterator ends; the bridge reconnects with backoff.

    A read timeout is enforced: if no line arrives within
    `stream_read_timeout_seconds` (default 45s, a small multiple of the server's
    15s keepalive interval), the stream is treated as dead and the iterator
    ends so the bridge can reconnect.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        read_timeout: float = 45.0,
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._read_timeout = read_timeout

    async def stream_events(self) -> AsyncIterator[Event]:
        url = f"{self._base_url}/api/stream/event"
        async with self._client.stream("GET", url) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise HeXOApiError(resp.status_code, None, body.decode("utf-8", "replace")[:200])
            line_iter = resp.aiter_lines()
            while True:
                try:
                    line = await asyncio.wait_for(line_iter.__anext__(), timeout=self._read_timeout)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    logger.warning(
                        "global stream: no data in %.0fs (dead socket?), reconnecting",
                        self._read_timeout,
                    )
                    break
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except json.JSONDecodeError:
                    logger.warning("global stream: skipping malformed line: %s", stripped[:100])
                    continue
                try:
                    yield Event.model_validate(data)
                except Exception as exc:
                    logger.warning("global stream: unparseable event: %s", exc)
                    continue


class HeXOPlay(PlayPort):
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    async def resign_game(self, game_id: str) -> bool:
        url = f"{self._base_url}/api/bot/game/{game_id}/resign"
        resp = await self._client.post(url)
        if resp.status_code == 200:
            return True
        if resp.status_code in (404, 409):
            return False
        raise _error(resp)

    async def list_games(self) -> list[GameEventInfo]:
        url = f"{self._base_url}/api/bot/games"
        resp = await self._client.get(url)
        if resp.status_code != 200:
            raise _error(resp)
        data = resp.json()
        return [GameEventInfo.model_validate(g) for g in data.get("games", [])]

    async def set_bot_status(self, open_for_challenge: bool) -> bool:
        url = f"{self._base_url}/api/bot/status"
        resp = await self._client.post(url, json={"openForChallenge": open_for_challenge})
        if resp.status_code != 200:
            raise _error(resp)
        return resp.json().get("openForChallenge", False)


class HeXOChallenges(ChallengesPort):
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    async def create_challenge(
        self,
        handle: str,
        variant: Variant,
        time_control: TimeControl,
        first_player: str = "random",
    ) -> Challenge:
        url = f"{self._base_url}/api/challenge/{handle}"
        body: dict[str, Any] = {
            "variant": str(variant.root) if hasattr(variant, "root") else str(variant),
            "timeControl": time_control.model_dump(by_alias=True, exclude_none=True),
        }
        if first_player:
            body["firstPlayer"] = first_player
        resp = await self._client.post(url, json=body)
        if resp.status_code != 200:
            raise _error(resp)
        return Challenge.model_validate(resp.json())

    async def accept_challenge(self, challenge_id: str) -> str:
        url = f"{self._base_url}/api/challenge/{challenge_id}/accept"
        resp = await self._client.post(url)
        if resp.status_code != 200:
            raise _error(resp)
        return resp.json()["gameId"]

    async def decline_challenge(
        self, challenge_id: str, reason: DeclineReason | None = None
    ) -> bool:
        url = f"{self._base_url}/api/challenge/{challenge_id}/decline"
        body: dict[str, Any] = {}
        if reason is not None:
            body["reason"] = str(reason.root) if hasattr(reason, "root") else str(reason)
        resp = await self._client.post(url, json=body)
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        raise _error(resp)

    async def cancel_challenge(self, challenge_id: str) -> bool:
        url = f"{self._base_url}/api/challenge/{challenge_id}/cancel"
        resp = await self._client.post(url)
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        raise _error(resp)

    async def list_challenges(self) -> tuple[list[Challenge], list[Challenge]]:
        url = f"{self._base_url}/api/challenges"
        resp = await self._client.get(url)
        if resp.status_code != 200:
            raise _error(resp)
        data = resp.json()
        incoming = [Challenge.model_validate(c) for c in data.get("in", [])]
        outgoing = [Challenge.model_validate(c) for c in data.get("out", [])]
        return (incoming, outgoing)

    async def get_challenge(self, challenge_id: str) -> Challenge | None:
        url = f"{self._base_url}/api/challenge/{challenge_id}/show"
        resp = await self._client.get(url)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise _error(resp)
        return Challenge.model_validate(resp.json())


class HeXOAccount(AccountPort):
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    async def whoami(self) -> BotInstance:
        url = f"{self._base_url}/api/account"
        resp = await self._client.get(url)
        if resp.status_code != 200:
            raise _error(resp)
        return BotInstance.model_validate(resp.json())

    async def revoke_token(self) -> bool:
        url = f"{self._base_url}/api/token"
        resp = await self._client.delete(url)
        if resp.status_code == 200:
            return True
        raise _error(resp)


class HeXODirectory(DirectoryPort):
    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    async def list_bots(
        self,
        variant: Variant | None = None,
        owner: str | None = None,
        open_for_challenge: bool | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[BotListing], str | None]:
        url = f"{self._base_url}/api/bots"
        params: dict[str, Any] = {"limit": limit}
        if variant is not None:
            params["variant"] = str(variant.root) if hasattr(variant, "root") else str(variant)
        if owner is not None:
            params["owner"] = owner
        if open_for_challenge is not None:
            params["openForChallenge"] = open_for_challenge
        if cursor is not None:
            params["cursor"] = cursor
        resp = await self._client.get(url, params=params)
        if resp.status_code != 200:
            raise _error(resp)
        data = resp.json()
        bots = [BotListing.model_validate(b) for b in data.get("bots", [])]
        next_cursor = data.get("cursor")
        return (bots, next_cursor)


class HeXORegister(RegisterPort):
    """Registration operations. Behind `bot:register`, off by default.

    Requires a separate operator token with `bot:register` scope, not the play
    token. The HeXOPlatform does not expose this by default; the operator must
    construct a HeXORegister with the operator token explicitly.
    """

    def __init__(self, client: httpx.AsyncClient, base_url: str) -> None:
        self._client = client
        self._base_url = base_url

    async def register_bot(
        self,
        handle: str,
        name: str | None = None,
        capabilities: dict | None = None,
        hardware: dict | None = None,
    ) -> RegisteredInstance:
        url = f"{self._base_url}/api/bot/register"
        body: dict[str, Any] = {"handle": handle}
        if name is not None:
            body["name"] = name
        if capabilities is not None:
            body["capabilities"] = capabilities
        if hardware is not None:
            body["hardware"] = hardware
        resp = await self._client.post(url, json=body)
        if resp.status_code != 200:
            raise _error(resp)
        return RegisteredInstance.model_validate(resp.json())

    async def retire_bot(self, handle: str) -> bool:
        url = f"{self._base_url}/api/bot/{handle}/retire"
        resp = await self._client.post(url)
        if resp.status_code == 200:
            return True
        if resp.status_code == 404:
            return False
        raise _error(resp)


def _error(resp: httpx.Response) -> HeXOApiError:
    try:
        data = resp.json()
        return HeXOApiError(
            resp.status_code,
            data.get("error"),
            data.get("message", str(data)[:200]),
        )
    except Exception:
        return HeXOApiError(resp.status_code, None, resp.text[:200])


class HeXOPlatform(PlatformPort):
    """The full HeXO platform surface.

    One instance per process: one token, one global event stream, one identity.
    A fleet is N processes, not multiplexing inside one.

    The `register` sub-port is created lazily and only when the caller provides
    an operator token with `bot:register` scope. By default it is None.
    """

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        *,
        register_token: str | None = None,
        timeout: float = 30.0,
        stream_read_timeout: float = 45.0,
    ) -> None:
        token = os.environ.get("HEXO_BRIDGE_TOKEN") or token
        if not token:
            raise ValueError("no HeXO token: set HEXO_BRIDGE_TOKEN or [platform.options] token")
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
        self._events = HeXOEvents(self._client, self._base_url, read_timeout=stream_read_timeout)
        self._play = HeXOPlay(self._client, self._base_url)
        self._challenges = HeXOChallenges(self._client, self._base_url)
        self._account = HeXOAccount(self._client, self._base_url)
        self._directory = HeXODirectory(self._client, self._base_url)
        self._register: HeXORegister | None = None
        if register_token is not None:
            reg_client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {register_token}"},
                timeout=timeout,
            )
            self._register = HeXORegister(reg_client, self._base_url)
            self._reg_client = reg_client
        else:
            self._reg_client = None

    @property
    def events(self) -> EventsPort:
        return self._events

    @property
    def play(self) -> PlayPort:
        return self._play

    @property
    def challenges(self) -> ChallengesPort:
        return self._challenges

    @property
    def account(self) -> AccountPort:
        return self._account

    @property
    def directory(self) -> DirectoryPort:
        return self._directory

    @property
    def register(self) -> RegisterPort:
        if self._register is None:
            raise RuntimeError(
                "register port is not available: no bot:register token was provided. "
                "Pass register_token to HeXOPlatform to enable registration."
            )
        return self._register

    async def close(self) -> None:
        await self._client.aclose()
        if self._reg_client is not None:
            await self._reg_client.aclose()
