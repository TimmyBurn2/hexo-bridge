"""SubprocessEngine: drive a child process speaking one JSON object per line.

Modelled on lichess-bot's protocol-adapter layer (UCI/XBoard): a uniform base
owns process lifecycle and line framing, concrete subclasses implement the
request/response translation. The base is engine-agnostic and language-
agnostic; the child may be Python (its own venv/ABI), Rust, C++, or anything
that reads JSON lines from stdin and writes JSON lines to stdout.

The base owns:
  - process spawn (its own venv/Python/ABI/language),
  - JSON-line framing over stdin/stdout,
  - stderr capture and surfacing in the error when the child fails (the fix
    for the opaque `EngineTranslationError`: a bad Python or failed import
    produces a real message with the captured stderr),
  - restart-on-death (kill + null on a broken pipe or empty response; the
    next request respawns a fresh child),
  - a per-instance asyncio.Lock so concurrent `get_move` calls do not
    interleave on the pipe.

    The base does NOT clamp think-time. The bridge's `asyncio.wait_for` is the
    hard bound on the engine call (clamped to `min(engine_timeout, clock)`). A
    subclass MAY include a `time_limit` field in its request payload as a
    suggested per-move budget; that is a hint the subclass sets, not a clamp.

    Concurrency: the bridge builds ONE engine instance shared across all games
    (see `bridge.run_bridge`). The per-instance `asyncio.Lock` serializes
    concurrent `get_move` calls on the single stdin/stdout pipe, so with N
    concurrent games a slow game A holds the lock and game B's call blocks
    behind it, counting against B's turn clock. If B's clock expires while
    waiting, B forfeits without ever calling its engine. For a real engine with
    sub-second think times this is fine; for a slow engine under many concurrent
    games, run one bridge process per game (one engine instance each) instead.

    Subclass contract: override `build_request(state) -> dict` and
    `parse_response(obj, state) -> Move`. `parse_response` may return a `Move`
    of one or two pieces; the bridge normalizes a one-piece move to two before
    it is sent on the wire.
    """

from __future__ import annotations

import asyncio
import json
from typing import Any

from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Move
from hexo_bridge.ports.engine import SubprocessEngineError


class SubprocessEngine:
    """Drive a child process speaking JSON lines over stdin/stdout.

    Concrete subclasses implement `build_request` and `parse_response`; the
    base handles spawn, framing, stderr capture, restart-on-death, and
    lifecycle.
    """

    def __init__(
        self,
        *,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        restart: bool = True,
    ) -> None:
        self._command = list(command)
        self._cwd = cwd
        self._env = env
        self._restart = restart
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()

    # subclass contract ----------------------------------------------------
    def build_request(self, state: GameState) -> dict[str, Any]:
        raise NotImplementedError

    def parse_response(self, obj: dict[str, Any], state: GameState) -> Move:
        raise NotImplementedError

    # EnginePort -----------------------------------------------------------
    async def get_move(self, state: GameState) -> Move:
        request = self.build_request(state)
        line = await self._send(request)
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SubprocessEngineError(
                f"malformed JSON line: {line!r}", stderr=await self._drain_stderr()
            ) from exc
        return self.parse_response(obj, state)

    # lifecycle ------------------------------------------------------------
    async def _ensure_proc(self) -> asyncio.subprocess.Process:
        if self._proc is not None and self._proc.returncode is None:
            return self._proc
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._command,
                cwd=self._cwd,
                env=self._env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise SubprocessEngineError(
                f"cannot spawn engine (command not found): {' '.join(self._command)}",
                stderr=None,
            ) from exc
        except OSError as exc:
            raise SubprocessEngineError(
                f"cannot spawn engine: {exc}", stderr=None
            ) from exc
        return self._proc

    async def _send(self, request: dict[str, Any]) -> bytes:
        async with self._lock:
            proc = await self._ensure_proc()
            assert proc.stdin is not None and proc.stdout is not None
            payload = (json.dumps(request) + "\n").encode()
            try:
                proc.stdin.write(payload)
                await proc.stdin.drain()
                line = await proc.stdout.readline()
            except (BrokenPipeError, ConnectionResetError) as exc:
                stderr = await self._drain_stderr()
                if self._restart:
                    await self._kill(proc)
                    self._proc = None
                raise SubprocessEngineError(
                    f"engine pipe broke: {exc}", stderr=stderr
                ) from exc
            if not line:
                # Child closed stdout or died. Surface stderr so an import or
                # ABI failure is debuggable instead of opaque.
                stderr = await self._drain_stderr()
                rc = proc.returncode
                if self._restart:
                    await self._kill(proc)
                    self._proc = None
                raise SubprocessEngineError(
                    f"engine produced no response (rc={rc})", stderr=stderr
                )
            return line

    async def _drain_stderr(self) -> str | None:
        """Read whatever stderr the child has buffered, with a short timeout.

        Reads line-by-line until EOF or a 0.5s total bound. A dead child closes
        stderr quickly and the full traceback is returned. A hung-but-alive
        child flushing slowly is cut at 0.5s; whatever it already flushed is
        returned (better than the old `read()` which discarded everything on
        timeout). 0.5s is a default chosen to capture an import/ABI traceback
        without wedging the bridge on a child that is stuck but alive.
        """
        proc = self._proc
        if proc is None or proc.stderr is None:
            return None
        lines: list[str] = []
        try:
            while True:
                line = await asyncio.wait_for(proc.stderr.readline(), timeout=0.5)
                if not line:
                    break
                lines.append(line.decode(errors="replace"))
        except TimeoutError:
            pass
        except Exception:
            return None
        text = "".join(lines).strip()
        return text or None

    async def _kill(self, proc: asyncio.subprocess.Process) -> None:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    async def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None and proc.returncode is None:
                try:
                    proc.stdin.write(b"quit\n")
                    await proc.stdin.drain()
                except (BrokenPipeError, ConnectionResetError, RuntimeError):
                    pass
                proc.stdin.close()
            if proc.returncode is None:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
        except (TimeoutError, ProcessLookupError, BrokenPipeError, RuntimeError):
            await self._kill(proc)
