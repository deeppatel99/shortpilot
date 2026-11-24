"""Topic ranking, research and episode writing with automatic validation + repair."""

import json
import os

from .llm import get_llm
from .util import EPISODES, env, extract_json, load_config, log, now_local, read_prompt, slug
from .validate import char_range, fallback_texts, language, validate


def _complete(cfg, prov, prompt, web=False):
    """Call the provider; if it fails (e.g. Claude Pro usage limit hit) fall back to llm.fallback when configured."""
    try:
        return get_llm(prov, cfg).complete(prompt, web=web), prov
    except Exception as e:
        fb = cfg["llm"].get("fallback")
        keyname = {"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}.get(fb)
        if not fb or fb == prov or (keyname and not env(keyname)):
            raise
        log.warning(f"{prov} failed ({str(e)[:160]}) -> falling back to {fb}")
        return get_llm(fb, cfg).complete(prompt, web=web), fb


def _fill(tpl, cfg=None, **kw):
    """Replace {{NAME}} placeholders. With cfg, the channel placeholders (see channel_vars) are filled too."""
    for k, v in {**(channel_vars(cfg) if cfg else {}), **kw}.items():
        tpl = tpl.replace("{{" + k + "}}", v if isinstance(v, str) else json.dumps(v, ensure_ascii=False))
    return tpl


def channel_vars(cfg):
    """Placeholders every prompt in channels/<name>/prompts/ can use, all taken from channel.json."""
    ch, content = cfg["channel"], cfg.get("content") or {}
    cats = content.get("categories") or {}
    return {
        "CHANNEL": ch["name"],
        "BRAND": (ch.get("brand") or ch["name"]).upper(),
        "NICHE": ch.get("niche", ""),
        "AUDIENCE": ch.get("audience") or "a broad, general audience on their phones",
        "LANGUAGE": language(cfg),
        "CATEGORIES": "\n".join(f"- {k}: {v}" for k, v in cats.items()) or "- (no fixed categories: pick what fits)",
        "TODAY": f"{now_local():%A, %d %B %Y}",
    }


# no strong news today -> the next step of this cycle (None = ranker's own choice), then it wraps around.
# content.evergreen_flow in channel.json (default: the content.categories names, in order)
_CONTENT = load_config().get("content") or {}
EVERGREEN_FLOW = [*(_CONTENT.get("evergreen_flow") or list(_CONTENT.get("categories") or {})), None]


def next_evergreen(recent_categories):
    """Step after the newest evergreen video in recent_categories; the first step if none yet."""
    for cat in reversed(recent_categories or []):
        if cat in EVERGREEN_FLOW:
            return EVERGREEN_FLOW[(EVERGREEN_FLOW.index(cat) + 1) % len(EVERGREEN_FLOW)]
    return EVERGREEN_FLOW[0]


def rank_topics(cfg, candidates, history, n=1, recent_categories=None):
    lines = []
    for c in candidates[:250]:
        extra = c.get("traffic") or c.get("publisher") or c.get("channel") or ""
        news = "; ".join(x["title"] for x in c.get("news", [])[:2])
        lines.append(
            f"- [{c['source']}] {c['title']} {('(' + extra + ')') if extra else ''} {('— ' + news) if news else ''}".strip()
        )
    prompt = _fill(
        read_prompt("rank.md"),
        cfg,
        HISTORY=", ".join(history[-40:]) or "none",
        RECENT_CATEGORIES=", ".join(recent_categories or []) or "none yet",
        CANDIDATES="\n".join(lines) or "(no candidates — suggest evergreen topics)",
        NEXT_EVERGREEN=next_evergreen(recent_categories) or "your own choice (any category above)",
        N=str(n),
    )
    # keep the ranker's order: it follows the PRIORITY list in rank.md, a raw score sort would undo that
    picks = extract_json(_complete(cfg, cfg["llm"]["ranker"], prompt)[0]).get("picks", [])
    return [p for p in picks if p.get("scores", {}).get("safety", 10) >= 8][:n]


def research(cfg, topic, angle):
    prov = cfg["llm"].get("researcher") or cfg["llm"]["writer"]
    return extract_json(
        _complete(cfg, prov, _fill(read_prompt("research.md"), cfg, TOPIC=topic, ANGLE=angle), web=True)[0]
    )


def write_episode(cfg, topic, angle="", hook="", feedback=None, provider=None, max_fixes=2, category="", fmt=""):
    """Returns (episode_dict, warnings). Research happens inside the writer call when the writer can browse
    (claude_cli / anthropic / gemini all can); a separate researcher can be configured instead."""
    prov = provider or cfg["llm"]["writer"]
    sep = cfg["llm"].get("researcher") and cfg["llm"]["researcher"] != prov and not provider
    research_txt, web_instr = (
        "",
        'Use web search to verify every number before you write. Put the URLs you used in "sources".',
    )
    if sep:
        facts = research(cfg, topic, angle)
        research_txt = "RESEARCH (use only these facts):\n" + json.dumps(facts, ensure_ascii=False, indent=1)
        web_instr = ""
    tts = cfg["voice"].get("tts", "kokoro")  # the length budget depends on how fast this voice speaks
    lang = language(cfg)
    lo, hi = char_range(tts, lang)
    prompt = _fill(
        read_prompt("episode.md"),
        cfg,
        CATEGORY=category or "(your choice, fits the topic)",
        FORMAT=fmt or "(your choice, fits the topic)",
        CHARS=f"{lo:.0f}–{hi:.0f}",
        TOPIC=topic,
        ANGLE=angle or topic,
        HOOK=hook or "(your choice)",
        RESEARCH=research_txt,
        WEB_INSTRUCTION=web_instr,
        FEEDBACK=("CHANGES REQUESTED BY THE EDITOR (apply them):\n" + feedback) if feedback else "",
    )
    log.info(f"writing episode with {prov}: {topic}")
    text, prov = (
        _complete(cfg, prov, prompt, web=not sep)
        if not provider
        else (get_llm(prov, cfg).complete(prompt, web=not sep), prov)
    )
    ep = extract_json(text)
    for attempt in range(max_fixes + 1):
        ep["tts"], ep["lang"] = tts, lang
        ep, errors, warnings = validate(ep)
        if not errors:
            break
        if attempt == max_fixes:
            raise RuntimeError("episode failed validation: " + "; ".join(errors))
        log.info(f"fixing {len(errors)} validation errors (attempt {attempt + 1})")
        fix = _fill(
            read_prompt("fix.md"),
            ERRORS="\n".join("- " + e for e in errors),
            EPISODE=json.dumps({k: v for k, v in ep.items() if not k.startswith("_")}, ensure_ascii=False, indent=1),
        )
        ep = extract_json(get_llm(prov, cfg).complete(fix))
    ep = fallback_texts(ep, cfg["channel"].get("handle", ""))
    ep.update(
        voice=cfg["voice"].get("voice", "af_heart"),
        speed=cfg["voice"].get("speed", 1.1),
        cta=(cfg.get("content") or {}).get("cta", "Comment below"),
        tts=tts,
        lang=lang,
        category=category,
        format=fmt,
        azure_voice=cfg["voice"].get("azure_voice", "en-US-AvaMultilingualNeural"),
        sarvam_voice=cfg["voice"].get("sarvam_voice", "ritu"),
        look=cfg.get("look") or ep.get("look"),
        _writer=prov,
        _angle=angle,
    )
    return ep, warnings


def save_episode(ep, name=None):
    d = EPISODES
    os.makedirs(d, exist_ok=True)
    name = name or f"{now_local():%Y%m%d-%H%M}_{slug(ep.get('topic') or ep.get('title', 'episode'))}"
    path = os.path.join(d, name + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ep, fh, ensure_ascii=False, indent=1)
    return path
