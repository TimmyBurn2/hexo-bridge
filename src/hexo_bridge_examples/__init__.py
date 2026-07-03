"""Example third-party engine package for hexo-bridge.

This is a tiny, self-contained package shipped next to `hexo_bridge` to show the
"write your own adapter" path. It registers one engine, `my_custom_engine`,
under the `hexo_bridge.engines` entry-point group (see `pyproject.toml`), and
the same class is also resolvable by dotted path
(`hexo_bridge_examples.custom_engine:FirstLegalMoveEngine`).

A real third-party adapter would live in its own repository and its own
distribution, registering its own entry point. The mechanism is identical:
implement `EnginePort`, declare the entry point, point a config at it. See
`docs/write-your-own-adapter.md`.
"""
