# Changelog

## v0.3.0

- Targets Bot API 0.11.0; older servers keep working.
- `analyzer.values` in the declaration: `scale`, `meaning`, and `cuts`, checked against the
  API's bounds before sending, a refusal naming the field.
  A server older than 0.11.0 gets the analyzer without it.
- Every request and websocket handshake carries `User-Agent: hexo-bridge/<version>`, the
  version read from the installed package.
- The engine contract is the stable surface, changed only by adding.

## v0.2.0

- Rebuilt as a small library and runner for Bot API 0.10.0, replacing 0.1.0's adapters.
- An engine is a Python `Engine` class or a process speaking JSON lines over stdio.
- The bridge holds the event stream, accepts challenges, plays each game on its websocket,
  passes the level a player picked, and reads positions as an analyzer.
