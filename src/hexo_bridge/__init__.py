"""hexo_bridge: reference adapter between a HeXO bot server and a bot engine.

Ports and adapters. Core is pure and does no I/O. The engine port returns a move
for a game state; the engine session port is the per-game gameplay channel; the
platform port is the HeXO lifecycle surface. Adapters are resolved by name via
entry points with a dotted-path fallback.
"""

from hexo_bridge.core.board import Board, GameState
from hexo_bridge.core.move import Coord, Move, Side

__all__ = ["Board", "Coord", "GameState", "Move", "Side"]

__version__ = "0.1.0"
