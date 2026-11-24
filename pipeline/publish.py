"""Publishers: Instagram Reels (Instagram API with Instagram Login), YouTube Data API, Azure Blob public URL."""

import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from .util import CHANNEL_DIR, DATA, env, http, log

# ------------------------------------------------------------------ public URL for Instagram
# Instagram's API can't take a file upload (Instagram Login apps): it downloads the video from a public https URL.
# We try several free hosts in order; publish() moves to the next one if a host refuses the upload
# or Instagram can't fetch the file.
#   tunnel     = your own PC serves the file through a temporary Cloudflare Quick Tunnel (free, no account,
#                nothing is uploaded to a third party; link dies when the post is done)
#   tmpfiles   = tmpfiles.org (free, deletes after 60 min)
#   uguu       = uguu.se (free, deletes after 3 h)
#   litterbox  = litterbox.catbox.moe (free, deletes after 1 h)
#   azure      = your Azure Blob container (needs AZURE_BLOB_CONTAINER_SAS_URL)
DEFAULT_HOSTS = ["tunnel", "tmpfiles", "uguu", "litterbox"]


def host_order():
    mode = (env("PUBLIC_HOST", "auto") or "auto").strip().lower()
    if mode == "auto":
        return (["azure"] if env("AZURE_BLOB_CONTAINER_SAS_URL") else []) + DEFAULT_HOSTS
    return [h.strip() for h in mode.split(",") if h.strip()]


def _bytes(path):
    with open(path, "rb") as fh:
        return fh.read()  # bytes (not a file handle) so http() retries re-send the body


# --- Azure Blob
def blob_upload(local_path, name):
    """PUT the file into a container using a container SAS URL (needs r,w,d,c permissions). Returns a readable URL."""
    sas = env("AZURE_BLOB_CONTAINER_SAS_URL", required=True)
    u = urlsplit(sas)
    path = u.path.rstrip("/") + "/" + name
    url = urlunsplit((u.scheme, u.netloc, path, u.query, ""))
    r = http(
        "PUT",
        url,
        data=_bytes(local_path),
        timeout=600,
        headers={"x-ms-blob-type": "BlockBlob", "Content-Type": "video/mp4"},
    )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"blob upload {r.status_code}: {r.text[:200]}")
    return url


def blob_delete(url):
    try:
        http("DELETE", url, timeout=60)
    except Exception as e:
        log.warning(f"blob delete: {e}")


# --- free temporary hosts
LITTERBOX = "https://litterbox.catbox.moe/resources/internals/api.php"


def litterbox_upload(local_path, expiry="1h"):
    r = http(
        "POST",
        LITTERBOX,
        timeout=600,
        data={"reqtype": "fileupload", "time": expiry},
        files={"fileToUpload": (os.path.basename(local_path), _bytes(local_path), "video/mp4")},
    )
    url = (r.text or "").strip()
    if r.status_code != 200 or not url.startswith("http"):
        raise RuntimeError(f"litterbox {r.status_code}: {_short(url)}")
    return url


def tmpfiles_upload(local_path):
    r = http(
        "POST",
        "https://tmpfiles.org/api/v1/upload",
        timeout=600,
        files={"file": (os.path.basename(local_path), _bytes(local_path), "video/mp4")},
    )
    try:
        page = r.json()["data"]["url"]
    except Exception:
        raise RuntimeError(f"tmpfiles {r.status_code}: {_short(r.text)}") from None
    u = urlsplit(page)  # https://tmpfiles.org/123/x.mp4 -> direct: /dl/123/x.mp4
    return urlunsplit(("https", u.netloc, "/dl" + u.path, "", ""))


def uguu_upload(local_path):
    r = http(
        "POST",
        "https://uguu.se/upload",
        timeout=600,
        files={"files[]": (os.path.basename(local_path), _bytes(local_path), "video/mp4")},
    )
    try:
        return r.json()["files"][0]["url"]
    except Exception:
        raise RuntimeError(f"uguu {r.status_code}: {_short(r.text)}") from None


def _short(t):
    import re

    t = re.sub(r"<[^>]+>", " ", t or "")
    return " ".join(t.split())[:160]


