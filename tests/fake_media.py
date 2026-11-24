"""Replace network media with local files so renders can be tested offline."""

import glob
import itertools
import os

import media as MEDIA


def install(folder):
    vids = itertools.cycle(sorted(glob.glob(os.path.join(folder, "*.mp4"))))
    imgs = itertools.cycle(sorted(glob.glob(os.path.join(folder, "*.jpg"))))

    def fake(kind, text, accent, dur, used, seed):
        os.makedirs(MEDIA.CACHE, exist_ok=True)
        k = MEDIA._key("fake", kind, text, seed)
        if kind == "stock":
            out = os.path.join(MEDIA.CACHE, k + ".v.mp4")
            if not os.path.exists(out):
                MEDIA.prepare_video(next(vids), out, dur)
            return {
                "kind": "video",
                "path": out,
                "credit": {"by": "Intel sample-videos (test)", "url": "", "site": "test"},
            }
        out = os.path.join(MEDIA.CACHE, k + ".i.jpg")
        MEDIA.prepare_image(open(next(imgs), "rb").read(), accent, out)
        return {"kind": "image", "path": out, "credit": None}

    MEDIA.fetch_shot = fake
    os.environ.setdefault("POLLINATIONS", "1")
