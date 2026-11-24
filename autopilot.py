#!/usr/bin/env python3
"""
ShortPilot — trend → script → (approve) → video → (approve) → Instagram + YouTube + Facebook.
One engine, many channels: each channel is a folder in channels/<slug>/ (channel.json, prompts/, assets/, .env).

  python autopilot.py demo                       # render the built-in demo video (no keys, no LLM)
  python autopilot.py new-channel mychannel      # copy channels/_template -> channels/mychannel
  python autopilot.py check                      # what's installed / configured
  python autopilot.py trends                     # today's raw candidates
  python autopilot.py pick                       # AI-ranked topics
  python autopilot.py write --topic "..."        # write + validate one episode JSON
  python autopilot.py render episodes/x.json     # render a video
  python autopilot.py make --topic "..."         # write + render, no approvals
  python autopilot.py compare --topic "..."      # Claude vs Gemini on the same topic
  python autopilot.py serve                      # 24x7: daily scout, Telegram approvals, scheduled publishing
  python autopilot.py telegram-setup | ig-whoami | ig-refresh | ig-limit | host-test | youtube-auth | publish <id> [--now]
  python autopilot.py cloud-setup                # move everything to GitHub Actions (PC can be off)

Several channels in channels/?  Add  --channel <slug>  to any command (or write the slug into the file .channel).
"""

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)


def new_channel(name):
    """Copy channels/_template to channels/<name> with its own .env. Runs before any channel is loaded."""
    import re

    slug_ = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
    src, dst = os.path.join(ROOT, "channels", "_template"), os.path.join(ROOT, "channels", slug_)
    if not slug_ or slug_.startswith("_"):
        sys.exit("Use a name like: mychannel")
    if os.path.exists(dst):
        sys.exit(f"channels/{slug_} already exists")
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".env", "data", "episodes"))
    shutil.copyfile(os.path.join(ROOT, ".env.example"), os.path.join(dst, ".env"))
    p = os.path.join(dst, "channel.json")
    with open(p, encoding="utf-8") as fh:
        cfg = json.load(fh)
    title = name.replace("-", " ").replace("_", " ").title()
    cfg["channel"].update(name=title, brand=title.upper(), handle="@" + slug_.replace("-", ""))
    st = cfg["channel"]["stage"]
    st.update(handle="@" + slug_.replace("-", ""))
    st.pop("brand_a", None), st.pop("brand_b", None)  # the stage splits the brand into its two-tone wordmark
    cfg["cloud"].update(repo=f"{slug_}-bot", worker=f"{slug_}-webhook")
    cfg["cloud"]["site"]["publisher"] = title
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    print(
        f"✅ channels/{slug_}/ created.\n"
        f"   1. Edit channels/{slug_}/channel.json  (niche, audience, content.language, schedule)  — docs/CONFIGURATION.md\n"
        f"   2. Fill channels/{slug_}/.env         (Telegram, YouTube, Instagram keys)          — docs/SETUP.md\n"
        f'   3. python autopilot.py make --topic "your first topic" --channel {slug_}'
    )


def _has_channel():
    d = os.path.join(ROOT, "channels")
    return any(
        not n.startswith(("_", ".")) and os.path.isfile(os.path.join(d, n, "channel.json")) for n in os.listdir(d)
    )


if (
    __name__ == "__main__"
    and sys.argv[1:2] == ["demo"]
    and not _has_channel()
    and "AUTOPILOT_CHANNEL" not in os.environ
):
    os.environ["AUTOPILOT_CHANNEL"] = "_template"  # first run: the demo works before any channel exists

if __name__ == "__main__" and sys.argv[1:2] == ["new-channel"]:
    new_channel(sys.argv[2] if len(sys.argv) > 2 else sys.exit("Use: python autopilot.py new-channel <name>"))
    sys.exit(0)

from pipeline import state as store  # noqa: E402
from pipeline.util import CHANNEL, CLOUD, DATA, env, load_config, load_env, log, now_local, slug  # noqa: E402

load_env()
CFG = load_config()
MODELS = os.path.join(ROOT, "models")
OUT = os.path.join(DATA, "videos")
os.makedirs(OUT, exist_ok=True)
NAME = CFG["channel"]["name"]
DEFAULT_TAGS = CFG["channel"].get("default_tags") or [NAME.lower(), "shorts"]
# render.engine in channel.json -> the renderer script in engine/
ENGINES = {"html": "html_short.py", "classic": "make_short.py"}


# ============================================================================ helpers
def render_cmd(ep_path, out_path, voice=None):
    engine = ENGINES.get(CFG["render"].get("engine", "html"), "make_short.py")
    cmd = [sys.executable, os.path.join(ROOT, "engine", engine), ep_path, out_path, "--models", MODELS]
    if CFG["render"].get("jobs"):
        cmd += ["--jobs", str(CFG["render"]["jobs"])]
    if voice:
        cmd += ["--voice", voice]
    return cmd


def render_now(ep_path, out_path=None, voice=None):
    out_path = out_path or os.path.join(OUT, os.path.splitext(os.path.basename(ep_path))[0] + ".mp4")
    subprocess.run(render_cmd(ep_path, out_path, voice), check=True, env={**os.environ, "PYTHONUTF8": "1"})
    return out_path


def script_preview(it, ep, warnings, auto=False):
    est = ep.get("_estimate", {})
    s = [
        f"📝 SCRIPT #{it['id']}  ({ep.get('_writer')}, ~{est.get('seconds', '?')}s)",
        f"🎯 {ep.get('topic', '')}",
        f"📌 {ep['title']}",
        "",
    ]
    for i, sc in enumerate(ep["scenes"], 1):
        card = " / ".join(c.replace("*", "") for c in (sc.get("card") or [sc.get("label", "")]))
        kind = (sc.get("visual") or {}).get("type", "")
        if isinstance(sc.get("shot"), dict):
            kind = f"{sc.get('sfx', '')}" + (" 🎞" if sc["shot"].get("clip") else "")
        s.append(f"{i}. [{kind}] {card}")
        if isinstance(sc.get("shot"), dict) and sc["shot"].get("ai"):
            s.append(f"   📷 {sc['shot']['ai'][:110]}")
        for ln in sc["lines"]:
            s.append(f"   🗣 {ln['cap']}")
    s += ["", "🔗 Sources:"] + [
        f"• {x.get('title', '')} {x.get('url', '')}" if isinstance(x, dict) else f"• {x}"
        for x in ep.get("sources", [])[:6]
    ]
    if warnings:
        s += ["", "⚠️ " + "\n⚠️ ".join(warnings[:6])]
    s += [
        "",
        f"⏰ Publishes itself at {next_publish_time(it.get('slot'), asap=bool(it.get('slot'))):%I:%M %p}. Tap Reject to stop it 👇"
        if auto
        else "Check the facts, then approve 👇",
    ]
    return "\n".join(s)