# --- Cloudflare Quick Tunnel from this PC
def _cloudflared():
    import platform
    import shutil

    cands = [
        env("CLOUDFLARED"),
        shutil.which("cloudflared"),
        r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
        r"C:\Program Files\cloudflared\cloudflared.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links", "cloudflared.exe"),
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    # download the official binary once (github.com/cloudflare/cloudflared releases) into data/bin
    win = os.name == "nt"
    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "amd64"
    name = f"cloudflared-windows-{arch}.exe" if win else f"cloudflared-linux-{arch}"
    dest = os.path.join(DATA, "bin", "cloudflared.exe" if win else "cloudflared")
    if not os.path.exists(dest):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        log.info("downloading cloudflared (official Cloudflare release, one time)…")
        r = http("GET", f"https://github.com/cloudflare/cloudflared/releases/latest/download/{name}", timeout=600)
        if r.status_code != 200 or len(r.content) < 5_000_000:
            raise RuntimeError(f"cloudflared download failed ({r.status_code})")
        with open(dest + ".part", "wb") as fh:
            fh.write(r.content)
        os.replace(dest + ".part", dest)
        if not win:
            os.chmod(dest, 0o755)
    return dest


def _file_server(path):
    """Serve the video (and its "<name>_cover.jpg" at ".../cover.jpg", if there is one) at a random path, with Range
    support. Returns (server, url_path of the video)."""
    import secrets
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    token = secrets.token_urlsafe(16)
    url_path = f"/{token}/{os.path.basename(path).replace(' ', '_')}"
    files = {url_path: (path, "video/mp4")}
    cover = os.path.splitext(path)[0] + "_cover.jpg"
    if os.path.exists(cover):
        files[f"/{token}/cover.jpg"] = (cover, "image/jpeg")

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body):
            if self.path.split("?")[0] not in files:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            fpath, ctype = files[self.path.split("?")[0]]
            size = os.path.getsize(fpath)
            start, end, code = 0, size - 1, 200
            rng = self.headers.get("Range", "")
            if rng.startswith("bytes="):
                a, _, b = rng[6:].split(",")[0].partition("-")
                try:
                    if a:
                        start = int(a)
                        end = min(int(b), size - 1) if b else size - 1
                    else:
                        start = max(size - int(b), 0)
                    code = 206
                except ValueError:
                    start, end, code = 0, size - 1, 200
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if not body:
                return
            with open(fpath, "rb") as fh:
                fh.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = fh.read(min(256 * 1024, left))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    left -= len(chunk)

        def do_GET(self):
            self._send(True)

        def do_HEAD(self):
            self._send(False)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, url_path


