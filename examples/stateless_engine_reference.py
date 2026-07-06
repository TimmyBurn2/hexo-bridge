"""Reference htttx-stateless engine: a tiny HTTP `/turn` server (stdlib only).

This is the worked example for the htttx-stateless engine boundary. It speaks
the htttx stateless v1-alpha `/turn` shape: it receives a `StatelessMoveRequest`
(`board` with `to_move` and `cells`, optional `time_limit`, optional
`request_id`) and returns a `StatelessMoveResponse` (`move.pieces` of two
coords). It picks the first two empty cells it finds near the board's centre,
good enough to prove the boundary end to end; it is not a real engine.

Run it (no dependencies beyond the Python stdlib):

    python3 examples/stateless_engine_reference.py --port 8080

Point the bridge at it:

    [engine]
    name = "htttx_stateless"
    [engine.options]
    base_url = "http://127.0.0.1:8080"

Dry-run the boundary:

    hexo-bridge validate examples/config.stateless-engine.toml

Why this example exists: an htttx-stateless engine is portable across the whole
ecosystem. It speaks BSoD's ecosystem engine spec, not a bridge-private
protocol, so the same server works with any conformant client, not just this
bridge. A non-Python author can implement the same `/turn` shape in any language
and host; the bridge does not care. See `docs/stdio-protocol.md` for the
portability trade-off against the bridge-coupled stdio boundary.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _pick_two_empty(cells: list[dict], to_move: str) -> list[list[int]]:
    """Pick two empty cells in a spiral around the board's centre."""
    occupied = {(c["q"], c["r"]) for c in cells}
    if cells:
        qs = [c["q"] for c in cells]
        rs = [c["r"] for c in cells]
        aq, ar = sum(qs) // len(qs), sum(rs) // len(rs)
    else:
        aq, ar = 0, 0
    picked: list[list[int]] = []
    radius = 0
    while len(picked) < 2 and radius < 50:
        for dq in range(-radius, radius + 1):
            for dr in range(-radius, radius + 1):
                if abs(dq + dr) > radius:
                    continue
                c = (aq + dq, ar + dr)
                if c in occupied:
                    continue
                picked.append([c[0], c[1]])
                if len(picked) == 2:
                    return picked
        radius += 1
    return picked


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # stdlib naming
        if self.path.rstrip("/") not in (
            "/stateless/v1-alpha/turn",
            "/turn",
        ):
            self.send_error(404, "not found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(400, "bad json")
            return
        board = req.get("board") or {}
        cells = board.get("cells") or []
        to_move = board.get("to_move", "o")
        pieces = _pick_two_empty(cells, to_move)
        resp = {"move": {"pieces": [{"q": q, "r": r} for q, r in pieces]}}
        if "request_id" in req:
            resp["request_id"] = req["request_id"]
        payload = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:  # silence default logging
        return


def main() -> None:
    p = argparse.ArgumentParser(description="Reference htttx-stateless engine.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    args = p.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), _Handler)
    print(f"reference stateless engine on http://{args.host}:{args.port}/stateless/v1-alpha/turn")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
