#!/usr/bin/env python3
"""Shorts Engine v4 — episode JSON -> 9:16 MP4, drawn as an HTML page and captured frame by frame in headless Chromium.

Why HTML: real typography in any script, illustrated characters and a clean layout, instead of Pillow drawings over
stock photos. Voice, timing, music and sound effects are shared with
make_short.py, so scripts, validation and the pipeline stay the same.

Every frame has three zones and nothing else: a 2–4 word heading (top), ONE visual (middle) and the speaking
character with a speech-bubble caption (bottom). Up to two characters: "host" and "guest" (skit partner).

Usage:  python engine/html_short.py episode.json out.mp4 --models ./models [--jobs 3]
Needs:  pip install playwright  +  a Chromium (python -m playwright install chromium, or PW_CHROMIUM=path/to/chrome)
"""

import argparse
import glob
import json
import math
import multiprocessing as mp
import os
import subprocess
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_short as M  # noqa: E402
from build_chars import groups  # noqa: E402

ASSETS = os.path.join(HERE, "assets")
# on-screen names of the cast; channel.json render.cast_names renames them, e.g. {"host": "Max", "guest": "Leo"}
NAMES = {"host": "Alex", "guest": "Sam", **((M.CH.config().get("render") or {}).get("cast_names") or {})}
# flat colours over the black-and-white Notionists line art
# base groups: 0 = face (white fill = skin), 1 = clothes (white fills, in order), 2 = hair
COLORS = {"guest": {"skin": "#B97A4E", "clothes": ["#2E2E2E"], "hair": "#140E0A"}}
# the guest's voice per base language and engine (the host keeps the episode's voice);
# channel.json voice.guest_voice {"kokoro": "...", "azure": "..."} overrides it
CAST_VOICE = {
    "en": {"kokoro": "am_michael", "azure": "en-US-AndrewMultilingualNeural"},
    "es": {"kokoro": "em_alex", "azure": "es-ES-AlvaroNeural"},
    "fr": {"kokoro": "ff_siwis", "azure": "fr-FR-HenriNeural"},
    "hi": {"kokoro": "hm_omega", "azure": "hi-IN-MadhurNeural", "sarvam": "manan"},
    "it": {"kokoro": "im_nicola", "azure": "it-IT-DiegoNeural"},
    "ja": {"kokoro": "jm_kumo", "azure": "ja-JP-KeitaNeural"},
    "pt": {"kokoro": "pm_alex", "azure": "pt-BR-AntonioNeural"},
    "zh": {"kokoro": "zm_yunxi", "azure": "zh-CN-YunxiNeural"},
}
MOOD = {"#FF4D4D": "danger", "#2BD67B": "happy", "#B36BFF": "think"}
MOODS = {"neutral", "happy", "shock", "think", "danger", "cta"}


def chromium():
    """Path to a Chromium for Playwright: PW_CHROMIUM, else Playwright's own download, else any cached one."""
    if os.environ.get("PW_CHROMIUM"):
        return os.environ["PW_CHROMIUM"]
    for root in (
        os.environ.get("PLAYWRIGHT_BROWSERS_PATH"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "ms-playwright"),
        os.path.expanduser("~/.cache/ms-playwright"),
    ):
        if root:
            hits = sorted(glob.glob(os.path.join(root, "chromium-*", "chrome-*", "chrome*")))
            hits = [h for h in hits if os.path.basename(h) in ("chrome.exe", "chrome")]
            if hits:
                return hits[-1]
    return None  # let Playwright find its default


def char_svg(name):
    # your own character: channels/<name>/assets/chars/<host|guest>.svg (layers marked data-brows / data-lips / data-gesture)
    custom = os.path.join(M.CH.DIR, "assets", "chars", f"{name}.svg")
    if os.path.exists(custom):
        with open(custom, encoding="utf-8") as fh:
            return fh.read()
    with open(os.path.join(ASSETS, "chars", f"{name}.json"), encoding="utf-8") as fh:
        c = json.load(fh)
    base, col = groups('<g mask="url(#viewboxMask)">' + c["base"] + "</g>"), COLORS.get(name)
    if col and len(base) > 2:
        base[0] = base[0].replace('fill="#fff"', f'fill="{col["skin"]}"')
        for cl in col["clothes"]:
            base[1] = base[1].replace('fill="#fff"', f'fill="{cl}"', 1)
        base[2] = base[2].replace('fill="#000"', f'fill="{col["hair"]}"')
    parts = ["".join(base)]
    for kind in ("brows", "lips", "gesture"):
        for k, g in c["layers"][kind].items():
            if kind == "gesture" and col:  # the hand is the first white shape (handPhone's second one is the phone)
                g = g.replace('fill="#fff"', f'fill="{col["skin"]}"', 1)
            parts.append(f'<g data-{kind}="{k}">{g}</g>')
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{c["viewBox"]}" fill="none">{c["defs"]}{"".join(parts)}</svg>'


