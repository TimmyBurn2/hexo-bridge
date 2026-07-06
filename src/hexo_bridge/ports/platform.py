"""Platform port: the HeXO lifecycle surface, split by capability into sub-ports.

Each sub-port maps to a scope or capability group. HeXO is one adapter
implementing all of them. A thinner platform may implement a subset. The port
interfaces themselves import no HTTP and no htttx; they speak only the HeXO
models (owned by the platform adapter) and core domain types.

Sub-ports:

  - `Events`: the global NDJSON event stream. One long-lived stream per process.
  - `Play`: resign, list active games, set bot status (openForChallenge).
  - `Challenges`: create, accept, decline, cancel, list, show.
  - `Account`: whoami, revoke own token.
  - `Directory`: list the public bot roster.
  - `Register`: register a bot instance, retire an instance. Behind
    `bot:register`, optional and off by default.

The HeXO models live in `hexo_bridge.adapters.platforms.hexo_models` (owned by
the adapter). The port references them as type hints only, imported under
`TYPE_CHECKING` so there is no runtime dependency from the port module to the
adapter.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
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


@runtime_checkable
class EventsPort(Protocol):
    """The global event stream (`GET /api/stream/event`).

    One long-lived NDJSON stream per process. Opening a new stream closes any
    previous one for the same token (SERVER-NOTES: event-stream supersede). A
    blank-line keepalive arrives at least every 15 seconds; the adapter skips
    blank lines. On a dropped stream, the bridge reconnects with backoff.

    Optional attribute `exhausted` (bool): a platform with a finite event
    supply sets it True when nothing more will ever be streamed, and the
    bridge shuts down instead of reconnecting. Absent means never exhausted
    (HeXO does not define it).
    """

    def stream_events(self) -> AsyncIterator[Event]:
        """Yield events from the global stream until it ends or is dropped."""
        ...


@runtime_checkable
class PlayPort(Protocol):
    """Bot-only play operations: resign, list active games, set status."""

    async def resign_game(self, game_id: str) -> bool:
        """Resign a game. Returns True on 200, False on 409/404 (already finished)."""
        ...

    async def list_games(self) -> list[GameEventInfo]:
        """List this bot's active (in-progress) games."""
        ...

    async def set_bot_status(self, open_for_challenge: bool) -> bool:
        """Advertise availability. Returns the resolved live status."""
        ...


@runtime_checkable
class ChallengesPort(Protocol):
    """Challenge lifecycle: create, accept, decline, cancel, list, show."""

    async def create_challenge(
        self,
        handle: str,
        variant: Variant,
        time_control: TimeControl,
        first_player: str = "random",
    ) -> Challenge: ...

    async def accept_challenge(self, challenge_id: str) -> str:
        """Accept a challenge. Returns the new game's id."""
        ...

    async def decline_challenge(
        self, challenge_id: str, reason: DeclineReason | None = None
    ) -> bool: ...

    async def cancel_challenge(self, challenge_id: str) -> bool: ...

    async def list_challenges(self) -> tuple[list[Challenge], list[Challenge]]:
        """Returns (incoming, outgoing) pending challenges."""
        ...

    async def get_challenge(self, challenge_id: str) -> Challenge | None: ...


@runtime_checkable
class AccountPort(Protocol):
    """Account identity and self-token revoke."""

    async def whoami(self) -> BotInstance: ...

    async def revoke_token(self) -> bool:
        """Revoke the token presented on this request. Self-revoke only."""
        ...


@runtime_checkable
class DirectoryPort(Protocol):
    """Browse the public bot roster. Read-only, paginated."""

    async def list_bots(
        self,
        variant: Variant | None = None,
        owner: str | None = None,
        open_for_challenge: bool | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> tuple[list[BotListing], str | None]:
        """Returns (bots, next_cursor). cursor is None when no more results."""
        ...


@runtime_checkable
class RegisterPort(Protocol):
    """Bot instance registration and retirement. Behind `bot:register`.

    Optional and off by default. The default play token (`bot:play`) cannot
    register or retire; these require the operator's `bot:register` credential.
    """

    async def register_bot(
        self,
        handle: str,
        name: str | None = None,
        capabilities: dict | None = None,
        hardware: dict | None = None,
    ) -> RegisteredInstance: ...

    async def retire_bot(self, handle: str) -> bool: ...


@runtime_checkable
class PlatformPort(Protocol):
    """The full HeXO platform surface. A concrete adapter implements all sub-ports."""

    @property
    def events(self) -> EventsPort: ...

    @property
    def play(self) -> PlayPort: ...

    @property
    def challenges(self) -> ChallengesPort: ...

    @property
    def account(self) -> AccountPort: ...

    @property
    def directory(self) -> DirectoryPort: ...

    @property
    def register(self) -> RegisterPort: ...

    async def close(self) -> None:
        """Release the underlying HTTP client and any open streams."""
        ...
