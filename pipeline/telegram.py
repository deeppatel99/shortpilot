"""Tiny Telegram Bot API client (approvals with inline buttons). Only talks to TELEGRAM_CHAT_ID."""

import json
import os
import subprocess
from contextlib import ExitStack

from .util import env, http, log

TG_MAX = 49 * 1024 * 1024  # Bot API upload limit is 50 MB


class Telegram:
    def __init__(self):
        self.token = env("TELEGRAM_BOT_TOKEN", required=True)
        self.chat = str(env("TELEGRAM_CHAT_ID", "") or "")
        self.base = f"https://api.telegram.org/bot{self.token}"

    def _call(self, method, files=None, http_timeout=60, **params):
        if files:
            data = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()}
            r = http("POST", f"{self.base}/{method}", data=data, files=files, timeout=http_timeout)
        else:
            r = http("POST", f"{self.base}/{method}", json=params, timeout=http_timeout)
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError(f"Telegram {method}: {j}")
        return j["result"]

    @staticmethod
    def kb(rows):
        """rows = [[("✅ Approve", "approve:ID"), ...], ...]"""
        return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}

    def send(self, text, buttons=None, html=False):
        chunks = [text[i : i + 3900] for i in range(0, len(text), 3900)] or [""]
        res = None
        for i, c in enumerate(chunks):
            p = {"chat_id": self.chat, "text": c, "disable_web_page_preview": True}
            if html:  # caller keeps the text short enough to be one chunk, or tags would split
                p["parse_mode"] = "HTML"
            if buttons and i == len(chunks) - 1:
                p["reply_markup"] = self.kb(buttons)
            res = self._call("sendMessage", **p)
        return res

    def send_video(self, path, caption="", buttons=None, cover=None):
        if os.path.getsize(path) > TG_MAX:
            # Bot API rejects uploads > 50 MB: send a 720p preview, the original stays for publishing.
            # ponytail: 2 Mbps cap fits ~3 min under the limit; probe duration if longer videos appear.
            small = os.path.splitext(path)[0] + "_tg.mp4"
            subprocess.run(
                [os.environ.get("FFMPEG", "ffmpeg"), "-y", "-loglevel", "error", "-i", path, "-vf", "scale=720:-2",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "26", "-maxrate", "2M", "-bufsize", "4M",
                 "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", small],
                check=True, timeout=600,
            )  # fmt: skip
            try:
                return self.send_video(small, caption, buttons, cover)
            finally:
                os.remove(small)
        p = {"chat_id": self.chat, "caption": caption[:1000], "supports_streaming": True}
        if buttons:
            p["reply_markup"] = self.kb(buttons)
        with ExitStack() as stack:
            files = {"video": (os.path.basename(path), stack.enter_context(open(path, "rb")), "video/mp4")}
            if cover and os.path.exists(cover):
                files["thumbnail"] = (os.path.basename(cover), stack.enter_context(open(cover, "rb")), "image/jpeg")
            return self._call("sendVideo", files=files, http_timeout=300, **p)

    def send_document(self, path, caption=""):
        with open(path, "rb") as fh:
            return self._call(
                "sendDocument",
                files={"document": (os.path.basename(path), fh)},
                http_timeout=300,
                chat_id=self.chat,
                caption=caption[:1000],
            )

    def answer(self, cb_id, text=""):
        try:
            self._call("answerCallbackQuery", callback_query_id=cb_id, text=text[:180])
        except Exception as e:
            log.warning(f"answerCallbackQuery: {e}")

    def clear_buttons(self, message):
        try:
            self._call(
                "editMessageReplyMarkup",
                chat_id=message["chat"]["id"],
                message_id=message["message_id"],
                reply_markup={"inline_keyboard": []},
            )
        except Exception:
            pass

    def updates(self, offset=None, timeout=25):
        p = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset:
            p["offset"] = offset
        return self._call("getUpdates", http_timeout=timeout + 15, **p)

    def is_me(self, update):
        msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
        return str(msg.get("chat", {}).get("id")) == self.chat
