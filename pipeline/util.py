"""Small shared helpers: config/env loading, logging, HTTP, JSON extraction."""

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from . import channel

ROOT = channel.ROOT
CHANNEL = channel.SLUG
CHANNEL_DIR = channel.DIR  # channels/<slug>: channel.json, prompts/, assets/, .env, data/, episodes/
DATA = os.environ.get("AUTOPILOT_DATA") or os.path.join(CHANNEL_DIR, "data")  # cloud mode keeps this on a git branch
EPISODES = os.environ.get("AUTOPILOT_EPISODES") or os.path.join(CHANNEL_DIR, "episodes")
CLOUD = bool(os.environ.get("GITHUB_ACTIONS"))


def _utc_offset_minutes(cfg):
    """schedule.utc_offset in channel.json, e.g. "+00:00" (UTC, the default), "-05:00" (New York), "+05:30" (India)."""
    raw = str((cfg.get("schedule") or {}).get("utc_offset", "+00:00")).strip()
    sign = -1 if raw.startswith("-") else 1
    hh, _, mm = raw.lstrip("+-").partition(":")
    return sign * (int(hh) * 60 + int(mm or 0))


UTC_OFFSET_MIN = _utc_offset_minutes(channel.config())
TZ = timezone(timedelta(minutes=UTC_OFFSET_MIN))  # every schedule time in channel.json is in this zone


def load_env(path=None):
    """Minimal .env loader (KEY=VALUE, # comments). Existing environment variables win.
    Tests set AUTOPILOT_ENV_FILE to a missing file so the real tokens in .env never reach them."""
    path = path or os.environ.get("AUTOPILOT_ENV_FILE") or os.path.join(CHANNEL_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = re.sub(r"(^|\s)#.*", "", v)  # inline comment: KEY=   # note
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def load_config():
    """The active channel's channel.json (channels/<slug>/channel.json)."""
    return channel.config()


def env(name, default=None, required=False):
    v = os.environ.get(name, default)
    if required and not v:
        raise RuntimeError(f"Missing {name} in .env")
    return v


def get_logger():
    lg = logging.getLogger("autopilot")
    if lg.handlers:
        return lg
    os.makedirs(os.path.join(DATA, "logs"), exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(os.path.join(DATA, "logs", "autopilot.log"), encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    lg.addHandler(fh)
    lg.addHandler(sh)
    lg.setLevel(logging.INFO)
    return lg


log = get_logger()


def now_local():
    """Now, in the channel's own time zone (schedule.utc_offset)."""
    return datetime.now(TZ)


def http(method, url, retries=2, **kw):
    import requests

    kw.setdefault("timeout", 60)
    kw.setdefault("headers", {}).setdefault("User-Agent", "Mozilla/5.0 (ShortPilot)")
    last = None
    for i in range(retries + 1):
        try:
            r = requests.request(method, url, **kw)
            if r.status_code >= 500 or r.status_code == 429:
                raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
            return r
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def extract_json(text):
    """Pull the first JSON object/array out of an LLM reply (handles ```json fences and chatter)."""
    if text is None:
        raise ValueError("empty LLM reply")
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    cand = m.group(1) if m else text
    for opener, closer in (("{", "}"), ("[", "]")):
        start = cand.find(opener)
        while start != -1:
            depth, in_str, esc = 0, False, False
            for i in range(start, len(cand)):
                c = cand[i]
                if in_str:
                    if esc:
                        esc = False
                    elif c == "\\":
                        esc = True
                    elif c == '"':
                        in_str = False
                elif c == '"':
                    in_str = True
                elif c == opener:
                    depth += 1
                elif c == closer:
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cand[start : i + 1])
                        except json.JSONDecodeError:
                            break
            start = cand.find(opener, start + 1)
    raise ValueError("no JSON found in LLM reply: " + text[:300])


def slug(s, n=40):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s[:n] or "episode"


def read_prompt(name):
    p = os.path.join(CHANNEL_DIR, "prompts", name)
    if not os.path.exists(p):  # shared default, e.g. fix.md
        p = os.path.join(ROOT, "prompts", name)
    with open(p, encoding="utf-8") as fh:
        return fh.read()
