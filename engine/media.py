"""Scene media: AI images + real stock footage behind the motion graphics.

Each scene may carry  "bg": [shot, shot]  where a shot is one of
    {"ai": "english prompt"}        AI image  (Cloudflare Workers AI FLUX -> Pollinations -> Pexels photo)
    {"stock": "english query"}      stock video (Pexels video -> Pexels photo -> AI image)
    {"photo": "english query"}      stock photo (Pexels photo -> AI image)
Everything is optional: no keys / no network / any failure -> that shot is dropped and the scene falls back to the
procedural background. Nothing here can stop a render.

Env:  CF_ACCOUNT_ID + CF_API_TOKEN (Workers AI, free 10k neurons/day ~ 150 images)
      PIXABAY_API_KEY (free) and/or PEXELS_API_KEY     POLLINATIONS=0 to disable that fallback     MEDIA=off to disable all media
"""

import base64
import hashlib
import json
import math
import os
import random
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import numpy as np
from PIL import Image, ImageFilter

W, H, FPS = 1080, 1920, 30
KB = 1.16  # prepared images are 16% larger than the frame -> room for Ken Burns moves
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("MEDIA_CACHE") or os.path.join(HERE, "..", "data", "media_cache")
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
# words that make a bad photo search when an AI prompt falls back to stock ("close up of a hand" -> random hands)
FILLER = set(
    "a an the of on in at with and or to for from by its his her their close up closeup shot macro photo of "
    "young old dark soft warm moody dramatic cinematic light lighting glow glowing background blur depth".split()
)
STYLE = (
    ", photorealistic, cinematic lighting, dramatic shadows, shallow depth of field, 35mm film still, high detail, "
    "moody color grade, no text, no letters, no watermark, no logo"
)
UA = {"User-Agent": "Mozilla/5.0 (ShortsAutopilot media)"}
_used_lock = threading.Lock()


def _log(*a):
    print("   [media]", *a, flush=True)


def _key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:16]


def _get(url, **kw):
    import requests

    kw.setdefault("timeout", 60)
    h = dict(UA)
    h.update(kw.pop("headers", {}) or {})
    last = None
    for i in range(3):
        try:
            r = requests.get(url, headers=h, **kw)
            if r.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {r.status_code}")
            return r
        except Exception as e:
            last = e
            time.sleep(2 + 3 * i)
    raise last


def _is_image(b):
    return len(b) > 20_000 and (b[:3] == b"\xff\xd8\xff" or b[:8] == b"\x89PNG\r\n\x1a\n" or b[8:12] == b"WEBP")


# ----------------------------------------------------------------------------- providers
def cf_image(prompt, seed):
    acc, tok = os.environ.get("CF_ACCOUNT_ID"), os.environ.get("CF_API_TOKEN")
    if not (acc and tok):
        return None
    import requests

    url = f"https://api.cloudflare.com/client/v4/accounts/{acc}/ai/run/@cf/black-forest-labs/flux-1-schnell"
    for i in range(2):
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {tok}"},
                json={"prompt": (prompt + STYLE)[:2000], "steps": 8},  # the API rejects "seed" (400) since 2026-10
                timeout=90,
            )
            if r.status_code == 200:
                j = r.json()
                b64 = (j.get("result") or {}).get("image") or j.get("image")
                if b64:
                    b = base64.b64decode(b64)
                    if _is_image(b):
                        return b
            elif r.status_code in (401, 403):
                _log(f"cloudflare auth error {r.status_code} (check CF_API_TOKEN / CF_ACCOUNT_ID)")
                return None
            else:
                _log(f"cloudflare {r.status_code}: {r.text[:120]}")
        except Exception as e:
            _log(f"cloudflare: {e}")
        time.sleep(3)
    return None


def pollinations_image(prompt, seed):
    if os.environ.get("POLLINATIONS", "1") in ("0", "off", "false"):
        return None
    url = f"https://image.pollinations.ai/prompt/{quote((prompt + STYLE)[:900])}?width={W}&height={H}&model=flux&nologo=true&seed={seed}"
    try:
        r = _get(url, timeout=120)
        if r.status_code == 200 and _is_image(r.content):
            return r.content
        _log(f"pollinations {r.status_code}")
    except Exception as e:
        _log(f"pollinations: {e}")
    return None


