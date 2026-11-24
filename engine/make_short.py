#!/usr/bin/env python3
"""
Shorts Engine v2 — episode JSON -> 9:16 MP4 tuned for YouTube Shorts + Instagram Reels.

What v2 does for retention:
  * Frame 0 already shows the hook (no fade-in, no blank start)
  * Voice is synthesised line-by-line, so captions are exactly in sync; active word pops in yellow
  * Something moves every second: camera push-in, punch-zoom on every line, slide transitions,
    drifting particles, scene animations (brute-force typing, counters, stamps, glitch, ticks)
  * Sound design: whoosh, typing ticks, pops, stamps, glitch, risers, impacts, dings + beat music
  * Everything important lives inside the Shorts/Reels safe zone (x<=984, 230<y<1560)
  * Ending line flows back into the opening line, so the video loops

Free & local: Kokoro (Apache-2.0) voice, Pillow, NumPy, FFmpeg.
Usage:  python3 make_short.py episodes/x.json out/x.mp4 --models ./models [--jobs 2] [--debug-safe]
"""

import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile

# one math thread per render worker: many-core PCs otherwise spawn workers x threads and run out of RAM
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")
import multiprocessing as mp

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, FPS, SR = 1080, 1920, 30, 24000
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import media as MEDIA

sys.path.append(os.path.dirname(HERE))
from pipeline import channel as CH  # noqa: E402  the active channel pack (channels/<slug>/)

BRAND = CH.brand()  # e.g. MY CHANNEL: channel.brand, else channel.name, from channel.json

FONTS = os.path.join(HERE, "assets", "fonts")
FB = os.path.join(FONTS, "InterDisplay-Black.otf")
FX = os.path.join(FONTS, "InterDisplay-ExtraBold.otf")
FS = os.path.join(FONTS, "InterDisplay-SemiBold.otf")
FMONO = os.path.join(FONTS, "DejaVuSansMono-Bold.ttf")
FEMOJI = os.path.join(FONTS, "NotoColorEmoji.ttf")
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
WHITE = (255, 255, 255)
YELLOW = (255, 226, 77)
NAVY = (10, 13, 26)
LINE_GAP, SCENE_GAP, LEAD, TAIL, TRANS = 0.07, 0.16, 0.05, 0.35, 0.18
STAGE_CY = 820  # camera pivot
CAP_Y = 1405  # caption centre (safe: < 1560)
CARD_Y = 895
CAP_X, CAP_W = 615, 690  # captions sit right of the host character (bottom-left)
HOST_X, HOST_Y = 150, 1400

# visual type -> minimum spoken lines in that scene (key moments are anchored to line starts)
VISUAL_SPECS = {
    "headline": 1,
    "big_emoji": 1,
    "counter": 1,
    "comment": 1,
    "checklist": 1,
    "myth_fact": 2,
    "phone_msg": 2,
    "versus": 2,
    "bars": 2,
    "lockscreen": 1,
    "setting": 2,
    "breaking": 1,
    "terminal": 2,
    "photo": 1,
    "kinetic": 1,
}


def key_t(L, i, off=0.0):
    return L[min(i, len(L) - 1)][0] + off


def _num(x, lo=0.0, hi=1e15, default=0.0):
    try:
        v = float(str(x).replace(",", "").replace("₹", "").replace("%", "").strip())
    except (TypeError, ValueError):
        v = default
    if v != v:
        v = default  # NaN
    return max(lo, min(hi, v))


def _s(x, n=None, default=""):
    v = default if x is None else str(x)
    v = v if v.strip() else default
    return v[:n] if n else v


def normalize(ep):
    """Coerce every LLM-written field to a safe type/length so no input can crash or break the layout."""
    import re as _re

    ep["lang"] = _s(ep.get("lang"), 16, "en-US")
    CTA[0] = _s(ep.get("cta"), 30, "Comment below")
    ep["brand"] = _s(ep.get("brand"), 24, BRAND)
    ep["tease"] = "".join(c for c in _s(ep.get("tease"), 44) if ord(c) < 0x2000).strip()  # Inter has no emoji
    for sc in ep.get("scenes", []):
        acc = _s(sc.get("accent"))
        sc["accent"] = acc if _re.fullmatch(r"#[0-9A-Fa-f]{6}", acc) else "#4DA3FF"
        card = sc.get("card")
        card = [card] if isinstance(card, str) else (card if isinstance(card, list) else [])
        sc["card"] = [_s(c, 40) for c in card if _s(c).strip()][:3]
        sc["emoji"] = _s(sc.get("emoji"), 16) or None
        sc["footnote"] = _s(sc.get("footnote"), 70) or None
        for ln in sc.get("lines", []):
            ln["cap"] = _s(ln.get("cap"), 160)
            ln["hi"] = [_s(h) for h in (ln.get("hi") or []) if _s(h)][:3]
        bg = sc.get("bg")
        bg = bg if isinstance(bg, list) else ([bg] if isinstance(bg, (dict, str)) else [])
        shots = []
        for b in bg:
            if isinstance(b, str) and b.strip():
                b = {"ai": b}
            if isinstance(b, dict):
                for k in ("stock", "ai", "photo"):
                    if _s(b.get(k)).strip():
                        shots.append({k: _s(b[k], 300).strip()})
                        break
        sc["bg"] = shots[:3]
        v = sc.get("visual") if isinstance(sc.get("visual"), dict) else {}
        t = v.get("type") if v.get("type") in VISUAL_SPECS else "big_emoji"
        v["type"] = t
        lst = lambda key, n, cap: [
            _s(i, cap) for i in (v.get(key) if isinstance(v.get(key), list) else []) if _s(i).strip()
        ][:n]
        side = lambda d: {
            "label": _s((d or {}).get("label") if isinstance(d, dict) else d, 30),
            "value": _s((d or {}).get("value") if isinstance(d, dict) else "", 24),
        }
        if t == "headline":
            v.update(label=_s(v.get("label"), 20, "ALERT"), text=_s(v.get("text"), 110), emoji=_s(v.get("emoji"), 16))
        elif t == "big_emoji":
            v["emoji"] = _s(v.get("emoji"), 16, sc.get("emoji") or "🤔")
        elif t == "phone_msg":
            v.update(
                app=_s(v.get("app"), 30, "SMS"),
                sender=_s(v.get("sender"), 22, "Unknown"),
                text=_s(v.get("text"), 220),
                stamp=_s(v.get("stamp"), 10),
            )
        elif t == "myth_fact":
            v.update(claim=_s(v.get("claim"), 90, "Myth"), fact=_s(v.get("fact"), 90, "Fact"))
        elif t == "versus":
            v.update(left=side(v.get("left")), right=side(v.get("right")))
            if v.get("winner") not in ("left", "right"):
                v["winner"] = None
        elif t == "bars":
            items = [i for i in (v.get("items") if isinstance(v.get("items"), list) else []) if isinstance(i, dict)][:4]
            v.update(
                title=_s(v.get("title"), 40),
                unit=_s(v.get("unit"), 8),
                items=[{"label": _s(i.get("label"), 18), "value": _num(i.get("value"))} for i in items],
            )
        elif t == "counter":
            v.update(
                label=_s(v.get("label"), 80),
                to=_num(v.get("to")),
                prefix=_s(v.get("prefix"), 3),
                suffix=_s(v.get("suffix"), 12),
                format=v.get("format") if v.get("format") in ("indian", "intl") else "intl",
            )
        elif t == "checklist":
            v["items"] = lst("items", 4, 28) or ["Done"]
        elif t == "setting":
            v.update(path=_s(v.get("path"), 40, "Settings"), rows=lst("rows", 4, 30) or ["Setting"])
            v["on"] = int(_num(v.get("on"), 0, len(v["rows"]) - 1))
        elif t == "comment":
            v["text"] = _s(v.get("text"), 60, CTA[0])
        elif t == "lockscreen":
            items = [
                i for i in (v.get("items") if isinstance(v.get("items"), list) else []) if isinstance(i, (dict, str))
            ][:3]
            items = [
                {
                    "app": _s(i.get("app"), 18, "Messages"),
                    "title": _s(i.get("title"), 40, "New message"),
                    "text": _s(i.get("text"), 90),
                }
                if isinstance(i, dict)
                else {"app": "Messages", "title": _s(i, 40), "text": ""}
                for i in items
            ]
            v.update(
                items=items or [{"app": "Messages", "title": "New message", "text": ""}],
                time=_s(v.get("time"), 5, "9:41"),
                date=_s(v.get("date"), 28),
            )
        elif t == "breaking":
            v.update(
                label=_s(v.get("label"), 16, "BREAKING"),
                text=_s(v.get("text"), 90, sc["card"][0] if sc["card"] else "Alert"),
                ticker=_s(v.get("ticker"), 140),
            )
        elif t == "terminal":
            v.update(
                rows=lst("rows", 6, 34) or ["connecting...", "bypassing OTP..."],
                result=_s(v.get("result"), 16, "ACCESS GRANTED"),
                title=_s(v.get("title"), 24, "terminal"),
            )
        sc["visual"] = v
    dm = ep.get("dm") if isinstance(ep.get("dm"), dict) else {}
    kw = _re.sub(r"[^A-Z0-9]", "", _s(dm.get("keyword")).upper())[:12]
    last = ep["scenes"][-1]["visual"] if ep.get("scenes") else {}
    if kw and last.get("type") == "comment":  # comment-keyword CTA: only when the script offers a real freebie
        last.update(keyword=kw, offer=_s(dm.get("offer"), 60))
    return ep


_fc = {}


def F(path, size):
    k = (path, max(1, int(size)))
    if k not in _fc:
        _fc[k] = ImageFont.truetype(path, k[1])
    return _fc[k]


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def eo(p):
    p = clamp(p)
    return 1 - (1 - p) ** 3


def eob(p, c=1.9):
    p = clamp(p)
    return 1 + (c + 1) * (p - 1) ** 3 + c * (p - 1) ** 2


def ein(p):
    p = clamp(p)
    return p**3


# ============================================================================ planning
def _trim(a):
    a = np.asarray(a, np.float32)
    idx = np.where(np.abs(a) > 0.012)[0]
    return a[max(0, idx[0] - int(0.02 * SR)) : idx[-1] + int(0.05 * SR)] if len(idx) else a


def _azure_line(text, voice, speed, lang="en-US"):
    """Azure AI Speech REST (free F0 tier = 0.5M neural chars/month). Needs env AZURE_SPEECH_KEY + AZURE_SPEECH_REGION."""
    import urllib.request
    from xml.sax.saxutils import escape

    key, region = os.environ.get("AZURE_SPEECH_KEY"), os.environ.get("AZURE_SPEECH_REGION", "eastus")
    if not key:
        sys.exit("Set AZURE_SPEECH_KEY (and AZURE_SPEECH_REGION) to use --tts azure")
    rate = f"{(speed - 1) * 100:+.0f}%"
    ssml = (
        f"<speak version='1.0' xmlns='http://www.w3.org/2001/10/synthesis' xml:lang='{lang}'>"
        f"<voice name='{voice}'><prosody rate='{rate}'>{escape(text)}</prosody></voice></speak>"
    )
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        method="POST",
        headers={
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "riff-24khz-16bit-mono-pcm",
            "User-Agent": "shorts-autopilot",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return _wav(r.read())


def _sarvam_line(text, voice, speed, lang="hi-IN"):
    """Sarvam Bulbul v3 REST (prepaid credits, ~₹30 per 10k chars, so it can't overspend). Needs env SARVAM_API_KEY."""
    import base64
    import urllib.error
    import urllib.request

    key = os.environ.get("SARVAM_API_KEY")
    if not key:
        sys.exit("Set SARVAM_API_KEY to use --tts sarvam")
    body = {"text": text, "language_code": lang, "speaker": voice, "model": "bulbul:v3", "pace": speed}
    req = urllib.request.Request(
        "https://api.sarvam.ai/text-to-speech",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"api-subscription-key": key, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return _wav(base64.b64decode(json.load(r)["audios"][0]))
    except urllib.error.HTTPError as e:  # shown in Telegram's "render fail" message, so say what to do
        sys.exit(
            f"Sarvam voice failed: HTTP {e.code} {e.read()[:200].decode(errors='replace')} — check credits on "
            "dashboard.sarvam.ai (or set voice.tts to kokoro in channel.json), then Render again"
        )


def _wav(data):
    """WAV bytes -> mono float32 at SR."""
    import io

    import soundfile as sf

    a, sr = sf.read(io.BytesIO(data), dtype="float32")
    if a.ndim > 1:
        a = a.mean(1)
    if sr != SR:
        a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * SR / sr)), np.arange(len(a)), a).astype(np.float32)
    return a


