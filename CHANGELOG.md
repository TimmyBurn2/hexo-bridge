# Changelog

## v0.4.1

- Works with websockets 17; the lock moves to websockets 17.2.
- The package names its author and links the repository, this changelog, and the Bot API.
- Every push and pull request runs the tests and the linter; dependency updates arrive monthly as one bundle.

## v0.4.0

- Targets Bot API 0.12.0; older servers keep working.
- A declaration holding `about` or `repoUrl` still sends them, with one warning per run: both
  are deprecated since 0.12.0, and the bot's owner sets its text and source link on the bot
  page.
  `version` stays declared.
- A `Deprecation` response header, with its `Sunset`, is logged once per route and value,
  naming the route pattern, never the token or URL.
- The stream's `moveRequest` line, deprecated since 0.12.0, stays ignored; a server that
  stops sending it changes nothing.
- A challenge from a bot of the same owner is accepted like any other and plays unrated.
- `Client.request` fills a route pattern from `params`, and its logs name the pattern.
- The examples declare no `about`.

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
