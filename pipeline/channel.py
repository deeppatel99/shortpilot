"""The active channel pack: channels/<slug>/ holds channel.json, prompts/, assets/ and, on the PC, .env, data/, episodes/.

Which channel is active (first match wins):
  1. --channel <slug> on the command line
  2. environment variable AUTOPILOT_CHANNEL (the cloud workflow sets it)
  3. the file .channel in the project folder (one line: the slug)
  4. the only folder in channels/ (folders starting with "_", like _template, are never picked automatically)

Standard library only: the renderers in engine/ run as separate processes and import this too."""

import html as _html
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHANNELS = os.path.join(ROOT, "channels")


def available():
    if not os.path.isdir(CHANNELS):
        return []
    return sorted(
        d
        for d in os.listdir(CHANNELS)
        if not d.startswith(("_", ".")) and os.path.isfile(os.path.join(CHANNELS, d, "channel.json"))
    )


def _from_argv():
    for i, a in enumerate(sys.argv):
        if a == "--channel" and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith("--channel="):
            return a.split("=", 1)[1]
    return None


def resolve():
    slug = _from_argv() or os.environ.get("AUTOPILOT_CHANNEL")
    if not slug:
        try:
            with open(os.path.join(ROOT, ".channel"), encoding="utf-8") as fh:
                slug = fh.read().strip()
        except OSError:
            slug = None
    have = available()
    if not slug and len(have) == 1:
        slug = have[0]
    if not slug:
        raise SystemExit(
            "Which channel? Add --channel <name>, or write the name into a file called .channel in the project folder.\n"
            f"Channels: {', '.join(have) or '(none yet - run: python autopilot.py new-channel <name>)'}"
        )
    if not os.path.isfile(os.path.join(CHANNELS, slug, "channel.json")):
        raise SystemExit(f"Channel '{slug}' not found. Channels: {', '.join(have) or '(none)'}")
    return slug


SLUG = resolve()
DIR = os.path.join(CHANNELS, SLUG)
os.environ["AUTOPILOT_CHANNEL"] = SLUG  # render subprocesses pick the same channel
os.environ["AUTOPILOT_CHANNEL_ASSETS"] = os.path.join(DIR, "assets")


def config():
    with open(os.path.join(DIR, "channel.json"), encoding="utf-8") as fh:
        return json.load(fh)


def asset(*rel):
    """A file from the channel's assets/ folder, else the shared one in engine/assets/."""
    p = os.path.join(DIR, "assets", *rel)
    return p if os.path.exists(p) else os.path.join(ROOT, "engine", "assets", *rel)


def brand(cfg=None):
    """The short name burnt into videos, e.g. MY CHANNEL."""
    ch = (cfg or config())["channel"]
    return (ch.get("brand") or ch["name"]).upper()


def file_url(path):
    return "file:///" + os.path.abspath(path).replace(os.sep, "/").lstrip("/")


STAGE_LABELS = {"follow": "Follow", "myth": "everyone says…", "fact": "the truth", "you": "@you"}


def stage_vars(cfg=None):
    """Brand texts for the HTML stages (engine/web/*.html). channel.stage in channel.json overrides the defaults:
    {"handle": "@x", "tagline": "...", "brand_a": "MY", "brand_b": "CHANNEL", "brand_text": "name in any script",
     "labels": {"follow": "Follow", "myth": "everyone says…", "fact": "the truth", "you": "@you"}}
    The labels are the few fixed words drawn on screen: translate them for a channel in another language."""
    cfg = cfg or config()
    ch = cfg["channel"]
    st = ch.get("stage") or {}
    lb = {**STAGE_LABELS, **(st.get("labels") or {})}
    words = brand(cfg).split()
    half = max(1, len(words) // 2)
    v = {
        "__HANDLE__": st.get("handle") or ch.get("handle", ""),
        "__TAGLINE__": st.get("tagline") or ch.get("tagline", ""),
        "__BRAND_A__": st.get("brand_a", " ".join(words[:half])),
        "__BRAND_B__": st.get("brand_b", " ".join(words[half:])),
        "__BRAND_TEXT__": st.get("brand_text") or ch["name"],
        **{f"__L_{k.upper()}__": x for k, x in lb.items()},
    }
    v = {k: _html.escape(x) for k, x in v.items()}
    v["__LOGO__"] = file_url(asset("logo_icon.png"))
    return v


def fill_stage(page, cfg=None):
    for k, x in stage_vars(cfg).items():
        page = page.replace(k, x)
    return page