# Kokoro (the free local voice) speaks these languages; anything else needs voice.tts "azure" (400+ voices).
# Keys: full code first ("en-gb"), then the base language ("en").
KOKORO_LANG = {
    "en-us": "en-us", "en-gb": "en-gb", "en": "en-us", "es": "es", "fr": "fr-fr", "hi": "hi",
    "it": "it", "ja": "ja", "pt": "pt-br", "zh": "cmn",
}  # fmt: skip
CTA = ["Comment below"]  # call to action on the comment card; set per episode by normalize() from "cta"


def kokoro_lang(lang):
    lang = str(lang or "en-US").lower()
    code = KOKORO_LANG.get(lang) or KOKORO_LANG.get(lang.split("-")[0])
    if not code:
        sys.exit(
            f"Kokoro has no voice for '{lang}'. Set voice.tts to \"azure\" in channel.json (free tier, 140+ languages)."
        )
    return code


# cloud voice engines: episode key holding the host voice, and its default
CLOUD_VOICE = {"azure": ("azure_voice", "en-US-AvaMultilingualNeural"), "sarvam": ("sarvam_voice", "ritu")}


def synth(ep, models, provider="kokoro"):
    """Line-by-line TTS with an on-disk cache (re-renders never re-bill / re-synthesise unchanged lines)."""
    import hashlib

    import soundfile as sf

    cache = os.path.join(HERE, ".tts_cache")
    os.makedirs(cache, exist_ok=True)
    speed = ep.get("speed", 1.1)
    lang = ep.get("lang", "en-US")
    if provider in CLOUD_VOICE:
        voice = ep.get(*CLOUD_VOICE[provider])
        k = None
    else:
        from kokoro_onnx import Kokoro

        k = Kokoro(os.path.join(models, "kokoro-v1.0.onnx"), os.path.join(models, "voices-v1.0.bin"))
        vname = ep.get("voice", "af_heart")
        # "hf_alpha+hf_beta" (or "hf_alpha*0.7+hf_beta*0.3") blends voice styles
        if "+" in vname:
            parts = [p.split("*") for p in vname.split("+")]
            ws = [float(p[1]) if len(p) > 1 else 1.0 for p in parts]
            voice = sum(k.get_voice_style(p[0]) * (w / sum(ws)) for p, w in zip(parts, ws))
        else:
            voice = vname
    clips = []
    for si, sc in enumerate(ep["scenes"]):
        row = []
        for ln in sc["lines"]:
            lv = ln.get("voice") or voice  # a second character speaks with its own voice
            vid = lv if isinstance(lv, str) else ep.get("voice")
            h = hashlib.sha1(f"{provider}|{vid}|{speed}|{lang}|{ln['tts']}".encode()).hexdigest()[:16]
            fp = os.path.join(cache, h + ".wav")
            if os.path.exists(fp):
                a, _ = sf.read(fp, dtype="float32")
            else:
                if provider == "azure":
                    a = _azure_line(ln["tts"], lv, speed, lang)
                elif provider == "sarvam":
                    a = _sarvam_line(ln["tts"], lv, speed, lang)
                else:
                    a, _ = k.create(ln["tts"], voice=lv, speed=speed, lang=kokoro_lang(lang))
                a = _trim(a)
                sf.write(fp, a, SR)
            row.append(np.asarray(a, np.float32))
        clips.append(row)
        print(f"  scene {si + 1}: " + " ".join(f"{len(a) / SR:.1f}s" for a in row), flush=True)
    del k
    import gc

    gc.collect()  # release the ~0.5 GB voice model before video rendering starts
    return clips


def hi_words(ln):
    """Highlight words of a line; 'hi' may hold multi-word phrases."""
    return {w.lower().strip(",.!?:") for h in ln.get("hi", []) for w in h.split()}


def word_times(words, a, b):
    """Start of each word (+ end) spread over [a, b] by word length - Kokoro gives no word timestamps."""
    w = np.array([len(x) + 2 for x in words], float)
    return np.concatenate([[0], np.cumsum(w / w.sum())]) * (b - a) + a


def split_hook(ep):
    """Scene 1's first line becomes its own opener scene: giant words slam in with the voice (kinetic typography),
    so the first seconds show one bold idea instead of the same sentence three times (widget + card + caption)."""
    ep = json.loads(json.dumps(ep))
    sc = ep["scenes"][0]
    lines = sc.get("lines") or []
    if not lines or (sc.get("visual") or {}).get("type") == "kinetic":
        return ep
    bg = sc.get("bg") if isinstance(sc.get("bg"), list) else ([sc["bg"]] if sc.get("bg") else [])
    hook = {"accent": sc.get("accent"), "card": [], "visual": {"type": "kinetic"}, "bg": bg[:1], "lines": lines[:1]}
    if len(lines) > 1:
        sc["lines"], sc["bg"] = lines[1:], bg[1:] or bg
        ep["scenes"].insert(0, hook)
        shift = 1
    else:
        ep["scenes"][0], shift = hook, 0
    n = len(ep["scenes"])
    try:  # "tease_until" = the 1-based scene number (as written) where the open loop pays off
        si = int(ep.get("tease_until")) - 1 + shift
    except (TypeError, ValueError):
        si = n - 2
    ep["_tease_si"] = min(max(si, 1), n - 1)
    return ep