def cast_of(ep):
    cast = [c for c in (ep.get("cast") or []) if c in NAMES][:2] or ["host"]
    return cast if cast[0] == "host" else ["host"] + [c for c in cast if c != "host"][:1]


def guest_voice(ep, prov):
    """The guest speaks with a second voice: voice.guest_voice in channel.json, else a default for the language."""
    own = (M.CH.config().get("voice") or {}).get("guest_voice") or {}
    lang = str(ep.get("lang", "en-US")).split("-")[0].lower()
    return own.get(prov) or CAST_VOICE.get(lang, {}).get(prov)


def assign_voices(ep, cast, prov):
    for sc in ep["scenes"]:
        for ln in sc["lines"]:
            ln["who"] = ln.get("who") if ln.get("who") in cast else cast[0]
            v = guest_voice(ep, prov) if ln["who"] == "guest" else None
            if v:
                ln["voice"] = v


def talk_envs(clips, scenes, ep, cast, total):
    """Per character: voice loudness per video frame (0..1) - drives the mouth."""
    n, hop = int(total * M.FPS) + 2, M.SR // M.FPS
    env = {c: np.zeros(n, np.float32) for c in cast}
    for si, S in enumerate(scenes):
        for (a, _), x, ln in zip(S["lines"], clips[si], ep["scenes"][si]["lines"]):
            k, i0 = len(x) // hop, int(a * M.FPS)
            if k and i0 < n:
                r = np.sqrt((x[: k * hop].reshape(k, hop) ** 2).mean(1))[: n - i0]
                env[ln["who"]][i0 : i0 + len(r)] = r
    out = {}
    for c, e in env.items():
        ref = np.percentile(e[e > 0], 90) if e.any() else 1.0
        out[c] = [round(float(v), 2) for v in np.clip(e / (ref + 1e-6), 0, 1)]
    return out


def photo_images(ep, scenes, tmp, log):
    """Only scenes with a 'photo' visual get a picture (shown as a polaroid); everything else is drawn."""
    want = [i for i, sc in enumerate(ep["scenes"]) if sc["visual"]["type"] == "photo" and sc.get("bg")]
    if not want:
        return {}
    sub = {
        "title": ep.get("title", ""),
        "topic": ep.get("topic", ""),
        "scenes": [dict(sc, bg=sc["bg"][:1] if i in want else []) for i, sc in enumerate(ep["scenes"])],
    }
    try:
        M.MEDIA.prepare_episode(sub, scenes, M.hex2rgb)
    except Exception as e:
        log(f"   photos skipped: {e}")
        return {}
    out = {}
    for i in want:
        shots = scenes[i].get("shots") or []
        if not shots:
            continue
        p = shots[0]["path"]
        if shots[0]["kind"] == "video":  # a still from the clip is enough inside a polaroid
            jpg = os.path.join(tmp, f"photo{i}.jpg")
            subprocess.run(
                [M.FFMPEG, "-y", "-loglevel", "error", "-ss", "1", "-i", p, "-frames:v", "1", jpg], check=False
            )
            p = jpg if os.path.exists(jpg) else None
        if p:
            out[i] = "file:///" + os.path.abspath(p).replace(os.sep, "/")
        scenes[i].pop("shots", None)
    return out


