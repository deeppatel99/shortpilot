"""Checks + safe auto-fixes for LLM-written episodes, so a bad script never reaches the renderer."""

import copy
import importlib.util
import os
import re

from . import channel
from .util import ROOT, load_config

_spec = importlib.util.spec_from_file_location("make_short", os.path.join(ROOT, "engine", "make_short.py"))
VISUAL_SPECS = None


def _specs():
    global VISUAL_SPECS
    if VISUAL_SPECS is None:
        m = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(m)
        VISUAL_SPECS = m.VISUAL_SPECS
    return VISUAL_SPECS


REQUIRED = {
    "headline": ["text"],
    "big_emoji": ["emoji"],
    "counter": ["to"],
    "comment": ["text"],
    "checklist": ["items"],
    "myth_fact": ["claim", "fact"],
    "phone_msg": ["text"],
    "versus": ["left", "right"],
    "bars": ["items"],
    "lockscreen": ["items"],
    "setting": ["rows"],
    "breaking": ["text"],
    "terminal": ["rows"],
}
ACCENTS = {"#FF4D4D", "#4DA3FF", "#FFB020", "#2BD67B", "#B36BFF"}
EMOJI_RE = re.compile("[\U0001f000-\U0001faff☀-➿️‍]")
# Spoken characters per second, by the language part of content.language ("es" for "es-MX") and voice engine.
# Alphabetic languages sit around 15/s; scripts that pack a word into 1-3 characters are much slower.
# Measured: Hindi (Devanagari) Azure ~10.1/s at rate +10%, Sarvam bulbul:v3 at pace 1.15.
CHARS_PER_SEC = {
    "hi": {"kokoro": 14.5, "azure": 10.5, "sarvam": 16.5},
    "ja": {"kokoro": 8.0},
    "zh": {"kokoro": 5.5},
    "ko": {"kokoro": 7.0},
}
DEFAULT_CPS = 15.0
LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


def language(cfg=None):
    """content.language in channel.json: a BCP-47 code such as "en-US", "es-MX", "hi-IN", "pt-BR", "ja-JP".
    It sets the language the script is written in and the voice's locale. Default "en-US"."""
    lang = str(((cfg or load_config()).get("content") or {}).get("language") or "en-US").strip()
    if not LANG_RE.match(lang):
        raise ValueError(f"content.language must be a language code like en-US, es-MX or hi-IN, got {lang!r}")
    return lang


def base_lang(lang):
    """ "es-MX" -> "es"."""
    return str(lang or "en").split("-")[0].lower()


def _cps(tts="kokoro", lang="en-US"):
    table = CHARS_PER_SEC.get(base_lang(lang), {})
    return table.get(tts) or table.get("kokoro") or DEFAULT_CPS


def char_range(tts="kokoro", lang="en-US"):
    """Spoken 'tts' character budget for a 25–35 s video with this voice engine and language."""
    c = _cps(tts, lang)
    return round(25 * c, -1), round(35 * c, -1)


def estimate_seconds(ep):
    chars = sum(len(l.get("tts", "")) for s in ep.get("scenes", []) for l in s.get("lines", []))
    lines = sum(len(s.get("lines", [])) for s in ep.get("scenes", []))
    c = _cps(ep.get("tts", "kokoro"), ep.get("lang", "en-US"))
    return chars / c + lines * 0.1 + 0.5, chars


