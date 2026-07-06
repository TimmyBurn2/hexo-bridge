"""Board state, replay-from-setup-and-moves, and occupancy tracking.

Stateless: the board is rebuilt by replaying the setup board plus the cumulative
move list on every stream line. Core does not reimplement legality or win
detection; the server is the referee. Core rebuilds only enough board to feed
the engine.

Server-neutral board model. The opening position is NOT baked in here. The board
the bot plays on is whatever the server delivered in the htttx `setup` packet
(`setup.board.cells`); the bridge carries that seed through `GameState.setup_cells`
and replays the cumulative moves on top of it. The standard HeXO server delivers
exactly one cross at the origin in that packet; a conformant server is free to
deliver a different starting position (under `free_setup`), and the bridge will
play it as delivered rather than falling back to a baked-in origin.

Side to move is NOT derived here. Per the htttx spec the server states which side
to play in each `move_request.side`; the bridge passes that through as
`GameState.side`. There is no ply-parity turn convention in core.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from hexo_bridge.core.move import Coord, Move, Side

SetupCell = tuple[int, int, str]
"""A board cell delivered in the htttx `setup` packet: (q, r, side) where side
is the engine alphabet ('x' or 'o'). The bridge consumes whatever the server
delivers; it does not require these to be the single origin cross."""


@dataclass
class GameState:
    """Full game state the bridge feeds to an engine.

    `setup_cells` is the board the server delivered in the `setup` packet, as a
    list of (q, r, side) cells. It is the seed the cumulative `moves` are
    replayed on top of. Empty when no setup packet has been seen (e.g. the
    offline `validate` dry-run, which plays on an empty board).

    `moves` is the cumulative move list as the server reports it (the completed
    two-stone turns, excluding the setup seed). The board is derived by
    replaying `moves` on top of `setup_cells`.

    `side` is the side this bot plays for this request, taken from
    `move_request.side`. The adapter maps the platform's p1/p2 to x/o at the
    boundary; core never sees p1/p2.

    This object is the value an `EnginePort.get_move` receives. It carries no
    I/O and no platform or engine-transport types.
    """

    side: Side
    setup_cells: list[SetupCell] = field(default_factory=list)
    moves: list[Move] = field(default_factory=list)
    moves_to_apply: list[Move] = field(default_factory=list)
    """Moves played since the last engine request (htttx `previous`). Empty if
    this is the engine's first move for this game. Mirrors the htttx move_request
    `previous` field: ordered, the bot's own prior move first then the client's."""

    time_limit_seconds: float | None = None
    """Clock-remaining for this move in seconds (the htttx `move_time_limit`),
    or None for no server limit. This is the turn clock the server enforces,
    NOT a think budget. The bridge clamps the engine call to
    `min(engine_timeout_seconds, time_limit_seconds)`; an adapter may pass a
    separate suggested budget to its engine (a hint), but the bridge's clamp is
    the hard bound."""

    request_id: int | None = None
    """Optional per-request id for answer-matching on transports that support it.

    Echoed unchanged on the answering move_response when present. When absent,
    the session correlates positionally (at most one request outstanding)."""

    def to_board(self) -> Board:
        """Build a Board by replaying the setup seed plus the cumulative moves."""
        return Board.replay(self.setup_cells, self.moves)


@dataclass
class Board:
    """A board state, rebuilt from the setup seed plus a cumulative move list.

    The board is a dict from Coord to the Side occupying it. Core does not
    validate legality (the server does) and does not detect wins (the server
    does); it only tracks occupancy so an engine can see the current position.
    """

    cells: dict[Coord, Side]

    @classmethod
    def empty(cls) -> Board:
        return cls(cells={})

    @classmethod
    def from_cells(cls, cells: Sequence[SetupCell]) -> Board:
        """Build the seed board from the setup packet's cells.

        `cells` is a list of (q, r, side) tuples as delivered by the server.
        The bridge consumes whatever is delivered; it does not require the
        single origin cross.
        """
        board = cls.empty()
        for q, r, side in cells:
            board.cells[Coord(q, r)] = Side(side)
        return board

    @classmethod
    def replay(
        cls, setup_cells: Sequence[SetupCell], moves: Sequence[Move]
    ) -> Board:
        """Rebuild the board: seed from the setup packet, then replay each move.

        The seed is exactly the `setup.board.cells` the server delivered. The
        moves list is the completed turns the server reports in
        `move_request.previous` over the life of the session; it excludes the
        setup seed. A conformant server delivers the seed in the setup packet,
        not in the move ledger, so replaying moves on top of the seed is
        correct without double-counting.
        """
        board = cls.from_cells(setup_cells)
        for move in moves:
            board._apply(move)
        return board

    def _apply(self, move: Move) -> None:
        for piece in move.pieces:
            self.cells[piece] = move.side

    def occupied(self, coord: Coord) -> bool:
        return coord in self.cells

    def side_at(self, coord: Coord) -> Side | None:
        return self.cells.get(coord)

    @classmethod
    def moves_since(cls, all_moves: Sequence[Move], after_index: int) -> list[Move]:
        """Slice the moves played since `after_index` (htttx `previous`).

        `after_index` is the number of moves the engine has already seen (the
        length of the move list at the last request). Returns the tail.
        """
        if after_index < 0:
            raise ValueError(f"after_index must be non-negative, got {after_index}")
        if after_index > len(all_moves):
            # The engine saw more moves than exist; the cumulative list shrank,
            # which should not happen on a stateless wire. Treat as a fresh view.
            return list(all_moves)
        return list(all_moves[after_index:])
