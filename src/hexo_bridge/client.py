"""The Bot API over HTTP: the declaration, the event stream, challenges, and resigning."""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator, Mapping
from importlib import metadata
from typing import Any
from urllib.parse import quote, urlencode, urljoin

log = logging.getLogger("hexo_bridge")

# Requests a 429 or 503 is retried for, waiting the Retry-After it names.
TRIES = 3
# A longer Retry-After, such as a daily cap's until midnight, is an answer, not a wait.
RETRY_CAP_SECONDS = 30
# The stream sends a bare newline every 10 s, so a read this long means the connection is gone.
STREAM_READ_SECONDS = 30
# Declaration keys an older server refuses, newest first: analyzer came in 0.10.0, levels in 0.9.0.
NEWER_KEYS = ("analyzer", "levels")

try:
    VERSION = metadata.version("hexo-bridge")
except metadata.PackageNotFoundError:
    # A source tree put on the path by hand was never installed, so it has no metadata.
    VERSION = "unknown"
# Sent with every request and handshake, so a server can count the bridge versions it serves.
USER_AGENT = f"hexo-bridge/{VERSION}"


class ApiError(Exception):
    """An answer outside 2xx, with the error body's `code` when it has one."""

    def __init__(
        self, status: int, code: str | None, message: str, retry_after: int | None
    ) -> None:
        super().__init__(f"{status} {code or ''} {message}".strip())
        self.status = status
        self.code = code
        self.retry_after = retry_after


class Client:
    """Calls the Bot API at `url` with a bot token."""

    def __init__(self, url: str, token: str, timeout: float = 30) -> None:
        self.url = url.rstrip("/") + "/"
        self.token = token
        self.timeout = timeout

    def request(
        self, method: str, path: str, body: Any = None, token: str | None = None, tries: int = TRIES
    ) -> Any:
        """One call, retrying a 429 or 503 after the short wait it names."""
        for attempt in range(tries):
            try:
                with self._open(method, path, body, token, self.timeout) as response:
                    text = response.read().decode()
                    return json.loads(text) if text else None
            except ApiError as error:
                wait = error.retry_after or 1
                retry = error.status in (429, 503) and wait <= RETRY_CAP_SECONDS
                if not retry or attempt == tries - 1:
                    raise
                log.info("%s %s: %s; retrying in %s s", method, path, error.status, wait)
                time.sleep(wait)
        raise AssertionError("unreachable")

    def declare(self, declaration: Mapping[str, Any]) -> dict[str, Any]:
        """Send the bot's declaration, dropping the newest keys while a 400 says the server
        predates them."""
        body = dict(declaration)
        dropped: list[str] = []
        while True:
            try:
                answer = self.request("PATCH", "/api/bot/account", body)
                if dropped:
                    log.warning("the server predates %s; declared without it", ", ".join(dropped))
                return answer
            except ApiError as error:
                newer = next((key for key in NEWER_KEYS if key in body), None)
                if error.status != 400 or newer is None:
                    raise
                del body[newer]
                dropped.append(newer)

    def account(self) -> dict[str, Any]:
        return self.request("GET", "/api/bot/account")

    def stream(
        self, open_for_challenges: bool = True, stop: threading.Event | None = None
    ) -> Iterator[dict[str, Any]]:
        """The stream's events, until the server or the connection ends it, or `stop` is set."""
        path = "/api/bot/stream?open=1" if open_for_challenges else "/api/bot/stream"
        with self._open("GET", path, None, None, STREAM_READ_SECONDS) as response:
            for line in response:
                if stop is not None and stop.is_set():
                    return
                if line.strip():
                    yield json.loads(line)

    def accept(self, challenge_id: str) -> None:
        # One try: a challenge lives 60 s, and a refusal now is the answer.
        self.request("POST", f"/api/bot/challenge/{quote(challenge_id)}/accept", tries=1)

    def decline(self, challenge_id: str) -> None:
        self.request("POST", f"/api/bot/challenge/{quote(challenge_id)}/decline")

    def challenge(self, name: str, body: Mapping[str, Any]) -> dict[str, Any]:
        """Challenge another bot; `body` holds `timeControl` and a fresh `requestId`, and may hold
        `openingPlies` and `firstPlayer`."""
        return self.request("POST", f"/api/bot/challenge/{quote(name)}", dict(body))

    def resign(self, game_id: str, game_token: str) -> None:
        self.request("POST", f"/api/bot/game/{quote(game_id)}/resign", token=game_token)

    def socket_url(self, socket_url: str, token: str) -> str:
        """A session's websocket address: its path on the API's origin, with its token."""
        url = urljoin(self.url, socket_url)
        url = url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        return f"{url}{'&' if '?' in url else '?'}{urlencode({'token': token})}"

    def _open(self, method: str, path: str, body: Any, token: str | None, timeout: float) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            urljoin(self.url, path.lstrip("/")), data=data, method=method
        )
        request.add_header("Authorization", f"Bearer {token or self.token}")
        request.add_header("Accept", "application/json")
        request.add_header("User-Agent", USER_AGENT)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            raise _api_error(error) from None


def _api_error(error: urllib.error.HTTPError) -> ApiError:
    code, message = None, error.reason
    try:
        body = json.loads(error.read().decode() or "null")
        if isinstance(body, dict):
            code, message = body.get("code"), body.get("error", message)
    except (ValueError, OSError):
        pass
    retry_after = error.headers.get("Retry-After") if error.headers else None
    try:
        wait = max(1, int(retry_after)) if retry_after else None
    except ValueError:
        wait = None
    return ApiError(error.code, code, str(message), wait)
