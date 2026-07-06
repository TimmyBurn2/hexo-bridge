"""Core domain for HeXO hexagonal tic-tac-toe (httt6).

Pure and I/O-free. This module imports no HTTP, no htttx, no HeXO. The leak test
is: if a HeXO or htttx type appears here, the boundary is wrong.

Facts encoded here (ground truth: Hexo-Bot-Api openapi.yaml, htttx basic_websocket
and stateless specs):

- Coordinates are axial `[q, r]`, unbounded integers. +q goes right, +r goes
  top-right (htttx `Coord`).
- Sides, on the htttx engine protocol, are `x` (crosses) and `o` (circles). The
  HeXO platform surface uses `p1` and `p2` (play order); the mapping is the
  adapter's job, not core's. Core speaks the engine alphabet.
- The opening board is delivered by the server in the htttx `setup` packet's
  `board.cells`. The bridge consumes whatever is delivered; it does not bake in
  an origin. The standard HeXO server delivers one cross at the origin there;
  a conformant server may deliver a different starting position (under
  `free_setup`), and the bridge plays it as delivered.
- The side to move is stated by the server in each `move_request.side`. Core
  does not derive side from ply parity; it takes the server's word.
- Every submitted turn is exactly two stones (htttx `Move`, `MoveOption`:
  `pieces` minItems=2 maxItems=2). A single-stone move is only ever the
  engine returning a first-cross that wins; the bridge pads it to two before
  sending it on the wire.
- The wire is stateless: the board is rebuilt by replaying the setup seed plus
  the cumulative move list on every stream line. Core does not reimplement
  legality or win detection; the server is the referee.
"""

from hexo_bridge.core.board import Board, GameState
from hexo_bridge.core.move import Coord, Move, Side

__all__ = ["Board", "Coord", "GameState", "Move", "Side"]