def validate(ep):
    """Returns (fixed_episode, errors, warnings). Errors must be fixed before rendering."""
    ep = copy.deepcopy(ep)
    lang = ep.get("lang", "en-US")
    E, Wn = [], []
    specs = _specs()
    if not isinstance(ep.get("scenes"), list) or not ep["scenes"]:
        return ep, ["no scenes"], []
    ep.setdefault("brand", channel.brand())
    t = (ep.get("title") or "").strip()
    if not t:
        E.append("missing title")
    elif "#shorts" not in t.lower():
        ep["title"] = (t[:88] + " #shorts").strip()
    if len(ep.get("title", "")) > 100:
        E.append("title longer than 100 characters")
    if not ep.get("sources"):
        E.append("sources is empty — every number needs a source")
    for k in ("yt_description", "ig_caption", "pinned_comment"):
        if not ep.get(k):
            Wn.append(f"missing {k} (a fallback will be generated)")
    tags = ep.get("tags")
    ep["tags"] = [str(x).strip() for x in tags if str(x).strip()][:15] if isinstance(tags, list) else []
    if not ep["tags"]:
        Wn.append("missing tags (default channel tags will be used)")
    n = len(ep["scenes"])
    cast = [c for c in (ep.get("cast") or ["host"]) if c in ("host", "guest")][:2] or ["host"]
    ep["cast"] = cast
    whos = [ln.get("who", cast[0]) for sc in ep["scenes"] for ln in (sc.get("lines") or [])]
    if any(w not in cast for w in whos):
        E.append(f"every line's 'who' must be one of {cast}")
    if len(cast) == 2 and whos.count("guest") < 3:
        E.append("duo: the guest needs at least 3 lines (or make it solo with cast ['host'])")
    if not 5 <= n <= 10:
        E.append(f"{n} scenes — use 7–8")
    prev = None
    for i, sc in enumerate(ep["scenes"], 1):
        p = f"scene {i}"
        acc = str(sc.get("accent") or "").upper()
        sc["accent"] = acc if acc in ACCENTS else "#4DA3FF"
        card = sc.get("card") or []
        if isinstance(card, str):
            card = [card]
        sc["card"] = [EMOJI_RE.sub("", str(c)).strip() for c in card if c and EMOJI_RE.sub("", str(c)).strip()][:3]
        if not sc["card"]:
            E.append(f"{p}: card is empty")
        for c in sc["card"]:
            if len(c.replace("*", "")) > 26:
                Wn.append(f"{p}: card line long ({c})")
        if sc.get("emoji") is not None and not EMOJI_RE.search(str(sc.get("emoji") or "")):
            sc.pop("emoji")
        if sc.get("footnote") is not None:
            sc["footnote"] = str(sc["footnote"])[:70]
        bg = sc.get("bg")
        bg = bg if isinstance(bg, list) else ([bg] if isinstance(bg, (dict, str)) else [])
        shots = []
        for b in bg:
            if isinstance(b, str) and b.strip():
                b = {"ai": b}
            if isinstance(b, dict):
                k = next((k for k in ("stock", "ai", "photo") if isinstance(b.get(k), str) and b[k].strip()), None)
                if k:
                    shots.append({k: re.sub(r"[\u0900-\u097F]", "", b[k]).strip()[:300]})
        sc["bg"] = [x for x in shots if list(x.values())[0]][:3]
        v = sc.get("visual") if isinstance(sc.get("visual"), dict) else {}
        sc["visual"] = v
        vt = v.get("type")
        if vt not in specs:
            E.append(f"{p}: visual.type '{vt}' not one of {sorted(specs)}")
            continue
        for f in REQUIRED.get(vt, []):
            if not v.get(f):
                E.append(f"{p}: visual '{vt}' needs '{f}'")
        if vt == "counter" and v.get("to") is not None:
            try:
                v["to"] = int(float(str(v["to"]).replace(",", "")))
            except ValueError:
                E.append(f"{p}: visual.to must be a number")
        if vt == "bars":
            for it in v.get("items", []):
                try:
                    it["value"] = float(str(it.get("value", 0)).replace(",", "").replace("%", ""))
                except ValueError:
                    E.append(f"{p}: bars value must be numeric")
        if vt == prev:
            Wn.append(f"{p}: same visual as previous scene ({vt})")
        prev = vt
        lines = sc.get("lines") or []
        if len(lines) < specs[vt]:
            E.append(f"{p}: visual '{vt}' needs at least {specs[vt]} lines (has {len(lines)})")
        if vt == "checklist" and len(lines) < len(v.get("items", [])):
            Wn.append(f"{p}: checklist has more items than lines")
        if not lines:
            E.append(f"{p}: no lines")
        for j, ln in enumerate(lines, 1):
            q = f"{p} line {j}"
            tts, cap = str(ln.get("tts") or ""), EMOJI_RE.sub("", str(ln.get("cap") or "")).strip()
            ln["tts"] = tts
            ln["cap"] = cap
            if not tts.strip():
                E.append(f"{q}: empty tts")
            if base_lang(lang) == "hi":  # Hindi voices read Devanagari far better than romanised Hindi
                if re.search(r"[A-Za-z0-9]", tts):
                    E.append(
                        f"{q}: tts must be Devanagari only — transliterate English words and write numbers as Hindi words: '{tts}'"
                    )
                if tts and not re.search(r"[ऀ-ॿ]", tts):
                    E.append(f"{q}: tts has no Devanagari")
            if not cap:
                E.append(f"{q}: empty cap")
            if len(cap.split()) > 14:
                E.append(f"{q}: cap too long ({len(cap.split())} words) — split into two lines")
            words = {w.lower().strip(",.!?:;\"'") for w in cap.split()}
            ln["hi"] = [str(h) for h in (ln.get("hi") or []) if str(h).lower().strip(",.!?:;\"'") in words][:3]
    secs, chars = estimate_seconds(ep)
    ep["_estimate"] = {"seconds": round(secs, 1), "tts_chars": chars}
    lo, hi = char_range(ep.get("tts", "kokoro"), lang)
    # viewers leave at 6-8 s whatever the length; a short one gets finished and replayed
    if secs < 20:
        E.append(f"too short: ~{secs:.0f}s ({chars} tts chars) — aim for {lo:.0f}–{hi:.0f} chars")
    elif secs > 45:
        E.append(f"too long: ~{secs:.0f}s ({chars} tts chars) — cut to {lo:.0f}–{hi:.0f} chars")
    elif not 24 <= secs <= 36:
        Wn.append(f"length ~{secs:.0f}s (best 25–35s)")
    first = (ep["scenes"][0].get("lines") or [{}])[0].get("tts", "")
    if len(first) > 60:  # read in full on frame 0 - a long line can't hit in the first 2.5 s
        E.append(
            f"scene 1 line 1 is {len(first)} tts chars — the hook must be ≤ 40 (≤ 7 words); move the rest to line 2"
        )
    elif len(first) > 45:
        Wn.append("first spoken line is long — hook should land in < 2.5 s")
    dm = ep.get("dm")
    if dm:
        kw = re.sub(r"[^A-Z0-9]", "", str((dm if isinstance(dm, dict) else {}).get("keyword") or "").upper())
        text = str((dm if isinstance(dm, dict) else {}).get("text") or "").strip()
        if not 3 <= len(kw) <= 12 or len(text) < 60:
            E.append(
                "dm needs a one-word keyword (3–12 letters) and the real extra in dm.text (≥ 60 chars) — or drop dm"
            )
        else:
            ep["dm"] = {"keyword": kw, "offer": str(dm.get("offer") or "")[:60], "text": text[:950]}
            last = " ".join(ln.get("cap", "") for ln in ep["scenes"][-1].get("lines", []))
            if kw.lower() not in last.lower():
                E.append(f"last scene must say the keyword out loud, e.g. 'Comment {kw} and I'll send it to you'")
    if (ep["scenes"][-1].get("visual") or {}).get("type") != "comment":
        Wn.append("last scene is not a comment question")
    v0 = ep["scenes"][0].get("visual") or {}
    vt0 = v0.get("type")
    if vt0 not in PROOF_OPENERS:  # frame 0 must already show something real, not a reaction emoji
        E.append(f"scene 1 visual '{vt0}' — the opening frame needs proof: one of {', '.join(PROOF_OPENERS)}")
    # frame 0 is read in under a second: the shots that win carry 2-5 big words, not a paragraph
    if vt0 in ("headline", "breaking") and len(str(v0.get("text", ""))) > 50:
        E.append(
            f"scene 1 headline is {len(str(v0.get('text', '')))} chars — ≤ 50 on frame 0: the named thing + the number"
        )
    if len(" ".join(ep["scenes"][0].get("card") or []).replace("*", "").split()) > 5:  # prompt asks ≤ 4
        E.append("scene 1 card is more than 5 words — 2–4 words: it shares frame 0 with the visual and the hook")
    if not re.search(r"\d", ep.get("title", "")):
        Wn.append("title has no number — a number or count is the most reliable single title fix")
    return ep, E, Wn


