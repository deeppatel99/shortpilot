"""Decide which heavy setup steps a workflow run needs (saves GitHub Actions minutes).
Prints  llm=true|false  and  render=true|false  for $GITHUB_OUTPUT."""

import json
import os
import sys

name = os.environ.get("GITHUB_EVENT_NAME", "")
try:
    with open(os.environ.get("GITHUB_EVENT_PATH", ""), encoding="utf-8") as fh:
        ev = json.load(fh)
except Exception:
    ev = {}
payload = ev.get("client_payload") or {}
llm = render = cron_daily = False
if name == "repository_dispatch" and "update" not in payload:
    llm = cron_daily = payload.get("task") == "daily"  # Cloudflare cron: morning scout + script
elif name == "workflow_dispatch":
    llm = (ev.get("inputs") or {}).get("task") == "daily"
elif name == "repository_dispatch":
    u = (ev.get("client_payload") or {}).get("update") or {}
    data = (u.get("callback_query") or {}).get("data") or ""
    action = data.split(":", 1)[0]
    text = ((u.get("message") or {}).get("text") or "").strip()
    if action:
        render = action in ("approve", "rerender")
        llm = action == "skip"
    elif text:
        cmd = text.split()[0].lower() if text.startswith("/") else ""
        if cmd.startswith("/render"):
            render = True
        elif cmd in ("/status", "/stats", "/help", "/start"):
            pass
        else:
            llm = True  # /new, /daily, or a feedback reply
try:  # auto mode renders right after writing, in the same run (publish.auto: true = always, "cron" = the scheduled run)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pipeline import channel

    auto = channel.config()["publish"].get("auto")
    render = render or (llm and auto is True) or (cron_daily and auto == "cron")
except Exception:
    pass
print(f"llm={'true' if llm else 'false'}")
print(f"render={'true' if render else 'false'}")
