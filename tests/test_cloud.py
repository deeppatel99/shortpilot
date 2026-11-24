"""Cloud-mode flow test: every GitHub Actions run is simulated as a fresh Autopilot with state on disk.
Fakes: claude CLI, Telegram, gh releases, Instagram, renderer (copies a sample mp4)."""

import glob
import json
import os
import subprocess
import sys
import tempfile
from datetime import timedelta
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))  # tests/
ROOT = os.path.dirname(HERE)
SLUG = "_template"
PACK = os.path.join(ROOT, "channels", SLUG)
os.environ["AUTOPILOT_CHANNEL"] = SLUG  # the engine is tested with the channel template
VIDEOS = os.environ.get("AUTOPILOT_TEST_VIDEOS") or os.path.join(PACK, "data", "videos")  # any rendered .mp4
sys.path.insert(0, ROOT)
os.environ["AUTOPILOT_ENV_FILE"] = os.path.join(HERE, "no.env")  # never load the real .env
tmp = tempfile.mkdtemp()
os.environ.update(
    FB_PAGE_ID="",  # never post to the real Facebook Page from tests
    FB_PAGE_TOKEN="",
    GITHUB_ACTIONS="true",
    AUTOPILOT_DATA=os.path.join(tmp, "state", "data"),
    AUTOPILOT_EPISODES=os.path.join(tmp, "state", "episodes"),
    CLAUDE_BIN=os.path.join(HERE, "fake_claude.py"),
    TELEGRAM_BOT_TOKEN="x",
    TELEGRAM_CHAT_ID="42",
    IG_USER_ID="1",
    IG_ACCESS_TOKEN="t",
    GITHUB_REPOSITORY="me/mychannel-bot",
)
import pipeline.validate as V

V.DEFAULT_CPS = 6  # the sample episode is short; relax the length check for the test
import pipeline.trends as T

T.collect = lambda cfg: [{"source": "google_news", "title": "Why phones die faster in the cold"}]
import pipeline.publish as P

ig_calls = []
P.ig_publish_with_fallback = lambda path, cap, first_comment=None: (ig_calls.append(path), ("IG55", "tunnel"))[1]
import pipeline.cloud as C

releases = {}


def store(iid, *paths):
    releases[iid] = {os.path.basename(p): open(p, "rb").read() for p in paths if os.path.exists(p)}


def fetch(iid, d):
    os.makedirs(d, exist_ok=True)
    for n, b in releases[iid].items():
        open(os.path.join(d, n), "wb").write(b)


C.store_video, C.fetch_video, C.cleanup_releases = store, fetch, (lambda days=21: None)
import pipeline.telegram as TG

SENT = []


class FakeTG:
    def __init__(self):
        self.sent = SENT

    def send(self, text, buttons=None, html=False):
        self.sent.append(("msg", text, buttons))
        return {}

    def send_video(self, path, caption="", buttons=None, cover=None):
        self.sent.append(("video", path, buttons))

    def send_document(self, path, caption=""):
        self.sent.append(("doc", path))

    def answer(self, *a):
        pass

    def clear_buttons(self, m):
        pass

    def is_me(self, u):
        return (
            str(
                ((u.get("message") or (u.get("callback_query") or {}).get("message") or {}).get("chat") or {}).get("id")
            )
            == "42"
        )


TG.Telegram = FakeTG
import autopilot as A

A.CFG["voice"]["tts"] = "kokoro"  # local voice: the test must not spend paid TTS credits

SAMPLE = sorted(glob.glob(os.path.join(VIDEOS, "*.mp4")))[0]
A.render_cmd = lambda ep, out, voice=None: [
    sys.executable,
    "-c",
    f'import shutil,sys; shutil.copy({SAMPLE!r}, {out!r}); open({out!r}[:-4] + \'_credits.json\',\'w\').write(\'[{{"by": "Jane Doe", "site": "Pexels", "url": "u"}}]\')',
]


def run(event_name, payload):
    p = os.path.join(tmp, "event.json")
    json.dump(payload, open(p, "w"))
    os.environ.update(GITHUB_EVENT_NAME=event_name, GITHUB_EVENT_PATH=p)
    A.cmd_cloud(SimpleNamespace(task=None))
    from pipeline import state

    return state.load()


def cb(data, mid=7):
    return {
        "update": {
            "update_id": 1,
            "callback_query": {"id": "c", "data": data, "message": {"chat": {"id": 42}, "message_id": mid}},
        }
    }


