"""Hand-written htttx stateless v1-alpha models.

A thin slice of the htttx stateless spec
(github.com/hex-tic-tac-toe/htttx-bot-api,
`definitions/stateless/stateless-v1-alpha.yaml`), modelled by hand. The adapter
is the HTTP client; the bot's engine is the HTTP server hosting `/turn`. The
adapter POSTs a `StatelessMoveRequest` and reads back a `StatelessMoveResponse`.

The stateless `Board` carries `to_move` (the engine needs to know whose turn it
is, there is no session); the bws `Board` does not, and lives in
`adapters/engine_sessions/htttx_models.py`. The stateless `Move` has no `side`
field (the side to move is already `Board.to_move`); the bws `Move` does.

Every model tolerates unknown additive fields (`extra="ignore"`). The contract
test fetches the spec at the commit pinned in `pyproject.toml` and enforces
that the examples still parse.

Pure pydantic, no HTTP.
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
    """stateless board: carries `to_move` because there is no session."""

    model_config = _TOLERANT
    to_move: Literal["x", "o"]
    cells: list[BoardCell]


class PositionEvaluation(BaseModel):
    model_config = _TOLERANT
    heuristic: float | None = None
    win_in: int | None = None


class MoveOption(BaseModel):
    """A chosen move of two placements, with optional evaluation. The stateless
    `Move` has no `side` field (the side is `Board.to_move`)."""

    model_config = _TOLERANT
    pieces: list[Coord] = Field(..., min_length=2, max_length=2)
    evaluation: PositionEvaluation | None = None


class StatelessMoveRequest(BaseModel):
    model_config = _TOLERANT
    board: Board
    time_limit: float | None = None
    request_id: int | None = None


class StatelessMoveResponse(BaseModel):
    model_config = _TOLERANT
    move: MoveOption
    considerations: list[MoveOption] | None = None
    request_id: int | None = None
