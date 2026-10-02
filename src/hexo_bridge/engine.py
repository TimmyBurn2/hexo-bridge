"""The engine interface, and an engine in any language run as a process speaking JSON lines."""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from hexo_bridge.answers import Line, Resign
from hexo_bridge.position import Position, Side, Turn

log = logging.getLogger("hexo_bridge")


@dataclass(frozen=True)
class Level:
    """The strength a game is played at: a declared level's id and budget, or the default's."""

    id: str | None
    budget: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Game:
    """A game as its `gameStart` describes it."""

    id: str
    side: Side
    opponent: str
    rated: bool
    level: Level
    time_control: Mapping[str, Any]
    opening_plies: int


@dataclass
class MoveRequest:
    game: Game
    # Seconds the engine has for this move: the server's limit less the bridge's reserve,
    # or None without a clock. Past it the bridge plays a fallback turn instead.
    time_limit: float | None
    # Set once the answer is no longer wanted; a long search should check it.
    stop: threading.Event = field(default_factory=threading.Event)


@dataclass
class AnalysisRequest:
    seconds: float
    lines: int
    stop: threading.Event = field(default_factory=threading.Event)


class Engine:
    """Override `move`, and `analyze` when the bot declares an analyzer.

    The bridge makes one instance per game, with its `Game`, and one per analysis
    session, with None. It never calls an instance from two threads at once: a call that
    outlives its request gets `kill()`, and the instance is closed once that call returns
    and replaced for the next request.
    """

    def __init__(self, game: Game | None) -> None:
        self.game = game

    def move(
        self, position: Position, request: MoveRequest
    ) -> Turn | Line | Sequence[Line] | Resign:
        """The turn to play, best line first; an evaluation on a line is published with the game."""
        raise NotImplementedError

    def analyze(self, position: Position, request: AnalysisRequest) -> Line | Sequence[Line]:
        """Up to `request.lines` lines, best first, each evaluated, within `request.seconds`."""
        raise NotImplementedError

    def kill(self) -> None:
        """Stop a call that outlived its request; a process can be killed, a thread only asked."""

    def close(self) -> None:
        """Release what the instance holds."""


def board_wire(position: Position) -> dict[str, Any]:
    cells = [{"q": q, "r": r, "p": side} for (q, r), side in position.stones.items()]
    return {"to_move": position.to_move, "cells": cells}


class StdioEngine(Engine):
    """An engine run as a process: a JSON request per line on stdin, an answer per line on stdout.

    A request is an htttx MoveRequest with a `type` of `move` or `analyze`; the answer is an
    htttx MoveResponse, or `{"resign": true}` for a move. The process starts on the first
    request and again after it dies or is killed, so it keeps no state the bridge relies on.
    """

    def __init__(self, game: Game | None, command: Sequence[str], cwd: str | None = None) -> None:
        super().__init__(game)
        self.command = list(command)
        self.cwd = cwd
        self.process: subprocess.Popen[str] | None = None

    def move(self, position: Position, request: MoveRequest) -> Line | list[Line] | Resign:
        game = request.game
        return self._ask(
            {
                "type": "move",
                "board": board_wire(position),
                "time_limit": request.time_limit,
                "game": {
                    "id": game.id,
                    "side": game.side,
                    "opponent": game.opponent,
                    "rated": game.rated,
                },
                "level": {"id": game.level.id, "budget": dict(game.level.budget)},
            },
            lambda answer: Resign() if answer.get("resign") else _lines(answer),
        )

    def analyze(self, position: Position, request: AnalysisRequest) -> list[Line]:
        return self._ask(
            {
                "type": "analyze",
                "board": board_wire(position),
                "time_limit": request.seconds,
                "lines": request.lines,
            },
            _lines,
        )

    def _ask(self, request: dict[str, Any], parse: Callable[[dict[str, Any]], Any]) -> Any:
        process = self.process
        if process is None or process.poll() is not None:
            process = self.process = subprocess.Popen(
                self.command,
                cwd=self.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        assert process.stdin is not None and process.stdout is not None
        try:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            while True:
                line = process.stdout.readline()
                if not line:
                    raise RuntimeError(f"engine process exited with {process.wait()}")
                if line.lstrip().startswith("{"):
                    break
                # Anything but a JSON object is the engine talking, not answering.
                log.debug("engine: %s", line.rstrip())
            answer = json.loads(line)
            if not isinstance(answer, dict):
                raise ValueError(f"engine answered {line.strip()[:200]!r}, not a JSON object")
            return parse(answer)
        except Exception:
            # One stray line would leave every later answer a request behind: start afresh.
            self.kill()
            self.process = None
            raise

    def kill(self) -> None:
        if self.process is not None:
            self.process.kill()

    def close(self) -> None:
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.stdin is not None:
                process.stdin.close()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()


def _lines(answer: Mapping[str, Any]) -> list[Line]:
    return [
        Line.from_wire(answer["move"]),
        *(Line.from_wire(item) for item in answer.get("considerations") or []),
    ]
