"""The smallest bot on the library: a random engine with two levels and an analyzer.

pip install hexo-bridge
HEXO_URL=https://<domain> HEXO_BOT_TOKEN=hxo_... python random_bot.py
"""

import os
import random

from hexo_bridge import Engine, Evaluation, Line, run
from hexo_bridge.position import NEIGHBOURS


class RandomEngine(Engine):
    def move(self, position, request):
        # A level means whatever the bot says it means: here "sharp" takes wins and blocks.
        if self.game.level.id == "sharp":
            return position.fallback_turn()
        taken = set(position.stones)
        free = sorted({(q + dq, r + dr) for q, r in taken for dq, dr in NEIGHBOURS} - taken)
        return random.sample(free, 2)

    def analyze(self, position, request):
        return Line(position.fallback_turn(), Evaluation(heuristic=0.0))


if __name__ == "__main__":
    run(
        RandomEngine,
        url=os.environ["HEXO_URL"],
        token=os.environ["HEXO_BOT_TOKEN"],
        declaration={
            "about": "Plays random cells beside the stones, or takes wins and blocks when sharp.",
            "version": "0.1.0",
            "accepts": {"turnMs": [5000, 300000], "match": True, "unlimited": True},
            "levels": {
                "default": "sharp",
                "list": [{"id": "random", "label": "random"}, {"id": "sharp", "label": "sharp"}],
            },
            "analyzer": {"lines": 1, "maxSeconds": 1},
        },
    )