def classify(event_name, payload):
    p = os.path.join(tmp, "ev2.json")
    json.dump(payload, open(p, "w"))
    out = subprocess.run(
        [sys.executable, os.path.join(ROOT, "cloud", "classify.py")],
        capture_output=True,
        text=True,
        env={**os.environ, "GITHUB_EVENT_NAME": event_name, "GITHUB_EVENT_PATH": p},
    ).stdout
    return dict(l.split("=") for l in out.split())


# 1) daily (manual trigger) -> script drafted
st = run("workflow_dispatch", {"inputs": {"task": "daily"}})
iid = [i for i, x in st["items"].items() if x["status"] == "awaiting_script"][-1]
assert os.path.exists(st["items"][iid]["episode"]) and st["items"][iid]["episode"].startswith(
    os.environ["AUTOPILOT_EPISODES"]
)
print("1) daily ->", iid, st["items"][iid]["status"])
# 2) approve -> render -> stored as release -> video sent -> books itself for the publish_at slot
st = run("repository_dispatch", {"client_payload": cb(f"approve:{iid}", mid=10)})
it = st["items"][iid]
assert it["status"] == "scheduled" and it["publish_time"] == A.next_publish_time().isoformat(), it
assert iid in releases and any(n.endswith(".mp4") for n in releases[iid])
assert any(m[0] == "video" for m in SENT)
print("2) approve -> rendered + stored:", sorted(releases[iid]))
# 3) duplicate tap on the same message is ignored (no second render)
n_before = len(SENT)
st = run("repository_dispatch", {"client_payload": cb(f"approve:{iid}", mid=10)})
assert st["items"][iid]["status"] == "scheduled" and not any(
    "rendering" in m[1] for m in SENT[n_before:] if m[0] == "msg"
)
print("3) duplicate tap ignored")
print("4) auto-scheduled for", st["items"][iid]["publish_time"])
# 5) the publish run on a fresh machine (video file gone) publishes when within 25 min of the slot
from pipeline import state as S

s = S.load()
s["items"][iid]["publish_time"] = (A.now_local() + timedelta(minutes=12)).isoformat()
S.save(s)
os.remove(s["items"][iid]["video"])
st = run("workflow_dispatch", {"inputs": {"task": "publish"}})
it = st["items"][iid]
assert it["status"] == "published" and it["ig_id"] == "IG55", it
yt = [m[1] for m in SENT if m[0] == "msg" and "upload this to YouTube Shorts" in m[1]][-1]
assert "Pexels" in yt and "Jane Doe" in yt, yt
print("5) published from a fresh runner; YouTube text has Pexels credit")
# 6) /status, messages from strangers ignored
n_before = len(SENT)
run(
    "repository_dispatch",
    {"client_payload": {"update": {"update_id": 9, "message": {"chat": {"id": 999}, "text": "/new hack"}}}},
)
assert len(SENT) == n_before
print("6) stranger ignored")
# 7) classify
assert classify("repository_dispatch", {"client_payload": cb("approve:x")}) == {"llm": "false", "render": "true"}
assert classify("repository_dispatch", {"client_payload": cb("pubsched:x")}) == {"llm": "false", "render": "false"}
assert classify("repository_dispatch", {"client_payload": {"update": {"message": {"text": "/new phone battery"}}}}) == {
    "llm": "true",
    "render": "false",
}
assert (
    classify("repository_dispatch", {"client_payload": {"update": {"message": {"text": "make the hook stronger"}}}})[
        "llm"
    ]
    == "true"
)
assert classify("workflow_dispatch", {"inputs": {"task": "status"}}) == {"llm": "false", "render": "false"}
print("7) classify OK")
# 8) YouTube API mode (token from secret): upload at scheduling with publishAt, comment at publish time
os.environ["YOUTUBE_TOKEN_JSON"] = "{}"
yt = []
P.youtube_upload = lambda path, title, desc, tags=None, publish_at=None, privacy=None, synthetic=True: (
    yt.append(("up", publish_at, desc)),
    "YT9",
)[1]
P.youtube_comment = lambda vid, text: yt.append(("comment", vid))
st = run("workflow_dispatch", {"inputs": {"task": "daily"}})
iid2 = [i for i, x in st["items"].items() if x["status"] == "awaiting_script"][-1]
st = run("repository_dispatch", {"client_payload": cb(f"approve:{iid2}", mid=20)})
it = st["items"][iid2]
assert it["yt_id"] == "YT9" and yt[0][0] == "up" and yt[0][1] is not None and "Pexels" in yt[0][2], (it, yt)
s = S.load()
s["items"][iid2]["publish_time"] = (A.now_local() + timedelta(minutes=5)).isoformat()
S.save(s)
n_manual = sum("upload this to YouTube Shorts" in m[1] for m in SENT if m[0] == "msg")
st = run("workflow_dispatch", {"inputs": {"task": "publish"}})
it = st["items"][iid2]
assert it["status"] == "published" and it.get("yt_commented") and ("comment", "YT9") in yt, it
assert sum("upload this to YouTube Shorts" in m[1] for m in SENT if m[0] == "msg") == n_manual, (
    "no manual YouTube message in api mode"
)
# upload failure -> manual fallback message at publish time
P.youtube_upload = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota"))
st = run("workflow_dispatch", {"inputs": {"task": "daily"}})
iid3 = [i for i, x in st["items"].items() if x["status"] == "awaiting_script"][-1]
run("repository_dispatch", {"client_payload": cb(f"approve:{iid3}", mid=30)})
s = S.load()
s["items"][iid3]["publish_time"] = (A.now_local() + timedelta(minutes=5)).isoformat()
S.save(s)
st = run("workflow_dispatch", {"inputs": {"task": "publish"}})
assert (
    st["items"][iid3]["status"] == "published"
    and sum("upload this to YouTube Shorts" in m[1] for m in SENT if m[0] == "msg") == n_manual + 1
)
print("8) YouTube API mode: scheduled upload, comment at publish, manual fallback OK")
# 9) Reject after the auto-schedule removes the YouTube copy that was already booked to go public
P.youtube_upload = lambda *a, **k: "YT10"
P.youtube_delete = lambda vid: yt.append(("del", vid))
st = run("workflow_dispatch", {"inputs": {"task": "daily"}})
iid4 = [i for i, x in st["items"].items() if x["status"] == "awaiting_script"][-1]
st = run("repository_dispatch", {"client_payload": cb(f"approve:{iid4}", mid=40)})
assert st["items"][iid4]["yt_id"] == "YT10"
st = run("repository_dispatch", {"client_payload": cb(f"reject:{iid4}", mid=41)})
assert st["items"][iid4]["status"] == "rejected" and "yt_id" not in st["items"][iid4] and ("del", "YT10") in yt
print("9) reject deletes the scheduled YouTube video")
# 10) "Publish now" on an auto-scheduled video: the booked YouTube copy is replaced by a public upload now
up = []
P.youtube_upload = lambda path, title, desc, tags=None, publish_at=None, privacy=None, synthetic=True: (
    up.append(publish_at),
    "YT11",
)[1]
st = run("workflow_dispatch", {"inputs": {"task": "daily"}})
iid5 = [i for i, x in st["items"].items() if x["status"] == "awaiting_script"][-1]
run("repository_dispatch", {"client_payload": cb(f"approve:{iid5}", mid=50)})
st = run("repository_dispatch", {"client_payload": cb(f"pubnow:{iid5}", mid=51)})
it = st["items"][iid5]
assert it["status"] == "published" and ("del", "YT11") in yt and up == [up[0], None], (it, up)
print("10) Publish now on a scheduled video re-uploads YouTube public now")
# 11) morning cron: script with only a Reject button, rendered and booked for publish_at without any tap
# quiet auto mode: the scheduled run renders and books without a tap
A.CFG["publish"].update(auto="cron", auto_preview=False)
assert classify("repository_dispatch", {"client_payload": {"task": "daily"}})["llm"] == "true"
P.youtube_upload = lambda *a, **k: "YT12"
s = S.load()
s.pop("last_daily", None)
S.save(s)
n_before = len(SENT)
st = run("repository_dispatch", {"client_payload": {"task": "daily"}})
iid6 = list(st["items"])[-1]
it = st["items"][iid6]
assert it["status"] == "scheduled" and it["yt_id"] == "YT12" and it["publish_time"] == A.next_publish_time().isoformat()
script = [m for m in SENT[n_before:] if m[0] == "msg" and "SCRIPT" in m[1]][-1]
assert script[2] == [[("❌ Reject", f"reject:{iid6}")]] and "Publishes itself" in script[1], script
assert not any(m[0] == "video" for m in SENT[n_before:])
st = run("repository_dispatch", {"client_payload": cb(f"reject:{iid6}", mid=60)})
assert st["items"][iid6]["status"] == "rejected" and ("del", "YT12") in yt
print("11) morning cron: Reject-only script, auto render + booking, Reject removes it")
print("ALL OK")
