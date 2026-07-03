"""Engine port: the abstract interface that returns a move for a game state.

This is the brain. It is NOT htttx; htttx is one implementation (the stateless
HTTP /turn client). An in-process Python callable is another. The port itself
imports no HTTP, no htttx, no HeXO: it speaks only core domain types
(`GameState`, `Move`).

The leak test: if a concrete protocol type appears in this interface, the
boundary is wrong.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Move


class EngineTranslationError(Exception):
    """A bridge-side translation or validation failure, NOT an engine error.

    Raised when an adapter cannot faithfully translate between core domain
    types and the engine's wire shapes (bad coordinate, malformed response,
    HTTP failure). This is a bridge bug, not an engine move: it must never be
    submitted as a move and must never be scored as an engine loss. The
    per-game loop catches this type distinctly from a genuine engine move.

    All engine adapters that do translation should raise this (or a subclass)
    so the bridge stays adapter-agnostic.
    """


@runtime_checkable
class EnginePort(Protocol):
    """Return a move for a game state.

    Implementations may be in-process (a Python callable), an HTTP client to a
    bot-hosted stateless /turn endpoint, or anything else. The bridge times out
    this call under `turn` or `match` time controls and decides the consequence
    if the engine blows the clock.
    """

    async def get_move(self, state: GameState) -> Move: ...
