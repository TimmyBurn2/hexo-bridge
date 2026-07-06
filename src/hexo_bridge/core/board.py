"""Board state, replay-from-cumulative-moves, and turn detection.

Stateless: the board is rebuilt by replaying the cumulative move list on every
stream line. Core does not reimplement legality or win detection; the server is
the referee. Core rebuilds only enough board to feed the engine and to know whose
turn it is.

Ply convention (ground truth: Hexo-Bot-Api SERVER-NOTES.md item 3, resolved):
- ply 0: the server auto-plays the opening, a single cross at the origin (0, 0).
  A bot never submits it.
- ply 1: p2's first turn, two stones.
- ply 2: p1's first turn, two stones.
- even ply >= 2: p1 (x) to move.
- odd ply >= 1: p2 (o) to move.

So with the opening seeded, p1 moves on even ply, p2 moves on odd ply.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from hexo_bridge.core.move import Coord, Move, Side

ORIGIN = Coord(0, 0)


@dataclass(frozen=True)
class Turn:
    """Whose turn it is at a given ply, and the side mapped to the engine alphabet.

    `side` is the engine side (x/o) to move. `ply` is the cumulative turn count
    (0 = opening already placed, no move to make).
    """

    ply: int
    side: Side

    @property
    def is_opening(self) -> bool:
        """True at ply 0, where only the server-placed opening stone exists."""
        return self.ply == 0


@dataclass
class GameState:
    """Full game state the bridge feeds to an engine.

    `moves` is the cumulative move list as the server reports it. The board is
    derived by replaying it on top of the seeded opening.

    This object is the value an `EnginePort.get_move` receives. It carries no
    I/O and no platform or engine-transport types.
    """

    side: Side
    """The side this bot plays. The adapter maps the platform's p1/p2 to x/o."""

    moves: list[Move] = field(default_factory=list)
    """Cumulative moves in order, excluding the server-placed opening at ply 0."""

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
    """Optional per-request id for answer-matching on transports that support it."""

    def to_board(self) -> Board:
        """Build a Board by replaying the opening plus the cumulative moves."""
        return Board.replay(self.moves)


@dataclass
class Board:
    """A board state, rebuilt from the opening plus a cumulative move list.

    The board is a dict from Coord to the Side occupying it. Core does not
    validate legality (the server does) and does not detect wins (the server
    does); it only tracks occupancy so an engine can see the current position.
    """

    cells: dict[Coord, Side]

    @classmethod
    def empty(cls) -> Board:
        return cls(cells={})

    @classmethod
    def with_opening(cls) -> Board:
        """The board after the server auto-plays the opening at the origin.

        The opening is one cross at (0, 0). A bot never submits it.
        """
        return cls(cells={ORIGIN: Side.X})

    @classmethod
    def replay(cls, moves: Sequence[Move]) -> Board:
        """Rebuild the board by seeding the opening then replaying each move.

        The opening is always seeded first: per SERVER-NOTES item 3 the server
        auto-plays the single centre stone at the origin before either side
        moves, and the move list the server reports does not include it.
        """
        board = cls.with_opening()
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
    def turn_for(cls, moves: Sequence[Move]) -> Turn:
        """Whose turn it is given the cumulative move list (opening assumed placed).

        The opening is ply 0: the server placed one cross at the origin. The
        moves list excludes that opening. So len(moves)=0 means the opening is
        placed and it is O's turn (ply 1 is the first submitted turn, played by
        p2/O). len(moves)=1 means one submitted turn (O's), and it is X's turn.

        Even len(moves) -> O to move. Odd len(moves) -> X to move.
        """
        ply = len(moves)
        side = Side.X if ply % 2 == 1 else Side.O
        return Turn(ply=ply, side=side)

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