# what a scroller sees on frame 0 next to the hook: a real headline, message, number or claim
PROOF_OPENERS = (
    "headline",
    "breaking",
    "phone_msg",
    "lockscreen",
    "setting",
    "myth_fact",
    "versus",
    "counter",
    "bars",
    "terminal",
)


def brand_tag():
    """The channel's own hashtag: the first of channel.hashtags, else #<channel name>."""
    return _hashtags().split()[0]


def _hashtags():
    """channel.hashtags from channel.json, e.g. ["#mychannel", "#aitools"]. Default: #<channel name>."""
    ch = load_config()["channel"]
    return " ".join(ch.get("hashtags") or ["#" + re.sub(r"[^a-z0-9]", "", ch["name"].lower())])


def _disclaimer():
    """channel.disclaimer: one fixed line under the YouTube description (AI voice note etc.). Optional."""
    d = load_config()["channel"].get("disclaimer", "")
    return "\n" + d if d else ""


def fallback_texts(ep, handle=None):
    """Platform texts if the LLM didn't provide them."""
    handle = handle or load_config()["channel"].get("handle", "")
    texts = (load_config().get("content") or {}).get("texts") or {}
    src = ep.get("sources") or []
    src_lines = "\n".join(
        f"• {s.get('title', '')} {s.get('url', '')}".strip() if isinstance(s, dict) else f"• {s}" for s in src[:5]
    )
    caps = [l["cap"] for s in ep["scenes"] for l in s["lines"]]
    ep.setdefault(
        "yt_description",
        f"{caps[0]} {caps[1] if len(caps) > 1 else ''}\n\nSources:\n{src_lines}\n\n{_hashtags()}{_disclaimer()}",
    )
    ep.setdefault(
        "ig_caption",
        f"{ep['title'].replace('#shorts', '').strip()}\n\n"
        + texts.get("share_line", "Send this to a friend who needs to know 👇")
        + f"\n\n{_hashtags()}",
    )
    ep.setdefault("pinned_comment", texts.get("comment_question", "Has this happened to you? Tell us below 👇"))
    return ep