def tunnel_start(local_path, wait=60):
    """Returns (public_url, stop_fn). The file is served from this PC only while the post is being made."""
    import re
    import subprocess
    import sys
    import threading

    exe = _cloudflared()
    srv, url_path = _file_server(local_path)
    port = srv.server_address[1]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    proc = subprocess.Popen(
        [
            *([sys.executable] if exe.endswith(".py") else []),
            exe,
            "tunnel",
            "--no-autoupdate",
            "--protocol",
            "http2",
            "--url",
            f"http://127.0.0.1:{port}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=flags,
    )
    found, ready, tail = {}, threading.Event(), []

    def reader():
        for line in proc.stdout:
            tail.append(line.strip())
            del tail[:-15]
            m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
            if m and "url" not in found:
                found["url"] = m.group(0)
            if "Registered tunnel connection" in line or "Connection registered" in line:
                ready.set()

    threading.Thread(target=reader, daemon=True).start()

    def stop():
        try:
            proc.terminate()
            proc.wait(10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            srv.shutdown()
            srv.server_close()
        except Exception:
            pass

    t0 = time.time()
    while time.time() - t0 < wait and not (found.get("url") and ready.is_set()):
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    if not (found.get("url") and ready.is_set()):
        stop()
        raise RuntimeError("cloudflare tunnel did not start: " + " | ".join(tail[-4:]))
    time.sleep(6)  # let the new hostname reach Cloudflare DNS before anyone looks it up
    return found["url"] + url_path, stop


# --- check + iterate
def verify_url(url, tries=6, gap=5):
    """Fetch the first bytes like Instagram would. Raises if the URL doesn't serve an MP4."""
    last = None
    for i in range(tries):
        try:
            import requests

            r = requests.get(
                url, headers={"Range": "bytes=0-63", "User-Agent": "facebookexternalhit/1.1"}, timeout=30, stream=True
            )
            head = r.raw.read(64) if r.status_code in (200, 206) else b""
            r.close()
            if r.status_code in (200, 206) and b"ftyp" in head[:16]:
                return True
            last = f"HTTP {r.status_code}, not an mp4"
        except Exception as e:
            last = str(e)[:160]
        time.sleep(gap)
    raise RuntimeError(f"public link not reachable: {last}")


def public_hosts(local_path, name=None):
    """Yields (host, url, cleanup) for each host that accepted the file, in order. Caller stops when one works."""
    name = name or os.path.basename(local_path)
    for host in host_order():
        try:
            if host == "azure":
                url = blob_upload(local_path, name)
                cleanup = lambda u=url: blob_delete(u)
            elif host == "tunnel":
                url, cleanup = tunnel_start(local_path)
            elif host == "tmpfiles":
                url, cleanup = tmpfiles_upload(local_path), (lambda: None)
            elif host == "uguu":
                url, cleanup = uguu_upload(local_path), (lambda: None)
            elif host == "litterbox":
                url, cleanup = litterbox_upload(local_path, env("LITTERBOX_EXPIRY", "1h")), (lambda: None)
            else:
                log.warning(f"unknown PUBLIC_HOST entry: {host}")
                continue
        except Exception as e:
            yield host, None, str(e)[:200]
            continue
        try:
            verify_url(url)
        except Exception as e:
            cleanup()
            yield host, None, str(e)[:200]
            continue
        yield host, url, cleanup


def ig_publish_with_fallback(local_path, caption, first_comment=None):
    """Try each host until Instagram accepts the video. Returns (media_id, host). Raises with every host's error."""
    errs = []
    for host, url, extra in public_hosts(local_path):
        if not url:
            errs.append(f"{host}: {extra}")
            log.warning(f"host {host} failed: {extra}")
            continue
        # the tunnel serves the cover next to the video; other hosts hold one file only, so Instagram picks a frame
        cover = os.path.splitext(local_path)[0] + "_cover.jpg"
        cover_url = url.rsplit("/", 1)[0] + "/cover.jpg" if host == "tunnel" and os.path.exists(cover) else None
        try:
            log.info(f"Instagram: fetching video via {host}" + (" (+ cover)" if cover_url else ""))
            return ig_publish_reel(
                url, caption, cover_url=cover_url, first_comment=first_comment, cover=cover, path=local_path
            ), host
        except Exception as e:
            errs.append(f"{host}→Instagram: {str(e)[:200]}")
            log.warning(f"Instagram via {host} failed: {e}")
            if "media_publish" in str(e) or "IG publish" in str(e):
                raise  # container was fine, publish itself failed: don't repost
        finally:
            extra()
    raise RuntimeError("Instagram publish failed on every host:\n" + "\n".join("• " + e for e in errs))


# ------------------------------------------------------------------ Instagram
IG = "https://graph.instagram.com"


def _ig_ver():
    return env("IG_API_VERSION", "v23.0")


def _ig_token():
    return env("IG_ACCESS_TOKEN", required=True)


FB_RESULT = {}  # what happened on Facebook during the last ig_publish_reel (the caller reports it in Telegram)


def ig_publish_reel(video_url, caption, cover_url=None, first_comment=None, cover=None, path=None):
    uid, tok, v = env("IG_USER_ID", required=True), _ig_token(), _ig_ver()
    p = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption[:2200],
        "share_to_feed": "true",
        "access_token": tok,
    }
    if cover_url:
        p["cover_url"] = cover_url
    r = http("POST", f"{IG}/{v}/{uid}/media", data=p)
    if r.status_code != 200:
        raise RuntimeError(f"IG create container {r.status_code}: {r.text[:400]}")
    cid = r.json()["id"]
    for _ in range(60):  # wait up to ~10 min for Instagram to process the video
        time.sleep(10)
        st = http("GET", f"{IG}/{v}/{cid}", params={"fields": "status_code,status", "access_token": tok}).json()
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(
                f"IG processing failed (Instagram couldn't use the video from this link): {st.get('status') or st}"
            )
    else:
        raise RuntimeError("IG processing timed out")
    r = http("POST", f"{IG}/{v}/{uid}/media_publish", data={"creation_id": cid, "access_token": tok})
    if r.status_code != 200:
        raise RuntimeError(f"IG publish {r.status_code}: {r.text[:400]}")
    media_id = r.json()["id"]
    if first_comment:
        try:
            http("POST", f"{IG}/{v}/{media_id}/comments", data={"message": first_comment, "access_token": tok})
        except Exception as e:
            log.warning(f"IG first comment: {e}")
    FB_RESULT.clear()
    if env("FB_PAGE_ID") and env("FB_PAGE_TOKEN"):
        try:
            FB_RESULT["url"] = fb_publish_reel(video_url, caption, cover=cover, first_comment=first_comment, path=path)
            log.info(f"Facebook Reel: {FB_RESULT['url']}")
        except Exception as e:
            FB_RESULT["error"] = str(e)
            log.warning(f"Facebook Reel failed (Instagram is already live): {e}")
    return media_id


# ------------------------------------------------------------------ Facebook Page
FB = "https://graph.facebook.com"
FB_UPLOAD = "https://rupload.facebook.com/video-upload"


def fb_publish_reel(video_url, caption, cover=None, first_comment=None, path=None):
    """Facebook Page Reel: start -> upload -> publish. Returns the Reel URL.
    With a local `path` the bytes go straight to Facebook: its fetcher is refused by trycloudflare.com ("403 Restricted
    by robots.txt"), so the public link is only the fallback."""
    pid, tok = env("FB_PAGE_ID", required=True), env("FB_PAGE_TOKEN", required=True)
    v = env("FB_API_VERSION", "v26.0")
    r = http("POST", f"{FB}/{v}/{pid}/video_reels", data={"upload_phase": "start", "access_token": tok})
    if r.status_code != 200:
        raise RuntimeError(f"FB start {r.status_code}: {r.text[:400]}")
    vid = r.json()["video_id"]
    if path and os.path.exists(path):
        r = http(
            "POST",
            f"{FB_UPLOAD}/{v}/{vid}",
            timeout=600,
            headers={"Authorization": f"OAuth {tok}", "offset": "0", "file_size": str(os.path.getsize(path))},
            data=_bytes(path),
        )
    else:
        r = http("POST", f"{FB_UPLOAD}/{v}/{vid}", headers={"Authorization": f"OAuth {tok}", "file_url": video_url})
    if r.status_code != 200:
        raise RuntimeError(f"FB upload {r.status_code}: {r.text[:400]}")
    if not (path and os.path.exists(path)):
        # Facebook fetches file_url on its own time; the caller closes the tunnel right after we return, so wait for
        # the bytes. (A local file was sent in the request above, so there is nothing to wait for.)
        for _ in range(36):  # up to ~6 min
            ph = (
                http("GET", f"{FB}/{v}/{vid}", params={"fields": "status", "access_token": tok}).json().get("status")
                or {}
            )
            up = (ph.get("uploading_phase") or {}).get("status")
            if up == "complete":
                break
            if up == "error" or ph.get("video_status") == "error":
                raise RuntimeError(f"FB could not fetch the video: {json.dumps(ph)[:300]}")
            time.sleep(10)
        else:
            raise RuntimeError("FB upload timed out (video not fetched in 6 min)")
    r = http(
        "POST",
        f"{FB}/{v}/{pid}/video_reels",
        data={
            "upload_phase": "finish",
            "video_id": vid,
            "video_state": "PUBLISHED",
            "description": caption[:2200],
            "access_token": tok,
        },
    )
    if r.status_code != 200 or not r.json().get("success"):
        raise RuntimeError(f"FB publish {r.status_code}: {r.text[:400]}")
    # extras: the reel is live already, so a failure here is only logged
    try:
        if cover and os.path.exists(cover):
            with open(cover, "rb") as fh:
                r = http(
                    "POST",
                    f"{FB}/{v}/{vid}/thumbnails",
                    retries=0,
                    data={"is_preferred": "true", "access_token": tok},
                    files={"source": ("cover.jpg", fh, "image/jpeg")},
                )
            if r.status_code != 200:
                log.warning(f"FB thumbnail {r.status_code}: {r.text[:200]}")
        if first_comment:
            r = http("POST", f"{FB}/{v}/{vid}/comments", data={"message": first_comment, "access_token": tok})
            if r.status_code != 200:
                log.warning(f"FB comment {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"FB cover/comment: {e}")
    return f"https://www.facebook.com/reel/{vid}"


def platform_stats(items):
    """items: [{"yt", "ig", "fb", "published"}] -> one {"yt": {...}, "ig": {...}, "fb": {...}} per item.
    A platform that fails (token, permission) is just left out. Watch times are seconds."""
    out = [{} for _ in items]
    try:
        yts = [i["yt"] for i in items if i.get("yt")]
        if yts:
            j = http(
                "GET",
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part": "statistics,contentDetails",
                    "id": ",".join(yts),
                    "key": env("YOUTUBE_API_KEY", required=True),
                },
            ).json()
            by = {v["id"]: v for v in j.get("items", [])}
            for o, i in zip(out, items):
                if y := by.get(i.get("yt")):
                    s = y["statistics"]
                    o["yt"] = {k: int(s.get(f"{k[:-1]}Count", 0)) for k in ("views", "likes", "comments")}
                    d = re.fullmatch(r"PT(?:(\d+)M)?(?:(\d+)S)?", y["contentDetails"].get("duration", ""))
                    o["yt"]["length"] = int(d[1] or 0) * 60 + int(d[2] or 0) if d else 0
    except Exception as e:
        log.warning(f"YT stats: {e}")
    for o, i in zip(out, items):
        if not i.get("ig") or i["ig"] == "manual":
            continue
        try:
            j = http(
                "GET",
                f"{IG}/{_ig_ver()}/{i['ig']}/insights",
                params={
                    "metric": "views,likes,comments,saved,shares,ig_reels_avg_watch_time",
                    "access_token": _ig_token(),
                },
            ).json()
            m = {d["name"]: d["values"][0]["value"] for d in j["data"]}
            o["ig"] = {k: m.get(k, 0) for k in ("views", "likes", "comments", "saved", "shares")}
            o["ig"]["watch"] = m.get("ig_reels_avg_watch_time", 0) / 1000
        except Exception as e:
            log.warning(f"IG stats {i['ig']}: {e}")
    if not (env("FB_PAGE_ID") and env("FB_PAGE_TOKEN")):
        return out
    pid, tok, v = env("FB_PAGE_ID"), env("FB_PAGE_TOKEN"), env("FB_API_VERSION", "v26.0")
    try:  # reels posted before fb_url was kept in state: match by post time
        reels = (
            http(
                "GET",
                f"{FB}/{v}/{pid}/video_reels",
                params={"fields": "id,created_time", "limit": 50, "access_token": tok},
            )
            .json()
            .get("data", [])
        )
        reels = [(r["id"], datetime.strptime(r["created_time"], "%Y-%m-%dT%H:%M:%S%z")) for r in reels]
    except Exception as e:
        log.warning(f"FB reels list: {e}")
        reels = []
    for o, i in zip(out, items):
        vid = i.get("fb")
        if not vid and i.get("published") and reels:
            t = datetime.fromisoformat(i["published"])
            rid, rt = min(reels, key=lambda r: abs(r[1] - t))
            vid = rid if abs(rt - t).total_seconds() < 3600 else None
        if not vid:
            continue
        try:
            j = http(
                "GET",
                f"{FB}/{v}/{vid}",
                params={
                    "fields": "views,likes.summary(true).limit(0),comments.summary(true).limit(0)",
                    "access_token": tok,
                },
            ).json()
            o["fb"] = {
                "views": j.get("views", 0),
                "likes": j["likes"]["summary"]["total_count"],
                "comments": j["comments"]["summary"]["total_count"],
            }
            # needs the read_insights permission; without it the rest still shows
            ins = http(
                "GET",
                f"{FB}/{v}/{vid}/video_insights",
                params={"metric": "post_video_avg_time_watched", "access_token": tok},
            ).json()
            if ins.get("data"):
                o["fb"]["watch"] = ins["data"][0]["values"][0]["value"] / 1000
        except Exception as e:
            log.warning(f"FB stats {vid}: {e}")
    return out


def ig_whoami():
    """Reads the IG professional account id + username from the token and saves IG_USER_ID into .env."""
    r = http(
        "GET", f"{IG}/{_ig_ver()}/me", params={"fields": "user_id,username,account_type", "access_token": _ig_token()}
    )
    j = r.json()
    if r.status_code != 200 or "error" in j:
        raise RuntimeError(f"IG token check failed: {j}")
    uid = str(j.get("user_id") or j.get("id"))
    _update_env("IG_USER_ID", uid)
    os.environ["IG_USER_ID"] = uid
    if not env("IG_TOKEN_REFRESHED"):
        _update_env("IG_TOKEN_REFRESHED", datetime.now().strftime("%Y-%m-%d"))
    return j


def ig_publish_limit():
    uid, tok = env("IG_USER_ID", required=True), _ig_token()
    return http(
        "GET",
        f"{IG}/{_ig_ver()}/{uid}/content_publishing_limit",
        params={"fields": "config,quota_usage", "access_token": tok},
    ).json()


def ig_refresh_token():
    """Long-lived tokens last 60 days; refresh after day 1 and before expiry. Writes the new token into .env."""
    r = http(
        "GET", f"{IG}/refresh_access_token", params={"grant_type": "ig_refresh_token", "access_token": _ig_token()}
    )
    j = r.json()
    if "access_token" not in j:
        raise RuntimeError(f"IG refresh failed: {j}")
    _update_env("IG_ACCESS_TOKEN", j["access_token"])
    _update_env("IG_TOKEN_REFRESHED", datetime.now().strftime("%Y-%m-%d"))
    os.environ["IG_ACCESS_TOKEN"] = j["access_token"]
    return j.get("expires_in")


def _update_env(key, value):
    p = os.path.join(CHANNEL_DIR, ".env")
    lines = open(p, encoding="utf-8").read().splitlines() if os.path.exists(p) else []
    for i, l in enumerate(lines):
        if l.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ------------------------------------------------------------------ YouTube
YT_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]
YT_TOKEN = os.path.join(DATA, "youtube_token.json")


