"""Publish path tests: local file server + Range, host fallback chain, tunnel start parsing, bot retry flow."""

import glob
import json
import os
import stat
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
    TELEGRAM_BOT_TOKEN="x",
    TELEGRAM_CHAT_ID="42",
    IG_USER_ID="1",
    IG_ACCESS_TOKEN="t",
    PUBLIC_HOST="auto",
)
os.environ.pop("AZURE_BLOB_CONTAINER_SAS_URL", None)
os.environ.update(FB_PAGE_ID="", FB_PAGE_TOKEN="")  # never post to the real Facebook Page from tests
import requests

import pipeline.publish as P

assert __import__("pipeline.util").util.DATA.startswith(TMP), "tests must not use the real data/ folder"

vids = glob.glob(os.path.join(VIDEOS, "*.mp4")) + glob.glob(os.path.join(VIDEOS, "*.mp4"))
assert vids, "need one rendered mp4 for the test"
VID = vids[0]
SIZE = os.path.getsize(VID)

# 1) local server: full GET, Range, suffix range, HEAD, wrong path, verify_url
srv, path = P._file_server(VID)
base = f"http://127.0.0.1:{srv.server_address[1]}"
r = requests.get(base + path)
assert r.status_code == 200 and len(r.content) == SIZE and r.headers["Content-Type"] == "video/mp4"
r = requests.get(base + path, headers={"Range": "bytes=0-99"})
assert r.status_code == 206 and len(r.content) == 100 and r.headers["Content-Range"] == f"bytes 0-99/{SIZE}"
r = requests.get(base + path, headers={"Range": "bytes=-50"})
assert r.status_code == 206 and len(r.content) == 50
r = requests.get(base + path, headers={"Range": f"bytes={SIZE - 10}-"})
assert r.status_code == 206 and len(r.content) == 10
r = requests.head(base + path)
assert r.status_code == 200 and int(r.headers["Content-Length"]) == SIZE
assert requests.get(base + "/nope/x.mp4").status_code == 404
assert requests.get(base + path, headers={"Range": f"bytes={SIZE + 5}-"}).status_code == 416
assert P.verify_url(base + path, tries=1)
try:
    P.verify_url(base + "/nope/x.mp4", tries=1, gap=0)
    raise SystemExit("verify should fail")
except RuntimeError:
    pass
srv.shutdown()
print("1) file server + Range + verify OK")

# 2) tunnel_start with a fake cloudflared that prints like the real one
d = tempfile.mkdtemp()
fake = os.path.join(d, "cloudflared.py")
with open(fake, "w") as fh:
    fh.write(
        "#!/usr/bin/env python3\nimport sys,time\nprint('INF Requesting new quick Tunnel on trycloudflare.com...',flush=True)\n"
        "print('INF |  https://brave-lion-test.trycloudflare.com  |',flush=True)\ntime.sleep(1)\n"
        "print('INF Registered tunnel connection connIndex=0 protocol=http2',flush=True)\ntime.sleep(300)\n"
    )
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
os.environ["CLOUDFLARED"] = fake
P_sleep = P.time.sleep
P.time.sleep = lambda s: P_sleep(min(s, 0.5))
url, stop = P.tunnel_start(VID, wait=20)
assert url.startswith("https://brave-lion-test.trycloudflare.com/") and url.endswith(".mp4"), url
stop()
P.time.sleep = P_sleep
with open(fake, "w") as fh:
    fh.write("#!/usr/bin/env python3\nprint('ERR failed to request quick Tunnel: 429 Too Many Requests',flush=True)\n")
try:
    P.tunnel_start(VID, wait=5)
    raise SystemExit("should fail")
except RuntimeError as e:
    assert "429" in str(e)
print("2) tunnel start/parse/stop OK")

# 3) fallback chain: tunnel fails, tmpfiles uploads but Instagram can't fetch, uguu works
log = []


def boom(*a, **k):
    raise RuntimeError("cloudflare tunnel did not start")


P.tunnel_start = boom
P.tmpfiles_upload = lambda p: (log.append("tmp-up"), "https://tmpfiles.org/dl/1/x.mp4")[1]
P.uguu_upload = lambda p: (log.append("uguu-up"), "https://d.uguu.se/x.mp4")[1]
P.litterbox_upload = lambda p, e="1h": (_ for _ in ()).throw(RuntimeError("litterbox 403: Forbidden"))
P.verify_url = lambda u, **k: True


def fake_reel(url, cap, cover_url=None, first_comment=None, cover=None, path=None):
    log.append(("ig", url))
    assert path == VID, path  # the local file reaches Facebook's direct upload
    if "tmpfiles" in url:
        raise RuntimeError("IG processing failed (Instagram couldn't use the video from this link): ERROR")
    return "IG777"


P.ig_publish_reel = fake_reel
mid, host = P.ig_publish_with_fallback(VID, "cap", "c")
assert (mid, host) == ("IG777", "uguu"), (mid, host)
assert log == ["tmp-up", ("ig", "https://tmpfiles.org/dl/1/x.mp4"), "uguu-up", ("ig", "https://d.uguu.se/x.mp4")], log
P.uguu_upload = lambda p: (_ for _ in ()).throw(RuntimeError("uguu 403"))
try:
    P.ig_publish_with_fallback(VID, "cap")
    raise SystemExit("should fail")