def timeline(ep, scenes, total, cast, talk, imgs):
    sc_out = []
    for si, (sc, S) in enumerate(zip(ep["scenes"], scenes)):
        lines = []
        for (a, b), ln in zip(S["lines"], sc["lines"]):
            words = ln["cap"].split() or [""]
            hi = M.hi_words(ln)
            hiw = [i for i, w in enumerate(words) if w.lower().strip(",.!?:") in hi]
            lines.append(
                {
                    "a": a,
                    "b": b,
                    "who": ln["who"],
                    "words": words,
                    "hiw": hiw,
                    "wt": [round(float(x), 3) for x in M.word_times(words, a, b)],
                }
            )
        vt = sc["visual"]["type"]
        base = "shock" if vt == "kinetic" else "cta" if vt == "comment" else MOOD.get(sc["accent"].upper(), "neutral")
        mood = sc.get("mood") if isinstance(sc.get("mood"), dict) else {}
        if vt == "kinetic":
            lines[0]["hiw"] = lines[0]["hiw"] or [len(lines[0]["words"]) - 1]  # the punch word is usually last
        sc_out.append(
            {
                "s0": S["s0"],
                "end": S["end"],
                "L": S["L"],
                "A": json.loads(json.dumps(S["A"], default=float)),
                "accent": sc["accent"],
                "card": sc.get("card", [])[:2],
                "visual": sc["visual"],
                "emoji": sc.get("emoji"),
                "img": imgs.get(si),
                "lines": lines,
                "mood": {c: (mood.get(c) if mood.get(c) in MOODS else base) for c in cast},
            }
        )
    return {
        "fps": M.FPS,
        "total": total,
        "cast": cast,
        "names": NAMES,
        "chars": {c: char_svg(c) for c in cast},
        "talk": talk,
        "scenes": sc_out,
        "cover": cover_text(ep),
        "series": series_label(ep),
        "cta": M.CTA[0],
    }


def series_label(ep):
    """The series pill, top left: a recurring name returning viewers recognise.
    channel.json content.series maps a category or format to its label, e.g. {"ai_news": "AI NEWS"};
    content.series_default (else the channel tagline, else the brand) is used for everything else."""
    cfg = M.CH.config()
    content, ch = cfg.get("content") or {}, cfg["channel"]
    series = content.get("series") or {}
    label = series.get(ep.get("category")) or series.get(ep.get("format"))
    return (label or content.get("series_default") or ch.get("tagline") or M.CH.brand(cfg)).upper()


# red tag on the cover, from the topic's format/category, when the writer gave none
COVER_TAG = {
    "ai_myths": "MYTH?",
    "reality_check": "TRUE OR FALSE?",
    "threat_fix": "ALERT",
    "launch_day": "NEW",
    "ai_news": "NEW",
    "hidden_features": "TRICK",
    "connect_x": "SETUP",
    "one_prompt_build": "PROMPT",
    "versus": "VS",
    "repo_drop": "REPO",
    "ai_career": "FREE",
}


def cover_text(ep):
    """Cover rows ("*word*" = highlighted): the writer's 'cover', else the hook line split in two."""
    rows = [str(r)[:22] for r in (ep.get("cover") or []) if str(r).strip()][:3]
    if not rows:
        ln = ep["scenes"][0]["lines"][0]
        words, hi = ln["cap"].split(), M.hi_words(ln)
        words = [f"*{w}*" if w.lower().strip(",.!?:") in hi else w for w in words][:8]
        if not any(w.startswith("*") for w in words):
            words[-1] = f"*{words[-1]}*"
        half = (len(words) + 1) // 2
        rows = [" ".join(words[:half]), " ".join(words[half:])]
    tag = ep.get("cover_tag") or COVER_TAG.get(ep.get("format")) or COVER_TAG.get(ep.get("category")) or ""
    return {"rows": [r for r in rows if r], "tag": str(tag)[:18]}


def render_cover(html_path, out):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        exe = chromium()
        br = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        pg = br.new_page(viewport={"width": M.W, "height": M.H})
        pg.goto("file:///" + os.path.abspath(html_path).replace(os.sep, "/"), wait_until="load")
        pg.evaluate("document.fonts.ready.then(() => 1)")
        pg.evaluate("cover()")
        pg.screenshot(path=out, type="jpeg", quality=92)
        br.close()
    return out


def stage_file(ep):
    """Page design: env STAGE / episode "stage" / channel.json render.stage — "stage" = notebook, "editorial" = print."""
    try:
        cfg = (M.CH.config().get("render") or {}).get("stage")
    except Exception:
        cfg = None
    name = os.environ.get("STAGE") or ep.get("stage") or cfg or "stage"
    name = name if name.startswith("stage") else "stage_" + name
    return name if os.path.exists(os.path.join(HERE, "web", name + ".html")) else "stage"


def page_html(tl, stage="stage"):
    with open(os.path.join(HERE, "web", stage + ".html"), encoding="utf-8") as fh:
        html = fh.read()
    assets = "file:///" + ASSETS.replace(os.sep, "/")
    return M.CH.fill_stage(html).replace("__ASSETS__", assets).replace("/*TL*/null", json.dumps(tl, ensure_ascii=False))


