"""Frame-level stress test of the renderer: random/extreme episodes, NO voice model, every frame drawn.
Usage: python tests/fuzz_render.py [n_episodes] [seed]"""

import copy
import os
import random
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))  # tests/
ROOT = os.path.dirname(HERE)
SLUG = "_template"
PACK = os.path.join(ROOT, "channels", SLUG)
os.environ["AUTOPILOT_CHANNEL"] = SLUG  # the engine is tested with the channel template
VIDEOS = os.environ.get("AUTOPILOT_TEST_VIDEOS") or os.path.join(PACK, "data", "videos")  # any rendered .mp4
sys.path.insert(0, os.path.join(ROOT, "engine"))
sys.path.insert(0, ROOT)
import make_short as M
import numpy as np

from pipeline.validate import validate

LONG = "This is a very long line of text that tries hard to break the layout and checks that wrapping still works " * 2
WORDS = [
    "",
    "OK",
    "Supercalifragilisticexpialidocious",
    "$36,000",
    "2FA ✓ ON",
    LONG,
    "Naam + mobile + DOB + address + email",
    "Call kaato, YONO kholo",
]
EMO = ["😱", "🤯", "🔥", "💀", "✅", "👇", "👨‍💻", "🇮🇳", "A", "", "😀😀"]
NUMS = [0, 1, -5, 3897, 208827064576, 10**15, 2.5, "36,448"]
ACC = ["#FF4D4D", "#4DA3FF", "#FFB020", "#2BD67B", "#B36BFF", "#123", "red", None]


def rnd_visual(r, t):
    w = lambda: r.choice(WORDS)
    n = lambda: r.choice(NUMS)
    items = lambda: [r.choice(WORDS) for _ in range(r.choice([0, 1, 2, 3, 4, 6]))]
    return {
        "type": t,
        **{
            "headline": lambda: {"label": w(), "text": w(), "emoji": r.choice(EMO)},
            "big_emoji": lambda: {"emoji": r.choice(EMO)},
            "phone_msg": lambda: {"app": w(), "sender": w(), "text": w(), "stamp": r.choice(["SCAM", "", LONG])},
            "myth_fact": lambda: {"claim": w(), "fact": w()},
            "versus": lambda: {
                "left": {"label": w(), "value": w()},
                "right": {"label": w(), "value": w()},
                "winner": r.choice(["left", "right", "", "x", None]),
            },
            "bars": lambda: {
                "title": w(),
                "unit": r.choice(["", "%", " crore"]),
                "items": [{"label": w(), "value": n()} for _ in range(r.choice([0, 1, 3, 6]))],
            },
            "counter": lambda: {
                "label": w(),
                "to": n(),
                "prefix": r.choice(["", "$", "€"]),
                "suffix": r.choice(["", " crore", LONG]),
                "format": r.choice(["indian", "intl"]),
            },
            "checklist": lambda: {"items": items()},
            "comment": lambda: {"text": w()},
            "kinetic": lambda: {},
            "lockscreen": lambda: {
                "time": r.choice(["9:41", "", LONG]),
                "date": w(),
                "items": r.choice(
                    [
                        [],
                        "x",
                        [w(), {"app": w(), "title": w(), "text": w()}],
                        [{"app": w(), "title": w(), "text": w()} for _ in range(r.choice([1, 3, 5]))],
                    ]
                ),
            },
            "breaking": lambda: {"label": w(), "text": w(), "ticker": w()},
            "terminal": lambda: {"title": w(), "rows": items(), "result": w()},
            "setting": lambda: {"path": w(), "rows": items(), "on": r.choice([0, 1, 5, -1, "x", None])},
            "photo": lambda: {},
        }[t](),
    }