def is_auto(cron=False):
    """publish.auto in channel.json:  true = every script renders and books itself (only a Reject button);
    "cron" = only the scheduled daily run does, /new and /daily keep the approve flow;  false = always approve."""
    m = CFG["publish"].get("auto")
    return m is True or (m == "cron" and cron)


def auto_preview():
    """publish.auto_preview (default true): in auto mode still send the finished video to Telegram. false = quiet:
    the script's Reject button is the only control."""
    return bool(CFG["publish"].get("auto_preview", True))


def ai_label():
    """publish.ai_label (default true): tick YouTube's 'Altered or synthetic content' on uploads."""
    return bool(CFG["publish"].get("ai_label", True))


def with_footer(text, key):
    """Fixed follow / cross-link footer from channel.json (channel.yt_footer / channel.ig_footer), added once."""
    f = CFG["channel"].get(key, "")
    return text if not f or f in text else text.rstrip() + "\n\n" + f


def yt_mode():
    """manual | api. 'auto' (default) = api when a YouTube login token exists (PC file or cloud secret)."""
    m = (CFG["publish"].get("youtube_mode") or "auto").lower()
    if m != "auto":
        return m
    from pipeline.publish import yt_token_available

    return "api" if yt_token_available() else "manual"


def with_credits(desc, video):
    """Pexels/Pixabay ask for attribution: append creator credits from the render's _credits.json."""
    try:
        with open(os.path.splitext(video)[0] + "_credits.json", encoding="utf-8") as fh:
            cr = [c for c in json.load(fh) if c.get("site") in ("Pexels", "Pixabay")]
    except Exception:
        cr = []
    if not cr or "Stock footage/photos:" in desc:
        return desc
    sites = " & ".join(dict.fromkeys(f"{c['site']} ({c['site'].lower()}.com)" for c in cr))
    names = ", ".join(dict.fromkeys(c.get("by", "") for c in cr if c.get("by")))[:300]
    return desc.rstrip() + f"\n\nStock footage/photos: {sites} — {names}"


def next_publish_time(slot=None, now=None, asap=False):
    """slot "08:00". Default: schedule.publish_at, or the last of schedule.slots for channels with several videos a
    day. Already past today -> tomorrow, or right now when asap (a planned "today" video is worthless tomorrow)."""
    now = now or now_local()
    sch = CFG["schedule"]
    hh, mm = map(int, (slot or sch.get("publish_at") or sch["slots"][-1]).split(":"))
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if t > now + timedelta(minutes=10):
        return t
    return now if asap else t + timedelta(days=1)