def youtube_auth():
    """One-time browser login. Needs the OAuth 'Desktop app' client JSON path in YOUTUBE_CLIENT_SECRET_FILE."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = env("YOUTUBE_CLIENT_SECRET_FILE", required=True)
    if not os.path.isabs(secret) and not os.path.exists(secret):  # a bare file name = inside the channel folder
        secret = os.path.join(CHANNEL_DIR, secret)
    flow = InstalledAppFlow.from_client_secrets_file(secret, YT_SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    os.makedirs(DATA, exist_ok=True)
    with open(YT_TOKEN, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())
    return YT_TOKEN


YT_OK = os.path.join(
    DATA, "youtube_api_ok"
)  # written by `autopilot.py youtube-test` when uploads are NOT locked private


def yt_token_available():
    return bool(env("YOUTUBE_TOKEN_JSON")) or (os.path.exists(YT_TOKEN) and os.path.exists(YT_OK))


def _yt():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    # cloud: the token comes from a GitHub secret and stays in memory (DATA is committed to the state branch there)
    secret = env("YOUTUBE_TOKEN_JSON")
    if secret:
        creds = Credentials.from_authorized_user_info(json.loads(secret), YT_SCOPES)
    else:
        creds = Credentials.from_authorized_user_file(YT_TOKEN, YT_SCOPES)
    if not creds.valid:
        creds.refresh(Request())
        if not secret:
            with open(YT_TOKEN, "w", encoding="utf-8") as fh:
                fh.write(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def _fit_tags(tags, limit=450):
    """YouTube rejects the upload when all tags together exceed 500 characters."""
    out, used = [], 0
    for t in dict.fromkeys(str(t).strip().lstrip("#") for t in tags or []):
        if t and used + len(t) + 1 <= limit:
            out.append(t)
            used += len(t) + 1
    return out


def _yt_text(s):
    """YouTube refuses a title/description holding < or > (400 invalidTitle/invalidDescription)."""
    return s.replace("<", "‹").replace(">", "›")


def youtube_upload(path, title, description, tags=None, publish_at=None, privacy=None, synthetic=True):
    """publish_at: aware datetime -> uploaded as private and YouTube itself makes it public at that time.
    synthetic=True ticks 'Altered or synthetic content' (config publish.ai_label)."""
    from googleapiclient.http import MediaFileUpload

    yt = _yt()
    status = {
        "privacyStatus": privacy or ("private" if publish_at else "public"),
        "selfDeclaredMadeForKids": False,
        "containsSyntheticMedia": bool(synthetic),
    }
    if publish_at:
        status["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = {
        "snippet": {
            "title": _yt_text(title)[:100],
            "description": _yt_text(description)[:4900],
            "tags": _fit_tags(tags),
            "categoryId": "28",
            "defaultLanguage": "hi",
            "defaultAudioLanguage": "hi",
        },
        "status": status,
    }
    req = yt.videos().insert(
        part="snippet,status", body=body, media_body=MediaFileUpload(path, chunksize=8 * 1024 * 1024, resumable=True)
    )
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def youtube_thumbnail(vid, path):
    """Custom thumbnail. YouTube may refuse it (channel not phone-verified) or not show it on the Shorts shelf."""
    from googleapiclient.http import MediaFileUpload

    _yt().thumbnails().set(videoId=vid, media_body=MediaFileUpload(path, mimetype="image/jpeg")).execute()


def youtube_comment(vid, text):
    _yt().commentThreads().insert(
        part="snippet", body={"snippet": {"videoId": vid, "topLevelComment": {"snippet": {"textOriginal": text}}}}
    ).execute()


def youtube_status(vid):
    items = _yt().videos().list(part="status", id=vid).execute().get("items", [])
    return items[0]["status"] if items else {}


def youtube_make_public(vid, synthetic=False):
    """Publish now a video that was uploaded private + publishAt (auto mode, then 🚀 tapped). No-op when already public."""
    st = youtube_status(vid)
    if st.get("privacyStatus") == "public":
        return False
    keep = ("embeddable", "license", "publicStatsViewable")
    # update replaces the whole status; containsSyntheticMedia isn't returned by list, so restate the channel's choice
    status = {k: st[k] for k in keep if k in st}
    status.update(privacyStatus="public", selfDeclaredMadeForKids=False, containsSyntheticMedia=bool(synthetic))
    body = {"id": vid, "status": status}
    _yt().videos().update(part="status", body=body).execute()
    return True


def youtube_delete(vid):
    _yt().videos().delete(id=vid).execute()


# ------------------------------------------------------------------ comment-keyword answers ("Comment ESIM")
def keyword_in(text, kw):
    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(kw)}(?![A-Za-z0-9])", text or "", re.I))


def ig_keyword_comments(media_id, kw):
    r = http(
        "GET",
        f"{IG}/{_ig_ver()}/{media_id}/comments",
        params={"fields": "id,text,username", "limit": 50, "access_token": _ig_token()},
    )
    return [c for c in (r.json().get("data", []) if r.status_code == 200 else []) if keyword_in(c.get("text"), kw)]


def ig_answer(comment_id, text):
    """DM the commenter (private reply) when the account allows messaging, else reply under the comment."""
    tok, v = _ig_token(), _ig_ver()
    r = http(
        "POST",
        f"{IG}/{v}/{env('IG_USER_ID', required=True)}/messages",
        params={"access_token": tok},
        json={"recipient": {"comment_id": comment_id}, "message": {"text": text[:1000]}},
    )
    if r.status_code == 200:
        return "dm"
    r = http("POST", f"{IG}/{v}/{comment_id}/replies", data={"message": text[:2000], "access_token": tok})
    if r.status_code != 200:
        raise RuntimeError(f"IG reply {r.status_code}: {r.text[:200]}")
    return "reply"


def yt_keyword_comments(vid, kw):
    res = _yt().commentThreads().list(part="snippet", videoId=vid, maxResults=100, textFormat="plainText").execute()
    out = []
    for th in res.get("items", []):
        c = th["snippet"]["topLevelComment"]
        if keyword_in(c["snippet"].get("textDisplay"), kw):
            out.append({"id": c["id"], "text": c["snippet"].get("textDisplay")})
    return out


def yt_answer(comment_id, text):
    _yt().comments().insert(
        part="snippet", body={"snippet": {"parentId": comment_id, "textOriginal": text[:9000]}}
    ).execute()
    return "reply"
