"""Exercises the REAL Telegram client against a fake HTTP layer (catches signature bugs the flow test can't)."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))  # tests/
ROOT = os.path.dirname(HERE)
SLUG = "_template"
PACK = os.path.join(ROOT, "channels", SLUG)
os.environ["AUTOPILOT_CHANNEL"] = SLUG  # the engine is tested with the channel template
VIDEOS = os.environ.get("AUTOPILOT_TEST_VIDEOS") or os.path.join(PACK, "data", "videos")  # any rendered .mp4
sys.path.insert(0, ROOT)
os.environ["AUTOPILOT_ENV_FILE"] = os.path.join(HERE, "no.env")  # never load the real .env
os.environ.update(TELEGRAM_BOT_TOKEN="T", TELEGRAM_CHAT_ID="42", FB_PAGE_ID="", FB_PAGE_TOKEN="")
import pipeline.telegram as TG

calls = []


class R:
    def __init__(s, res):
        s.res = res

    def json(s):
        return {"ok": True, "result": s.res}


def fake_http(method, url, **kw):
    calls.append((url.rsplit("/", 1)[-1], kw.get("json") or kw.get("data"), kw.get("timeout")))
    if url.endswith("getUpdates"):
        return R(
            [
                {
                    "update_id": 7,
                    "callback_query": {
                        "id": "c1",
                        "data": "approve:1021-1",
                        "message": {"chat": {"id": 42}, "message_id": 5},
                    },
                }
            ]
        )
    return R({"message_id": 1})


TG.http = fake_http
tg = TG.Telegram()
ups = tg.updates(offset=3, timeout=20)
assert ups[0]["callback_query"]["data"] == "approve:1021-1" and tg.is_me(ups[0])
m, body, t = calls[-1]
assert m == "getUpdates" and body["timeout"] == 20 and body["offset"] == 3 and t == 35, calls[-1]
tg.send("hi", [[("✅ Approve", "approve:x")]])
assert calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == "approve:x"
tg.answer("c1", "ok")
tg.clear_buttons({"chat": {"id": 42}, "message_id": 5})
p = os.path.join(HERE, "_v.mp4")
open(p, "wb").write(b"x")
tg.send_video(p, "cap", [[("🚀", "pubnow:x")]])
assert calls[-1][0] == "sendVideo" and calls[-1][2] == 300
tg.send_document(p, "doc")
assert calls[-1][0] == "sendDocument"
os.remove(p)
print("TELEGRAM OK", [c[0] for c in calls])