# ============================================================================ the bot
class Autopilot:
    def __init__(self):
        from pipeline.telegram import Telegram

        self.state = store.load()
        self.tg = Telegram()
        self.jobs = queue.Queue()
        self.renders = {}
        self.lock = threading.RLock()
        threading.Thread(target=self._worker, daemon=True).start()

    def save(self):
        with self.lock:
            store.save(self.state)

    def set_status(self, iid, status, **fields):
        with self.lock:
            return store.set_status(self.state, iid, status, **fields)

    # ---------------- background worker for slow jobs (LLM, publishing)
    def _worker(self):
        while True:
            fn, args = self.jobs.get()
            try:
                fn(*args)
            except Exception as e:
                log.error(traceback.format_exc())
                self.safe_send(f"❌ Error in {fn.__name__}: {e}")
            finally:
                self.jobs.task_done()

    def drain(self, timeout=None):
        """Cloud mode: wait until every queued job and running render has finished."""
        timeout = timeout or CFG["schedule"].get("drain_minutes", 50) * 60
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.poll_renders()
            if not self.renders and self.jobs.unfinished_tasks == 0:
                time.sleep(1.5)
                self.poll_renders()
                if not self.renders and self.jobs.unfinished_tasks == 0:
                    return True
            time.sleep(2)
        log.error("drain: timed out")
        return False

    def safe_send(self, text, buttons=None, html=False):
        try:
            return self.tg.send(text, buttons, html=html)
        except Exception as e:
            log.error(f"telegram send failed: {e}")

    def stats(self, last=7):
        """/stats: views, likes, saves and watch time of the last published videos on YouTube, Instagram, Facebook."""
        from html import escape

        from pipeline.publish import platform_stats

        pub = [(i, x) for i, x in self.state["items"].items() if x.get("status") == "published"][-last:][::-1]
        if not pub:
            return self.safe_send("No published videos yet.")
        rows = platform_stats(
            [
                {
                    "yt": x.get("yt_id"),
                    "ig": x.get("ig_id"),
                    "fb": (x.get("fb_url") or "").rstrip("/").rsplit("/", 1)[-1] or None,
                    "published": x.get("published"),
                }
                for _, x in pub
            ]
        )
        seen = self.state.setdefault("stats_seen", {})
        plat, cards, best, grew = {"yt": 0, "ig": 0, "fb": 0}, [], None, 0
        for n, ((iid, x), s) in enumerate(zip(pub, rows), 1):
            views = sum(p["views"] for p in s.values())
            for k in s:
                plat[k] += s[k]["views"]
            up = views - seen.get(iid, views)
            seen[iid], grew = views, grew + max(up, 0)
            name = escape((x.get("topic") or iid).split(":")[0].strip()[:32])
            if not best or views > best[1]:
                best = (name, views)
            # comments left out: most are our own pinned comment
            likes = sum(p["likes"] for p in s.values())
            # YouTube's API has no watch time: average Instagram + Facebook, weighted by views
            w = [(p["watch"], p["views"]) for p in s.values() if p.get("watch")]
            watch = sum(a * b for a, b in w) / (sum(b for _, b in w) or 1)
            length = s.get("yt", {}).get("length")
            day = datetime.fromisoformat(x["published"]).strftime("%d %b") if x.get("published") else ""
            card = [
                f"<b>{n}. {name}</b>  <i>{day}</i>",
                f"👁 <b>{views:,}</b> views" + (f"  (+{up:,})" if up > 0 else ""),
                "     "
                + " · ".join(
                    f"{icon} {s[k]['views']:,}" if k in s else f"{icon} —"
                    for k, icon in (("yt", "▶️"), ("ig", "📸"), ("fb", "📘"))
                ),
                f"❤️ {likes} likes · 🔖 {s.get('ig', {}).get('saved', 0)} saves",
            ]
            if watch:
                card.append(
                    f"⏱ Watched ~{watch:.0f}s on average"
                    + (f" (of {length}s · {watch / length:.0%})" if length else "")
                )
            cards.append("\n".join(card))
        self.save()
        head = [
            f"📊 <b>Stats</b> · {now_local():%d %b, %I:%M %p}",
            "",
            f"👁 <b>Total: {sum(plat.values()):,} views</b>" + (f"  (+{grew:,} since last check)" if grew else ""),
            f"▶️ YouTube  <b>{plat['yt']:,}</b>",
            f"📸 Instagram  <b>{plat['ig']:,}</b>",
            f"📘 Facebook  <b>{plat['fb']:,}</b>",
            f"🏆 Best: <b>{best[0]}</b> ({best[1]:,})",
            "━━━━━━━━━━━━",
        ]
        self.safe_send("\n".join(head) + "\n" + "\n\n".join(cards), html=True)

    # ---------------- step 1: scout + draft
    def daily(self, cron=False):
        """cron = started by the schedule (not by /daily). In auto mode (see is_auto) there is no approval step: the
        script is rendered and booked unless you tap Reject."""
        from pipeline import trends
        from pipeline.writer import rank_topics

        auto = is_auto(cron)
        n = CFG["schedule"].get("videos_per_day", 1)
        self.safe_send("🔎 Looking for today's trending topics…")
        cands = trends.collect(CFG)
        recent = [i["category"] for i in self.state["items"].values() if i.get("category")][-6:]
        picks = rank_topics(CFG, cands, self.state.get("history", []), n=n + 2, recent_categories=recent)
        if not picks:
            return self.safe_send("No safe, on-niche topic today. Send one yourself: /new <topic>")
        for p in picks[:n]:
            self.draft(
                p["topic"],
                p.get("angle", ""),
                p.get("hook", ""),
                alternates=picks[n:],
                category=p.get("category", ""),
                fmt=p.get("format", ""),
                auto=auto,
            )

    def draft(
        self,
        topic,
        angle="",
        hook="",
        feedback=None,
        iid=None,
        alternates=None,
        category="",
        fmt="",
        auto=None,
        slot=None,
    ):
        from pipeline.writer import save_episode, write_episode

        auto = is_auto() if auto is None else auto  # /new, edits and "Other topic": auto only when publish.auto is true

        with self.lock:
            it = (
                self.state["items"][iid]
                if iid
                else store.new_item(
                    self.state,
                    topic=topic,
                    angle=angle,
                    hook=hook,
                    alternates=alternates or [],
                    category=category,
                    format=fmt,
                    **({"slot": slot} if slot else {}),
                )
            )
            category = category or it.get("category", "")
            fmt = fmt or it.get("format", "")
            it["status"] = "writing"
            self.save()
        self.safe_send(f"✍️ #{it['id']} writing the script: {topic}")
        ep, warnings = write_episode(CFG, topic, angle, hook, feedback=feedback, category=category, fmt=fmt)
        path = save_episode(ep, name=f"{it['id']}_{slug(topic)}")
        with self.lock:
            self.state.setdefault("history", []).append(topic)
            self.set_status(it["id"], "awaiting_script", episode=path, title=ep["title"], auto=auto)
        if auto:
            self.safe_send(script_preview(it, ep, warnings, auto=True), [[("❌ Reject", f"reject:{it['id']}")]])
            return self.start_render(it["id"], quiet=not auto_preview())
        self.safe_send(
            script_preview(it, ep, warnings),
            [
                [("✅ Approve & render", f"approve:{it['id']}")],
                [("✏️ Request changes", f"edit:{it['id']}"), ("🔄 Other topic", f"skip:{it['id']}")],
                [("❌ Reject", f"reject:{it['id']}")],
            ],
        )

    # ---------------- step 2: render
    def start_render(self, iid, quiet=False):
        it = self.state["items"][iid]
        out = os.path.join(OUT, f"{iid}_{slug(it['topic'])}.mp4")
        logf = open(os.path.join(DATA, "logs", f"render_{iid}.log"), "w", encoding="utf-8")
        proc = subprocess.Popen(
            render_cmd(it["episode"], out), stdout=logf, stderr=subprocess.STDOUT, env={**os.environ, "PYTHONUTF8": "1"}
        )
        self.renders[iid] = (proc, out, logf, time.time())
        self.set_status(iid, "rendering", video=out)
        if not quiet:
            self.safe_send(
                f"🖥️ #{iid} GitHub runner ready — rendering (~8–12 min)…"
                if CLOUD
                else f"🎬 #{iid} rendering (~3–6 min)…"
            )

    def poll_renders(self):
        for iid, (proc, out, logf, t0) in list(self.renders.items()):
            if proc.poll() is None and time.time() - t0 > 25 * 60:  # watchdog: never let a render hang forever
                proc.kill()
                proc.wait()
                log.error(f"render {iid} killed after 25 min")
            if proc.poll() is None:
                continue
            logf.close()
            del self.renders[iid]
            if self.state["items"][iid].get("status") == "rejected":  # Reject tapped on the script while rendering
                continue
            if proc.returncode != 0 or not os.path.exists(out):
                self.set_status(iid, "render_failed")
                try:
                    with open(
                        os.path.join(DATA, "logs", f"render_{iid}.log"), encoding="utf-8", errors="replace"
                    ) as fh:
                        full = fh.read()
                    tail = full[-400:]
                    log.error(
                        f"render {iid} failed (rc {proc.returncode}):\n{full[-3000:]}"
                    )  # visible in the Actions log
                except Exception:
                    tail = ""
                self.safe_send(
                    f"❌ #{iid} render failed.\n…{tail}\nLog: data/logs/render_{iid}.log",
                    [[("🔁 Render again", f"approve:{iid}"), ("❌ Reject", f"reject:{iid}")]],
                )
                continue
            it = self.set_status(iid, "awaiting_publish", cover=os.path.splitext(out)[0] + "_cover.jpg")
            if CLOUD:
                from pipeline import cloud

                base = os.path.splitext(out)[0]
                try:
                    cloud.store_video(iid, out, base + "_cover.jpg", base + "_credits.json")
                except Exception as e:
                    self.safe_send(
                        f"⚠️ #{iid} could not save the video to GitHub ({e}). You may need /render {iid} before it publishes."
                    )
            # a finished video books itself for its slot (planned ones go out at once if the slot already passed);
            # the buttons are only there to stop it
            pt = next_publish_time(it.get("slot"), asap=bool(it.get("slot")))
            self.jobs.put((self.schedule, (iid, pt)))
            if it.get("auto") and not auto_preview():  # quiet auto: the script's Reject button is the only control
                continue
            try:
                self.tg.send_video(
                    out,
                    f"🎬 #{iid} ready ({time.time() - t0:.0f}s render)\n{it['title']}\n"
                    f"⏰ Publishes itself on {pt:%d %b %H:%M}. Tap Reject to stop it.",
                    [
                        [("🚀 Publish now", f"pubnow:{iid}")],
                        [("🔁 Re-render", f"approve:{iid}"), ("❌ Reject", f"reject:{iid}")],
                    ],
                    cover=it.get("cover"),
                )
            except Exception as e:
                self.safe_send(f"Video ready but Telegram upload failed ({e}). File: {out}")

    # ---------------- step 3: publish
    def _ensure_video(self, iid):
        it = self.state["items"][iid]
        if not os.path.exists(it.get("video", "")) and CLOUD:
            from pipeline import cloud

            cloud.fetch_video(iid, os.path.dirname(it["video"]))
        if not os.path.exists(it.get("video", "")):
            raise RuntimeError(f"video file missing: {it.get('video')} — send /render {iid}")

    def _yt_upload(self, iid, ep, when=None):
        """Upload to YouTube via API. when in the future -> YouTube publishes it itself at that time. Never raises."""
        from pipeline.publish import youtube_upload

        it = self.state["items"][iid]
        try:
            self._ensure_video(iid)
            later = when if when and when > now_local() + timedelta(minutes=15) else None
            it["yt_id"] = youtube_upload(
                it["video"],
                ep["title"],
                with_credits(with_footer(ep.get("yt_description", ""), "yt_footer"), it["video"]),
                ep.get("tags") or DEFAULT_TAGS,
                publish_at=later,
                synthetic=ai_label(),
            )
            it.pop("yt_error", None)
            self.save()
            cover = os.path.splitext(it["video"])[0] + "_cover.jpg"
            if os.path.exists(cover):
                try:
                    from pipeline.publish import youtube_thumbnail

                    youtube_thumbnail(it["yt_id"], cover)
                except Exception as e:  # not fatal: YouTube then picks a frame itself
                    log.warning(f"YT thumbnail #{iid}: {e}")
            self.safe_send(
                f"▶️ #{iid} uploaded to YouTube: https://youtube.com/shorts/{it['yt_id']}"
                + (f" (goes public {later:%d %b %H:%M})" if later else " (public)")
            )
        except Exception as e:
            it["yt_error"] = str(e)[:300]
            self.save()
            log.error(f"youtube upload #{iid}: {e}")
            hint = (
                "\nThe YouTube login expired: on your PC run `autopilot.py youtube-auth`, then `youtube-test`."
                if "invalid_grant" in str(e)
                else ""
            )
            self.safe_send(
                f"⚠️ #{iid} YouTube auto-upload failed ({str(e)[:200]}).{hint}\n"
                "It is retried once at publish time; otherwise you get the title, description and file to upload by hand."
            )

    def schedule(self, iid, when):
        it = self.state["items"][iid]
        if it.get("status") in ("rejected", "rendering"):  # Reject / Re-render tapped while this job was queued
            return
        with open(it["episode"], encoding="utf-8") as fh:
            ep = json.load(fh)
        if yt_mode() == "api" and not it.get("yt_id"):
            self._yt_upload(iid, ep, when)
        self.set_status(iid, "scheduled", publish_time=when.isoformat())

    def unschedule(self, iid):
        """Reject / re-render of a booked video: its YouTube copy is already up with a publish time, so remove it."""
        it = self.state["items"][iid]
        if it.get("status") != "scheduled" or not it.get("yt_id"):
            return
        from pipeline.publish import youtube_delete

        try:
            youtube_delete(it.pop("yt_id"))
        except Exception as e:
            self.safe_send(f"⚠️ #{iid} could not delete the scheduled YouTube video ({e}). Remove it in YouTube Studio.")
        self.save()

    def publish_due(self, early_min=0):
        for iid, it in list(self.state["items"].items()):
            if it.get("status") != "scheduled" or datetime.fromisoformat(it["publish_time"]) > now_local() + timedelta(
                minutes=early_min
            ):
                continue
            self.set_status(iid, "publishing")
            self.jobs.put((self.publish, (iid,)))

    def publish(self, iid):
        from pipeline.publish import ig_publish_with_fallback

        it = self.state["items"][iid]
        with open(it["episode"], encoding="utf-8") as fh:
            ep = json.load(fh)
        self._ensure_video(iid)
        ep["yt_description"] = with_credits(with_footer(ep.get("yt_description", ""), "yt_footer"), it["video"])
        ep["ig_caption"] = with_footer(ep["ig_caption"], "ig_footer")
        done, failed, fb_tried = [], None, False
        # YouTube: API upload (normally already done at scheduling time, retried here once); manual hand-off as the fallback
        if yt_mode() == "api" and not it.get("yt_id") and not it.get("yt_sent"):
            self._yt_upload(iid, ep)
        if it.get("yt_id"):
            try:  # booked uploads are private + publishAt; a publish before that time must still go public now
                from pipeline.publish import youtube_make_public

                if youtube_make_public(it["yt_id"], synthetic=ai_label()):
                    log.info(f"YT #{iid}: made public now (was scheduled)")
            except Exception as e:
                log.warning(f"YT make public #{iid}: {e}")
            if not it.get("yt_commented") and ep.get("pinned_comment"):
                try:
                    from pipeline.publish import youtube_comment

                    youtube_comment(it["yt_id"], ep["pinned_comment"])
                    it["yt_commented"] = True
                    self.save()
                except Exception as e:
                    log.warning(f"YT comment #{iid}: {e}")
            done.append(f"YouTube ✅ https://youtube.com/shorts/{it['yt_id']}")
        else:
            if not it.get("yt_sent"):
                self.safe_send(
                    f"📤 #{iid} upload this to YouTube Shorts:\n\nTITLE:\n{ep['title']}\n\nDESCRIPTION:\n{ep['yt_description']}\n\nPIN COMMENT:\n{ep.get('pinned_comment', '')}"
                )
                try:
                    self.tg.send_document(it["video"], "Original file (best quality) 👆")
                except Exception as e:
                    log.warning(f"send_document: {e}")
                it["yt_sent"] = True
                self.save()
            done.append("YouTube: manual")
        # Instagram
        if CFG["publish"].get("instagram") and env("IG_USER_ID") and not it.get("ig_id"):
            self.safe_send(f"⏳ #{iid} posting to Instagram (2–10 min)…")
            try:
                it["ig_id"], host = ig_publish_with_fallback(
                    it["video"], ep["ig_caption"], first_comment=ep.get("pinned_comment")
                )
                it["ig_host"] = host
                self.save()
                done.append("Instagram ✅")
                from pipeline.publish import FB_RESULT

                fb_tried = bool(FB_RESULT)  # the Facebook Reel goes out inside the Instagram publish
                if FB_RESULT.get("url"):
                    it["fb_url"] = FB_RESULT["url"]  # /stats reads the reel id from it
                    self.save()
                    done.append(f"Facebook ✅ {FB_RESULT['url']}")
                elif FB_RESULT.get("error"):
                    expired = "Session has expired" in FB_RESULT["error"] or 'code":190' in FB_RESULT["error"]
                    done.append(
                        "Facebook ❌ "
                        + (
                            "Page token expired — put a new one in FB_PAGE_TOKEN (.env, or the GitHub secret in cloud mode)"
                            if expired
                            else FB_RESULT["error"][:200]
                        )
                    )
            except Exception as e:
                log.error(f"instagram publish #{iid}: {e}")
                failed = str(e)
        elif it.get("ig_id"):
            done.append("Instagram ✅ (already)")
        # Facebook uploads the local file itself, so it does not need Instagram: when Instagram is off, failed, or was
        # posted in an earlier try without Facebook, the Reel still goes out here (never twice: fb_url is kept).
        if env("FB_PAGE_ID") and env("FB_PAGE_TOKEN") and not it.get("fb_url") and not fb_tried:
            from pipeline.publish import fb_publish_reel

            try:
                it["fb_url"] = fb_publish_reel(
                    None,
                    ep["ig_caption"],
                    cover=os.path.splitext(it["video"])[0] + "_cover.jpg",
                    first_comment=ep.get("pinned_comment"),
                    path=it["video"],
                )
                self.save()
                log.info(f"Facebook Reel: {it['fb_url']}")
                done.append(f"Facebook ✅ {it['fb_url']}")
            except Exception as e:
                log.warning(f"facebook publish #{iid}: {e}")
                expired = "Session has expired" in str(e) or 'code":190' in str(e)
                done.append(
                    "Facebook ❌ "
                    + (
                        "Page token expired — put a new one in FB_PAGE_TOKEN (.env, or the GitHub secret in cloud mode)"
                        if expired
                        else str(e)[:200]
                    )
                )
        if failed:
            self.set_status(iid, "publish_failed")
            self.safe_send(
                f"❌ #{iid} Instagram post failed.\n{failed[:1500]}\n\n{', '.join(done)}\n\nTap Retry, or copy the caption and post it from the app:\n\n{ep['ig_caption']}",
                [[("🔁 Retry Instagram", f"pubnow:{iid}"), ("✅ I posted it myself", f"igdone:{iid}")]],
            )
            return
        self.set_status(iid, "published", published=now_local().isoformat(timespec="seconds"))
        self.safe_send(
            f"🎉 #{iid} published — " + ", ".join(done) + "\nPin the comment + reply to first comments in 1 hour."
        )

    # ---------------- Telegram handling
    def handle(self, u):
        if not self.tg.is_me(u):
            return
        if "callback_query" in u:
            cq = u["callback_query"]
            action, iid = cq["data"].split(":", 1)
            self.tg.answer(cq["id"], "👍")
            self.tg.clear_buttons(cq["message"])
            key = f"{cq['message'].get('message_id')}:{cq['data']}"
            seen = self.state.setdefault("handled_cb", [])
            if key in seen:
                return log.info(f"duplicate tap ignored: {key}")  # double taps / Telegram retries
            seen.append(key)
            del seen[:-300]
            self.save()
            it = self.state["items"].get(iid)
            if not it:
                return self.safe_send(f"#{iid} not found")
            busy = {
                "approve": ("rendering", "writing"),
                "pubnow": ("queued", "publishing", "published"),
                "pubsched": ("scheduled", "publishing", "published"),
            }
            if it.get("status") in busy.get(action, ()):
                return self.safe_send(f"#{iid} is already '{it['status']}' 👍")
            if action == "approve":
                it.pop("auto", None)  # you stepped in: back to the normal flow with the video preview
                self.unschedule(iid)
                self.start_render(iid)
            elif action == "edit":
                self.set_status(iid, "awaiting_feedback")
                self.safe_send(f"✏️ #{iid}: what should change? Reply with your notes.")
            elif action == "skip":
                self.set_status(iid, "skipped")
                alts = it.get("alternates") or []
                if alts:
                    p = alts[0]
                    self.jobs.put(
                        (
                            self.draft,
                            (
                                p["topic"],
                                p.get("angle", ""),
                                p.get("hook", ""),
                                None,
                                None,
                                alts[1:],
                                p.get("category", ""),
                                p.get("format", ""),
                            ),
                        )
                    )
                else:
                    self.safe_send("No more topics left. Send /new <topic> or /daily.")
            elif action == "reject":
                self.unschedule(iid)
                self.set_status(iid, "rejected")
                self.safe_send(f"🗑 #{iid} rejected.")
            elif action == "igdone":
                self.set_status(iid, "published", published=now_local().isoformat(timespec="seconds"), ig_id="manual")
                self.safe_send(f"👍 #{iid} published (Instagram manual).")
            elif action == "pubnow":
                self.unschedule(iid)  # the YouTube copy booked for the publish_at slot would stay private till then
                self.set_status(iid, "queued")
                self.jobs.put((self._sched_then_publish, (iid, now_local())))
            elif action == "pubsched":
                pt = next_publish_time(it.get("slot"))
                self.jobs.put((self.schedule, (iid, pt)))
                self.safe_send(f"⏰ #{iid} scheduled for {pt:%d %b %H:%M}")
            return
        text = (u.get("message", {}).get("text") or "").strip()
        if not text:
            return
        if text.startswith("/new"):
            topic = text[4:].strip()
            if not topic:
                return self.safe_send("Use: /new why the sky is blue")
            self.jobs.put((self.draft, (topic, "", "")))
        elif text.startswith("/daily"):
            self.jobs.put((self.daily, ()))
        elif text.startswith("/render"):
            iid = text[7:].strip().lstrip("#")
            if iid in self.state["items"] and self.state["items"][iid].get("episode"):
                self.start_render(iid)
            else:
                self.safe_send("Use: /render <id>  (ids are listed by /status)")
        elif text.startswith("/status"):
            rows = [
                f"#{i}: {x.get('status')} — {x.get('topic', '')[:40]}"
                for i, x in list(self.state["items"].items())[-12:]
            ]
            self.safe_send("\n".join(rows) or "No items yet.")
        elif text.startswith("/stats"):
            self.stats()
        elif text.startswith("/help") or text.startswith("/start"):
            self.safe_send(
                "Commands:\n/new <topic> – write a script on this topic\n/daily – pick a trending topic now\n/status – last items\n/stats – views, likes, watch time\n/render <id> – render the video again\n"
                "Use the buttons to approve, request changes or publish."
            )
        else:
            waiting = [i for i, x in self.state["items"].items() if x.get("status") == "awaiting_feedback"]
            if waiting:
                iid = waiting[-1]
                it = self.state["items"][iid]
                self.jobs.put((self.draft, (it["topic"], it.get("angle", ""), it.get("hook", ""), text, iid)))
            else:
                self.safe_send("Didn't get that. See /help, or send /new <topic>.")

    def _sched_then_publish(self, iid, when):
        self.schedule(iid, when)
        self.publish_due()

    def recover(self, quiet=False):
        """After a restart: renders that were running are gone -> offer a one-tap re-render."""
        for iid, it in self.state["items"].items():
            if it.get("status") in (("rendering",) if quiet else ("rendering", "render_failed")):
                self.set_status(iid, "render_failed")
                self.safe_send(
                    f"⚠️ #{iid} render did not finish: {it.get('title', '')}",
                    [[("🔁 Render again", f"approve:{iid}"), ("❌ Reject", f"reject:{iid}")]],
                )
            elif it.get("status") in ("publishing", "queued"):
                self.set_status(iid, "publish_failed")
                self.safe_send(
                    f"⚠️ #{iid} publishing stopped halfway: {it.get('title', '')}",
                    [[("🔁 Retry publish", f"pubnow:{iid}"), ("✅ I posted it myself", f"igdone:{iid}")]],
                )
            elif it.get("status") in ("writing",):
                self.set_status(iid, "failed")
                self.safe_send(
                    f"⚠️ #{iid} the bot stopped while writing this script. Send /new {it.get('topic', '')} again."
                )

    # ---------------- main loop
    def serve(self):
        log.info("serve: started")
        self.safe_send(f"🤖 {NAME} · ShortPilot online. /help")
        self.recover()
        hh, mm = map(int, CFG["schedule"]["scout_at"].split(":"))
        while True:
            try:
                now = now_local()
                today = now.strftime("%Y-%m-%d")
                if self.state.get("last_daily") != today and (now.hour, now.minute) >= (hh, mm):
                    self.state["last_daily"] = today
                    self.save()
                    self.jobs.put((self.daily, (True,)))
                self.poll_renders()
                self.publish_due()
                self.maybe_refresh_ig()
                self.answer_keywords()
                for u in self.tg.updates(self.state.get("tg_offset"), timeout=20):
                    self.state["tg_offset"] = u["update_id"] + 1
                    self.save()
                    self.handle(u)
            except KeyboardInterrupt:
                log.info("serve: stopped")
                return
            except RuntimeError as e:
                if "Conflict" in str(e) or "webhook" in str(e).lower():
                    log.error(
                        "Telegram webhook is active (cloud mode). PC bot stopped. To use the PC again: autopilot.py telegram-webhook --delete"
                    )
                    return
                log.error(f"serve loop: {e}")
                time.sleep(10)
            except Exception as e:
                log.error(f"serve loop: {e}")
                time.sleep(10)

    def answer_keywords(self, every_min=10):
        """'Comment ESIM' -> send the promised extra to everyone who commented the keyword (last 7 days of videos)."""
        if (time.time() - self.state.get("kw_checked", 0)) < every_min * 60:
            return
        self.state["kw_checked"] = time.time()
        from pipeline import publish as P

        for iid, it in list(self.state["items"].items()):
            pub = it.get("published")
            if it.get("status") != "published" or not pub or not os.path.exists(it.get("episode", "")):
                continue
            if (now_local() - datetime.fromisoformat(pub)).days > 7:  # Instagram allows private replies for 7 days
                continue
            with open(it["episode"], encoding="utf-8") as fh:
                ep = json.load(fh)
            dm, ours = ep.get("dm") or {}, (ep.get("pinned_comment") or "").strip()
            kw, text = str(dm.get("keyword") or "").upper(), dm.get("text")
            if not kw or not text:
                continue
            done = set(it.get("answered", []))
            for plat, vid, find, answer in (
                ("ig", it.get("ig_id"), P.ig_keyword_comments, P.ig_answer),
                ("yt", it.get("yt_id"), P.yt_keyword_comments, P.yt_answer),
            ):
                if not vid:
                    continue
                try:
                    for c in find(vid, kw):
                        if c["id"] not in done and (c.get("text") or "").strip() != ours:  # skip our own pinned comment
                            how = answer(c["id"], text)
                            done.add(c["id"])
                            log.info(f"#{iid} {plat} '{kw}' -> {how}")
                except Exception as e:
                    log.warning(f"#{iid} {plat} keyword answers: {e}")
            it["answered"] = sorted(done)[-500:]
        self.save()

    def maybe_refresh_ig(self):
        if not env("IG_ACCESS_TOKEN"):
            return
        last = self.state.get("ig_refreshed") or env("IG_TOKEN_REFRESHED")
        if not last:
            self.state["ig_refreshed"] = now_local().strftime("%Y-%m-%d")
            self.save()
            return
        if (now_local().date() - datetime.strptime(last, "%Y-%m-%d").date()).days < 30:
            return
        try:
            from pipeline.publish import ig_refresh_token

            ig_refresh_token()
            log.info("Instagram token refreshed")
            if CLOUD:
                from pipeline import cloud

                if not cloud.set_secret("IG_ACCESS_TOKEN", os.environ["IG_ACCESS_TOKEN"]):
                    self.safe_send(
                        "⚠️ Instagram token refreshed, but the GitHub secret was not updated (GH_SECRETS_TOKEN missing). It expires in 60 days."
                    )
                    return
            self.state["ig_refreshed"] = now_local().strftime("%Y-%m-%d")
            self.save()
        except Exception as e:
            log.warning(f"IG token refresh: {e}")


