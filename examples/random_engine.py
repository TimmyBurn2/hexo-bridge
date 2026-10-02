#!/usr/bin/env python3
"""A HeXO engine as a process: one JSON request per line on stdin, one answer per line on stdout.

hexo-bridge runs it from bot.toml. It plays two random empty cells beside the stones, which
is always legal and never strong; replace `choose` with a search. Only the standard library,
so the same loop reads the same in any language.
"""

import json
import random
import sys

NEIGHBOURS = ((1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1))


def choose(board):
    taken = {(cell["q"], cell["r"]) for cell in board["cells"]}
    free = sorted({(q + dq, r + dr) for q, r in taken for dq, dr in NEIGHBOURS} - taken)
    return random.sample(free, 2)


for line in sys.stdin:
    request = json.loads(line)
    move = {"pieces": [{"q": q, "r": r} for q, r in choose(request["board"])]}
    if request["type"] == "analyze":
        # A reading must carry an evaluation; 0 says this engine has no opinion.
        move["evaluation"] = {"heuristic": 0}
    print(json.dumps({"move": move}), flush=True)
