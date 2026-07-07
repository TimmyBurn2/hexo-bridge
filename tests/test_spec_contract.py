"""Spec contract test: the hand-written models still match the pinned specs.

Replaces the deleted codegen drift gate. For each spec at the commit pinned in
`pyproject.toml` under `[tool.hexo_bridge.specs]`, this test:

  - fetches the spec at that exact commit from GitHub,
  - parses every example the spec carries against the matching hand-written
    model, failing on any that does not parse,
  - asserts the discriminator enums/consts the bridge branches on match between
    spec and code (`Event.type`, `GameEventInfo.finishReason`, `TimeControl.mode`,
    `DeclineReason`, `Side`, and the htttx packet `type` consts). `Variant` is
    intentionally excluded: it is an open server-scoped string in both spec and
    code, so there is no enum to assert.

CI-only and skippable offline: it is skipped unless `RUN_CONTRACT_TESTS=1` is
set, so `pytest` stays green without network. Unit tests use checked-in fixtures
and stay offline.

`test_contract_check_bites_on_stale_model` proves the check actually fails on a
deliberately-stale model, not just on gross breakage.
"""

from __future__ import annotations

import json
import types
import typing
from typing import get_args, get_origin

import httpx
import pytest
import yaml

from hexo_bridge.adapters.engine_sessions.htttx_models import (
    Board as BwsBoard,
)
from hexo_bridge.adapters.engine_sessions.htttx_models import (
    ConfigurationPacket,
    Coord,
    EvaluationRequestPacket,
    EvaluationResponsePacket,
    GameSetupPacket,
    HeartbeatPacket,
    InterruptPacket,
)
from hexo_bridge.adapters.engine_sessions.htttx_models import (
    Move as BwsMove,
)
from hexo_bridge.adapters.engine_sessions.htttx_models import (
    MoveOption as BwsMoveOption,
)
from hexo_bridge.adapters.engine_sessions.htttx_models import (
    MoveRequestPacket as BwsMoveRequestPacket,
)
from hexo_bridge.adapters.engine_sessions.htttx_models import (
    MoveResponsePacket as BwsMoveResponsePacket,
)
from hexo_bridge.adapters.engines.htttx_stateless_models import (
    MoveOption as StatelessMoveOption,
)
from hexo_bridge.adapters.engines.htttx_stateless_models import (
    StatelessMoveRequest,
    StatelessMoveResponse,
)
from hexo_bridge.adapters.platforms.hexo_models import (
    BotInstance,
    BotListing,
    Capabilities,
    Challenge,
    ChallengeCanceledEvent,
    ChallengeDeclinedEvent,
    ChallengeExpiredEvent,
    DeclineReason,
    EngineSession,
    Event,
    GameEventInfo,
    GameFinishEvent,
    GameStartEvent,
    HardwareInfo,
    OpponentGoneEvent,
    Player,
    RegisteredInstance,
    Side,
    TimeControl,
    Variant,
)
from hexo_bridge.specs import HEXO_REPO, HTTTX_REPO, load_spec_pins

CONTRACT = pytest.mark.contract


def _literal_values(tp: typing.Any) -> set[str]:
    """Return the members of a `Literal[...]` annotation, or an empty set.

    Handles `Literal[...] | None` (pydantic stores these as a union of the
    Literal and `NoneType`); we pick the Literal member out of the union.
    """
    if get_origin(tp) is typing.Literal:
        return {str(a) for a in get_args(tp)}
    origin = get_origin(tp)
    if origin is typing.Union or origin is types.UnionType:
        out: set[str] = set()
        for arg in get_args(tp):
            out |= _literal_values(arg)
        return out
    return set()


def _field_literal(model_cls: type, field_name: str) -> set[str]:
    """Return the Literal members of a pydantic model field annotation."""
    ann = model_cls.model_fields[field_name].annotation
    return _literal_values(ann)