# ============================================================================ CLI
def cmd_check(_):
    ok = lambda b: "✅" if b else "❌"
    rows = [
        ("Python", sys.version.split()[0], sys.version_info >= (3, 10)),
        (
            "ffmpeg",
            shutil.which(os.environ.get("FFMPEG", "ffmpeg")) or "not on PATH",
            bool(shutil.which(os.environ.get("FFMPEG", "ffmpeg"))),
        ),
        ("Kokoro model", MODELS, os.path.exists(os.path.join(MODELS, "kokoro-v1.0.onnx"))),
    ]
    claude = env("CLAUDE_BIN") or shutil.which("claude") or shutil.which("claude.cmd")
    rows.append(("Claude Code CLI", claude or "not found", bool(claude)))
    mods = ["numpy", "PIL", "kokoro_onnx", "soundfile", "requests"]
    for mod in mods:
        try:
            __import__(mod)
            rows.append((f"python: {mod}", "ok", True))
        except ImportError:
            rows.append((f"python: {mod}", "missing", False))
    for k, need in (
        ("TELEGRAM_BOT_TOKEN", "approvals"),
        ("TELEGRAM_CHAT_ID", "approvals"),
        ("GEMINI_API_KEY", "only for gemini"),
        ("YOUTUBE_API_KEY", "YouTube trending (optional)"),
        ("AZURE_SPEECH_KEY", "Azure voices (optional)"),
        ("SARVAM_API_KEY", "Sarvam voices (optional)"),
        ("IG_USER_ID", "Instagram auto-publish"),
        ("IG_ACCESS_TOKEN", "Instagram auto-publish"),
        ("YOUTUBE_CLIENT_SECRET_FILE", "YouTube auto-upload login (youtube-auth)"),
    ):
        rows.append((k, need, bool(env(k))))
    rows.append(("YouTube token", "data/youtube_token.json", os.path.exists(os.path.join(DATA, "youtube_token.json"))))
    from pipeline.publish import host_order

    rows.append(("Instagram video hosts", " -> ".join(host_order()) + "  (test: autopilot.py host-test)", True))
    for name, val, good in rows:
        print(f"{ok(good)} {name:32} {val}")
    print(
        f"\nLLM: writer={CFG['llm']['writer']} ranker={CFG['llm']['ranker']} researcher={CFG['llm'].get('researcher') or '(writer)'}"
    )