def _pexels(path, params):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    try:
        r = _get(f"https://api.pexels.com/{path}", params=params, headers={"Authorization": key}, timeout=30)
        if r.status_code == 200:
            return r.json()
        _log(f"pexels {path} {r.status_code}")
    except Exception as e:
        _log(f"pexels: {e}")
    return None


def pexels_photo(query, used):
    j = _pexels("v1/search", {"query": query, "orientation": "portrait", "per_page": 15})
    for p in (j or {}).get("photos", []):
        if p["id"] in used:
            continue
        src = p.get("src", {})
        u = src.get("original") or src.get("large2x")
        if not u:
            continue
        u = u.split("?")[0] + f"?auto=compress&cs=tinysrgb&fit=crop&w={W}&h={H}"
        try:
            r = _get(u, timeout=60)
            if r.status_code == 200 and _is_image(r.content):
                with _used_lock:
                    used.add(p["id"])
                return r.content, {"by": p.get("photographer", ""), "url": p.get("url", ""), "site": "Pexels"}
        except Exception as e:
            _log(f"pexels photo dl: {e}")
    return None


def pexels_video(query, used, need_s):
    j = _pexels("videos/search", {"query": query, "orientation": "portrait", "size": "medium", "per_page": 15})
    for v in (j or {}).get("videos", []):
        if v["id"] in used or (v.get("duration") or 0) < min(3, need_s):
            continue
        files = [
            f
            for f in v.get("video_files", [])
            if f.get("file_type") == "video/mp4" and f.get("link") and f.get("height") and f.get("width")
        ]
        portrait = [f for f in files if f["height"] >= f["width"]]
        cands = sorted(portrait or files, key=lambda f: (abs(f["height"] - 1920), -(f.get("fps") or 30)))
        for f in cands[:2]:
            if f["height"] > 2600:
                continue  # skip 4K: big download, no gain at 1080p
            try:
                r = _get(f["link"], timeout=180)
                if r.status_code == 200 and len(r.content) > 100_000:
                    with _used_lock:
                        used.add(v["id"])
                    return r.content, {
                        "by": (v.get("user") or {}).get("name", ""),
                        "url": v.get("url", ""),
                        "site": "Pexels",
                    }
            except Exception as e:
                _log(f"pexels video dl: {e}")
    return None


def _pixabay(path, params):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    try:
        r = _get(f"https://pixabay.com/api/{path}", params={"key": key, "safesearch": "true", **params}, timeout=30)
        if r.status_code == 200:
            return r.json()
        _log(f"pixabay {path or 'images'} {r.status_code}")
    except Exception as e:
        _log(f"pixabay: {e}")
    return None


def _relevant(query, tags):
    """Pixabay returns loose matches (a waterfall for "crowded subway train at rush hour"): need enough query words in its tags."""
    q = [w for w in re.findall(r"[a-z]+", query.lower()) if w not in FILLER and len(w) > 2]
    t = re.findall(r"[a-z]+", (tags or "").lower())
    return sum(any(x[:5] == w[:5] for x in t) for w in q) >= max(1, round(len(q) * 0.6))


def pixabay_photo(query, used):
    j = _pixabay("", {"q": query[:100], "image_type": "photo", "orientation": "vertical", "per_page": 20})
    for p in (j or {}).get("hits", []):
        key = f"pb{p['id']}"
        if key in used or not p.get("largeImageURL") or not _relevant(query, p.get("tags")):
            continue
        try:
            r = _get(p["largeImageURL"], timeout=60)
            if r.status_code == 200 and _is_image(r.content):
                with _used_lock:
                    used.add(key)
                return r.content, {"by": p.get("user", ""), "url": p.get("pageURL", ""), "site": "Pixabay"}
        except Exception as e:
            _log(f"pixabay photo dl: {e}")
    return None


def pixabay_video(query, used, need_s):
    j = _pixabay("videos/", {"q": query[:100], "video_type": "film", "per_page": 20})
    hits = (j or {}).get("hits", [])
    hits.sort(
        key=lambda h: (
            0
            if any((v or {}).get("height", 0) > (v or {}).get("width", 1) for v in (h.get("videos") or {}).values())
            else 1
        )
    )
    for h in hits:  # vertical clips first; landscape ones get centre-cropped
        key = f"pbv{h['id']}"
        if key in used or (h.get("duration") or 0) < min(3, need_s) or not _relevant(query, h.get("tags")):
            continue
        vids = h.get("videos") or {}
        big = vids.get("large") or {}
        pick = big if big.get("url") and 0 < (big.get("size") or 0) <= 60_000_000 else (vids.get("medium") or {})
        if not pick.get("url"):
            continue
        try:
            r = _get(pick["url"], timeout=240)
            if r.status_code == 200 and len(r.content) > 100_000:
                with _used_lock:
                    used.add(key)
                return r.content, {"by": h.get("user", ""), "url": h.get("pageURL", ""), "site": "Pixabay"}
        except Exception as e:
            _log(f"pixabay video dl: {e}")
    return None


