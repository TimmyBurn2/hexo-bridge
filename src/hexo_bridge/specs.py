"""Spec provenance: the exact commits the hand-written models were built against.

Single source of truth: `[tool.hexo_bridge.specs]` in `pyproject.toml`. The
contract test (`tests/test_spec_contract.py`) and the README both read from here
so the SHA can never disagree between prose and code.

The specs themselves are NOT vendored into the repo; they are fetched at the
pinned commit by the contract test. See OPEN-QUESTIONS item 3 (closed).
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

HEXO_REPO = "TimmyBurn2/Hexo-Bot-Api"
HTTTX_REPO = "hex-tic-tac-toe/htttx-bot-api"


@dataclass(frozen=True)
class SpecPins:
    hexo: str
    htttx: str

    def raw_url(self, repo: str, path: str) -> str:
        sha = self.hexo if repo == HEXO_REPO else self.htttx
        return f"https://raw.githubusercontent.com/{repo}/{sha}/{path}"


def load_spec_pins() -> SpecPins:
    """Read the pinned spec commits from `[tool.hexo_bridge.specs]` in pyproject."""
    import pathlib

    pyproject = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)
    specs = data["tool"]["hexo_bridge"]["specs"]
    return SpecPins(hexo=str(specs["hexo"]), htttx=str(specs["htttx"]))