def cmd_trends(_):
    from pipeline import trends

    for c in trends.collect(CFG):
        print(f"[{c['source']}] {c['title']}")


def cmd_pick(a):
    from pipeline import state, trends
    from pipeline.writer import rank_topics

    s = state.load()
    recent = [i["category"] for i in s.get("items", {}).values() if i.get("category")][-6:]
    picks = rank_topics(CFG, trends.collect(CFG), s.get("history", []), n=a.n, recent_categories=recent)
    print(json.dumps(picks, ensure_ascii=False, indent=1))


def cmd_write(a):
    from pipeline.writer import save_episode, write_episode

    ep, warns = write_episode(CFG, a.topic, a.angle or "", a.hook or "", provider=a.provider)
    path = save_episode(ep, name=a.name)
    print(f"saved: {path}  (~{ep['_estimate']['seconds']}s)")
    [print("⚠️", w) for w in warns]
    return path


def cmd_make(a):
    path = cmd_write(a)
    print("video:", render_now(path, voice=a.voice))


def cmd_compare(a):
    from pipeline.writer import save_episode, write_episode

    res = {}
    for prov in ("claude_cli", "gemini"):
        try:
            t0 = time.time()
            ep, warns = write_episode(CFG, a.topic, a.angle or "", "", provider=prov)
            path = save_episode(ep, name=f"compare_{slug(a.topic)}_{prov}")
            res[prov] = {
                "path": path,
                "secs": round(time.time() - t0),
                "est_len": ep["_estimate"]["seconds"],
                "warnings": warns,
                "sources": len(ep.get("sources", [])),
                "visuals": [s["visual"]["type"] for s in ep["scenes"]],
                "title": ep["title"],
            }
            if a.render:
                res[prov]["video"] = render_now(path)
        except Exception as e:
            res[prov] = {"error": str(e)}
    print(json.dumps(res, ensure_ascii=False, indent=1))