# ----------------------------------------------------------------------------- preparation
def _grade(img, accent, darken=0.70):
    a = np.asarray(img.convert("RGB"), np.float32)
    lum = a.mean(axis=2, keepdims=True)
    a = lum + (a - lum) * 1.12  # saturation
    a = (a - 128) * 1.08 + 128  # contrast
    a = a * darken + np.array(accent, np.float32) * 0.06  # darken + a touch of the scene colour
    hh, ww = a.shape[:2]
    yy = np.linspace(-1, 1, hh, dtype=np.float32)[:, None]
    xx = np.linspace(-1, 1, ww, dtype=np.float32)[None, :]
    vig = 1 - 0.38 * np.clip((xx**2 * 0.8 + yy**2) - 0.25, 0, 1)
    a *= vig[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8), "RGB")


def _cover(img, w, h):
    s = max(w / img.width, h / img.height)
    im = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    l, t = (im.width - w) // 2, (im.height - h) // 2
    return im.crop((l, t, l + w, t + h))


def prepare_image(raw, accent, out):
    """Square images get a 'hero' layout (sharp square over a blurred copy); tall images fill the frame."""
    img = Image.open(__import__("io").BytesIO(raw)).convert("RGB")
    cw, ch = int(W * KB), int(H * KB)
    if img.width / img.height > 0.72:  # square-ish (FLUX 1024x1024) or landscape
        back = _cover(img, cw // 4, ch // 4).filter(ImageFilter.GaussianBlur(10)).resize((cw, ch), Image.BILINEAR)
        back = Image.fromarray((np.asarray(back, np.float32) * 0.55).astype(np.uint8))
        side = cw
        hero = _cover(img, side, side)
        mask = np.ones((side, side), np.float32)
        f = int(side * 0.16)
        ramp = np.linspace(0, 1, f, dtype=np.float32) ** 1.6
        mask[:f] *= ramp[:, None]
        mask[-f:] *= ramp[::-1][:, None]
        top = int(ch * 0.40 - side / 2)  # hero centred a bit above the middle (where the visuals live)
        canvas = back.copy()
        canvas.paste(hero, (0, top), Image.fromarray((mask * 255).astype(np.uint8), "L"))
        img = canvas
    else:
        img = _cover(img, cw, ch)
    _grade(img, accent).save(out, quality=92)
    return out


def prepare_video(raw_path, out, dur):
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},"
        "eq=brightness=-0.07:contrast=1.06:saturation=1.1,vignette=angle=PI/5,format=yuv420p"
    )
    cmd = [
        FFMPEG,
        "-y",
        "-loglevel",
        "error",
        "-stream_loop",
        "-1",
        "-i",
        raw_path,
        "-t",
        f"{dur + 0.6:.2f}",
        "-an",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-g",
        "30",
        out,
    ]
    subprocess.run(cmd, check=True, timeout=300)
    return out


def _shot_spec(s):
    if isinstance(s, str):
        return ("ai", s)
    if not isinstance(s, dict):
        return None
    for k in ("stock", "ai", "photo"):
        if isinstance(s.get(k), str) and s[k].strip():
            return (k, s[k].strip()[:300])
    return None


