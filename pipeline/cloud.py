"""GitHub Actions helpers: keep videos as GitHub Release assets between runs, update repo secrets, Telegram webhook."""

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone

from .util import env, http, load_config, log


def _gh(*args, check=True, token=None, timeout=600):
    e = dict(os.environ)
    if token:
        e["GH_TOKEN"] = token
    r = subprocess.run(["gh", *args], capture_output=True, text=True, env=e, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r


def _repo():
    return os.environ.get("GITHUB_REPOSITORY", "")


def store_video(iid, *paths):
    """Upload the rendered video (+cover/credits) as assets of a pre-release tagged vid-<iid> in this (private) repo."""
    files = [p for p in paths if p and os.path.exists(p)]
    if not files:
        return
    tag = f"vid-{iid}"
    r = _gh("release", "view", tag, "--repo", _repo(), check=False)
    if r.returncode == 0:
        _gh("release", "upload", tag, *files, "--clobber", "--repo", _repo())
    else:
        _gh(
            "release",
            "create",
            tag,
            *files,
            "--prerelease",
            "--title",
            f"#{iid}",
            "--notes",
            f"{load_config()['channel']['name']} render (auto)",
            "--repo",
            _repo(),
        )
    log.info(f"stored {len(files)} file(s) in release {tag}")


def fetch_video(iid, dest_dir):
    os.makedirs(dest_dir, exist_ok=True)
    _gh("release", "download", f"vid-{iid}", "--dir", dest_dir, "--clobber", "--repo", _repo())


def cleanup_releases(days=21):
    """Delete render releases older than N days (keeps the repo small)."""
    r = _gh("release", "list", "--repo", _repo(), "--limit", "200", "--json", "tagName,createdAt", check=False)
    if r.returncode != 0:
        return
    cut = datetime.now(timezone.utc) - timedelta(days=days)
    for x in json.loads(r.stdout or "[]"):
        if not x["tagName"].startswith("vid-"):
            continue
        if datetime.fromisoformat(x["createdAt"].replace("Z", "+00:00")) < cut:
            _gh("release", "delete", x["tagName"], "--yes", "--cleanup-tag", "--repo", _repo(), check=False)


def set_secret(name, value):
    """Needs GH_SECRETS_TOKEN (fine-grained PAT on this repo with 'Secrets: Read and write')."""
    tok = env("GH_SECRETS_TOKEN")
    if not tok:
        log.warning(f"GH_SECRETS_TOKEN not set -> could not save {name} as a repo secret")
        return False
    _gh("secret", "set", name, "--body", value, "--repo", _repo(), token=tok)
    return True


def set_webhook(url, secret):
    tok = env("TELEGRAM_BOT_TOKEN", required=True)
    r = http(
        "POST",
        f"https://api.telegram.org/bot{tok}/setWebhook",
        json={
            "url": url,
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": False,
        },
    )
    return r.json()


def delete_webhook():
    tok = env("TELEGRAM_BOT_TOKEN", required=True)
    return http("POST", f"https://api.telegram.org/bot{tok}/deleteWebhook").json()


def webhook_info():
    tok = env("TELEGRAM_BOT_TOKEN", required=True)
    return http("GET", f"https://api.telegram.org/bot{tok}/getWebhookInfo").json()