def cmd_render(a):
    print(render_now(a.episode, a.out, a.voice))


def cmd_demo(a):
    """Render engine/demo_episode.json with the active channel's look: proves the install works, no keys needed."""
    ep = os.path.join(ROOT, "engine", "demo_episode.json")
    out = render_now(ep, os.path.join(OUT, "demo.mp4"), a.voice)
    print(f"\n🎬 Demo video: {out}\nNext: python autopilot.py new-channel <name>  (docs/QUICKSTART.md)")


def cmd_serve(_):
    Autopilot().serve()


def cmd_publish(a):
    ap = Autopilot()
    if a.id not in ap.state["items"]:
        sys.exit(f"#{a.id} not found. Ids: " + ", ".join(list(ap.state["items"])[-10:]))
    if a.now:
        ap.schedule(a.id, now_local())
        ap.set_status(a.id, "publishing")
        ap.publish(a.id)  # runs right here, waits till done
    else:
        t = next_publish_time(ap.state["items"][a.id].get("slot"))
        ap.schedule(a.id, t)
        print(f"scheduled for {t:%d %b %H:%M} (the running bot will post it)")


def cmd_host_test(a):
    """Checks each free video host from this PC (nothing is posted to Instagram)."""
    from pipeline.publish import host_order, public_hosts

    path = a.file
    if not path:
        vids = sorted((os.path.join(OUT, f) for f in os.listdir(OUT) if f.endswith(".mp4")), key=os.path.getmtime)
        if not vids:
            sys.exit("no video in data/videos — pass a file path")
        path = vids[-1]
    print(f"file: {path}\norder: {', '.join(host_order())}\n")
    ok = 0
    import pipeline.publish as P

    saved = P.host_order
    for h in saved():
        P.host_order = lambda h=h: [h]
        for host, url, extra in public_hosts(path):
            if url:
                print(f"✅ {host:10} {url[:90]}")
                extra()
                ok += 1
            else:
                print(f"❌ {host:10} {extra}")
    P.host_order = saved
    print(
        "\n"
        + (
            "OK — Instagram publish will work via the first ✅ host."
            if ok
            else "No host worked — open an issue with this output, or ask Claude Code to debug it."
        )
    )