def fetch_shot(kind, text, accent, dur, used, seed):
    """-> dict(kind=image|video, path, credit) or None. Cached by content."""
    os.makedirs(CACHE, exist_ok=True)
    k = _key(kind, text, seed)
    for ext, mk in ((".v.mp4", "video"), (".i.jpg", "image")):
        p = os.path.join(CACHE, k + ext)
        if os.path.exists(p) and os.path.getsize(p) > 10_000 and (mk == "image" or _vid_long_enough(p, dur)):
            meta = os.path.join(CACHE, k + ".json")
            cr = json.load(open(meta, encoding="utf-8")) if os.path.exists(meta) else None
            return {"kind": mk, "path": p, "credit": cr}

    def save_credit(cr):
        if cr:
            json.dump(cr, open(os.path.join(CACHE, k + ".json"), "w", encoding="utf-8"))

    if kind == "stock":
        res = pexels_video(text, used, dur) or pixabay_video(text, used, dur)
        if res:
            raw = os.path.join(CACHE, k + ".raw.mp4")
            open(raw, "wb").write(res[0])
            try:
                out = prepare_video(raw, os.path.join(CACHE, k + ".v.mp4"), dur)
                save_credit(res[1])
                return {"kind": "video", "path": out, "credit": res[1]}
            except Exception as e:
                _log(f"video prep failed: {e}")
            finally:
                try:
                    os.remove(raw)
                except OSError:
                    pass
    order = {"stock": ["photo", "ai"], "photo": ["photo", "ai"], "ai": ["ai", "photo"]}[kind]
    for src in order:
        got, cr = None, None
        if src == "photo":
            q = (
                text
                if kind != "ai"
                else " ".join([w for w in re.findall(r"[A-Za-z]+", text) if w.lower() not in FILLER][:4])
            )
            res = pexels_photo(q, used) or pixabay_photo(q, used)
            if res:
                got, cr = res
        else:
            prompt = text if kind == "ai" else f"cinematic photo of {text}"
            got = cf_image(prompt, seed) or pollinations_image(prompt, seed)
        if got:
            try:
                out = prepare_image(got, accent, os.path.join(CACHE, k + ".i.jpg"))
                save_credit(cr)
                return {"kind": "image", "path": out, "credit": cr}
            except Exception as e:
                _log(f"image prep failed: {e}")
    return None


def _vid_long_enough(p, dur):
    try:
        out = subprocess.run([FFMPEG, "-hide_banner", "-i", p], capture_output=True, text=True, timeout=30).stderr
        m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", out)
        return m and (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))) >= dur + 0.3
    except Exception:
        return False


def prepare_episode(ep, scenes, hex2rgb):
    """Fills scenes[i]['shots'] = [{kind, path, t0, t1, kb}] (scene-local seconds). Returns list of credits."""
    if os.environ.get("MEDIA", "auto").lower() in ("off", "0", "false"):
        return []
    have_any = any(os.environ.get(k) for k in ("CF_ACCOUNT_ID", "PEXELS_API_KEY", "PIXABAY_API_KEY")) or os.environ.get(
        "POLLINATIONS", "1"
    ) not in ("0", "off", "false")
    if not have_any:
        return []
    used, jobs = set(), []
    for si, (sc, S) in enumerate(zip(ep["scenes"], scenes)):
        specs = [x for x in (_shot_spec(s) for s in (sc.get("bg") or [])) if x][:3]
        S["shots"] = []
        if not specs:
            continue
        L = S["L"]
        n = len(specs)
        starts = [0.0] + [
            L[min(len(L) - 1, round(j * len(L) / n))][0] - 0.05 if len(L) >= n else S["dur"] * j / n
            for j in range(1, n)
        ]
        starts = sorted(set(round(max(0.0, s), 3) for s in starts))
        for j, (kind, text) in enumerate(specs[: len(starts)]):
            t0 = starts[j]
            t1 = starts[j + 1] if j + 1 < len(starts) else S["dur"] + 0.3
            jobs.append((si, j, kind, text, t0, t1, hex2rgb(sc.get("accent", "#4DA3FF"))))
    if not jobs:
        return []
    t_start = time.time()
    _log(f"fetching {len(jobs)} shots…")

    def run(job):
        si, j, kind, text, t0, t1, acc = job
        seed = int(_key(ep.get("title", ""), si, j), 16) % 2_000_000_000
        try:
            return job, fetch_shot(kind, text, acc, t1 - t0, used, seed)
        except Exception as e:
            _log(f"shot {si + 1}.{j + 1} failed: {e}")
            return job, None

    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(run, jobs))
    credits, ok = [], 0
    for (si, j, kind, text, t0, t1, acc), res in sorted(results, key=lambda r: (r[0][0], r[0][1])):
        if not res:
            continue
        ok += 1
        r = random.Random(f"{si}-{j}")
        z0, z1 = (1.0, 1.09) if r.random() < 0.6 else (1.09, 1.0)
        res.update(t0=t0, t1=t1, kb=(z0, z1, r.uniform(-1, 1), r.uniform(-1, 1)), kind_req=kind)
        scenes[si]["shots"].append(res)
        if res.get("credit") and res["credit"] not in credits:
            credits.append(res["credit"])
    # a scene whose first shot failed but a later one worked: stretch the first available shot to the scene start
    for S in scenes:
        if S.get("shots"):
            S["shots"][0]["t0"] = 0.0
    _log(f"{ok}/{len(jobs)} shots ready in {time.time() - t_start:.0f}s")
    return credits


