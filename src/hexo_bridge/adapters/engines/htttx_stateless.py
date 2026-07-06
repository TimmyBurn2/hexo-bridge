"""htttx stateless HTTP engine adapter.

Implements `EnginePort` by calling a bot-hosted stateless `/turn` endpoint
(htttx stateless v1-alpha). The adapter is the HTTP client; the bot's engine
is the HTTP server. This is the "htttx is one adapter (HTTP to a bot-hosted
endpoint)" case named in the dispatcher.

The adapter translates core domain types (`GameState`, `Move`) to and from the
htttx stateless wire shapes (`StatelessMoveRequest`, `StatelessMoveResponse`)
defined in `hexo_bridge.adapters.engines.htttx_stateless_models`. The
translation is the only place htttx types appear; it does not leak into core or
into the port interface.

Error handling: a bridge-side translation or validation failure (bad coordinate,
malformed response) raises `EngineTranslationError` and is NOT submitted as a
move, so it is never scored as an engine loss. A genuine engine move that the
server later rejects as illegal is a different path (the server ends the game
with `finishReason: illegal-move`).
"""

from __future__ import annotations

import httpx

from hexo_bridge.adapters.engines.htttx_stateless_models import (
    Board as HtttxBoard,
)
from hexo_bridge.adapters.engines.htttx_stateless_models import (
    BoardCell,
    StatelessMoveRequest,
    StatelessMoveResponse,
)
from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move, Side
from hexo_bridge.ports.engine import EngineTranslationError


class HtttxStatelessEngine:
    """EnginePort adapter: HTTP client to a bot-hosted stateless /turn endpoint.

    The endpoint URL is the base; the adapter appends `stateless/v1-alpha/turn`
    unless the caller provides `turn_path` (which lets a bot override via its
    capabilities.json `api_root`).
    """

    def __init__(
        self,
        base_url: str,
        *,
        turn_path: str = "stateless/v1-alpha/turn",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._turn_url = f"{self._base_url}/{turn_path.lstrip('/')}"
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = client
        self._owns_client = client is None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def get_move(self, state: GameState) -> Move:
        request = self._build_request(state)
        client = await self._ensure_client()
        try:
            resp = await client.post(
                self._turn_url,
                json=request.model_dump(by_alias=True, exclude_none=True),
            )
        except httpx.HTTPError as exc:
            raise EngineTranslationError(f"engine HTTP call failed: {exc}") from exc
        if resp.status_code != 200:
            raise EngineTranslationError(f"engine returned {resp.status_code}: {resp.text[:200]}")
        try:
            parsed = StatelessMoveResponse.model_validate(resp.json())
        except Exception as exc:
            raise EngineTranslationError(f"malformed engine response: {exc}") from exc
        return self._parse_response(parsed, state.side)

    def _build_request(self, state: GameState) -> StatelessMoveRequest:
        board = state.to_board()
        cells: list[BoardCell] = []
        for coord, side in board.cells.items():
            cells.append(BoardCell(q=coord.q, r=coord.r, p=side.value))
        # The side to move is what the server stated in `move_request.side`,
        # carried through as `state.side`. The bridge does not derive it from
        # ply parity or an origin convention.
        htttx_board = HtttxBoard(
            to_move=state.side.value,
            cells=cells,
        )
        return StatelessMoveRequest(
            board=htttx_board,
            time_limit=state.time_limit_seconds,
            request_id=state.request_id,
        )

    def _parse_response(self, resp: StatelessMoveResponse, side: Side) -> Move:
        move_opt = resp.move
        if move_opt is None or move_opt.pieces is None or len(move_opt.pieces) != 2:
            raise EngineTranslationError(f"engine response has no valid move: {move_opt}")
        c1, c2 = move_opt.pieces[0], move_opt.pieces[1]
        p1 = Coord(c1.q, c1.r)
        p2 = Coord(c2.q, c2.r)
        if p1 == p2:
            raise EngineTranslationError(f"engine returned two identical pieces: {p1}")
        return Move(side=side, pieces=(p1, p2))

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None