def cmd_cloud(a):
    """Entry point for GitHub Actions (.github/workflows/bot.yml). One run = one event."""
    ap = Autopilot()
    ap.recover(quiet=True)
    name = os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch")
    ev = {}
    if os.environ.get("GITHUB_EVENT_PATH") and os.path.exists(os.environ["GITHUB_EVENT_PATH"]):
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
            ev = json.load(fh)
    payload = ev.get("client_payload") or {}
    task = a.task or ((ev.get("inputs") or {}).get("task") if name == "workflow_dispatch" else payload.get("task"))
    if name == "repository_dispatch" and "update" in payload:
        u = payload["update"]
        log.info(f"cloud: telegram update {u.get('update_id')}")
        ap.handle(u)
    elif task:
        today = now_local().strftime("%Y-%m-%d")
        if task == "daily":
            if ap.state.get("last_daily") == today and name != "workflow_dispatch":
                log.info("cloud: daily already done today")
            else:
                ap.state["last_daily"] = today
                ap.save()
                ap.jobs.put((ap.daily, (name == "repository_dispatch",)))  # cron = auto; manual run = approve flow
        elif task == "status":
            rows = [
                f"#{i}: {x.get('status')} — {x.get('topic', '')[:40]}" for i, x in list(ap.state["items"].items())[-12:]
            ]
            ap.safe_send("☁️ Cloud status\n" + ("\n".join(rows) or "No items yet."))
        ap.maybe_refresh_ig()
        try:
            from pipeline import cloud

            cloud.cleanup_releases()
        except Exception as e:
            log.warning(f"cleanup: {e}")
    ap.drain()
    ap.publish_due(early_min=25)
    ap.drain()
    ap.answer_keywords(every_min=0)
    ap.save()
    log.info("cloud: done")


def cmd_telegram_webhook(a):
    from pipeline import cloud

    if a.delete:
        print(cloud.delete_webhook())
        print("PC mode: you can start the local bot again (autopilot.py serve).")
        return
    if a.info or not a.url:
        print(json.dumps(cloud.webhook_info(), indent=1))
        return
    import secrets

    sec = a.secret or secrets.token_urlsafe(24)
    print(cloud.set_webhook(a.url, sec))
    print(
        f"\nWebhook set. Cloudflare Worker variable TG_SECRET = {sec}\n(Keep the PC bot stopped now - the two cannot run at the same time.)"
    )


