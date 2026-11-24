import json
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
from pipeline import llm, trends, util, validate

GT = """<?xml version="1.0"?><rss xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel>
<item><title>iphone 18</title><ht:approx_traffic>50,000+</ht:approx_traffic>
<ht:news_item><ht:news_item_title>New phone launch price</ht:news_item_title><ht:news_item_url>https://x</ht:news_item_url><ht:news_item_source>Daily News</ht:news_item_source></ht:news_item></item>
</channel></rss>"""
GN = """<rss><channel><item><title>Battery myth explained - Times</title><link>https://n</link><pubDate>Tue, 20 Oct 2026</pubDate><source url="https://t">Times</source></item></channel></rss>"""
a = trends.parse_google_trends(GT)
b = trends.parse_google_news(GN, "battery")
assert a[0]["title"] == "iphone 18" and a[0]["traffic"] == "50,000+" and a[0]["news"][0]["source"] == "Daily News", a
assert b[0]["publisher"] == "Times", b
g = trends.parse_github(
    {"items": [{"full_name": "a/b", "description": None, "stargazers_count": 1234, "html_url": "u"}]}, "mcp"
)
assert g[0]["title"] == "a/b: " and g[0]["traffic"] == "1,234 stars" and g[0]["source"] == "github", g
# no-news fallback cycle (content.evergreen_flow): how_it_works → myth_check → body_brain → tech_tip → None (the end of a round) → wraps
from pipeline.writer import next_evergreen  # noqa: E402

assert next_evergreen([]) == "how_it_works"
assert next_evergreen(["old_category", "news_explained"]) == "how_it_works"  # categories outside the flow are ignored
assert next_evergreen(["how_it_works", "news_explained", "news_explained"]) == "myth_check"  # news days don't move it
assert next_evergreen(["tech_tip"]) is None and next_evergreen(["body_brain", "myth_check"]) == "body_brain"
assert util.extract_json('blah ```json\n{"a": [1, "}"]}\n``` x') == {"a": [1, "}"]}
assert util.extract_json('note {"x": {"y": 2}} trailing') == {"x": {"y": 2}}
# Gemini request shape + grounding sources
seen = {}


class R:
    status_code = 200

    def json(self):
        return {
            "candidates": [
                {
                    "content": {"parts": [{"text": '{"ok":1}'}]},
                    "groundingMetadata": {"groundingChunks": [{"web": {"title": "PIB", "uri": "https://pib"}}]},
                }
            ]
        }


def fake_http(method, url, **kw):
    seen.update(url=url, body=kw["json"], headers=kw["headers"])
    return R()


llm.http = fake_http
os.environ["GEMINI_API_KEY"] = "k"
out = llm.Gemini().complete("hi", web=True)
assert (
    "google_search" in json.dumps(seen["body"])
    and "gemini-flash-latest:generateContent" in seen["url"]
    and seen["headers"]["x-goog-api-key"] == "k"
)
assert "PIB: https://pib" in out


# Anthropic request shape
class RA:
    status_code = 200

    def json(self):
        return {"content": [{"type": "text", "text": "A"}, {"type": "server_tool_use"}, {"type": "text", "text": "B"}]}


llm.http = lambda m, u, **kw: (seen.update(aurl=u, abody=kw["json"], ah=kw["headers"]), RA())[1]
os.environ["ANTHROPIC_API_KEY"] = "a"
assert llm.AnthropicAPI().complete("x", web=True) == "AB" and seen["abody"]["tools"][0]["type"] == "web_search_20250305"
# validator on the hand-written demo episode
ep = json.load(open(os.path.join(ROOT, "engine", "demo_episode.json"), encoding="utf-8"))
fixed, E, W = validate.validate(ep)
print("demo episode:", "errors", E, "| warnings", W, "| est", fixed["_estimate"])
assert not E, E
bad = json.loads(json.dumps(ep))
bad["scenes"][1]["visual"] = {"type": "versus"}
bad["scenes"][0]["lines"][0]["tts"] = ""
_, E2, _ = validate.validate(bad)
print("bad episode errors:", E2)
assert any("empty tts" in e for e in E2) and any("needs 'left'" in e for e in E2)
assert not any("opening frame" in e for e in E2)  # a counter is proof
# Hindi channels: the voice reads Devanagari, so Latin letters in tts are an error; other languages are free
hi = json.loads(json.dumps(ep))
hi["lang"] = "hi-IN"
assert any("Devanagari" in e for e in validate.validate(hi)[1])
es = json.loads(json.dumps(ep))
es["lang"] = "es-MX"
assert not validate.validate(es)[1]
# language codes and speaking speed
assert validate.base_lang("pt-BR") == "pt" and validate._cps("kokoro", "ja-JP") < validate._cps("kokoro", "en-US")
for good in ("en-US", "es", "zh-Hans-CN"):
    assert validate.LANG_RE.match(good), good
assert not validate.LANG_RE.match("English")
bad["scenes"][0]["visual"] = {"type": "big_emoji", "emoji": "😱"}
assert any("opening frame needs proof" in e for e in validate.validate(bad)[1])  # frame 0 can't be a reaction emoji
# frame 0 text budget: a settings screen is proof, a full news sentence is not
bad["scenes"][0]["visual"] = {"type": "setting", "rows": ["Adaptive charging"]}
assert not any("opening frame" in e or "headline" in e for e in validate.validate(bad)[1])
bad["scenes"][0]["visual"] = {"type": "headline", "text": "OpenAI launches GPT-6.1 Terra for ChatGPT Work and Codex"}
assert any("scene 1 headline" in e for e in validate.validate(bad)[1])
# length: 25-35 s is the target, over 45 s is rejected
long = json.loads(json.dumps(ep))
long["scenes"][1]["lines"][0]["tts"] = "a" * 900
assert any("too long" in e for e in validate.validate(long)[1])
# YouTube refuses a title/description holding < or > (400 invalidDescription, #1104-22: "Settings > System > Backup")
from pipeline import publish  # noqa: E402

sent = {}


class FakeYT:
    def videos(self):
        return self

    def insert(self, part, body, media_body):
        sent.update(body["snippet"])
        return self

    def next_chunk(self):
        return None, {"id": "YT1"}


publish._yt = FakeYT
assert publish.youtube_upload(__file__, "a < b", "Settings > System > Backup") == "YT1"
assert sent["description"] == "Settings › System › Backup" and sent["title"] == "a ‹ b", sent
print("UNITS OK")