except RuntimeError as e:
    msg = str(e)
    assert "tunnel" in msg and "tmpfiles" in msg and "uguu 403" in msg and "litterbox 403" in msg, msg
os.environ["PUBLIC_HOST"] = "uguu,tmpfiles"
assert P.host_order() == ["uguu", "tmpfiles"]
os.environ["PUBLIC_HOST"] = "auto"


class FR:
    status_code, text = 200, ""

    def __init__(self, j):
        self.j = j

    def json(self):
        return self.j


fb_calls = []


def fb_http(method, url, **kw):
    fb_calls.append((url, kw))
    if (kw.get("params") or {}).get("fields") == "status":  # link upload: the engine waits until Facebook has the bytes
        return FR({"status": {"uploading_phase": {"status": "complete"}}})
    return FR(
        {"video_id": "V1"}
        if kw.get("data", {}) == {"upload_phase": "start", "access_token": "pt"}
        else {"success": True}
    )


_http = P.http
P.http = fb_http
os.environ.update(FB_PAGE_ID="9", FB_PAGE_TOKEN="pt")
P.fb_publish_reel("https://x/v.mp4", "cap", path=VID)
_u, _kw = fb_calls[1]
assert "rupload" in _u and "file_url" not in _kw["headers"] and _kw["headers"]["file_size"] == str(SIZE), _kw["headers"]
assert len(_kw["data"]) == SIZE, "Facebook gets the bytes, not our tunnel link (trycloudflare robots.txt blocks it)"
fb_calls.clear()
P.fb_publish_reel("https://x/v.mp4", "cap")
assert fb_calls[1][1]["headers"]["file_url"] == "https://x/v.mp4"  # no local file: link fallback
P.http = _http
os.environ.update(FB_PAGE_ID="", FB_PAGE_TOKEN="")
os.environ["AZURE_BLOB_CONTAINER_SAS_URL"] = "https://a/b?sig"
assert P.host_order()[0] == "azure"
os.environ.pop("AZURE_BLOB_CONTAINER_SAS_URL")
assert P.host_order() == ["tunnel", "tmpfiles", "uguu", "litterbox"]
print("3) host fallback chain OK")

# 4) bot: publish fails -> publish_failed + retry button, YouTube text sent once; retry works; restart recovery
import pipeline.telegram as TG


class FakeTG:
    def __init__(self):
        self.sent = []

    def send(self, text, buttons=None, html=False):
        self.sent.append(("msg", text, buttons))
        return {}

    def send_document(self, path, caption=""):
        self.sent.append(("doc", path))

    def answer(self, *a):
        pass

    def clear_buttons(self, m):
        pass

    def is_me(self, u):
        return True


TG.Telegram = FakeTG
import pipeline.state as S

S.PATH = os.path.join(d, "state.json")
import autopilot as A

ap = A.Autopilot()
tg = ap.tg
ep_path = os.path.join(HERE, "sample_episode.json")
ep = json.load(open(ep_path, encoding="utf-8"))
ep.setdefault("ig_caption", "cap")
ep.setdefault("yt_description", "desc")
ep_tmp = os.path.join(d, "ep.json")
json.dump(ep, open(ep_tmp, "w", encoding="utf-8"))
ap.state["items"]["T-1"] = {
    "id": "T-1",
    "status": "scheduled",
    "publish_time": A.now_local().isoformat(),
    "episode": ep_tmp,
    "video": VID,
    "title": "t",
}
state = {"fail": True}


def fake_fb(path, cap, first_comment=None):
    if state["fail"]:
        raise RuntimeError("Instagram publish failed on every host:\n• litterbox: 403")
    return "IG9", "tunnel"


P.ig_publish_with_fallback = fake_fb
ap.publish("T-1")
it = ap.state["items"]["T-1"]
assert it["status"] == "publish_failed", it
last = tg.sent[-1]
assert last[2][0][0][1] == "pubnow:T-1" and "Retry" in last[2][0][0][0] and "cap" in last[1]
assert sum("upload this to YouTube Shorts" in m[1] for m in tg.sent if m[0] == "msg") == 1 and any(
    m[0] == "doc" for m in tg.sent
)
cb = lambda data: {
    "update_id": 1,
    "callback_query": {"id": "c", "data": data, "message": {"chat": {"id": 42}, "message_id": 1}},
}
state["fail"] = False
ap.handle(cb("pubnow:T-1"))
t0 = time.time()
while ap.state["items"]["T-1"]["status"] != "published" and time.time() - t0 < 20:
    time.sleep(0.3)
it = ap.state["items"]["T-1"]
assert it["status"] == "published" and it["ig_id"] == "IG9" and it["ig_host"] == "tunnel", it
assert sum("upload this to YouTube Shorts" in m[1] for m in tg.sent if m[0] == "msg") == 1, (
    "YouTube text must not repeat on retry"
)
ap.state["items"]["T-2"] = dict(ap.state["items"]["T-1"], id="T-2", status="publishing", ig_id=None, yt_sent=False)
ap.recover()
assert ap.state["items"]["T-2"]["status"] == "publish_failed" and tg.sent[-1][2][0][0][1] == "pubnow:T-2"
ap.handle(cb("igdone:T-2"))
assert ap.state["items"]["T-2"]["status"] == "published"
print("4) bot fail -> retry -> published, recovery, manual-done OK")
print("ALL OK")
