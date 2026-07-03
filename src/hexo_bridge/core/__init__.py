"""Core domain for HeXO hexagonal tic-tac-toe (httt6).

Pure and I/O-free. This module imports no HTTP, no htttx, no HeXO. The leak test
is: if a HeXO or htttx type appears here, the boundary is wrong.

Facts encoded here (ground truth: Hexo-Bot-Api openapi.yaml, htttx basic_websocket
and stateless specs, and Hexo-Bot-Api SERVER-NOTES.md):

- Coordinates are axial `[q, r]`, unbounded integers. +q goes right, +r goes
  top-right (htttx `Coord`).
- Sides, on the htttx engine protocol, are `x` (crosses) and `o` (circles). The
  HeXO platform surface uses `p1` and `p2` (play order); the mapping is the
  adapter's job, not core's. Core speaks the engine alphabet.
- The server auto-plays the ply-0 opening as a single centre stone at the origin
  (0, 0) before either side moves. A bot never submits the opening (SERVER-NOTES
  item 3, resolved).
- Every submitted turn from ply 1 onward is exactly two stones (htttx `Move`,
  `MoveOption`: `pieces` minItems=2 maxItems=2). The opening ply is one stone.
- The wire is stateless: the board is rebuilt by replaying the cumulative move
  list on every stream line. Core does not reimplement legality or win detection;
  the server is the referee.
- Turn detection: p1 moves on even ply, p2 moves on odd ply. The opening is ply 0
  (the single server-placed cross at origin); ply 1 is p2's first turn, ply 2 is
  p1's first turn, and so on, two stones per turn.
"""

from hexo_bridge.core.board import Board, GameState, Turn
from hexo_bridge.core.move import Coord, Move, Side

__all__ = ["Board", "Coord", "GameState", "Move", "Side", "Turn"]