def _union_members(annotated_ann: typing.Any) -> list[type]:
    """Return the member classes of a union annotation.

    Pydantic strips `Annotated[...]` wrappers and stores bare `X | Y | Z`
    unions (`types.UnionType`), so handle both the bare union and the
    `Annotated[Union[...], ...]` shapes.
    """
    inner = annotated_ann
    # Unwrap one level of Annotated[Union[...], ...].
    if get_origin(inner) is typing.Annotated:
        inner = get_args(inner)[0]
    origin = get_origin(inner)
    if origin is typing.Union or origin is types.UnionType:
        return [m for m in get_args(inner) if isinstance(m, type)]
    return [inner] if isinstance(inner, type) else []


def _discriminator_values(model_cls: type, root_field: str, disc_field: str) -> set[str]:
    """Collect the discriminator Literal values across a RootModel union."""
    ann = model_cls.model_fields[root_field].annotation
    out: set[str] = set()
    for member in _union_members(ann):
        out |= _field_literal(member, disc_field)
    return out


# --- Spec-side extraction (from parsed YAML) --------------------------------


def _yaml_enum(node: dict, *path: str) -> set[str]:
    """Read `enum` from a nested YAML node; empty if absent."""
    cur: object = node
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return set()
        cur = cur[key]
    if isinstance(cur, dict) and "enum" in cur:
        return {str(v) for v in cur["enum"]}
    return set()


def _yaml_const(node: dict, *path: str) -> str | None:
    """Read a `const` from a nested YAML node; None if absent."""
    cur: object = node
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    if isinstance(cur, dict) and "const" in cur:
        return str(cur["const"])
    return None


def _hexo_timecontrol_modes(tc_yaml: dict) -> set[str]:
    """Collect `mode` consts across the TimeControl oneOf branches."""
    out: set[str] = set()
    for branch in tc_yaml.get("oneOf", []):
        c = _yaml_const(branch, "properties", "mode")
        if c is not None:
            out.add(c)
    return out


def _hexo_event_types(event_yaml: dict) -> set[str]:
    """The Event discriminator mapping keys (the `type` consts)."""
    mapping = event_yaml.get("discriminator", {}).get("mapping", {})
    return set(mapping.keys())


def _bws_packet_types(bws_yaml: dict) -> set[str]:
    """All packet `type` consts defined in the bws spec."""
    schemas = bws_yaml["components"]["schemas"]
    out: set[str] = set()
    for _name, node in schemas.items():
        c = _yaml_const(node, "properties", "type")
        if c is not None:
            out.add(c)
    return out


# --- Assertion helpers (reused by the bite test) ----------------------------


def assert_enum_matches(name: str, spec_values: set[str], model_values: set[str]) -> None:
    if spec_values != model_values:
        raise AssertionError(
            f"{name} mismatch: spec={sorted(spec_values)} code={sorted(model_values)}; "
            f"only-in-spec={sorted(spec_values - model_values)} "
            f"only-in-code={sorted(model_values - spec_values)}"
        )


def assert_example_parses(name: str, payload: object, model_cls: type) -> None:
    try:
        model_cls.model_validate(payload)
    except Exception as exc:
        raise AssertionError(
            f"{name}: example failed to parse against {model_cls.__name__}: {exc}"
        ) from exc


# --- Fetch helper -----------------------------------------------------------


def _fetch_text(url: str) -> str:
    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _fetch_yaml(pins, repo: str, path: str) -> dict:
    return yaml.safe_load(_fetch_text(pins.raw_url(repo, path)))


# --- The live contract test (CI-only, skippable offline) --------------------


