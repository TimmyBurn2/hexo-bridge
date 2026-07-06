"""The runnable bridge: load config, resolve adapters, run the loop.

Responsibilities:
  - Load TOML config and resolve adapters via the registry.
  - Open the global event stream and dispatch events.
  - For each `gameStart`, open an engine session and run the per-game loop.
  - Handle `409`/`422`/`429` from platform calls (resign returns False on 409,
    challenges surface errors as exceptions).
  - Reconnect on a dropped stream with backoff (event-stream supersede:
    opening a new stream closes the previous one for the same token).
  - Isolate per-game failures so one crashing game does not kill the whole loop.
  - Time out engine calls under `turn` or `match` time controls.

The per-game loop (run as an `asyncio.Task`):
  1. Open an EngineSessionPort to `gameStart.engine.socketUrl` with the per-game token.
  2. recv loop: on `move_request`, build a `GameState`, call `EnginePort.get_move`
     (with a timeout), and send the result via `send_move_response`.
  3. On `SessionClosed`, the game is done; reconcile with the `gameFinish` event.
  4. On a bridge-side translation error, do NOT send a move; surface as a fault.
     A genuine engine move that the server rejects is handled by the server
     (`finishReason: illegal-move`), not by the bridge resigning.

Retry safety: there is no HeXO move POST, so there is no CAS `ply` to guard.
The "a resent move cannot double-apply" property lives in htttx `request_id`
answer-matching, enforced inside the engine session adapter: it echoes the
server-assigned `request_id` unchanged on each `move_response` and drops a
stale (interrupted) or mismatched (reordered) answer rather than sending it.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from hexo_bridge.core.board import GameState
from hexo_bridge.core.move import Coord, Move, Side
from hexo_bridge.ports.engine import EnginePort, EngineTranslationError
from hexo_bridge.ports.engine_session import (
    EngineSessionPort,
    MoveRequestPacket,
    SessionClosed,
)
from hexo_bridge.ports.platform import PlatformPort
from hexo_bridge.registry.config import BridgeConfig
from hexo_bridge.registry.resolver import (
    ENGINE_GROUP,
    ENGINE_SESSION_GROUP,
    PLATFORM_GROUP,
    resolve_adapter,
)

logger = logging.getLogger("hexo_bridge")


def build_platform(config: BridgeConfig) -> PlatformPort:
    """Resolve and construct the platform adapter from config.

    The `[bridge] stream_read_timeout_seconds` is a bridge-level concern threaded
    into the platform options here (the platform adapter accepts it as
    `stream_read_timeout`), so it does not need to be duplicated under
    `[platform.options]`.

    Credentials are the platform adapter's concern, not the bridge's: the HeXO
    adapter requires a token and fails without one, a platform that needs none
    (the loopback harness) constructs with none.
    """
    cls = resolve_adapter(config.platform.name, PLATFORM_GROUP)
    options = dict(config.platform.options)
    if "stream_read_timeout" not in options:
        options["stream_read_timeout"] = config.stream_read_timeout_seconds
    return cls(**options)


def build_engine(config: BridgeConfig) -> EnginePort:
    """Resolve and construct the engine adapter from config."""
    cls = resolve_adapter(config.engine.name, ENGINE_GROUP)
    return cls(**config.engine.options)


def build_engine_session_factory(config: BridgeConfig) -> Any:
    """Resolve the engine session adapter class from config."""
    return resolve_adapter(config.engine_session.name, ENGINE_SESSION_GROUP)


@dataclass
class GameContext:
    game_id: str
    side: Side
    session: EngineSessionPort
    engine: EnginePort
    moves_seen: int = 0
    cumulative_moves: list[Move] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.cumulative_moves is None:
            self.cumulative_moves = []


async def run_game(
    ctx: GameContext,
    engine_timeout: float,
    stop_event: asyncio.Event,
) -> None:
    """Run one game's session loop until the session closes or the bridge stops.

    Per-game isolation: any exception is caught here and logged, so one crashing
    game does not kill the whole bridge loop. The game's task simply ends.
    """
    try:
        await ctx.session.__aenter__()
        while not stop_event.is_set():
            try:
                packet = await asyncio.wait_for(ctx.session.recv(), timeout=None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("game %s: recv error: %s", ctx.game_id, exc)
                break

            if isinstance(packet, SessionClosed):
                logger.info("game %s: session closed (%s)", ctx.game_id, packet.reason)
                break

            if isinstance(packet, MoveRequestPacket):
                await _handle_move_request(ctx, packet, engine_timeout)
                continue
            # setup, heartbeat: no action needed
    except Exception:
        logger.exception("game %s: per-game loop crashed", ctx.game_id)
    finally:
        try:
            await ctx.session.close()
        except Exception:
            pass


async def _handle_move_request(
    ctx: GameContext, packet: MoveRequestPacket, engine_timeout: float
) -> None:
    """Handle a single move_request: build state, call engine, send response.

    Distinguishes bridge-side translation errors from engine errors:
    - EngineTranslationError: a bridge bug. Do NOT send a move. The server
      will time out the side and forfeit via `finishReason: timeout` (its call),
      but the bridge does not resign, so it is not scored as an engine loss.
    - A genuine engine move: send as-is. If the server rejects it, the server
      ends the game with `finishReason: illegal-move`. No retry loop.

    Move tracking: `previous` is the delta since the last move_request. It
    includes the bot's own last move as its first element (htttx spec). The
    bridge does NOT append its own move when sending a move_response; it lets
    the move come back in `previous` on the next request. This avoids
    double-counting.

    Retry safety: the `request_id` carried on the packet is echoed unchanged on
    the `move_response`. The session adapter drops a stale or mismatched answer,
    so a resent or reordered response cannot double-apply (htttx answer-matching).
    """
    for mv_side, pieces in packet.previous:
        p1 = (pieces[0][0], pieces[0][1])
        p2 = (pieces[1][0], pieces[1][1])
        ctx.cumulative_moves.append(Move(side=mv_side, pieces=(Coord(*p1), Coord(*p2))))
        ctx.moves_seen += 1

    if packet.side is not ctx.side:
        return

    time_limit = packet.time_limit_seconds
    timeout = min(engine_timeout, time_limit) if time_limit else engine_timeout

    state = GameState(
        side=ctx.side,
        moves=list(ctx.cumulative_moves),
        moves_to_apply=list(ctx.cumulative_moves[-len(packet.previous) :])
        if packet.previous
        else [],
        time_limit_seconds=time_limit,
        request_id=packet.request_id,
    )

    try:
        move = await asyncio.wait_for(ctx.engine.get_move(state), timeout=timeout)
    except TimeoutError:
        logger.warning(
            "game %s: engine timed out after %.1fs (clock: %s)",
            ctx.game_id,
            timeout,
            "turn" if time_limit else "no-limit",
        )
        return
    except EngineTranslationError as exc:
        logger.error("game %s: bridge translation error (NOT submitting): %s", ctx.game_id, exc)
        return
    except Exception:
        logger.exception("game %s: engine.get_move crashed", ctx.game_id)
        return

    try:
        await ctx.session.send_move_response(move, packet.request_id)
        # Do NOT append to cumulative_moves here. The bot's move comes back in
        # the next move_request's `previous` field. Appending here would
        # double-count it on the next request.
    except EngineTranslationError as exc:
        logger.error("game %s: bridge translation error on send (NOT scored): %s", ctx.game_id, exc)
    except Exception:
        logger.exception("game %s: send_move_response failed", ctx.game_id)


async def run_bridge(
    config: BridgeConfig,
    *,
    platform: PlatformPort | None = None,
    engine: EnginePort | None = None,
) -> None:
    """Run the bridge: open the global stream, dispatch events, manage games.

    Reconnects on a dropped stream with backoff. Per-game failures are isolated.
    `platform` and `engine` override config resolution when the caller already
    holds a constructed adapter (tests inspect the instance after the run).
    """
    platform = platform or build_platform(config)
    engine = engine or build_engine(config)
    session_cls = build_engine_session_factory(config)

    games: dict[str, asyncio.Task] = {}
    stop_event = asyncio.Event()

    try:
        await _run_stream_loop(platform, engine, session_cls, config, games, stop_event)
    finally:
        stop_event.set()
        for task in games.values():
            task.cancel()
        await asyncio.gather(*games.values(), return_exceptions=True)
        await platform.close()
        if hasattr(engine, "close"):
            await engine.close()


async def _run_stream_loop(
    platform: PlatformPort,
    engine: EnginePort,
    session_cls: Any,
    config: BridgeConfig,
    games: dict[str, asyncio.Task],
    stop_event: asyncio.Event,
) -> None:
    backoff = config.reconnect_backoff_seconds
    max_backoff = config.reconnect_max_seconds

    while not stop_event.is_set():
        try:
            # Re-assert availability after (re)connecting the stream. Per the
            # spec, an instance is open only while it both advertises open AND
            # holds a stream; after a reconnect it must re-assert.
            try:
                resolved = await platform.play.set_bot_status(True)
                if not resolved:
                    logger.warning("bot is not open for challenges despite advertising: no stream?")
            except Exception:
                logger.debug("set_bot_status failed (non-fatal)", exc_info=True)

            async for event in platform.events.stream_events():
                await _dispatch_event(
                    event, platform, engine, session_cls, config, games, stop_event
                )
            logger.info("global stream ended")
        except Exception:
            logger.exception("global stream error")

        # A platform whose event supply is finite marks its events sub-port
        # `exhausted` when there is nothing left to stream; the bridge stops
        # instead of reconnecting. Absent attribute (HeXO) means never
        # exhausted, so the reconnect loop is unchanged there.
        if getattr(platform.events, "exhausted", False):
            logger.info("event supply exhausted; shutting down")
            break
        if stop_event.is_set():
            break
        logger.info("reconnecting in %.1fs", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, max_backoff)


async def _dispatch_event(
    event: Any,
    platform: PlatformPort,
    engine: EnginePort,
    session_cls: Any,
    config: BridgeConfig,
    games: dict[str, asyncio.Task],
    stop_event: asyncio.Event,
) -> None:
    """Dispatch one global stream event."""
    etype = getattr(event, "type", None) or (event.root.type if hasattr(event, "root") else None)

    if etype == "gameStart":
        await _on_game_start(event, engine, session_cls, config, games, stop_event)
    elif etype == "gameFinish":
        await _on_game_finish(event, games)
    elif etype == "challenge":
        logger.info("challenge received: %s", _challenge_id(event))
    elif etype == "challengeDeclined":
        logger.info("challenge declined: %s", _challenge_id(event))
    elif etype == "challengeCanceled":
        logger.info("challenge canceled: %s", _challenge_id(event))
    elif etype == "challengeExpired":
        logger.info("challenge expired: %s", _challenge_id(event))
    elif etype == "opponentGone":
        gid = getattr(_unwrap(event), "gameId", "?")
        logger.info("opponent gone in game %s", gid)


async def _on_game_start(
    event: Any,
    engine: EnginePort,
    session_cls: Any,
    config: BridgeConfig,
    games: dict[str, asyncio.Task],
    stop_event: asyncio.Event,
) -> None:
    """Open an engine session for a new game and start the per-game loop.

    On reconnect, the server re-emits gameStart for in-progress games with a
    fresh engine bootstrap. If a task for this game_id already exists (stale
    session from a dropped connection), cancel it before creating the
    replacement so the old session is not orphaned.
    """
    ev = _unwrap(event)
    game = ev.game
    game_id = game.id
    engine_info = ev.engine

    old_task = games.get(game_id)
    if old_task is not None and not old_task.done():
        logger.info("game %s: replacing stale session on reconnect", game_id)
        old_task.cancel()
        try:
            await old_task
        except (asyncio.CancelledError, Exception):
            pass

    platform_side = str(game.side.root) if hasattr(game.side, "root") else str(game.side)
    side = Side.X if platform_side == "p1" else Side.O

    session = session_cls(**config.engine_session.options)
    try:
        # socketUrl is a pydantic AnyUrl; the session port takes a str.
        await session.connect(str(engine_info.socketUrl), engine_info.token)
    except Exception:
        logger.exception("game %s: failed to connect engine session", game_id)
        return

    ctx = GameContext(game_id=game_id, side=side, session=session, engine=engine)
    task = asyncio.create_task(
        run_game(ctx, config.engine_timeout_seconds, stop_event),
        name=f"game-{game_id}",
    )
    games[game_id] = task
    logger.info("game %s: started (side=%s)", game_id, platform_side)


async def _on_game_finish(event: Any, games: dict[str, asyncio.Task]) -> None:
    """Handle a gameFinish event: cancel the per-game task."""
    ev = _unwrap(event)
    game = ev.game
    game_id = game.id
    task = games.pop(game_id, None)
    if task is not None:
        task.cancel()
    reason = getattr(game, "finishReason", "?")
    winner = getattr(game, "winner", "?")
    logger.info("game %s: finished (reason=%s, winner=%s)", game_id, reason, winner)


def _unwrap(event: Any) -> Any:
    """The Event is a RootModel union; unwrap to the inner model."""
    if hasattr(event, "root"):
        return event.root
    return event


def _challenge_id(event: Any) -> str:
    ev = _unwrap(event)
    return getattr(getattr(ev, "challenge", None), "id", "?")