def anchors_for(sc, L):
    """Key moments (scene-local seconds) + sound events. Single source of truth for visuals and audio."""
    v = sc.get("visual", {})
    t = v.get("type")
    A = {}
    ev = []
    if t == "kinetic":
        ln = (sc.get("lines") or [{}])[0]
        words = ln.get("cap", "").split() or [""]
        A["words"] = list(word_times(words, *L[0])[:-1])
        A["words"][0] = -0.1  # first word is already on screen in frame 0
        hi = hi_words(ln) or {words[-1].lower().strip(",.!?:")}
        ev += [(0.0, "impact", 0)]
        ev += [(x, "pop", 0) for x, w in zip(A["words"][1:], words[1:]) if w.lower().strip(",.!?:") in hi]
    elif t == "setting":
        A["flip"] = key_t(L, 1, 0.1)
        ev += [(A["flip"], "pop", 0)]
    elif t == "checklist":
        A["ticks"] = [L[min(i, len(L) - 1)][0] + 0.25 for i in range(len(v["items"]))]
        ev += [(x, "ding", 0) for x in A["ticks"]]
    elif t == "comment":
        A["bubble"] = 0.25
        ev += [(0.25, "pop", 0)]
        kw = v.get("keyword") or ""
        ev += [(0.55 + 0.6 * i / max(1, len(kw)), "tick", 0) for i in range(len(kw))]
    elif t == "headline":
        ev += [(0.12, "impact", 0), (0.35, "pop", 0)]
    elif t == "big_emoji":
        ev += [(0.15, "pop", 0)]
    elif t == "myth_fact":
        A["myth"] = min(0.7, L[0][1] - 0.3) if len(L) > 1 else 0.6
        A["flip"] = key_t(L, 1)
        ev += [(A["myth"], "stamp", 0), (A["flip"], "whoosh", 0), (A["flip"] + 0.25, "ding", 0)]
    elif t == "phone_msg":
        A["msg"] = 0.35
        A["stamp"] = key_t(L, 1, 0.15)
        ev += [(A["msg"], "pop", 0)] + ([(A["stamp"], "stamp", 0), (A["stamp"], "impact", 0)] if v.get("stamp") else [])
    elif t == "versus":
        A["vs"] = 0.3
        A["win"] = key_t(L, 1, 0.2)
        ev += [(A["vs"], "impact", 0), (A["win"], "ding", 0)]
    elif t == "bars":
        A["grow"] = key_t(L, 1)
        A["grow_end"] = A["grow"] + 1.2
        ev += [(A["grow"], "riser", 1.2)] + [
            (A["grow"] + 0.25 * i + 0.4, "pop", 0) for i in range(len(v.get("items", [])))
        ]
    elif t == "counter":
        A["count"] = key_t(L, 1) if len(L) > 1 else 0.35
        A["count_end"] = A["count"] + 1.4
        ev += [(A["count"], "riser", 1.4), (A["count_end"], "ding", 0)]
    elif t == "lockscreen":
        n = len(v.get("items", [])) or 1
        A["notifs"] = [key_t(L, i, 0.12) if len(L) >= n else 0.25 + i * 0.55 for i in range(n)]
        ev += [(x, "notif", 0) for x in A["notifs"]]
    elif t == "breaking":
        A["in"] = 0.08
        ev += [(0.08, "impact", 0), (0.2, "whoosh", 0)]
    elif t == "terminal":
        A["result"] = key_t(L, 1, 0.1)
        A["type_end"] = max(0.5, A["result"] - 0.15)
        nch = sum(len(r) for r in v.get("rows", []))
        ev += [(x, "tick", 0) for x in np.linspace(0.15, A["type_end"], max(2, min(40, nch // 2)))]
        ev += [(A["result"], "stamp", 0), (A["result"], "impact", 0)]
    if sc.get("emoji"):
        ev.append((0.32, "pop", 0))
    return A, ev


def plan(ep, clips):
    normalize(ep)
    t = LEAD
    scenes = []
    for si, sc in enumerate(ep["scenes"]):
        if si:
            t += SCENE_GAP
        lines = []
        for li, a in enumerate(clips[si]):
            if li:
                t += LINE_GAP
            d = len(a) / SR
            lines.append((t, t + d))
            t += d
        scenes.append({"s0": lines[0][0] - 0.15 if si else -0.10, "lines": lines})
    total = t + TAIL
    events = []
    for si, sc in enumerate(ep["scenes"]):
        s = scenes[si]
        s0 = s["s0"]
        s["end"] = scenes[si + 1]["s0"] if si + 1 < len(scenes) else total
        L = [(a - s0, b - s0) for a, b in s["lines"]]
        s["L"] = L
        s["dur"] = s["end"] - s0
        A, ev = anchors_for(sc, L)
        s["A"] = A
        if si:
            ev.append((0.0, "whoosh", 0))
        for lt, kind, dur in ev:
            events.append((s0 + lt, kind, dur))
    return scenes, sorted(events), total


# ============================================================================ audio
MUSIC_SEED = [0]


def sfx(kind, dur, rng):
    def env(n, a, d):
        x = np.arange(n) / SR
        return np.exp(-x * d) * (1 - np.exp(-x * a))

    if kind == "tick":
        n = int(0.03 * SR)
        return (
            rng.standard_normal(n) * env(n, 4000, 260) * 0.35
            + np.sin(2 * np.pi * 2400 * np.arange(n) / SR) * env(n, 4000, 300) * 0.2
        )
    if kind == "pop":
        n = int(0.12 * SR)
        x = np.arange(n) / SR
        f = 300 + 700 * np.exp(-x * 40)
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(n, 3000, 30) * 0.6
    if kind == "ding":
        n = int(0.7 * SR)
        x = np.arange(n) / SR
        return (
            (np.sin(2 * np.pi * 1568 * x) + 0.6 * np.sin(2 * np.pi * 2349 * x) + 0.3 * np.sin(2 * np.pi * 3136 * x))
            * env(n, 2000, 6)
            * 0.28
        )
    if kind == "stamp":
        n = int(0.25 * SR)
        x = np.arange(n) / SR
        return (
            np.sin(2 * np.pi * (55 + 120 * np.exp(-x * 30)) * x) * env(n, 2000, 14) * 0.9
            + rng.standard_normal(n) * env(n, 3000, 40) * 0.35
        )
    if kind == "impact":
        n = int(0.9 * SR)
        x = np.arange(n) / SR
        return (
            np.sin(2 * np.pi * (38 + 80 * np.exp(-x * 12)) * x) * env(n, 1500, 4.5) * 1.0
            + np.convolve(rng.standard_normal(n), np.ones(30) / 30, "same") * env(n, 2000, 9) * 0.9
        )
    if kind == "glitch":
        n = int(0.32 * SR)
        out = np.zeros(n)
        for s in range(0, n, int(0.035 * SR)):
            e = min(n, s + int(0.035 * SR))
            f = rng.uniform(150, 1800)
            out[s:e] = np.sign(np.sin(2 * np.pi * f * np.arange(e - s) / SR)) * rng.uniform(0.15, 0.4)
        return np.round(out * 6) / 6 * 0.5
    if kind == "riser":
        n = int(max(0.3, dur) * SR)
        x = np.arange(n) / SR
        p = x / x[-1]
        nz = np.convolve(rng.standard_normal(n), np.ones(12) / 12, "same")
        sw = np.sin(2 * np.pi * np.cumsum(220 + 900 * p**2) / SR)
        return (nz * 0.6 + sw * 0.25) * p**2 * 0.45
    if kind == "notif":
        n = int(0.42 * SR)
        x = np.arange(n) / SR
        half = int(0.11 * SR)
        f = np.where(np.arange(n) < half, 1318.5, 1760.0)
        return (
            np.sin(2 * np.pi * f * x)
            * np.where(np.arange(n) < half, env(n, 3000, 9), np.roll(env(n, 3000, 9), half))
            * 0.32
        )
    if kind == "whoosh":
        n = int(0.35 * SR)
        nz = np.convolve(rng.standard_normal(n), np.ones(20) / 20, "same")
        return nz * np.sin(np.linspace(0, np.pi, n)) ** 2 * 1.4
    return np.zeros(1)


PROGRESSIONS = [  # (bass roots, chords) - a different mood per episode so the channel doesn't sound identical every day
    (
        [110.0, 87.31, 130.81, 98.0],
        [[220, 261.63, 329.63], [174.61, 220, 261.63], [261.63, 329.63, 392], [196, 246.94, 293.66]],
    ),  # Am F C G
    (
        [73.42, 58.27, 87.31, 65.41],
        [[146.83, 174.61, 220], [116.54, 146.83, 174.61], [174.61, 220, 261.63], [130.81, 164.81, 196]],
    ),  # Dm Bb F C
    (
        [82.41, 65.41, 98.0, 73.42],
        [[164.81, 196, 246.94], [130.81, 164.81, 196], [196, 246.94, 293.66], [146.83, 185, 220]],
    ),  # Em C G D
    (
        [98.0, 77.78, 116.54, 87.31],
        [[196, 233.08, 293.66], [155.56, 196, 233.08], [233.08, 293.66, 349.23], [174.61, 220, 261.63]],
    ),  # Gm Eb Bb F
]


def music(total, seed=0):
    r = random.Random(seed)
    roots, chords = PROGRESSIONS[seed % len(PROGRESSIONS)]
    n = int(total * SR)
    bpm = r.choice([104, 110, 116, 122])
    beat = 60 / bpm
    bar = 4 * beat
    L = np.zeros(n)
    R = np.zeros(n)
    rng = np.random.default_rng(3 + seed % 97)
    for b0 in np.arange(0, total, bar):
        ci = int(round(b0 / bar)) % 4
        s = int(b0 * SR)
        e = min(n, int((b0 + bar) * SR))
        x = np.arange(e - s) / SR
        for j, f in enumerate(chords[ci]):
            tone = np.sin(2 * np.pi * f * x) + 0.3 * np.sin(2 * np.pi * f * 2.003 * x)
            g = 0.05 * (1 - np.exp(-x * 6)) * np.exp(-x * 0.25)
            L[s:e] += tone * g * (1.1 - 0.2 * j)
            R[s:e] += tone * g * (0.7 + 0.2 * j)
        for k in range(8):  # bass 8ths
            bs = int((b0 + k * beat / 2) * SR)
            be = min(n, bs + int(beat / 2 * SR))
            if bs >= n:
                break
            xb = np.arange(be - bs) / SR
            f = roots[ci] / 2
            bass = (np.sin(2 * np.pi * f * xb) + 0.35 * np.sin(2 * np.pi * 2 * f * xb)) * np.exp(-xb * 7) * 0.22
            L[bs:be] += bass
            R[bs:be] += bass
    kl = int(0.25 * SR)
    kx = np.arange(kl) / SR
    kick = np.sin(2 * np.pi * (50 + 90 * np.exp(-kx * 35)) * kx) * np.exp(-kx * 13) * 0.5
    hl = int(0.04 * SR)
    hat = np.diff(rng.standard_normal(hl + 1)) * np.exp(-np.arange(hl) / SR * 120) * 0.05
    for bt in np.arange(0, total, beat):
        s = int(bt * SR)
        e = min(n, s + kl)
        L[s:e] += kick[: e - s]
        R[s:e] += kick[: e - s]
    for bt in np.arange(beat / 2, total, beat):
        s = int(bt * SR)
        e = min(n, s + hl)
        L[s:e] += hat[: e - s] * 0.8
        R[s:e] += hat[: e - s] * 1.2
    fade = np.ones(n)
    fl = int(0.25 * SR)
    fade[-fl:] = np.linspace(1, 0, fl)
    return np.stack([L * fade, R * fade], 1)


def mix(clips, scenes, events, total, out_wav, tmp):
    import soundfile as sf

    n = int(total * SR) + SR
    voice = np.zeros(n)
    for si, s in enumerate(scenes):
        for a, (st, _) in zip(clips[si], s["lines"]):
            i = int(st * SR)
            voice[i : i + len(a)] += a[: n - i]
    voice /= np.abs(voice).max() + 1e-9
    mus = music(n / SR, MUSIC_SEED[0])
    mus /= np.abs(mus).max() + 1e-9
    env = np.convolve(np.abs(voice), np.ones(int(0.12 * SR)) / int(0.12 * SR), "same")
    duck = 1 - 0.6 * np.clip(env / (env.max() + 1e-9) * 3.5, 0, 1)
    fx = np.zeros(n)
    rng = np.random.default_rng(11)
    for t, kind, dur in events:
        s = sfx(kind, dur, rng)
        i = int(max(0, t) * SR)
        if kind == "riser":
            s = s[: n - i]
        fx[i : i + len(s)] += s[: n - i]
    fx /= max(1.0, np.abs(fx).max())
    out = np.zeros((n, 2))
    out += (voice * 0.95)[:, None]
    out += mus * 0.30 * duck[:, None]
    out += np.stack([fx * 0.38, fx * 0.38], 1)
    out = out[: int(total * SR)]
    out /= np.abs(out).max() / 0.9
    raw = os.path.join(tmp, "raw.wav")
    sf.write(raw, out, SR)
    # two-pass loudness normalisation to -14 LUFS (Shorts/Reels standard)
    p1 = subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-i",
            raw,
            "-af",
            "loudnorm=I=-14:TP=-1.5:LRA=11:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    ).stderr
    m = json.loads(p1[p1.rindex("{") : p1.rindex("}") + 1])
    af = (
        f"loudnorm=I=-14:TP=-1.5:LRA=11:measured_I={m['input_i']}:measured_TP={m['input_tp']}:"
        f"measured_LRA={m['input_lra']}:measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true"
    )
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", raw, "-af", af, "-ar", "48000", out_wav], check=True)


# ============================================================================ visual helpers
class BG:
    def __init__(self):
        y = np.linspace(0, 1, H, dtype=np.float32)[:, None, None]
        top, bot = np.array([10, 13, 26], np.float32), np.array([22, 16, 42], np.float32)
        self.base = np.broadcast_to(top * (1 - y) + bot * y, (H, W, 3)).astype(np.float32)
        yy = np.arange(H, dtype=np.float32)[:, None]
        xx = np.arange(W, dtype=np.float32)[None, :]
        self.glow = np.exp(-(((xx - 540) ** 2) / (2 * 520**2) + ((yy - 700) ** 2) / (2 * 620**2))).astype(np.float32)[
            ..., None
        ]
        sp = 64
        g = Image.new("RGBA", (W + sp, H + sp), (0, 0, 0, 0))
        d = ImageDraw.Draw(g)
        for gy in range(0, H + sp, sp):
            for gx in range(0, W + sp, sp):
                d.ellipse([gx - 2, gy - 2, gx + 2, gy + 2], fill=(255, 255, 255, 20))
        self.grid, self.sp = g, sp
        r = random.Random(5)
        self.parts = [
            (
                r.uniform(30, 1050),
                r.uniform(0, H),
                r.uniform(25, 70),
                r.choice("01"),
                r.randint(26, 50),
                r.randint(30, 75),
            )
            for _ in range(42)
        ]

    def frame(self, t, accent, k):
        if not hasattr(self, "_buf"):
            self._buf = np.empty((H, W, 3), np.float32)
            self._u8 = np.empty((H, W, 3), np.uint8)
        np.multiply(self.glow, np.array(accent, np.float32) * (0.34 * k), out=self._buf)
        np.add(self._buf, self.base, out=self._buf)
        np.clip(self._buf, 0, 255, out=self._buf)
        np.copyto(self._u8, self._buf, casting="unsafe")
        img = Image.fromarray(self._u8, "RGB").convert("RGBA")
        off = int((t * 22) % self.sp)
        img.alpha_composite(self.grid.crop((off, 0, off + W, H)))
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        for x, y0, sp, ch, sz, a in self.parts:
            y = (y0 - t * sp) % (H + 60) - 30
            d.text((x, y), ch, font=F(FMONO, sz), fill=accent + (a,))
        img.alpha_composite(lay)
        return img


_emoji, _logo = {}, None


def emoji_img(ch, size):
    k = (ch, size)
    if k not in _emoji:
        im = Image.new("RGBA", (420, 160), (0, 0, 0, 0))
        try:
            ImageDraw.Draw(im).text((10, 10), ch, font=F(FEMOJI, 109), embedded_color=True)
        except Exception:
            pass
        bb = im.getbbox()
        if not bb:
            _emoji[k] = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        else:
            im = im.crop(bb)
            _emoji[k] = im.resize((size, max(1, int(size * im.height / im.width))), Image.LANCZOS)
    return _emoji[k]


def paste_c(layer, im, cx, cy, scale=1.0, alpha=1.0, rot=0.0):
    if scale <= 0.01 or alpha <= 0.01:
        return
    if scale != 1.0:
        im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.BILINEAR)
    if rot:
        im = im.rotate(rot, Image.BICUBIC, expand=True)
    if alpha < 1:
        im = im.copy()
        im.putalpha(im.getchannel("A").point(lambda v: int(v * alpha)))
    layer.alpha_composite(im, (int(cx - im.width / 2), int(cy - im.height / 2)))


def segs(line):
    return [(p, i % 2 == 1) for i, p in enumerate(line.split("*")) if p]


def fit(segments, path, start, maxw, lo=40):
    s = start
    while s > lo and sum(F(path, s).getlength(x) for x, _ in segments) > maxw:
        s -= 2
    return s


def fitf(text, path, start, maxw, lo=18):
    return F(path, fit([(text, 0)], path, start, maxw, lo))


# ============================================================================ scene visuals
def stamp(layer, text, cx, cy, p, color, size=58, rot=-11):
    if p <= 0:
        return
    f = F(FB, size)
    tw = f.getlength(text)
    im = Image.new("RGBA", (int(tw + 70), size + 50), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle(
        [4, 4, im.width - 4, im.height - 4], radius=16, outline=color + (255,), width=7, fill=color + (60,)
    )
    d.text((35, 18), text, font=f, fill=color + (255,))
    paste_c(layer, im, cx, cy, scale=1 + 1.1 * (1 - eo(p / 0.18)), alpha=clamp(p / 0.1), rot=rot)


def draw_visual(layer, sc, S, lt, accent):
    d = ImageDraw.Draw(layer)
    v = sc.get("visual", {})
    t = v.get("type")
    A = S["A"]
    if t == "kinetic":
        return draw_kinetic(layer, sc, S, lt, accent)
    if t == "setting":  # this engine draws the settings screen as a checklist that ticks on the flip
        t, v, A = "checklist", {"type": "checklist", "items": v["rows"]}, {**A, "ticks": [A["flip"]] * len(v["rows"])}
    if t in ("headline", "big_emoji", "myth_fact", "phone_msg", "versus", "bars", "counter"):
        return draw_visual_generic(layer, sc, S, lt, accent, v, t, A)
    if t in ("lockscreen", "breaking", "terminal", "photo"):
        return draw_visual_v3(layer, sc, S, lt, accent, v, t, A)
    if t == "checklist":
        items = v["items"]
        n = len(items)
        rh = 128 if n <= 3 else 104
        y0 = 380 if n <= 3 else 350
        bh = rh - 22
        cb = min(58, bh - 30)
        fs = 56 if n <= 3 else 48
        for i, it in enumerate(
            items
        ):  # 4 items: tighter rows so the list never reaches the footnote (y 852) / card (y 895)
            p = eo((lt - 0.1 - i * 0.1) / 0.3)
            if p <= 0:
                continue
            x = 140 + (1 - p) * 120
            y = y0 + i * rh
            done = lt >= A["ticks"][i]
            d.rounded_rectangle(
                [x, y, x + 800, y + bh],
                radius=24,
                fill=(accent + (70,)) if done else (255, 255, 255, 20),
                outline=(accent + (255,)) if done else (255, 255, 255, 70),
                width=3,
            )
            bx, by = x + 30, y + (bh - cb) / 2
            d.rounded_rectangle([bx, by, bx + cb, by + cb], radius=12, outline=WHITE + (255,), width=5)
            if done:
                tp = eo((lt - A["ticks"][i]) / 0.2)
                k = cb / 58
                d.line(
                    [
                        (bx + 12 * k, by + 31 * k),
                        (bx + 25 * k, by + 45 * k),
                        (bx + (12 + 36 * tp) * k, by + (45 - 36 * tp) * k),
                    ],
                    fill=accent + (255,),
                    width=int(10 * k),
                    joint="curve",
                )
            fi = fitf(it, FX, fs, 660, 26)
            d.text((x + 112, y + (bh - fi.size * 1.2) / 2), it, font=fi, fill=WHITE + (255,))
    elif t == "comment" and v.get("keyword"):  # 'Comment "WORD"' card: the word types itself, the offer below
        kw = v["keyword"]
        p = eob((lt - A["bubble"]) / 0.35)
        if p > 0:
            lay = Image.new("RGBA", (860, 330), (0, 0, 0, 0))
            ld = ImageDraw.Draw(lay)
            ld.rounded_rectangle([0, 0, 859, 329], radius=48, fill=(255, 255, 255, 242))
            fc = F(FB, 64)
            ld.text((430 - fc.getlength("Comment") / 2, 26), "Comment", font=fc, fill=NAVY + (255,))
            n = math.ceil(clamp((lt - A["bubble"] - 0.3) / 0.6) * len(kw))
            fk = fitf(f'"{kw}"', FB, 96, 660, 40)
            pw = fk.getlength(f'"{kw}"') + 100
            ld.rounded_rectangle([430 - pw / 2, 126, 430 + pw / 2, 290], radius=80, fill=accent + (255,))
            s = f'"{kw[:n]}' + ('"' if n >= len(kw) else "|" if int(lt * 4) % 2 else "")
            ld.text((430 - fk.getlength(f'"{kw}"') / 2, 208 - fk.size * 0.62), s, font=fk, fill=WHITE + (255,))
            paste_c(layer, lay, 540, 540, scale=p)
        if v.get("offer") and lt > A["bubble"] + 0.9:
            q = eo((lt - A["bubble"] - 0.9) / 0.3)
            text_block(d, v["offer"], F(FB, 48), 540, 730 + 20 * (1 - q), 860, WHITE + (int(255 * q),), max_rows=2)
    elif t == "comment":
        p = eob((lt - A["bubble"]) / 0.35)
        if p > 0:
            lay = Image.new("RGBA", (900, 260), (0, 0, 0, 0))
            ld = ImageDraw.Draw(lay)
            ld.ellipse([0, 40, 96, 136], fill=accent + (255,))
            fq = F(FB, 60)
            ld.text((48 - fq.getlength("?") / 2, 50), "?", font=fq, fill=NAVY + (255,))
            ld.rounded_rectangle([120, 20, 880, 220], radius=36, fill=(255, 255, 255, 235))
            ld.text((156, 46), "@you", font=F(FX, 32), fill=(90, 90, 110, 255))
            txt = v.get("text", "")
            sz = 40
            while sz > 24 and (
                len(wrap(txt, F(FX, sz), 700)) > 2
                or any(F(FX, sz).getlength(r) > 700 for r in wrap(txt, F(FX, sz), 700))
            ):
                sz -= 2
            yy = 88 if len(wrap(txt, F(FX, sz), 700)) > 1 else 96
            for r in wrap(txt, F(FX, sz), 700)[:2]:
                ld.text((156, yy), r, font=F(FX, sz), fill=NAVY + (255,))
                yy += int(sz * 1.15)
            for k in range(3):
                a = 0.4 + 0.6 * (math.sin(lt * 8 - k * 0.9) > 0)
                ld.ellipse([780 + k * 26, 186, 798 + k * 26, 204], fill=(120, 120, 140, int(255 * a)))
            paste_c(layer, lay, 540, 520, scale=p)
        f = F(FX, 40)
        s = CTA[0] + "  →"
        d.text((540 - f.getlength(s) / 2, 720), s, font=f, fill=accent + (255,))


def wrap(text, font, maxw):
    rows, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if cur and font.getlength(t) > maxw:
            rows.append(cur)
            cur = w
        else:
            cur = t
    return rows + ([cur] if cur else [])


def text_block(d, text, font, cx, y, maxw, fill, lh=1.18, max_rows=4):
    size = font.size
    while True:  # shrink until every row fits and the row count is acceptable
        rows = wrap(text, font, maxw)
        if size <= 20 or (len(rows) <= max_rows and all(font.getlength(r) <= maxw for r in rows)):
            break
        size -= 4
        font = F(font.path, size)
    rows = rows[:max_rows]
    for r in rows:
        d.text((cx - font.getlength(r) / 2, y), r, font=font, fill=fill)
        y += int(font.size * lh)
    return y


def fmt_num(v, style="intl"):
    return fmt_indian(v) if style == "indian" else f"{int(v):,}"


def draw_visual_generic(layer, sc, S, lt, accent, v, t, A):
    d = ImageDraw.Draw(layer)
    if t == "headline":
        lab = v.get("label", "BREAKING")
        fl = F(FB, 38)
        lw = fl.getlength(lab)
        blink = 255 if int(lt * 2.5) % 2 == 0 else 120
        d.rounded_rectangle([540 - lw / 2 - 52, 330, 540 + lw / 2 + 30, 392], radius=31, fill=accent + (255,))
        d.ellipse([540 - lw / 2 - 34, 351, 540 - lw / 2 - 14, 371], fill=(255, 255, 255, blink))
        d.text((540 - lw / 2 + 2, 339), lab, font=fl, fill=WHITE + (255,))
        p = eo(lt / 0.25)
        box = Image.new("RGBA", (880, 330), (0, 0, 0, 0))
        bd = ImageDraw.Draw(box)
        bd.rounded_rectangle([0, 0, 879, 329], radius=34, fill=(255, 255, 255, 240))
        fh = F(FB, 58)
        rows = wrap(v.get("text", ""), fh, 800)[:4]
        y = 165 - len(rows) * 70 / 2
        for r in rows:
            bd.text((440 - fh.getlength(r) / 2, y), r, font=fh, fill=NAVY + (255,))
            y += 70
        sw = clamp((lt - 0.2) / 0.6)
        if sw > 0:
            bd.rectangle([40, 300, 40 + 800 * eo(sw), 312], fill=accent + (255,))
        paste_c(layer, box, 540, 590, scale=0.92 + 0.08 * p, alpha=max(0.85, p))
        if v.get("emoji"):
            paste_c(
                layer,
                emoji_img(v["emoji"], 150),
                900,
                430 + 6 * math.sin(lt * 4),
                scale=eob((lt - 0.3) / 0.35),
                rot=10 * math.sin(lt * 3),
            )
    elif t == "big_emoji":
        e = v.get("emoji", "🤔")
        p = eob((lt - 0.12) / 0.4)
        for k in range(3):
            rp = (lt * 0.8 + k / 3) % 1.0
            r = 160 + 260 * rp
            d.ellipse([540 - r, 600 - r, 540 + r, 600 + r], outline=accent + (int(160 * (1 - rp)),), width=6)
        paste_c(layer, emoji_img(e, 300), 540, 600 + 10 * math.sin(lt * 3), scale=p, rot=6 * math.sin(lt * 2))
    elif t == "myth_fact":
        flip = A["flip"]
        before = lt < flip
        ph = clamp((lt - flip) / 0.3)
        squash = abs(math.cos(math.pi * ph)) if 0 < ph < 1 else 1.0
        showing_fact = ph >= 0.5
        col = (43, 214, 123) if showing_fact else (255, 77, 77)
        box = Image.new("RGBA", (860, 380), (0, 0, 0, 0))
        bd = ImageDraw.Draw(box)
        bd.rounded_rectangle([0, 0, 859, 379], radius=36, fill=col + (55,), outline=col + (255,), width=5)
        tag = "FACT ✓" if showing_fact else "MYTH ✗"
        ft = F(FB, 40)
        bd.rounded_rectangle([30, 26, 30 + ft.getlength(tag) + 40, 86], radius=30, fill=col + (255,))
        bd.text((50, 32), tag, font=ft, fill=WHITE + (255,))
        text_block(
            bd, v.get("fact" if showing_fact else "claim", ""), F(FX, 54), 430, 120, 780, WHITE + (255,), max_rows=4
        )
        box = box.resize((860, max(2, int(380 * squash))), Image.BILINEAR)
        paste_c(layer, box, 540, 590, alpha=eo(lt / 0.25))
        if before and lt >= A["myth"]:
            stamp(layer, "MYTH", 760, 760, lt - A["myth"], (255, 77, 77), size=56, rot=-9)
        if showing_fact:
            stamp(layer, "FACT", 760, 760, lt - flip - 0.15, (43, 214, 123), size=56, rot=-9)
    elif t == "phone_msg":
        ph = Image.new("RGBA", (500, 590), (0, 0, 0, 0))
        pd = ImageDraw.Draw(ph)
        pd.rounded_rectangle([0, 0, 499, 589], radius=60, fill=(18, 20, 32, 255), outline=(200, 205, 220, 255), width=8)
        pd.rounded_rectangle([190, 18, 310, 40], radius=11, fill=(0, 0, 0, 255))
        app = v.get("app", "SMS")
        sender = v.get("sender", "Unknown")
        pd.rectangle([8, 60, 491, 140], fill=(255, 255, 255, 18))
        pd.ellipse([28, 74, 80, 126], fill=accent + (255,))
        fi = F(FB, 30)
        pd.text((54 - fi.getlength(sender[:1]) / 2, 82), sender[:1].upper(), font=fi, fill=NAVY + (255,))
        pd.text((96, 72), sender[:20], font=F(FX, 30), fill=WHITE + (255,))
        pd.text((96, 106), app, font=F(FS, 22), fill=(255, 255, 255, 140))
        mp = eob((lt - A["msg"]) / 0.35)
        if mp > 0:
            fm = F(FS, 32)
            rows = wrap(v.get("text", ""), fm, 380)[:7]
            bh = 40 + len(rows) * 40
            bub = Image.new("RGBA", (440, bh + 10), (0, 0, 0, 0))
            bbd = ImageDraw.Draw(bub)
            bbd.rounded_rectangle([0, 0, 430, bh], radius=26, fill=(235, 236, 242, 255))
            y = 20
            for r in rows:
                bbd.text((24, y), r, font=fm, fill=NAVY + (255,))
                y += 40
            ph.alpha_composite(bub.resize((max(1, int(bub.width * mp)), max(1, int(bub.height * mp)))), (26, 170))
        else:
            for k in range(3):
                a = 0.4 + 0.6 * (math.sin(lt * 9 - k) > 0)
                pd.ellipse([40 + k * 30, 190, 60 + k * 30, 210], fill=(200, 200, 210, int(255 * a)))
        paste_c(layer, ph, 540, 590, scale=0.94 + 0.06 * eo(lt / 0.3))
        if v.get("stamp") and lt >= A["stamp"]:
            stamp(layer, v["stamp"], 540, 640, lt - A["stamp"], accent, size=84, rot=-14)
    elif t == "versus":
        L_, R_ = v.get("left", {}), v.get("right", {})
        win = v.get("winner")
        for side, item, x0 in (("left", L_, 125), ("right", R_, 575)):
            p = eo((lt - (0.05 if side == "left" else 0.15)) / 0.3)
            if p <= 0:
                continue
            won = win == side and lt >= A["win"]
            lost = win and win != side and lt >= A["win"]
            col = accent if won else (255, 255, 255)
            al = 110 if lost else 255
            dx = (-60 if side == "left" else 60) * (1 - p)
            d.rounded_rectangle(
                [x0 + dx, 380, x0 + 380 + dx, 780],
                radius=34,
                fill=col + (60 if won else 22,),
                outline=col + (al,),
                width=5 if won else 3,
            )
            fl = F(FX, 46)
            text_block(d, item.get("label", ""), fl, x0 + 190 + dx, 420, 340, WHITE + (al,), max_rows=2)
            vv = item.get("value", "")
            fv = fitf(vv, FB, 92, 330, 22)
            if fv.size < 60 and " " in vv.strip():  # long value -> two bigger lines instead of one tiny line
                rs = 60
                while rs > 22 and (
                    len(wrap(vv, F(FB, rs), 330)) > 2
                    or any(F(FB, rs).getlength(r_) > 330 for r_ in wrap(vv, F(FB, rs), 330))
                ):
                    rs -= 2
                fr = F(FB, rs)
                rows = wrap(vv, fr, 330)[:2]
                for j, r_ in enumerate(rows):
                    d.text(
                        (x0 + 190 + dx - fr.getlength(r_) / 2, 580 + j * rs * 1.12),
                        r_,
                        font=fr,
                        fill=(accent if won else WHITE) + (al,),
                    )
                vv = ""
            d.text((x0 + 190 + dx - fv.getlength(vv) / 2, 600), vv, font=fv, fill=(accent if won else WHITE) + (al,))
            if won:
                paste_c(
                    layer, emoji_img("👑", 110), x0 + 190, 360 + 6 * math.sin(lt * 4), scale=eob((lt - A["win"]) / 0.35)
                )
        vp = eob((lt - A["vs"]) / 0.3)
        if vp > 0:
            d.ellipse([540 - 56 * vp, 580 - 56 * vp, 540 + 56 * vp, 580 + 56 * vp], fill=accent + (255,))
            fv = F(FB, 44 * vp)
            d.text((540 - fv.getlength("VS") / 2, 580 - 30 * vp), "VS", font=fv, fill=NAVY + (255,))
    elif t == "bars":
        items = v.get("items", [])[:4]
        mx = max([float(i.get("value", 0)) for i in items] + [1])
        unit = v.get("unit", "")
        if v.get("title"):
            ft = fitf(v["title"], FX, 40, 840, 22)
            d.text((540 - ft.getlength(v["title"]) / 2, 345), v["title"], font=ft, fill=(255, 255, 255, 190))
        for i, it in enumerate(items):
            y = 420 + i * 112
            p = eo((lt - 0.1 - i * 0.1) / 0.3)
            if p <= 0:
                continue
            d.text((120, y), str(it.get("label", ""))[:18], font=F(FX, 40), fill=WHITE + (int(255 * p),))
            g = eo((lt - A["grow"] - 0.25 * i) / 0.8)
            frac = float(it.get("value", 0)) / mx
            d.rounded_rectangle([120, y + 52, 960, y + 88], radius=18, fill=(255, 255, 255, 28))
            if g > 0:
                hot = i == int(np.argmax([float(x.get("value", 0)) for x in items]))
                d.rounded_rectangle(
                    [120, y + 52, 120 + max(36, 840 * frac * g), y + 88],
                    radius=18,
                    fill=(accent if hot else (200, 205, 220)) + (255,),
                )
                val = f"{float(it.get('value', 0)) * g:,.0f}{unit}"
                fv = F(FB, 38)
                d.text((960 - fv.getlength(val), y), val, font=fv, fill=(accent if hot else WHITE) + (255,))
    elif t == "counter":
        if v.get("label"):
            fl = F(FX, 44)
            text_block(d, v["label"], fl, 540, 400, 840, (255, 255, 255, 200), max_rows=2)
        p = clamp((lt - A["count"]) / (A["count_end"] - A["count"]))
        val = float(v.get("to", 0)) * eo(p) ** 1.4
        txt = f"{v.get('prefix', '')}{fmt_num(val, v.get('format', 'intl'))}{v.get('suffix', '')}"
        fin = f"{v.get('prefix', '')}{fmt_num(v.get('to', 0), v.get('format', 'intl'))}{v.get('suffix', '')}"
        if lt < A["count"]:  # waiting for the reveal line: digits spin like a slot machine instead of a dead "0"
            r = random.Random(int(lt * 24))
            txt = "".join(r.choice("0123456789") if c.isdigit() else c for c in fin)
        sz = fit([(fin, 0)], FB, 130, 780)
        pop = 1 + 0.08 * math.exp(-max(0, lt - A["count_end"]) * 8) * (lt > A["count_end"])
        d.rounded_rectangle([110, 540, 970, 760], radius=40, fill=accent + (45,), outline=accent + (230,), width=5)
        ff = F(FB, sz * pop)
        d.text((540 - ff.getlength(txt) / 2, 650 - sz * pop * 0.6), txt, font=ff, fill=WHITE + (255,))


# ============================================================================ v3 visuals
def _initial_icon(ch, accent, size=64):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.26), fill=accent + (255,))
    f = F(FB, int(size * 0.52))
    c = (ch or "?")[:1].upper()
    d.text((size / 2 - f.getlength(c) / 2, size * 0.17), c, font=f, fill=WHITE + (255,))
    return im


def draw_visual_v3(layer, sc, S, lt, accent, v, t, A):
    d = ImageDraw.Draw(layer)
    if t == "photo":
        return  # the background image carries the scene
    if t == "lockscreen":
        p = 1.0 if S.get("s0", 0) < 0 else eo(lt / 0.3)
        ft = F(FB, 172)
        tm = v.get("time", "9:41")
        d.text((540 - ft.getlength(tm) / 2, 268 + (1 - p) * 30), tm, font=ft, fill=(255, 255, 255, int(235 * p)))
        if v.get("date"):
            fd = fitf(v["date"], FX, 38, 700, 20)
            d.text((540 - fd.getlength(v["date"]) / 2, 470), v["date"], font=fd, fill=(255, 255, 255, int(200 * p)))
        items = v.get("items", [])
        shown = [i for i, ta in enumerate(A["notifs"]) if lt >= ta - 0.02]
        for rank, i in enumerate(reversed(shown[-2:])):  # newest on top, max two visible
            it = items[i]
            q = eob((lt - A["notifs"][i]) / 0.38)
            y = 548 + rank * 134 - (1 - min(1, q)) * 60
            if q <= 0:
                continue
            card = Image.new("RGBA", (880, 122), (0, 0, 0, 0))
            cd = ImageDraw.Draw(card)
            cd.rounded_rectangle([0, 0, 879, 121], radius=32, fill=(244, 245, 250, 238))
            card.alpha_composite(_initial_icon(it.get("app", "M"), accent, 66), (22, 28))
            fa = F(FX, 24)
            cd.text((106, 16), it.get("app", "").upper(), font=fa, fill=(110, 112, 130, 255))
            cd.text((862 - fa.getlength("now"), 16), "now", font=fa, fill=(110, 112, 130, 255))
            fti = fitf(it.get("title", ""), FB, 36, 740, 22)
            cd.text((106, 44), it.get("title", ""), font=fti, fill=NAVY + (255,))
            tx = it.get("text", "")
            if tx:
                fx_ = F(FS, 28)
                row = wrap(tx, fx_, 740)[:1]
                row = row[0] if row else ""
                if len(wrap(tx, fx_, 740)) > 1:
                    row = row.rstrip(" .,") + "…"
                cd.text((106, 84), row, font=fx_, fill=(60, 62, 80, 255))
            paste_c(
                layer, card, 540, y + 61, scale=0.9 + 0.1 * min(1, q), alpha=min(1, q * 1.4) * (0.78 if rank else 1)
            )
    elif t == "breaking":
        first = S.get("s0", 0) < 0  # opening scene: fully visible on frame 0 (hook rule)
        p = 1.0 if first else eo((lt - A["in"]) / 0.3)
        red = (226, 28, 42)
        blink = 255 if int(lt * 2.2) % 2 == 0 else 90
        d.rounded_rectangle([60, 330, 196, 380], radius=10, fill=(0, 0, 0, 150))
        d.ellipse([76, 345, 96, 365], fill=red + (blink,))
        d.text((106, 338), "LIVE", font=F(FB, 32), fill=WHITE + (255,))
        lab = v.get("label", "BREAKING")
        fl = F(FB, 46)
        lw = fl.getlength(lab) + 56
        x0 = 70 - (1 - p) * 900
        d.rectangle([x0, 560, x0 + lw, 626], fill=red + (255,))
        d.text((x0 + 28, 568), lab, font=fl, fill=WHITE + (255,))
        q = 1.0 if first else eo((lt - A["in"] - 0.12) / 0.35)
        x1 = 70 - (1 - q) * 1000
        box = Image.new("RGBA", (900, 190), (0, 0, 0, 0))
        bd = ImageDraw.Draw(box)
        bd.rectangle([0, 0, 899, 189], fill=(255, 255, 255, 244))
        bd.rectangle([0, 0, 12, 189], fill=red + (255,))
        fh = F(FB, 54)
        txt = v.get("text", "")
        size = 54
        while size > 30 and (
            len(wrap(txt, F(FB, size), 820)) > 2
            or any(F(FB, size).getlength(r) > 820 for r in wrap(txt, F(FB, size), 820))
        ):
            size -= 2
        fh = F(FB, size)
        rows = wrap(txt, fh, 820)[:2]
        yy = 95 - len(rows) * size * 1.12 / 2
        for r in rows:
            bd.text((40, yy), r, font=fh, fill=NAVY + (255,))
            yy += size * 1.12
        layer.alpha_composite(box, (int(x1), 626))
        tk = v.get("ticker") or " • ".join(c.replace("*", "") for c in sc.get("card", []))
        if tk:
            d.rectangle([0, 816, W, 866], fill=(12, 14, 30, 235))
            d.rectangle([0, 816, 182, 866], fill=red + (255,))
            fb_ = F(FB, 26)
            d.text((91 - fb_.getlength(BRAND) / 2, 826), BRAND, font=fb_, fill=WHITE + (255,))
            fk = F(FX, 30)
            unit = tk + "     •     "
            uw = fk.getlength(unit)
            off = (lt * 170) % max(1, uw)
            strip = Image.new("RGBA", (W - 182, 50), (0, 0, 0, 0))
            sd = ImageDraw.Draw(strip)
            x = -off
            while x < W:
                sd.text((x + 16, 7), unit, font=fk, fill=WHITE + (235,))
                x += uw
            layer.alpha_composite(strip, (182, 816))
    elif t == "terminal":
        p = eo(lt / 0.28)
        y0 = 300 + (1 - p) * 40
        win = Image.new("RGBA", (900, 520), (0, 0, 0, 0))
        wd = ImageDraw.Draw(win)
        wd.rounded_rectangle([0, 0, 899, 519], radius=26, fill=(6, 10, 8, 232), outline=(80, 255, 140, 150), width=3)
        wd.rounded_rectangle([0, 0, 899, 58], radius=26, fill=(36, 40, 44, 255))
        wd.rectangle([0, 34, 899, 58], fill=(36, 40, 44, 255))
        for k, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
            wd.ellipse([24 + k * 34, 18, 46 + k * 34, 40], fill=c + (255,))
        ftt = F(FX, 24)
        tt = v.get("title", "terminal")
        wd.text((450 - ftt.getlength(tt) / 2, 15), tt, font=ftt, fill=(200, 200, 205, 255))
        rows = v.get("rows", [])
        total = sum(len(r) for r in rows) or 1
        typed = int(total * clamp((lt - 0.15) / max(0.2, A["type_end"] - 0.15)))
        yy = 82
        green = (80, 255, 140)
        for r in rows:
            n = min(len(r), max(0, typed))
            typed -= len(r)
            if n <= 0 and typed < 0:
                break
            fr = fitf("> " + r, FMONO, 34, 840, 18)
            wd.text((30, yy), "> " + r[:n], font=fr, fill=green + (255,))
            if n < len(r) and int(lt * 4) % 2 == 0:
                wd.rectangle(
                    [30 + fr.getlength("> " + r[:n]) + 4, yy + 4, 30 + fr.getlength("> " + r[:n]) + 22, yy + 38],
                    fill=green + (255,),
                )
            yy += 50
        pb = clamp((lt - 0.15) / max(0.2, A["result"] - 0.15))
        wd.rounded_rectangle([30, 456, 870, 486], radius=15, fill=(255, 255, 255, 30))
        wd.rounded_rectangle(
            [30, 456, 30 + max(30, 840 * eo(pb)), 486], radius=15, fill=(green if lt < A["result"] else accent) + (255,)
        )
        layer.alpha_composite(win, (90, int(y0)))
        if lt >= A["result"]:
            stamp(layer, v.get("result", "DONE"), 540, int(y0) + 300, lt - A["result"], accent, size=64, rot=-8)


# ============================================================================ v3 effects (used when a scene has media)
_FX = {}


def fx_assets():
    if _FX:
        return _FX
    yy = np.arange(H, dtype=np.float32)[:, None]
    top = np.clip(1 - yy / 380, 0, 1) ** 1.5 * 135
    bot = np.clip((yy - 1060) / 700, 0, 1) ** 1.3 * 205
    a = np.broadcast_to(np.maximum(top, bot), (H, W)).astype(np.uint8)
    sc = Image.new("RGBA", (W, H), (4, 5, 12, 0))
    sc.putalpha(Image.fromarray(a, "L"))
    _FX["scrim"] = sc
    rng = np.random.default_rng(7)
    _FX["grain"] = []
    for _ in range(4):  # soft, low-alpha grain: film feel without wrecking compression
        n = rng.normal(128, 34, (H // 3, W // 3)).clip(0, 255).astype(np.uint8)
        g = Image.fromarray(n, "L").resize((W, H), Image.BILINEAR).convert("RGBA")
        g.putalpha(10)
        _FX["grain"].append(g)
    r = 520
    ly = np.linspace(-1, 1, 2 * r, dtype=np.float32)
    d2 = ly[None, :] ** 2 + ly[:, None] ** 2
    al = (np.clip(1 - d2, 0, 1) ** 2 * 255).astype(np.uint8)
    leak = Image.new("RGBA", (2 * r, 2 * r), (255, 150, 60, 0))
    leak.putalpha(Image.fromarray(al, "L"))
    _FX["leak"] = leak
    return _FX


_GL = {}


def glass_under(bg_rgb, stage_rgba, strength=0.88):
    """Frosted dark glass behind every widget: blur + darken the background wherever the stage has content."""
    from PIL import ImageFilter

    if "lut" not in _GL:
        _GL["lut"] = [min(255, int(v * 3.2 * strength)) for v in range(256)]
    small = stage_rgba.getchannel("A").resize((W // 8, H // 8), Image.BILINEAR)
    m = small.filter(ImageFilter.GaussianBlur(3)).point(_GL["lut"])
    blur = (
        bg_rgb.resize((W // 6, H // 6), Image.BILINEAR)
        .filter(ImageFilter.GaussianBlur(3))
        .resize((W, H), Image.BILINEAR)
    )
    blur = Image.eval(blur, lambda v: int(v * 0.42))
    return Image.composite(blur, bg_rgb, m.resize((W, H), Image.BILINEAR))


def persp(img, yaw, pitch=0.0, cx=540, cy=STAGE_CY, f=1700.0):
    """Rotate a flat layer in 3D around its centre (degrees) and project it back - cheap '3D card' swing."""
    if abs(yaw) < 0.05 and abs(pitch) < 0.05:
        return img
    ya, pa = math.radians(yaw), math.radians(pitch)
    src = [(0, 0), (W, 0), (W, H), (0, H)]
    dst = []
    for x, y in src:
        X, Y, Z = x - cx, y - cy, 0.0
        X, Z = X * math.cos(ya) + Z * math.sin(ya), -X * math.sin(ya) + Z * math.cos(ya)
        Y, Z = Y * math.cos(pa) - Z * math.sin(pa), Y * math.sin(pa) + Z * math.cos(pa)
        k = f / (f + Z)
        dst.append((cx + X * k, cy + Y * k))
    A_ = []
    B_ = []
    for (xd, yd), (xs, ys) in zip(dst, src):  # coefficients map output (dst) -> input (src)
        A_.append([xd, yd, 1, 0, 0, 0, -xs * xd, -xs * yd])
        B_.append(xs)
        A_.append([0, 0, 0, xd, yd, 1, -ys * xd, -ys * yd])
        B_.append(ys)
    co = np.linalg.solve(np.array(A_, np.float64), np.array(B_, np.float64))
    return img.transform((W, H), Image.PERSPECTIVE, tuple(co), Image.BILINEAR)


def whip(prev_rgb, next_rgb, p, direction=1):
    """Whip-pan: previous frame flies out, next flies in, with a little motion blur."""
    a = np.asarray(prev_rgb, np.float32)
    b = np.asarray(next_rgb, np.float32)
    e = p * p * (3 - 2 * p)
    shift = int(W * e) * direction
    out = np.empty_like(a)
    if direction > 0:
        out[:, : W - shift] = a[:, shift:] if shift < W else 0
        out[:, W - shift :] = b[:, :shift] if shift else 0
    else:
        s = -shift
        out[:, s:] = a[:, : W - s] if s < W else 0
        out[:, :s] = b[:, W - s :] if s else 0
    blur = int(60 * math.sin(math.pi * p))
    if blur > 2:
        acc = out.copy()
        for k in (1, 2, 3):
            acc += np.roll(out, k * blur // 3 * direction, axis=1)
        out = acc / 4
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGB")


def fmt_indian(v):
    s = str(int(v))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def draw_card(layer, sc, S, lt, accent, first):
    d = ImageDraw.Draw(layer)
    y = CARD_Y
    line_pulse = sum(math.exp(-max(0, lt - ls) * 7) * (lt >= ls) for ls, _ in S["L"][1:])
    for i, line in enumerate(sc["card"]):
        p = 1.0 if first else eo((lt - 0.05 - i * 0.08) / 0.3)
        sg = segs(line)
        hl = any(h for _, h in sg)
        if p <= 0:
            y += 120
            continue
        base = fit(sg, FB, 116 if hl else 78, 840)
        size = base * ((1 + 0.07 * min(1, line_pulse) + (0.1 * math.exp(-lt * 5) if first else 0)) if hl else 1)
        f = F(FB, size)
        tw = sum(f.getlength(s) for s, _ in sg)
        x = 540 - tw / 2
        yy = y + (1 - p) * 60 - (size - base) / 2
        a = int(255 * p)
        for s, h in sg:
            d.text((x + 4, yy + 7), s, font=f, fill=(0, 0, 0, int(a * 0.5)))
            d.text((x, yy), s, font=f, fill=(accent if h else WHITE) + (a,))
            x += f.getlength(s)
        y += int(base * 1.17)


def draw_kinetic(layer, sc, S, lt, accent):
    """Hook opener: the line's words land one by one, huge, in sync with the voice. Key words sit on an accent block."""
    ln = sc["lines"][0]
    words = ln["cap"].split()
    if not words:
        return
    wt = S["A"]["words"]
    hi = hi_words(ln) or {words[-1].lower().strip(",.!?:")}  # nothing marked: the punch word is usually last
    size = 150
    while True:
        f = F(FB, size)
        sp = f.getlength(" ")
        rows, cur, cw = [], [], 0
        for i, w in enumerate(words):
            ww = f.getlength(w)
            if cur and cw + sp + ww > 900:
                rows.append(cur)
                cur, cw = [], 0
            cur.append((i, w, ww))
            cw += (sp if len(cur) > 1 else 0) + ww
        rows.append(cur)
        widest = max(sum(w for _, _, w in r) + sp * (len(r) - 1) for r in rows)
        if size <= 70 or (len(rows) <= 4 and widest <= 940):
            break
        size -= 10
    d = ImageDraw.Draw(layer)
    lh = size * 1.14
    y = STAGE_CY - 60 - len(rows) * lh / 2
    for row in rows:
        x = 540 - (sum(w for _, _, w in row) + sp * (len(row) - 1)) / 2
        for i, w, ww in row:
            p = (lt - wt[i]) / 0.2
            if p > 0:
                s = 1 + 0.6 * (1 - eo(p))  # lands big, settles
                a = int(255 * clamp(p * 1.8))
                fs = F(FB, size * s)
                cx, cy = x + ww / 2, y + size * 0.55
                tw = fs.getlength(w)
                if w.lower().strip(",.!?:") in hi:
                    pad = 18 * s
                    d.rounded_rectangle(
                        [cx - tw / 2 - pad, cy - size * s * 0.5 - pad * 0.4, cx + tw / 2 + pad, cy + size * s * 0.62],
                        radius=int(22 * s),
                        fill=accent + (int(a * 0.95),),
                    )
                d.text(
                    (cx - tw / 2, cy - size * s * 0.62),
                    w,
                    font=fs,
                    fill=WHITE + (a,),
                    stroke_width=12,
                    stroke_fill=(0, 0, 0, a),
                )
            x += ww + sp
        y += lh


def talk_env(clips, scenes, total):
    """Voice loudness per video frame (0..1) - drives the host's mouth."""
    n, hop = int(total * FPS) + 2, SR // FPS
    e = np.zeros(n, np.float32)
    for si, S in enumerate(scenes):
        for (a, _), x in zip(S["lines"], clips[si]):
            k = len(x) // hop
            i0 = int(a * FPS)
            if k and i0 < n:
                r = np.sqrt((x[: k * hop].reshape(k, hop) ** 2).mean(1))[: n - i0]
                e[i0 : i0 + len(r)] = r
    return np.clip(e / (np.percentile(e[e > 0], 90) + 1e-6), 0, 1) if e.any() else e


HOST_MOOD = {"#FF4D4D": "shock", "#2BD67B": "happy", "#B36BFF": "think"}


def draw_host(layer, t, lt, talk, accent, mood, cx=150, cy=1215):
    """The classic engine's host: a phone-bot that talks with the voice, blinks and reacts to the scene.
    Every reference reel keeps one character on screen; a stock-photo slideshow has nobody to watch."""
    d = ImageDraw.Draw(layer)
    pop = eob(lt / 0.35) if lt < 0.35 else 1.0  # small hop on every scene change
    cy += 6 * math.sin(t * 3.1) - 18 * (1 - pop) - 5 * talk
    w, h = 150 * (1 + 0.06 * (1 - pop)), 206 * (1 - 0.06 * (1 - pop))
    x0, y0 = cx - w / 2, cy - h / 2
    ay = y0 - 34 + 4 * math.sin(t * 5)
    d.line([cx, y0, cx, ay], fill=(200, 210, 230, 255), width=6)
    d.ellipse([cx - 13, ay - 13, cx + 13, ay + 13], fill=accent + (255,))
    for sgn in (-1, 1):  # arms, waving a little while talking
        ang = math.radians(35 + 25 * talk * math.sin(t * 9 + sgn) + (30 if mood == "happy" else 0))
        sx = cx + sgn * w / 2
        ex, ey = sx + sgn * 46 * math.cos(ang), cy + 10 - 46 * math.sin(ang)
        d.line([sx, cy + 18, ex, ey], fill=(200, 210, 230, 255), width=10)
        d.ellipse([ex - 12, ey - 12, ex + 12, ey + 12], fill=(200, 210, 230, 255))
    d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=34, fill=(22, 28, 52, 255), outline=accent + (255,), width=6)
    d.rounded_rectangle([x0 + 14, y0 + 16, x0 + w - 14, y0 + h - 30], radius=24, fill=(12, 16, 32, 255))
    blink = (t % 3.3) < 0.12
    ey, er = y0 + 72, 19 if mood == "shock" else 15
    look = (-5, -6) if mood == "think" else (0, 0)
    for ex in (cx - 32, cx + 32):
        if blink:
            d.line([ex - er, ey, ex + er, ey], fill=WHITE + (255,), width=6)
        elif mood == "happy":
            d.arc([ex - er, ey - er, ex + er, ey + er], 200, 340, fill=WHITE + (255,), width=7)
        else:
            d.ellipse([ex - er, ey - er * 1.2, ex + er, ey + er * 1.2], fill=WHITE + (255,))
            d.ellipse([ex - 7 + look[0], ey - 7 + look[1], ex + 7 + look[0], ey + 7 + look[1]], fill=NAVY + (255,))
    my = y0 + 128
    mw, mh = (22, 22) if mood == "shock" and talk < 0.2 else (30, 4 + 30 * talk)
    if mh > 7 or mood == "shock":
        d.ellipse([cx - mw, my - mh / 2, cx + mw, my + mh / 2], fill=accent + (255,))
    else:
        d.arc([cx - 26, my - 18, cx + 26, my + 12], 20, 160, fill=accent + (255,), width=6)


def draw_tease(layer, ep, scenes, t):
    """Open loop pinned next to the logo: what is still coming, with a bar that fills until the payoff scene."""
    txt = ep.get("tease")
    si = ep.get("_tease_si")
    if not txt or not si or si >= len(scenes):
        return
    a, b = scenes[0]["end"], scenes[si]["s0"]
    if not a <= t < b + 0.9 or b - a < 2:
        return
    done = t >= b
    alpha = (1 - clamp((t - b - 0.5) / 0.4)) * eo((t - a) / 0.3)
    col = (43, 214, 123) if done else YELLOW
    x0 = 56 + F(FX, 32).getlength(ep.get("brand", BRAND)) + 120 + (1 - eo((t - a) / 0.35)) * 400
    x1 = 976
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(
        [x0, 236, x1, 300], radius=32, fill=(0, 0, 0, int(160 * alpha)), outline=col + (int(200 * alpha),), width=2
    )
    prog = 1.0 if done else clamp((t - a) / (b - a))
    d.rounded_rectangle([x0 + 24, 290, x0 + 24 + (x1 - x0 - 48) * prog, 295], radius=3, fill=col + (int(255 * alpha),))
    r = 7 * (1 + 0.35 * math.sin(t * 8))
    d.ellipse([x0 + 30 - r, 264 - r, x0 + 30 + r, 264 + r], fill=col + (int(255 * alpha),))
    f = fitf(txt, FX, 30, x1 - x0 - 72, 18)
    d.text((x0 + 50, 264 - f.size * 0.62), txt, font=f, fill=col + (int(255 * alpha),))


def draw_emoji(layer, sc, lt):
    e = sc.get("emoji")
    if not e or sc.get("visual", {}).get("type") in ("headline", "big_emoji"):
        return  # these visuals carry their own emoji
    p = eob((lt - 0.3) / 0.4)
    if p <= 0:
        return
    paste_c(layer, emoji_img(e, 118), 900, 285 + 8 * math.sin(lt * 3), scale=p, rot=8 * math.sin(lt * 2.2))


def draw_caption(layer, sc, S, t, s0):
    lines = S["lines"]
    idx = None
    for i, (a, b) in enumerate(lines):
        nxt = lines[i + 1][0] if i + 1 < len(lines) else b + 0.35
        if a - 0.04 <= t < nxt:
            idx = i
    if idx is None:
        return
    ln = sc["lines"][idx]
    a, b = lines[idx]
    words = ln["cap"].split()
    if not words:
        return
    cum = word_times(words, a, b)
    active = int(np.searchsorted(cum, t, side="right") - 1)
    active = min(max(active, 0), len(words) - 1) if t <= b else -1
    hi = hi_words(ln)
    pop = eob((t - a) / 0.16)
    base = 70
    while True:
        f = F(FX, base * (0.82 + 0.18 * pop))
        sp = f.getlength(" ") + 16
        rows, cur, cw = [], [], 0
        for i, w in enumerate(words):
            ww = f.getlength(w)
            if cur and cw + sp + ww > CAP_W:
                rows.append(cur)
                cur, cw = [], 0
            cur.append((i, w, ww))
            cw += (sp if len(cur) > 1 else 0) + ww
        rows.append(cur)
        widest = max(sum(w for _, _, w in r) + sp * (len(r) - 1) for r in rows)
        if base <= 36 or (len(rows) <= 3 and widest <= CAP_W + 30):
            break
        base -= 6
    d = ImageDraw.Draw(layer)
    lh = base * 1.16
    y = CAP_Y - (len(rows) * lh) / 2
    for row in rows:
        rw = sum(w for _, _, w in row) + sp * (len(row) - 1)
        x = CAP_X - rw / 2
        for i, w, ww in row:
            col = WHITE if i == active else (hex2rgb(sc["accent"]) if w.lower().strip(",.!?:") in hi else WHITE)
            if i == active:  # spoken word sits on an accent pill (the reference reels' caption style)
                fa = F(FX, base * 1.06 * (0.82 + 0.18 * pop))
                aw = fa.getlength(w)
                dx = (aw - ww) / 2
                d.rounded_rectangle(
                    [x - dx - 14, y - base * 0.02, x - dx + aw + 14, y + base * 1.12],
                    radius=int(base * 0.28),
                    fill=hex2rgb(sc["accent"]) + (255,),
                )
                d.text(
                    (x - dx, y - base * 0.05),
                    w,
                    font=fa,
                    fill=col + (255,),
                    stroke_width=4,
                    stroke_fill=(0, 0, 0, 160),
                )
            else:
                d.text((x, y), w, font=f, fill=col + (255,), stroke_width=10, stroke_fill=(0, 0, 0, 255))
            x += ww + sp
        y += lh


def draw_chrome(layer, ep):
    global _logo
    if _logo is None:
        _logo = Image.open(CH.asset("logo_icon.png")).convert("RGBA").resize((52, 52), Image.LANCZOS)
    d = ImageDraw.Draw(layer)
    f = F(FX, 32)
    b = ep.get("brand", BRAND)
    tw = f.getlength(b)
    d.rounded_rectangle(
        [56, 236, 56 + tw + 106, 300], radius=32, fill=(0, 0, 0, 90), outline=(255, 255, 255, 80), width=2
    )
    layer.alpha_composite(_logo, (68, 242))
    d.text((128, 249), b, font=f, fill=WHITE + (235,))


def stage_for(ep, scenes, si, t, first_frame_scene):
    sc = ep["scenes"][si]
    S = dict(scenes[si])
    S["footnote"] = sc.get("footnote")
    lt = t - S["s0"]
    accent = hex2rgb(sc["accent"])
    st = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_visual(st, sc, S, lt, accent)
    draw_card(st, sc, S, lt, accent, first=(si == 0))
    draw_emoji(st, sc, lt)
    fn = sc.get("footnote")
    if fn and sc.get("visual", {}).get("type") != "breaking":
        f = fitf(fn, FS, 26, 900, 16)
        ImageDraw.Draw(st).text(
            (540 - f.getlength(fn) / 2, 852), fn, font=f, fill=(255, 255, 255, int(140 * clamp(lt / 0.4)))
        )
    punch = sum(0.028 * math.exp(-(lt - ls) * 9) for ls, _ in S["L"] if lt >= ls)
    scale = 1 + 0.065 * clamp(lt / max(1, S["dur"])) + punch
    return st, scale, lt


def affine(img, scale, dx=0, dy=0, alpha=1.0):
    """Scale the layer around the stage pivot and shift it. Only the non-empty part is resized (much faster)."""
    cx, cy = 540, STAGE_CY
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bb = img.getbbox()
    if not bb or alpha <= 0.004:
        return out
    x0, y0, x1, y1 = bb
    nx0, ny0 = cx + (x0 - cx) * scale + dx, cy + (y0 - cy) * scale + dy
    nw, nh = max(1, round((x1 - x0) * scale)), max(1, round((y1 - y0) * scale))
    part = img.crop(bb) if abs(scale - 1) < 1e-4 else img.resize((nw, nh), Image.BILINEAR, box=bb)
    if alpha < 1:
        part.putalpha(part.getchannel("A").point(lambda v: int(v * alpha)))
    out.paste(part, (int(round(nx0)), int(round(ny0))))  # canvas is empty -> plain copy (clipped at the edges)
    return out


# ============================================================================ render
G = {}


def _init_worker(state):
    G.update(state)
    G["bg"] = BG()
    G["media"] = MEDIA.MediaBG()


TSTYLE = ["whip", "zoom", "whip", "glitch"]  # scene-change styles when both scenes have media (cycled)


def tstyle(si):
    return TSTYLE[si % len(TSTYLE)] if si else None


WHIP = 0.22


def render_frame(fi):
    ep, scenes, events, bg = G["ep"], G["scenes"], G["events"], G["bg"]
    t = fi / FPS
    si = max(i for i, s in enumerate(scenes) if s["s0"] <= t or i == 0)
    sc = ep["scenes"][si]
    accent = hex2rgb(sc["accent"])
    s0 = scenes[si]["s0"]
    lt = t - s0
    pulse = sum(math.exp(-(t - a) * 6) for a, _ in scenes[si]["lines"] if t >= a)
    shake = sum(math.exp(-(t - et) * 10) for (et, k, _) in events if k in ("stamp", "impact") and 0 <= t - et < 0.5)
    sx = 16 * shake * math.sin(t * 90)
    sy = 10 * shake * math.cos(t * 77)
    S = scenes[si]
    mimg = G["media"].frame(S, lt) if S.get("shots") else None
    st, scale, _ = stage_for(ep, scenes, si, t, si)
    if mimg is not None:
        # ---- v3: real media background, 3D swing-in, frosted glass behind widgets, scrims, grain, light leak
        style = tstyle(si)
        prevS = scenes[si - 1] if si else None
        if style == "whip" and lt < WHIP and prevS and prevS.get("shots"):
            prev = G["media"].frame(prevS, t - prevS["s0"])
            mimg = whip(prev, mimg, lt / WHIP, 1 if si % 2 else -1)
        elif style == "zoom" and lt < 0.3:
            mimg = MEDIA._zoom(mimg, 1 + 0.22 * (1 - eo(lt / 0.3)), 0, 0)
        sw = 1 - eo(lt / 0.42) if si else 0.0
        st_t = affine(st, scale, sx, sy + 6 * math.sin(t * 1.7))
        if sw > 0.002:
            st_t = persp(st_t, (22 if si % 2 else -22) * sw, 7 * sw)
        base = glass_under(mimg, st_t).convert("RGBA")
        fx = fx_assets()
        base.alpha_composite(fx["scrim"])
        if si and lt < TRANS:  # old widgets fade out quickly
            pst, pscale, _ = stage_for(ep, scenes, si - 1, t, si - 1)
            base.alpha_composite(affine(pst, pscale * (1 - 0.06 * eo(lt / TRANS)), sx, sy, (1 - eo(lt / TRANS)) ** 3))
        base.alpha_composite(st_t)
        if si and lt < 0.75:
            q = lt / 0.75
            paste_c(base, fx["leak"], -400 + 1900 * q, 760 - 260 * q, scale=1.0, alpha=0.5 * math.sin(math.pi * q))
        base.alpha_composite(fx["grain"][(fi // 2) % len(fx["grain"])])
        img = base
    else:
        img = bg.frame(t, accent, 0.7 + 0.3 * min(1, pulse) + 0.4 * clamp(1 - lt / 0.4) * (si > 0))
        if si > 0 and lt < TRANS:
            p = eo(lt / TRANS)
            pst, pscale, _ = stage_for(ep, scenes, si - 1, t, si - 1)
            img.alpha_composite(affine(pst, pscale * (1 - 0.06 * p), -W * 0.32 * p + sx, sy, (1 - p) ** 3))
            img.alpha_composite(affine(st, scale * (1.08 - 0.08 * p), W * 0.32 * (1 - p) + sx, sy, p))
        else:
            img.alpha_composite(affine(st, scale, sx, sy + 7 * math.sin(t * 1.7)))
    top = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if sc["visual"]["type"] != "kinetic":  # the kinetic hook already shows the spoken words
        draw_caption(top, sc, scenes[si], t, s0)
    tk = G.get("talk", ())
    talk = float(tk[fi]) if fi < len(tk) else 0.0
    mood = "shock" if sc["visual"]["type"] == "kinetic" else HOST_MOOD.get(sc["accent"].upper(), "neutral")
    draw_host(top, t, lt, talk, accent, mood, HOST_X, HOST_Y)
    draw_chrome(top, ep)
    draw_tease(top, ep, scenes, t)
    img.alpha_composite(top)
    for et, k, _ in events:  # flashes
        dtt = t - et
        if k == "impact" and 0 <= dtt < 0.18:
            img.alpha_composite(Image.new("RGBA", (W, H), accent + (int(90 * (1 - dtt / 0.18)),)))
        if k == "whoosh" and 0 <= dtt < 0.1:
            img.alpha_composite(Image.new("RGBA", (W, H), (255, 255, 255, int(40 * (1 - dtt / 0.1)))))
    arr = np.asarray(img.convert("RGB")).copy()
    for et, k, _ in events:  # glitch
        dtt = t - et
        if k == "glitch" and 0 <= dtt < 0.32:
            r = np.random.default_rng(fi)
            for _ in range(7):
                y0 = int(r.uniform(250, 1500))
                h = int(r.uniform(14, 70))
                arr[y0 : y0 + h] = np.roll(arr[y0 : y0 + h], int(r.uniform(-60, 60)), axis=1)
            arr[:, :, 0] = np.roll(arr[:, :, 0], 9, axis=1)
            arr[:, :, 2] = np.roll(arr[:, :, 2], -7, axis=1)
    if G.get("debug"):
        o = Image.fromarray(arr).convert("RGBA")
        dd = ImageDraw.Draw(o, "RGBA")
        dd.rectangle([0, 0, W, 230], fill=(255, 0, 0, 70))
        dd.rectangle([0, 1560, W, H], fill=(255, 0, 0, 70))
        dd.rectangle([984, 0, W, H], fill=(255, 0, 0, 70))
        arr = np.asarray(o.convert("RGB"))
    return arr.tobytes()


def render_part(args):
    a, b, path, state = args
    if not G:
        _init_worker(state)  # lazy init: an error here fails the render instead of respawning workers forever
    cmd = [
        FFMPEG,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{W}x{H}",
        "-r",
        str(FPS),
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-maxrate",
        "7M",
        "-bufsize",
        "14M",
        "-threads",
        "2",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-g",
        "60",
        path,
    ]  # 7 Mbps cap: 60 s stays < 50 MB (Telegram bot limit)
    pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for fi in range(a, b):
        pr.stdin.write(render_frame(fi))
        if (fi - a) % 300 == 0:
            print(f"    [{a}-{b}] frame {fi}", flush=True)
    pr.stdin.close()
    pr.wait()
    return path


def free_ram_gb():
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong) for n in ("tp", "ap", "tpf", "apf", "tv", "av", "aev")
                ]

            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ap / 2**30
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable"):
                    return int(line.split()[1]) / 2**20
    except Exception:
        return None


def auto_jobs():
    cpu = max(1, (os.cpu_count() or 2) // 2)
    ram = free_ram_gb()
    by_ram = 4 if ram is None else int((ram - 0.8) / 0.5)  # ~0.5 GB per worker incl. its encoder
    return max(1, min(4, cpu, by_ram)), ram


def render_episode(ep, out, models, tts=None, voice=None, jobs=None, debug=False, log=print):
    """Episode dict -> MP4 (+ _cover.jpg). Returns dict(duration, video, cover)."""
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = tempfile.mkdtemp()
    prov = tts or ep.get("tts", "kokoro")
    if voice:
        ep[CLOUD_VOICE.get(prov, ("voice",))[0]] = voice
    ep = split_hook(ep)
    log(f"1/4 voice ({prov}, line by line)…")
    clips = synth(ep, models, prov)
    scenes, events, total = plan(ep, clips)
    log(f"   duration {total:.1f}s, {len(events)} sound events")
    log("   media (AI images / stock footage)…")
    try:
        credits = MEDIA.prepare_episode(ep, scenes, hex2rgb)
    except Exception as e:
        log(f"   media skipped: {e}")
        credits = []
    for si, S in enumerate(scenes):
        if si and S.get("shots") and scenes[si - 1].get("shots") and tstyle(si) == "glitch":
            events.append((S["s0"] + 0.01, "glitch", 0))
    events.sort()
    MUSIC_SEED[0] = int(MEDIA._key(ep.get("title", ""), ep.get("topic", "")), 16) % 10007
    n_media = sum(1 for S in scenes if S.get("shots"))
    log(f"   {n_media}/{len(scenes)} scenes with media")
    log("2/4 music + sfx + loudness…")
    wav = os.path.join(tmp, "mix.wav")
    mix(clips, scenes, events, total, wav, tmp)
    log("3/4 render…")
    state = dict(ep=ep, scenes=scenes, events=events, total=total, debug=debug, talk=talk_env(clips, scenes, total))
    nf = int(total * FPS)
    k, ram = (jobs, free_ram_gb()) if jobs else auto_jobs()
    log(f"   free RAM: {'?' if ram is None else f'{ram:.1f} GB'} -> {k} render worker(s)")
    if ram is not None and ram < 1.0:
        log("   WARNING: very little free RAM - close other apps / stuck python processes")
    while True:
        step = math.ceil(nf / k)
        parts = [
            (i, min(nf, i + step), os.path.join(tmp, f"part{j}.mp4"), state) for j, i in enumerate(range(0, nf, step))
        ]
        try:
            if k == 1:
                files = [render_part(parts[0])]  # no extra processes at all
            else:
                with mp.get_context("spawn").Pool(len(parts), maxtasksperchild=1) as pool:
                    files = pool.map(render_part, parts)
            break
        except MemoryError:
            if k == 1:
                raise
            log(f"   out of memory with {k} workers -> retrying with 1")
            k = 1
            G.clear()
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        fh.write("".join(f"file '{f.replace(os.sep, '/')}'\n" for f in files))
    log("4/4 mux…")
    subprocess.run(
        [
            FFMPEG,
            "-y",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            lst,
            "-i",
            wav,
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-shortest",
            out,
        ],
        check=True,
    )
    with open(os.path.splitext(out)[0] + "_credits.json", "w", encoding="utf-8") as fh:
        json.dump(credits, fh, ensure_ascii=False)
    cover = os.path.splitext(out)[0] + "_cover.jpg"
    cover_t = f"{max(0.0, scenes[0]['end'] - 0.25):.2f}"  # end of the hook: the whole hook phrase is on screen
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", "-ss", cover_t, "-i", out, "-frames:v", "1", "-q:v", "2", cover],
        check=True,
    )
    log(f"done -> {out}")
    return {"duration": round(total, 2), "video": out, "cover": cover, "media_scenes": n_media, "credits": credits}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("out")
    ap.add_argument("--models", default=os.path.join(HERE, "..", "models"))
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--debug-safe", action="store_true")
    ap.add_argument("--tts", choices=["kokoro", "azure", "sarvam"], default=None)
    ap.add_argument("--voice", default=None)
    a = ap.parse_args()
    with open(a.episode, encoding="utf-8") as fh:
        ep = json.load(fh)
    res = render_episode(ep, a.out, a.models, a.tts, a.voice, a.jobs, a.debug_safe)
    print(json.dumps(res))


if __name__ == "__main__":
    main()
