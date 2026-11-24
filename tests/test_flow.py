"""End-to-end flow test with fakes: fake `claude` CLI, fake Telegram, fake Instagram/Blob. Real renderer."""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))  # tests/
ROOT = os.path.dirname(HERE)
SLUG = "_template"
PACK = os.path.join(ROOT, "channels", SLUG)
os.environ["AUTOPILOT_CHANNEL"] = SLUG  # the engine is tested with the channel template
VIDEOS = os.environ.get("AUTOPILOT_TEST_VIDEOS") or os.path.join(PACK, "data", "videos")  # any rendered .mp4
sys.path.insert(0, ROOT)
os.environ["AUTOPILOT_ENV_FILE"] = os.path.join(HERE, "no.env")  # never load the real .env
TMP = tempfile.mkdtemp()
os.environ.update(
    FB_PAGE_ID="",  # never post to the real Facebook Page from tests
    FB_PAGE_TOKEN="",
    # never touch the real data/ folder: it holds the real YouTube login and bot state
    AUTOPILOT_DATA=os.path.join(TMP, "data"),
    AUTOPILOT_EPISODES=os.path.join(TMP, "episodes"),
    YOUTUBE_TOKEN_JSON="",
    MEDIA="off",
    CLAUDE_BIN=os.path.join(HERE, "fake_claude.py"),
    TELEGRAM_BOT_TOKEN="x",
    TELEGRAM_CHAT_ID="42",
    IG_USER_ID="1",
    IG_ACCESS_TOKEN="t",
    SARVAM_API_KEY="",  # a paid key must never be used by tests
)
import pipeline.validate as V

assert __import__("pipeline.util").util.DATA.startswith(TMP), "tests must not use the real data/ folder"

V.DEFAULT_CPS = 6  # the sample episode is short; relax the length check for the test
import pipeline.trends as T

T.collect = lambda cfg: [{"source": "google_news", "title": "Why phones die faster in the cold"}]
import pipeline.publish as P

calls = []
P.ig_publish_with_fallback = lambda path, cap, first_comment=None: (
    calls.append(("ig", os.path.basename(path), cap[:20], first_comment)),
    ("IG123", "tunnel"),
)[1]
import pipeline.telegram as TG


class FakeTG:
    def __init__(self):
        self.sent = []
        self.q = []

    def send(self, text, buttons=None, html=False):
        self.sent.append(("msg", text, buttons))
        return {}

    def send_video(self, path, caption="", buttons=None, cover=None):
        self.sent.append(("video", path, buttons, cover))
        assert os.path.exists(path)

    def send_document(self, path, caption=""):
        self.sent.append(("doc", path))

    def answer(self, *a):
        pass

    def clear_buttons(self, m):
        pass

    def updates(self, offset=None, timeout=0):
        q, self.q = self.q, []
        return q

    def is_me(self, u):
        return True


TG.Telegram = FakeTG
import autopilot as A

A.CFG["voice"]["tts"] = "kokoro"  # local voice: the test must not spend paid TTS credits

ap = A.Autopilot()
tg = ap.tg


def cb(data):
    return {
        "update_id": 1,
        "callback_query": {"id": "c", "data": data, "message": {"chat": {"id": 42}, "message_id": 1}},
    }


def wait(cond, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        ap.poll_renders()
        ap.publish_due()
        if cond():
            return True
        time.sleep(1)
    return False


ap.daily()
iid = [i for i, x in ap.state["items"].items() if x["status"] == "awaiting_script"][-1]
it = ap.state["items"][iid]
print("1) drafted", iid, it["title"], "| buttons:", [b[0][1] for b in tg.sent[-1][2]])
assert "Phone battery" in it["topic"] and os.path.exists(it["episode"])
preview = [m for m in tg.sent if m[0] == "msg" and "SCRIPT" in m[1]][-1][1]
print(preview[:300].replace("\n", " | "))

ap.handle(cb(f"edit:{iid}"))
assert ap.state["items"][iid]["status"] == "awaiting_feedback"
ap.handle({"update_id": 2, "message": {"chat": {"id": 42}, "text": "make the hook more dramatic"}})
assert wait(lambda: ap.state["items"][iid]["status"] == "awaiting_script", 60)
print("2) feedback redraft ok")

ap.handle(cb(f"approve:{iid}"))
print("3) rendering…")
assert wait(lambda: ap.state["items"][iid]["status"] == "awaiting_publish", 900), ap.state["items"][iid]
v = [m for m in tg.sent if m[0] == "video"][-1]
print(
    "   video sent:",
    os.path.basename(v[1]),
    "cover:",
    os.path.exists(v[3]),
    "buttons:",
    [b[1] for r in v[2] for b in r],
)

ap.handle(cb(f"pubnow:{iid}"))
assert wait(lambda: ap.state["items"][iid]["status"] == "published", 60), ap.state["items"][iid]
print(
    "4) published:",
    [c[0] for c in calls],
    "| manual YT msg:",
    any("upload this to YouTube Shorts" in m[1] for m in tg.sent if m[0] == "msg"),
    "| doc:",
    any(m[0] == "doc" for m in tg.sent),
)

fake = {
    "yt": {"views": 7, "likes": 1, "comments": 1, "length": 46},
    "ig": {"views": 126, "likes": 2, "saved": 3, "watch": 5},
}
P.platform_stats = lambda items: [{k: dict(v) for k, v in fake.items()} for _ in items]
ap.handle({"update_id": 3, "message": {"chat": {"id": 42}, "text": "/stats"}})
s1 = tg.sent[-1][1]
assert "👁 <b>133</b> views" in s1 and "▶️ 7 · 📸 126 · 📘 —" in s1, s1
assert "❤️ 3 likes · 🔖 3 saves" in s1 and "⏱ Watched ~5s on average (of 46s · 11%)" in s1, s1
assert "(+" not in s1, s1
fake["ig"]["views"] = 140
ap.handle({"update_id": 4, "message": {"chat": {"id": 42}, "text": "/stats"}})
s2 = tg.sent[-1][1]
assert "👁 <b>147</b> views  (+14)" in s2 and "(+14 since last check)" in s2, s2
print("5) /stats ok")
print("ALL OK")