def _run_contract() -> None:
    pins = load_spec_pins()

    # ===== HeXO =====
    # 1. Examples: the event-stream.ndjson fixture, parsed line by line as Event.
    ndjson = _fetch_text(pins.raw_url(HEXO_REPO, "examples/event-stream.ndjson"))
    for i, line in enumerate(ndjson.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        assert_example_parses(f"hexo event-stream.ndjson:{i}", json.loads(stripped), Event)

    # 2. Per-schema examples that map to fully-modelled types.
    schemas_dir = "components/schemas"
    schema_example_models: dict[str, type] = {
        "Side.yaml": Side,
        "Variant.yaml": Variant,
        "DeclineReason.yaml": DeclineReason,
        "TimeControl.yaml": TimeControl,
        "Player.yaml": Player,
        "Challenge.yaml": Challenge,
        "GameEventInfo.yaml": GameEventInfo,
        "EngineSession.yaml": EngineSession,
        "GameStartEvent.yaml": GameStartEvent,
        "GameFinishEvent.yaml": GameFinishEvent,
        "OpponentGoneEvent.yaml": OpponentGoneEvent,
        "ChallengeDeclinedEvent.yaml": ChallengeDeclinedEvent,
        "ChallengeCanceledEvent.yaml": ChallengeCanceledEvent,
        "ChallengeExpiredEvent.yaml": ChallengeExpiredEvent,
        # Loose round-trip objects: their examples must still parse.
        "BotInstance.yaml": BotInstance,
        "BotListing.yaml": BotListing,
        "RegisteredInstance.yaml": RegisteredInstance,
        "Capabilities.yaml": Capabilities,
        "HardwareInfo.yaml": HardwareInfo,
    }
    for fname, model in schema_example_models.items():
        node = _fetch_yaml(pins, HEXO_REPO, f"{schemas_dir}/{fname}")
        if "example" in node:
            assert_example_parses(f"hexo {fname} example", node["example"], model)

    # 3. Discriminator enums match.
    side_yaml = _fetch_yaml(pins, HEXO_REPO, f"{schemas_dir}/Side.yaml")
    decline_yaml = _fetch_yaml(pins, HEXO_REPO, f"{schemas_dir}/DeclineReason.yaml")
    finish_reason_yaml = _fetch_yaml(pins, HEXO_REPO, f"{schemas_dir}/FinishReason.yaml")
    tc_yaml = _fetch_yaml(pins, HEXO_REPO, f"{schemas_dir}/TimeControl.yaml")
    event_yaml = _fetch_yaml(pins, HEXO_REPO, f"{schemas_dir}/Event.yaml")

    assert_enum_matches("Side", _yaml_enum(side_yaml), _field_literal(Side, "root"))
    # `Variant` is an open server-scoped string in both spec (no `enum`) and code
    # (`RootModel[str]`), so there is nothing to assert; its example still parses
    # above. A future spec that re-introduces a closed `Variant` enum would need
    # an assertion re-added here to guard the bridge's branching.
    assert_enum_matches(
        "DeclineReason", _yaml_enum(decline_yaml), _field_literal(DeclineReason, "root")
    )
    assert_enum_matches(
        "TimeControl.mode",
        _hexo_timecontrol_modes(tc_yaml),
        _discriminator_values(TimeControl, "root", "mode"),
    )
    # `finishReason` lives on its own `FinishReason` schema (single source of
    # truth), `$ref`'d from `GameEventInfo` and the bulk-pairing results read.
    assert_enum_matches(
        "FinishReason",
        _yaml_enum(finish_reason_yaml),
        _field_literal(GameEventInfo, "finishReason"),
    )
    assert_enum_matches(
        "Event.type", _hexo_event_types(event_yaml), _discriminator_values(Event, "root", "type")
    )

    # ===== htttx bws =====
    bws = _fetch_yaml(pins, HTTTX_REPO, "definitions/basic_websocket/bws-v1-alpha.yaml")
    bws_schemas = bws["components"]["schemas"]
    bws_example_models: dict[str, type] = {
        "MoveRequestPacket": BwsMoveRequestPacket,
        "MoveResponsePacket": BwsMoveResponsePacket,
        "InterruptPacket": InterruptPacket,
        "MoveOption": BwsMoveOption,
        "Move": BwsMove,
        "Board": BwsBoard,
        "Coord": Coord,
        "GameSetupPacket": GameSetupPacket,
        "HeartbeatPacket": HeartbeatPacket,
        "ConfigurationPacket": ConfigurationPacket,
        "EvaluationRequestPacket": EvaluationRequestPacket,
        "EvaluationResponsePacket": EvaluationResponsePacket,
    }
    for name, model in bws_example_models.items():
        node = bws_schemas.get(name, {})
        for ex in node.get("examples", []):
            assert_example_parses(f"bws {name} example", ex, model)

    assert_enum_matches(
        "bws packet type consts", _bws_packet_types(bws), _bws_packet_types_in_code()
    )

    # ===== htttx stateless =====
    stateless = _fetch_yaml(pins, HTTTX_REPO, "definitions/stateless/stateless-v1-alpha.yaml")
    sl_schemas = stateless["components"]["schemas"]
    sl_example_models: dict[str, type] = {
        "MoveRequest": StatelessMoveRequest,
        "MoveResponse": StatelessMoveResponse,
        "Move": StatelessMoveOption,
    }
    for name, model in sl_example_models.items():
        node = sl_schemas.get(name, {})
        for ex in node.get("examples", []):
            assert_example_parses(f"stateless {name} example", ex, model)


def _bws_packet_types_in_code() -> set[str]:
    """The packet `type` consts the hand-written bws models declare."""
    models = [
        GameSetupPacket,
        BwsMoveRequestPacket,
        BwsMoveResponsePacket,
        HeartbeatPacket,
        ConfigurationPacket,
        InterruptPacket,
        EvaluationRequestPacket,
        EvaluationResponsePacket,
    ]
    out: set[str] = set()
    for m in models:
        out |= _field_literal(m, "type")
    return out


CONTRACT_ENV = "RUN_CONTRACT_TESTS"
skip_offline = pytest.mark.skipif(
    CONTRACT_ENV not in __import__("os").environ,
    reason=f"set {CONTRACT_ENV}=1 to run the spec contract test (needs network)",
)


@CONTRACT
@skip_offline
async def test_specs_at_pinned_commit_match_models() -> None:
    """The live fetch + parse + enum check. Skipped offline."""
    _run_contract()


# --- The bite test: proves the check fails on a stale model (offline) -------


def test_contract_check_bites_on_stale_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spec enum that gains a value the code does not know must fail the check.

    This is the proof that the contract check catches an additive enum drift
    (a new `finishReason`), not just gross structural breakage.
    """

    class StaleGameEventInfo(GameEventInfo):
        # Intentionally missing `illegal-move` from the finishReason set.
        finishReason: (
            typing.Literal["surrender", "timeout", "disconnect", "terminated", "six-in-a-row"]
            | None
        ) = None

    spec_values = {
        "surrender",
        "timeout",
        "disconnect",
        "terminated",
        "six-in-a-row",
        "illegal-move",
    }
    code_values = _field_literal(StaleGameEventInfo, "finishReason")
    with pytest.raises(AssertionError, match="illegal-move"):
        assert_enum_matches("GameEventInfo.finishReason (stale)", spec_values, code_values)


def test_contract_check_bites_on_renamed_required_field() -> None:
    """A renamed required field must fail example parsing.

    This is the proof that the contract check catches a renamed required field,
    not just an enum drift.
    """
    payload = {
        "id": "g",
        "side": "p1",
        "opponent": {"id": "b", "name": "B"},
        "variant": "httt6",
        "rated": True,
    }
    # GameEventInfo requires `opponent` as a Player with `name`; rename it and
    # parsing must fail.
    bad_payload = dict(payload)
    bad_payload["opponent"] = {"id": "b"}  # missing required `name`
    with pytest.raises(AssertionError, match="failed to parse"):
        assert_example_parses("renamed-required", bad_payload, GameEventInfo)


def test_contract_check_passes_on_valid_example() -> None:
    """Sanity: a valid example parses cleanly, so the bite tests above are
    meaningful (the check is not just always-failing)."""
    payload = {
        "id": "g",
        "side": "p1",
        "opponent": {"id": "b", "name": "B"},
        "variant": "httt6",
        "rated": True,
        "status": "finished",
        "finishReason": "six-in-a-row",
        "winner": "p1",
    }
    assert_example_parses("valid", payload, GameEventInfo)
