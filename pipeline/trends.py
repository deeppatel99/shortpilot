"""Trend scout: collects candidate topics from free sources.

- Google Trends "Trending now" RSS for trends.geo (a country code: US, IN, BR, …)
- Google News RSS searches for your niche keywords (trends.news_queries, last trends.news_days days)
- Hacker News (Show HN + front page): what people are building, top tech stories
- YouTube most-popular videos in trends.geo, one category (needs a free YOUTUBE_API_KEY)
- GitHub: new repos for trends.github_queries gaining stars fastest ("this repo got 20K stars in a week")
"""

import time
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from urllib.parse import quote_plus

from .util import env, http, log

HT = "{https://trends.google.com/trending/rss}"


def parse_google_trends(xml_text):
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        news = [
            {
                "title": (n.findtext(f"{HT}news_item_title") or "").strip(),
                "url": n.findtext(f"{HT}news_item_url") or "",
                "source": n.findtext(f"{HT}news_item_source") or "",
            }
            for n in it.findall(f"{HT}news_item")
        ]
        out.append(
            {
                "source": "google_trends",
                "title": (it.findtext("title") or "").strip(),
                "traffic": it.findtext(f"{HT}approx_traffic") or "",
                "news": news[:3],
            }
        )
    return out


def parse_google_news(xml_text, query):
    out = []
    root = ET.fromstring(xml_text)
    for it in root.iter("item"):
        src = it.find("source")
        out.append(
            {
                "source": "google_news",
                "query": query,
                "title": (it.findtext("title") or "").strip(),
                "url": it.findtext("link") or "",
                "published": it.findtext("pubDate") or "",
                "publisher": src.text if src is not None else "",
            }
        )
    return out


def google_trends(geo="US"):
    r = http("GET", f"https://trends.google.com/trending/rss?geo={geo}")
    return parse_google_trends(r.text) if r.status_code == 200 else []


def google_news(query, days=2, hl="en-US", gl="US"):
    """Google News search RSS in one edition: hl = language (en-US, es-MX…), gl = country (US, MX…)."""
    ceid = f"{gl}:{hl.split('-')[0]}"
    url = f"https://news.google.com/rss/search?q={quote_plus(query + f' when:{days}d')}&hl={hl}&gl={gl}&ceid={ceid}"
    r = http("GET", url)
    return parse_google_news(r.text, query)[:5] if r.status_code == 200 else []


def hacker_news(tags, days=2, min_points=50, n=15):
    """What people are building / talking about: 'show_hn' = projects people made, 'front_page' = top tech stories."""
    since = int(time.time()) - days * 86400
    r = http(
        "GET",
        "https://hn.algolia.com/api/v1/search",
        params={"tags": tags, "numericFilters": f"created_at_i>{since},points>{min_points}", "hitsPerPage": n},
    )
    if r.status_code != 200:
        return []
    return [
        {
            "source": "hacker_news",
            "query": tags,
            "title": h["title"],
            "traffic": f"{h.get('points')} points",
            "url": h.get("url") or "",
        }
        for h in r.json().get("hits", [])
        if h.get("title")
    ]


def parse_github(j, query):
    return [
        {
            "source": "github",
            "query": query,
            "title": f"{r['full_name']}: {(r.get('description') or '').strip()[:140]}",
            "traffic": f"{r['stargazers_count']:,} stars",
            "url": r["html_url"],
        }
        for r in j.get("items", [])
    ]


def github_repos(query, days=14, n=8):
    """Repos created in the last `days` days, most stars first.
    ponytail: stars-since-creation stands in for star velocity (GitHub has no trending API); a daily star
    snapshot in state would catch old repos that suddenly take off."""
    since = (date.today() - timedelta(days=days)).isoformat()
    tok = env("GH_TOKEN") or env("GITHUB_TOKEN")
    r = http(
        "GET",
        "https://api.github.com/search/repositories",
        params={"q": f"{query} in:name,description,topics created:>{since}", "sort": "stars", "per_page": n},
        headers={"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {tok}"} if tok else {})},
    )
    return parse_github(r.json(), query) if r.status_code == 200 else []


def youtube_trending(region="IN", category="28", n=25):
    key = env("YOUTUBE_API_KEY")
    if not key:
        return []
    r = http(
        "GET",
        "https://www.googleapis.com/youtube/v3/videos",
        params={
            "part": "snippet,statistics",
            "chart": "mostPopular",
            "regionCode": region,
            "videoCategoryId": category,
            "maxResults": n,
            "key": key,
        },
    )
    if r.status_code != 200:
        log.warning(f"YouTube trending {r.status_code}: {r.text[:200]}")
        return []
    return [
        {
            "source": "youtube_trending",
            "title": i["snippet"]["title"],
            "channel": i["snippet"]["channelTitle"],
            "views": i.get("statistics", {}).get("viewCount"),
        }
        for i in r.json().get("items", [])
    ]


def collect(cfg):
    """Today's raw topic candidates from every source switched on in channel.json -> trends."""
    from .validate import language

    t = cfg.get("trends", {})
    geo, hl = t.get("geo", "US"), language(cfg)
    items = []
    jobs = []
    if t.get("google_trends", True):
        jobs.append(("Google Trends", lambda: google_trends(geo)))
    for q in t.get("news_queries", []):
        jobs.append((f"News '{q}'", lambda q=q: google_news(q, t.get("news_days", 2), hl=hl, gl=geo)))
    for q in t.get("world_news_queries", []):  # the US English edition, for global stories
        jobs.append((f"World news '{q}'", lambda q=q: google_news(q, t.get("news_days", 2), hl="en-US", gl="US")))
    for tags in t.get("hacker_news", []):  # e.g. ["show_hn", "front_page"] for a tech channel
        jobs.append((f"Hacker News {tags}", lambda tags=tags: hacker_news(tags, t.get("news_days", 2))))
    if t.get("youtube_trending", True):
        jobs.append(("YouTube trending", lambda: youtube_trending(geo, t.get("youtube_category", "28"))))
    for q in t.get("github_queries", []):
        jobs.append((f"GitHub '{q}'", lambda q=q: github_repos(q)))
    for name, fn in jobs:
        try:
            got = fn()
            items += got
            log.info(f"trends: {name}: {len(got)}")
        except Exception as e:
            log.warning(f"trends: {name} failed: {e}")
    seen, uniq = set(), []
    for it in items:
        k = it["title"].lower()[:70]
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq
