"""Hand-written HeXO Bot API models.

A thin slice of the HeXO Bot API (github.com/TimmyBurn2/Hexo-Bot-Api), modelled
by hand instead of generated from the spec. The bridge branches on a small set
of discriminators (`Event.type`, `GameEventInfo.finishReason`, `TimeControl.mode`,
`DeclineReason`); those are typed as `Literal` so a bad value fails loudly. The
read-mostly display objects the bridge only passes through (`Capabilities`,
`BotInstance`, `BotListing`, `HardwareInfo`, `RegisteredInstance`) are kept loose
and round-trip-tolerant: they accept and re-emit unknown fields rather than
transcribing the whole tree.

Every model tolerates unknown additive fields (`extra="ignore"` for the fully
modelled slice, `extra="allow"` for the loose round-trip objects) so an additive
spec change does not break the bridge. The contract test
(`tests/test_spec_contract.py`) fetches the spec at the commit pinned in
`pyproject.toml` and enforces that the discriminator enums and examples still
match these models.

This module imports no HTTP, no websocket, no htttx. It is pure pydantic.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AnyUrl, BaseModel, ConfigDict, Field, RootModel

# Tolerate additive fields, drop unknown ones on round-trip. Used by the fully
# modelled slice the bridge branches on.
_TOLERANT = ConfigDict(extra="ignore")

# Round-trip unknown fields. Used by the loose display objects the bridge only
# passes through (Capabilities, BotInstance, BotListing, HardwareInfo,
# RegisteredInstance). The whole tree is not transcribed; unknown sub-fields
# survive a parse -> dump cycle.
_ROUNDTRIP = ConfigDict(extra="allow")


# --- Discriminators and single-source enums ---------------------------------


class Side(RootModel[Literal["p1", "p2"]]):
    """Which side a player is on, by play order. `p1` opens, `p2` replies."""

    root: Literal["p1", "p2"]


class Variant(RootModel[Literal["httt6"]]):
    """The HeXO game variant key. Currently only `httt6`."""

    root: Literal["httt6"]


class DeclineReason(
    RootModel[
        Literal[
            "generic",
            "later",
            "tooFast",
            "tooSlow",
            "timeControl",
            "rated",
            "casual",
            "noBot",
            "onlyBot",
        ]
    ]
):
    """Why a challenge was declined. Carried back to the challenger."""

    root: Literal[
        "generic",
        "later",
        "tooFast",
        "tooSlow",
        "timeControl",
        "rated",
        "casual",
        "noBot",
        "onlyBot",
    ]


# --- Time control union (discriminated on `mode`) ---------------------------


class UnlimitedTimeControl(BaseModel):
    model_config = _TOLERANT
    mode: Literal["unlimited"]


class TurnTimeControl(BaseModel):
    model_config = _TOLERANT
    mode: Literal["turn"]
    turnTimeMs: int


class MatchTimeControl(BaseModel):
    model_config = _TOLERANT
    mode: Literal["match"]
    mainTimeMs: int
    incrementMs: int


class TimeControl(
    RootModel[
        Annotated[
            UnlimitedTimeControl | TurnTimeControl | MatchTimeControl,
            Field(discriminator="mode"),
        ]
    ]
):
    """The game's time control. Exactly one branch applies, selected by `mode`."""

    root: Annotated[
        UnlimitedTimeControl | TurnTimeControl | MatchTimeControl,
        Field(discriminator="mode"),
    ]


# --- Loose display objects (round-trip tolerant, not transcribed) -----------


class HardwareInfo(BaseModel):
    """Opt-in coarse hardware self-report. Label only. Kept loose."""

    model_config = _ROUNDTRIP


class Capabilities(BaseModel):
    """Engine capability declaration. Read-mostly; the bridge does not branch on
    its sub-fields. The whole tree is not transcribed; unknown sub-fields
    round-trip so a real htttx Capabilities document passes through."""

    model_config = _ROUNDTRIP


# --- Fully modelled payloads the bridge branches on ------------------------


class Player(BaseModel):
    model_config = _TOLERANT
    id: str
    name: str
    rating: int | None = None
    title: Literal["BOT"] | None = None
    hardware: HardwareInfo | None = None


class GameEventInfo(BaseModel):
    """Lightweight pointer to a game, carried by `gameStart`, `gameFinish`, and
    the active-games list. The bridge branches on `side`, `finishReason`, and
    `winner`."""

    model_config = _TOLERANT
    id: str
    side: Side
    opponent: Player
    variant: Variant
    rated: bool
    status: Literal["finished"] | None = None
    finishReason: (
        Literal["surrender", "timeout", "disconnect", "terminated", "six-in-a-row", "illegal-move"]
        | None
    ) = None
    winner: Side | None = None


class EngineSession(BaseModel):
    """Server-issued dial bootstrap for one game's engine session."""

    model_config = _TOLERANT
    socketUrl: AnyUrl
    token: str


class Challenge(BaseModel):
    model_config = _TOLERANT
    id: str
    challenger: Player
    destUser: Player
    variant: Variant
    rated: bool
    timeControl: TimeControl
    status: Literal["created", "declined", "canceled", "expired"]
    declineReason: DeclineReason | None = None


# --- Event union (discriminated on `type`) ----------------------------------


class ChallengeEvent(BaseModel):
    model_config = _TOLERANT
    type: Literal["challenge"]
    challenge: Challenge


class GameStartEvent(BaseModel):
    model_config = _TOLERANT
    type: Literal["gameStart"]
    game: GameEventInfo
    engine: EngineSession


class GameFinishEvent(BaseModel):
    model_config = _TOLERANT
    type: Literal["gameFinish"]
    game: GameEventInfo


class OpponentGoneEvent(BaseModel):
    model_config = _TOLERANT
    type: Literal["opponentGone"]
    gameId: str
    gone: Literal[True]
    finishesInSeconds: int


class ChallengeDeclinedEvent(BaseModel):
    model_config = _TOLERANT
    type: Literal["challengeDeclined"]
    challenge: Challenge


class ChallengeCanceledEvent(BaseModel):
    model_config = _TOLERANT
    type: Literal["challengeCanceled"]
    challenge: Challenge


class ChallengeExpiredEvent(BaseModel):
    model_config = _TOLERANT
    type: Literal["challengeExpired"]
    challenge: Challenge


class Event(
    RootModel[
        Annotated[
            ChallengeEvent
            | GameStartEvent
            | GameFinishEvent
            | OpponentGoneEvent
            | ChallengeDeclinedEvent
            | ChallengeCanceledEvent
            | ChallengeExpiredEvent,
            Field(discriminator="type"),
        ]
    ]
):
    """One line of the global event stream. Discriminated on `type`."""

    root: Annotated[
        ChallengeEvent
        | GameStartEvent
        | GameFinishEvent
        | OpponentGoneEvent
        | ChallengeDeclinedEvent
        | ChallengeCanceledEvent
        | ChallengeExpiredEvent,
        Field(discriminator="type"),
    ]


# --- Loose response objects (round-trip tolerant) ---------------------------


class BotInstance(BaseModel):
    """A registered bot instance (whoami response). Loose: the bridge does not
    branch on its fields."""

    model_config = _ROUNDTRIP


class BotListing(BaseModel):
    """One entry in the public bot roster (directory listing). Loose."""

    model_config = _ROUNDTRIP


class RegisteredInstance(BaseModel):
    """Result of registering a bot instance. Loose."""

    model_config = _ROUNDTRIP
