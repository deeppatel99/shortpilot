#!/usr/bin/env python3
"""Stands in for `claude -p --output-format json` in tests. Reads prompt on stdin, answers by prompt type."""

import json
import os
import sys

prompt = sys.stdin.read()
here = os.path.dirname(os.path.abspath(__file__))
if "Choose the" in prompt and "picks" in prompt:
    res = {
        "picks": [
            {
                "topic": "Phone battery in the cold",
                "angle": "Cold hides charge, it does not destroy it",
                "hook": "Your phone died at 20%?",
                "why_now": "news",
                "scores": {"niche_fit": 9, "curiosity": 9, "usefulness": 9, "freshness": 8, "safety": 9},
                "total": 44,
            },
            {
                "topic": "Battery myth",
                "angle": "Charging overnight",
                "hook": "",
                "why_now": "",
                "scores": {"safety": 10},
                "total": 30,
            },
        ]
    }
    text = "Here you go:\n```json\n" + json.dumps(res) + "\n```"
elif "failed automatic checks" in prompt:
    ep = json.load(open(os.path.join(here, "sample_episode.json"), encoding="utf-8"))
    text = json.dumps(ep, ensure_ascii=False)
else:
    ep = json.load(open(os.path.join(here, "sample_episode.json"), encoding="utf-8"))
    ep["scenes"][0]["lines"][0]["tts"] = ""  # deliberate error (empty tts) -> exercises the fix loop
    text = "Sure! " + json.dumps(ep, ensure_ascii=False)
print(json.dumps({"type": "result", "is_error": False, "result": text, "num_turns": 3, "total_cost_usd": 0.04}))