def cmd_telegram_setup(_):
    from pipeline.util import http

    tok = env("TELEGRAM_BOT_TOKEN", required=True)
    ups = http("GET", f"https://api.telegram.org/bot{tok}/getUpdates").json().get("result", [])
    ids = {str((u.get("message") or {}).get("chat", {}).get("id")) for u in ups if u.get("message")}
    if not ids:
        print("First send your bot any message in Telegram (e.g. 'hi'), then run this command again.")
        return
    print("Your chat id(s):", ", ".join(ids), f"→ put TELEGRAM_CHAT_ID=<id> in channels/{CHANNEL}/.env")


def cmd_youtube_auth(_):
    from pipeline.publish import youtube_auth

    print("saved:", youtube_auth())
    print("Now test it:  python autopilot.py youtube-test")


def cmd_youtube_test(a):
    """Uploads a test video as UNLISTED, checks YouTube kept it unlisted (= not locked to private), then deletes it.
    On success the login token is copied to GitHub secrets so the cloud bot uploads too."""
    from pipeline.publish import YT_OK, YT_TOKEN, youtube_delete, youtube_status, youtube_upload

    path = a.file
    if not path:
        vids = sorted((os.path.join(OUT, f) for f in os.listdir(OUT) if f.endswith(".mp4")), key=os.path.getmtime)
        if not vids:
            sys.exit("no .mp4 in data/videos — pass a file: autopilot.py youtube-test <file.mp4>")
        path = vids[-1]
    print(f"uploading test (unlisted): {os.path.basename(path)} …")
    vid = youtube_upload(
        path, f"{NAME} API test - delete", "API upload test", ["test"], privacy="unlisted", synthetic=ai_label()
    )
    time.sleep(5)
    st = youtube_status(vid)
    print("YouTube status:", json.dumps(st))
    ok = st.get("privacyStatus") == "unlisted"
    try:
        youtube_delete(vid)
        print("test video deleted")
    except Exception as e:
        print(f"(could not delete the test video: {e} — delete it in YouTube Studio: https://youtu.be/{vid})")
    if not ok:
        print(
            "\n❌ YouTube locked the video to PRIVATE — your Google Cloud project needs an API audit first. youtube_mode stays manual."
        )
        return
    open(YT_OK, "w").write(now_local().isoformat())
    print("\n✅ YouTube API uploads can go PUBLIC — auto-upload is ON (channel.json publish.youtube_mode = auto).")
    try:
        from pipeline.cloud_setup import push_secret

        if push_secret("YOUTUBE_TOKEN_JSON", open(YT_TOKEN, encoding="utf-8").read()):
            print("✅ token also saved to GitHub secrets — the cloud bot uploads to YouTube too.")
    except Exception as e:
        print(f"(GitHub secret not set: {e}) — later: gh secret set YOUTUBE_TOKEN_JSON < data/youtube_token.json")


def cmd_ig_refresh(_):
    from pipeline.publish import ig_refresh_token

    print("new token valid for (s):", ig_refresh_token())


def cmd_ig_whoami(_):
    from pipeline.publish import ig_whoami

    j = ig_whoami()
    print(f"OK: @{j.get('username')} ({j.get('account_type')}) -> IG_USER_ID saved in .env")


def cmd_ig_limit(_):
    from pipeline.publish import ig_publish_limit

    print(json.dumps(ig_publish_limit(), indent=1))


def main():
    ap = argparse.ArgumentParser(prog="autopilot.py", description=f"ShortPilot — channel: {NAME} ({CHANNEL})")
    ap.add_argument("--channel", help="channel folder in channels/ (default: .channel file, or the only one)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("demo", help="render the sample video (no keys needed)")
    p.add_argument("--voice")
    p.set_defaults(f=cmd_demo)
    sp.add_parser("new-channel", help="copy channels/_template to channels/<name>")  # handled before the channel loads
    sp.add_parser("check", help="what is installed and which keys are missing").set_defaults(f=cmd_check)
    sp.add_parser("trends").set_defaults(f=cmd_trends)
    p = sp.add_parser("pick")
    p.add_argument("-n", type=int, default=3)
    p.set_defaults(f=cmd_pick)
    for name, fn in (("write", cmd_write), ("make", cmd_make)):
        p = sp.add_parser(name)
        p.add_argument("--topic", required=True)
        p.add_argument("--angle")
        p.add_argument("--hook")
        p.add_argument("--provider", choices=["claude_cli", "gemini", "anthropic"])
        p.add_argument("--name")
        p.add_argument("--voice")
        p.set_defaults(f=fn)
    p = sp.add_parser("compare")
    p.add_argument("--topic", required=True)
    p.add_argument("--angle")
    p.add_argument("--render", action="store_true")
    p.set_defaults(f=cmd_compare)
    p = sp.add_parser("render")
    p.add_argument("episode")
    p.add_argument("--out")
    p.add_argument("--voice")
    p.set_defaults(f=cmd_render)
    sp.add_parser("serve").set_defaults(f=cmd_serve)
    p = sp.add_parser("publish")
    p.add_argument("id")
    p.add_argument("--now", action="store_true")
    p.set_defaults(f=cmd_publish)
    sp.add_parser("telegram-setup").set_defaults(f=cmd_telegram_setup)
    sp.add_parser("youtube-auth").set_defaults(f=cmd_youtube_auth)
    p = sp.add_parser("youtube-test")
    p.add_argument("file", nargs="?")
    p.set_defaults(f=cmd_youtube_test)
    sp.add_parser("ig-refresh").set_defaults(f=cmd_ig_refresh)
    sp.add_parser("ig-limit").set_defaults(f=cmd_ig_limit)
    sp.add_parser("ig-whoami").set_defaults(f=cmd_ig_whoami)
    p = sp.add_parser("host-test")
    p.add_argument("file", nargs="?")
    p.set_defaults(f=cmd_host_test)
    p = sp.add_parser("cloud")
    p.add_argument("--task", choices=["daily", "publish", "status"])
    p.set_defaults(f=cmd_cloud)
    sp.add_parser("cloud-setup").set_defaults(f=lambda a: __import__("pipeline.cloud_setup", fromlist=["main"]).main())
    sp.add_parser("worker-deploy").set_defaults(
        f=lambda a: __import__("pipeline.cloud_setup", fromlist=["redeploy_worker"]).redeploy_worker()
    )
    p = sp.add_parser("telegram-webhook")
    p.add_argument("--url")
    p.add_argument("--secret")
    p.add_argument("--delete", action="store_true")
    p.add_argument("--info", action="store_true")
    p.set_defaults(f=cmd_telegram_webhook)
    argv = sys.argv[1:]
    for i, x in enumerate(argv):  # --channel may sit anywhere on the line; pipeline/channel.py has already read it
        if x == "--channel":
            del argv[i : i + 2]
            break
        if x.startswith("--channel="):
            del argv[i]
            break
    a = ap.parse_args(argv)
    a.f(a)


if __name__ == "__main__":
    main()
