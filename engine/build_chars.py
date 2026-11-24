#!/usr/bin/env python3
"""One-time builder for the cast: downloads Notionists avatars (CC0 1.0, by Zoish, via the DiceBear API) and stores
each character as layers, so the renderer can swap mouth / brows / gesture per frame.

    python engine/build_chars.py        -> engine/assets/chars/<name>.json

Each JSON holds: viewBox, defs, "base" (face, hair, body, nose, eyes, glasses) and the swappable layers.
"""

import json
import os
import re
import time
import urllib.request

API = "https://api.dicebear.com/9.x/notionists/svg"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "chars")

CAST = {
    # host: the smart friend who explains (matches the default host voice)
    "host": dict(seed="host", hair="variant28", glasses="variant11", glassesProbability=100, beardProbability=0),
    # guest: the skit partner who asks the questions viewers have
    "guest": dict(seed="guest", hair="variant17", beard="variant03", beardProbability=100, glassesProbability=0),
}
LIPS = {"closed": "variant23", "smile": "variant03", "mid": "variant25", "open": "variant14", "shock": "variant15"}
BROWS = {"neutral": "variant05", "up": "variant01", "worried": "variant13"}
GESTURES = ["none", "hand", "point", "pointLongArm", "ok", "okLongArm", "waveLongArm", "handPhone"]


def fetch(**kw):
    q = "&".join(f"{k}={v}" for k, v in kw.items())
    for i in range(4):
        try:
            with urllib.request.urlopen(f"{API}?{q}", timeout=30) as r:
                return r.read().decode()
        except Exception:
            time.sleep(2 + 2 * i)
    raise RuntimeError(f"DiceBear fetch failed: {q}")


def groups(svg):
    """Top-level component groups inside the masked root group, in order."""
    body = svg.split('<g mask="url(#viewboxMask)">', 1)[1]
    out, depth, start = [], 0, None
    for m in re.finditer(r"<g\b[^>]*>|</g>", body):
        if m.group(0).startswith("<g"):
            if depth == 0:
                start = m.start()
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                out.append(body[start : m.end()])
            if depth < 0:
                break
    return out


def build(name, opts):
    common = dict(opts, bodyIconProbability=0, backgroundColor="transparent")
    ref = fetch(**common, lips=LIPS["closed"], brows=BROWS["neutral"], gestureProbability=0)
    g0 = groups(ref)
    other = groups(fetch(**common, lips=LIPS["open"], brows=BROWS["up"], gestureProbability=100, gesture="point"))
    diff = [i for i, (a, b) in enumerate(zip(g0, other)) if a != b]
    # which differing layer is which: change one component at a time
    lips_i = next(
        i
        for i, (a, b) in enumerate(
            zip(g0, groups(fetch(**common, lips=LIPS["open"], brows=BROWS["neutral"], gestureProbability=0)))
        )
        if a != b
    )
    brows_i = next(
        i
        for i, (a, b) in enumerate(
            zip(g0, groups(fetch(**common, lips=LIPS["closed"], brows=BROWS["up"], gestureProbability=0)))
        )
        if a != b
    )
    gest_i = [i for i in diff if i not in (lips_i, brows_i)]
    layers = {"lips": {}, "brows": {}, "gesture": {"none": ""}}
    for k, v in LIPS.items():
        layers["lips"][k] = groups(fetch(**common, lips=v, brows=BROWS["neutral"], gestureProbability=0))[lips_i]
    for k, v in BROWS.items():
        layers["brows"][k] = groups(fetch(**common, lips=LIPS["closed"], brows=v, gestureProbability=0))[brows_i]
    for gname in GESTURES[1:]:
        gs = groups(fetch(**common, lips=LIPS["closed"], brows=BROWS["neutral"], gestureProbability=100, gesture=gname))
        layers["gesture"][gname] = "".join(gs[i] for i in gest_i) if len(gs) == len(g0) else gs[-1]
    base = [g for i, g in enumerate(g0) if i not in (lips_i, brows_i, *gest_i)]
    head = ref.split('<g mask="url(#viewboxMask)">', 1)[0]
    defs = re.findall(r"<defs>.*?</defs>|<mask[^>]*>.*?</mask>", head, re.S)
    vb = re.search(r'viewBox="([^"]+)"', ref).group(1)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"{name}.json"), "w", encoding="utf-8") as fh:
        json.dump(
            {
                "viewBox": vb,
                "defs": "".join(defs),
                "base": "".join(base),
                "layers": layers,
                "license": "Notionists by Zoish, CC0 1.0 (via DiceBear)",
            },
            fh,
        )
    print(
        name, "layers ok:", {k: len(v) for k, v in layers.items()}, "lips", lips_i, "brows", brows_i, "gesture", gest_i
    )


if __name__ == "__main__":
    for n, o in CAST.items():
        build(n, o)
