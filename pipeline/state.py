"""Tiny JSON state store: one record per video, plus topic history."""

import json
import os
import threading

from .util import DATA, now_local

PATH = os.path.join(DATA, "state.json")
_lock = threading.Lock()


def load():
    if not os.path.exists(PATH):
        return {"items": {}, "history": [], "tg_offset": None, "last_daily": None, "seq": 0}
    with open(PATH, encoding="utf-8") as fh:
        return json.load(fh)


def save(st):
    os.makedirs(DATA, exist_ok=True)
    tmp = PATH + ".tmp"
    with _lock:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, PATH)


def new_item(st, **fields):
    st["seq"] = st.get("seq", 0) + 1
    iid = f"{now_local():%m%d}-{st['seq']}"
    st["items"][iid] = {"id": iid, "created": now_local().isoformat(timespec="seconds"), **fields}
    return st["items"][iid]


def set_status(st, iid, status, **fields):
    it = st["items"][iid]
    it.update(status=status, updated=now_local().isoformat(timespec="seconds"), **fields)
    save(st)
    return it