def rnd_episode(r, types):
    scenes = []
    for t in types:
        nl = r.choice([1, 2, 3, 4])
        lines = [
            {"tts": "क", "cap": r.choice([w for w in WORDS if w] + [LONG]), "hi": [r.choice(["OK", "x", ""])]}
            for _ in range(nl)
        ]
        scenes.append(
            {
                "accent": r.choice(ACC),
                "emoji": r.choice(EMO),
                "card": [r.choice(WORDS) for _ in range(r.choice([0, 1, 2, 3, 4]))],
                "visual": rnd_visual(r, t),
                "lines": lines,
                "footnote": r.choice(["", "Source: test", LONG]),
            }
        )
    return {"title": "fuzz", "brand": r.choice(["MY CHANNEL", "", LONG]), "scenes": scenes}


def run_episode(ep, r, label):
    clips = [[np.zeros(int(M.SR * r.uniform(0.4, 2.2)), np.float32) for _ in sc["lines"]] for sc in ep["scenes"]]
    try:
        scenes, events, total = M.plan(ep, clips)
    except Exception:
        return f"{label}: plan()\n" + traceback.format_exc()
    if MEDIA_FILES and r.random() < 0.7:  # random media shots (images / videos, 0-3 per scene, odd timings)
        for S in scenes:
            k = r.choice([0, 1, 2, 3])
            S["shots"] = []
            for j in range(k):
                kind = r.choice(["image", "video"])
                t0 = 0.0 if j == 0 else r.uniform(0, S["dur"])
                S["shots"].append(
                    {
                        "kind": kind,
                        "path": MEDIA_FILES[kind],
                        "t0": t0,
                        "t1": S["dur"] + 0.3,
                        "kb": (r.uniform(0.9, 1.2), r.uniform(0.9, 1.2), r.uniform(-2, 2), r.uniform(-2, 2)),
                    }
                )
            S["shots"].sort(key=lambda x: x["t0"])
    M.G.clear()
    M._init_worker(dict(ep=ep, scenes=scenes, events=events, total=total, debug=False))
    nf = int(total * M.FPS)
    for fi in range(nf):
        try:
            b = M.render_frame(fi)
            assert len(b) == M.W * M.H * 3
        except Exception:
            return f"{label}: frame {fi}/{nf} t={fi / M.FPS:.2f}s\n" + traceback.format_exc()
    return None


MEDIA_FILES = {}


def make_media():
    import subprocess
    import tempfile

    from PIL import Image

    d = tempfile.mkdtemp()
    M.MEDIA.CACHE = d
    raw = os.path.join(d, "src.png")
    Image.effect_noise((900, 700), 60).convert("RGB").save(raw)
    MEDIA_FILES["image"] = M.MEDIA.prepare_image(open(raw, "rb").read(), (255, 77, 77), os.path.join(d, "i.jpg"))
    src = os.path.join(d, "src.mp4")
    subprocess.run(
        [M.FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=2", src],
        check=True,
    )
    MEDIA_FILES["video"] = M.MEDIA.prepare_video(
        src, os.path.join(d, "v.mp4"), 2.0
    )  # short on purpose: reader must hold last frame


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    make_media()
    r = random.Random(seed)
    types = list(M.VISUAL_SPECS)
    fails, t0, frames = [], time.time(), 0
    for i in range(n):
        order = types[:]
        r.shuffle(order)
        raw = rnd_episode(r, order)
        for mode, ep in (("raw", copy.deepcopy(raw)), ("validated", validate(copy.deepcopy(raw))[0])):
            if not ep.get("scenes"):
                continue
            err = run_episode(ep, r, f"ep{i}-{mode}")
            if err:
                fails.append(err)
                print("FAIL", err.splitlines()[0], "|", err.strip().splitlines()[-1][:200], flush=True)
            else:
                print(f"ok ep{i}-{mode}", flush=True)
    print(f"\n{len(fails)} failures in {time.time() - t0:.0f}s")
    for f in fails[:3]:
        print("-" * 60 + "\n" + f[-1500:])
    sys.exit(1 if fails else 0)
