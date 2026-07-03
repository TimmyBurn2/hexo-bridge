"""Hand-written htttx basic_websocket v1-alpha packet models.

A thin slice of the htttx basic_websocket spec
(github.com/hex-tic-tac-toe/htttx-bot-api,
`definitions/basic_websocket/bws-v1-alpha.yaml`), modelled by hand. The adapter
plays the htttx **bot** role: it receives `setup`, `move_request`, `heartbeat`,
`config`, and `interrupt` from the server (the htttx client) and sends
`move_response` back. The packet `type` consts are the discriminators the
adapter switches on; they are typed as `Literal` so a bad value fails loudly.

Every model tolerates unknown additive fields (`extra="ignore"`) so an additive
spec change does not break the adapter. The contract test
(`tests/test_spec_contract.py`) fetches the spec at the commit pinned in
`pyproject.toml` and enforces that the `type` consts and examples still match.

This module imports no HTTP, no websocket transport. It is pure pydantic. The
bws `Board` has no `to_move` field (the server is the referee and tracks whose
turn it is); the stateless `Board` does, and lives in
`adapters/engines/htttx_stateless_models.py`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_TOLERANT = ConfigDict(extra="ignore")


class Coord(BaseModel):
    model_config = _TOLERANT
    q: int
    r: int


class BoardCell(BaseModel):
    model_config = _TOLERANT
    q: int
    r: int
    p: Literal["x", "o"]


class Board(BaseModel):
    """bws board: a list of cells. No `to_move` (the server owns the turn)."""

    model_config = _TOLERANT
    cells: list[BoardCell]


class PositionEvaluation(BaseModel):
    model_config = _TOLERANT
    heuristic: float | None = None
    win_in: int | None = None


class Move(BaseModel):
    """A single complete move of two placements, played by one side."""

    model_config = _TOLERANT
    side: Literal["x", "o"]
    pieces: list[Coord] = Field(..., min_length=2, max_length=2)


class MoveOption(BaseModel):
    """A complete evaluated move of two placements."""

    model_config = _TOLERANT
    pieces: list[Coord] = Field(..., min_length=2, max_length=2)
    evaluation: PositionEvaluation | None = None


# --- Packets the adapter receives (sent by the htttx client) ----------------


class GameSetupPacket(BaseModel):
    model_config = _TOLERANT
    type: Literal["setup"]
    board: Board | None = None


class MoveRequestPacket(BaseModel):
    """Sent from the client, requesting the bot to make a move."""

    model_config = _TOLERANT
    type: Literal["move_request"]
    side: Literal["x", "o"]
    previous: list[Move]
    move_time_limit: float | None = None
    request_id: int | None = None


class HeartbeatPacket(BaseModel):
    model_config = _TOLERANT
    type: Literal["heartbeat"]
    waiting: bool


class ConfigurationPacket(BaseModel):
    model_config = _TOLERANT
    type: Literal["config"]
    depth: int | None = None


class InterruptPacket(BaseModel):
    model_config = _TOLERANT
    type: Literal["interrupt"]
    request_id: int | None = None


class EvaluationRequestPacket(BaseModel):
    model_config = _TOLERANT
    type: Literal["eval_request"]
    side: Literal["x", "o"]
    evaluation_time_limit: float | None = None


# --- Packets the adapter sends (sent by the htttx bot) ----------------------


class MoveResponsePacket(BaseModel):
    """Sent by the bot in response to a move request."""

    model_config = _TOLERANT
    type: Literal["move_response"]
    move: MoveOption
    considerations: list[MoveOption] | None = None
    request_id: int | None = None


class EvaluationResponsePacket(BaseModel):
    model_config = _TOLERANT
    type: Literal["eval_response"]
    evaluation: PositionEvaluation