# ----------------------------------------------------------------------------- per-frame access (inside render workers)
class VideoReader:
    """Sequential frame reader with seek-on-demand (render workers walk frames in order)."""

    def __init__(self, path):
        self.path, self.proc, self.next_i = path, None, None

    def _open(self, i):
        self.close()
        self.proc = subprocess.Popen(
            [
                FFMPEG,
                "-loglevel",
                "error",
                "-ss",
                f"{i / FPS:.3f}",
                "-i",
                self.path,
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{W}x{H}",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self.next_i, self.last = i, None

    def frame(self, i):
        if self.proc is None or self.next_i is None or i < self.next_i - 1 or i > self.next_i + 45:
            self._open(i)
        if i == self.next_i - 1 and self.last is not None:
            return self.last
        while self.next_i <= i:
            buf = self.proc.stdout.read(W * H * 3)
            if len(buf) < W * H * 3:  # ran out (clip shorter than planned): hold the last frame
                return self.last if self.last is not None else np.zeros((H, W, 3), np.uint8)
            self.last = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
            self.next_i += 1
        return self.last

    def close(self):
        if self.proc:
            try:
                self.proc.stdout.close()
                self.proc.kill()
                self.proc.wait(5)
            except Exception:
                pass
        self.proc = None


class MediaBG:
    def __init__(self):
        self.imgs, self.readers = {}, {}

    def _img(self, p):
        if p not in self.imgs:
            if len(self.imgs) > 3:
                self.imgs.pop(next(iter(self.imgs)))  # keep memory small
            self.imgs[p] = Image.open(p).convert("RGB")
        return self.imgs[p]

    def frame(self, S, lt):
        """RGB PIL frame for scene-local time lt, or None when the scene has no media."""
        shots = S.get("shots") or []
        if not shots:
            return None
        sh = shots[0]
        for s in shots:
            if lt >= s["t0"]:
                sh = s
        p = clamp01((lt - sh["t0"]) / max(0.5, sh["t1"] - sh["t0"]))
        punch = 0.045 * math.exp(-max(0, lt - sh["t0"]) * 9)  # small zoom kick on every cut
        if sh["kind"] == "video":
            rd = self.readers.pop(sh["path"], None) or VideoReader(sh["path"])
            self.readers[sh["path"]] = rd  # most recent last
            while len(self.readers) > 2:
                self.readers.pop(next(iter(self.readers))).close()
            arr = rd.frame(int(max(0, lt - sh["t0"]) * FPS))
            img = Image.fromarray(arr, "RGB")
            z = 1.0 + 0.04 * p + punch
            if z > 1.001:
                img = _zoom(img, z, 0, 0)
            return img
        z0, z1, px, py = sh["kb"]
        e = p * p * (3 - 2 * p)
        z = (z0 + (z1 - z0) * e) + punch
        im = self._img(sh["path"])
        return _kb(im, z, px * 40 * (e - 0.5), py * 60 * (e - 0.5))


def clamp01(x):
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def _kb(im, z, dx, dy):
    """Crop a W x H window out of the (KB-larger) prepared image at zoom z (1 = whole prepared width / KB)."""
    sw, sh = im.width / KB / z, im.height / KB / z
    cx, cy = im.width / 2 + dx, im.height / 2 + dy
    cx = min(max(cx, sw / 2), im.width - sw / 2)
    cy = min(max(cy, sh / 2), im.height - sh / 2)
    return im.resize((W, H), Image.BILINEAR, box=(cx - sw / 2, cy - sh / 2, cx + sw / 2, cy + sh / 2))


def _zoom(img, z, dx, dy):
    sw, sh = img.width / z, img.height / z
    cx, cy = img.width / 2 + dx, img.height / 2 + dy
    return img.resize((W, H), Image.BILINEAR, box=(cx - sw / 2, cy - sh / 2, cx + sw / 2, cy + sh / 2))