def render_part(args):
    a, b, html_path, out = args
    from playwright.sync_api import sync_playwright

    cmd = [
        M.FFMPEG,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "image2pipe",
        "-framerate",
        str(M.FPS),
        "-c:v",
        "mjpeg",
        "-i",
        "-",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        os.environ.get("RENDER_CRF", "19"),
        "-pix_fmt",
        "yuv420p",
        out,
    ]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with sync_playwright() as p:
        exe = chromium()
        br = p.chromium.launch(**({"executable_path": exe} if exe else {}))
        pg = br.new_page(viewport={"width": M.W, "height": M.H})
        pg.goto("file:///" + os.path.abspath(html_path).replace(os.sep, "/"), wait_until="load")
        pg.evaluate("document.fonts.ready.then(() => 1)")
        for fi in range(a, b):
            pg.evaluate(f"render({fi / M.FPS:.4f})")
            enc.stdin.write(pg.screenshot(type="jpeg", quality=93))
            if (fi - a) % 300 == 0:
                print(f"    [{a}-{b}] frame {fi}", flush=True)
        br.close()
    enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("ffmpeg failed on " + out)
    return out


def render_episode(ep, out, models, tts=None, voice=None, jobs=None, log=print):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    tmp = tempfile.mkdtemp()
    prov = tts or ep.get("tts", "kokoro")
    if voice:
        ep[M.CLOUD_VOICE.get(prov, ("voice",))[0]] = voice
    # no split_hook here: scene 1 keeps its heading + visual and shows the hook line in full on frame 0
    # (our one breakout reel opened on a full news card; the word-by-word opener started on a near-blank page)
    M.normalize(ep)
    cast = cast_of(ep)
    assign_voices(ep, cast, prov)
    log(f"1/4 voice ({prov}, cast: {', '.join(cast)})…")
    clips = M.synth(ep, models, prov)
    scenes, events, total = M.plan(ep, clips)
    log(f"   duration {total:.1f}s, {len(events)} sound events")
    imgs = photo_images(ep, scenes, tmp, log)
    M.MUSIC_SEED[0] = int(M.MEDIA._key(ep.get("title", ""), ep.get("topic", "")), 16) % 10007
    log("2/4 music + sfx + loudness…")
    wav = os.path.join(tmp, "mix.wav")
    M.mix(clips, scenes, events, total, wav, tmp)
    log("3/4 render (HTML stage)…")
    tl = timeline(ep, scenes, total, cast, talk_envs(clips, scenes, ep, cast, total), imgs)
    html_path = os.path.join(tmp, "stage.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(page_html(tl, stage_file(ep)))
    if (
        stage_file(ep) != "stage"
    ):  # paper grain + halftone make every pixel move: a little more compression keeps files ~15 MB
        os.environ.setdefault("RENDER_CRF", "23")
    nf = int(total * M.FPS)
    k = jobs or max(1, min(4, (os.cpu_count() or 2) - 1))
    step = math.ceil(nf / k)
    parts = [
        (i, min(nf, i + step), html_path, os.path.join(tmp, f"part{j}.mp4")) for j, i in enumerate(range(0, nf, step))
    ]
    if len(parts) == 1:
        files = [render_part(parts[0])]
    else:
        with mp.get_context("spawn").Pool(len(parts)) as pool:
            files = pool.map(render_part, parts)
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w", encoding="utf-8") as fh:
        fh.write("".join(f"file '{f.replace(os.sep, '/')}'\n" for f in files))
    log("4/4 mux…")
    subprocess.run(
        [
            M.FFMPEG,
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
        json.dump([], fh)
    cover = render_cover(html_path, os.path.splitext(out)[0] + "_cover.jpg")
    log(f"done -> {out}")
    return {"duration": round(total, 2), "video": out, "cover": cover, "media_scenes": len(imgs), "credits": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode")
    ap.add_argument("out")
    ap.add_argument("--models", default=os.path.join(HERE, "..", "models"))
    ap.add_argument("--jobs", type=int, default=None)
    ap.add_argument("--tts", choices=["kokoro", "azure", "sarvam"], default=None)
    ap.add_argument("--voice", default=None)
    a = ap.parse_args()
    with open(a.episode, encoding="utf-8") as fh:
        ep = json.load(fh)
    print(json.dumps(render_episode(ep, a.out, a.models, a.tts, a.voice, a.jobs)))


if __name__ == "__main__":
    main()
