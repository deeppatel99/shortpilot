"""One-command move to the cloud (run on your PC):   python autopilot.py cloud-setup --channel <name>

Does, in order (safe to re-run; finished steps are skipped):
 1. checks git / gh / npx           4. asks for the keys that are missing (saved in your local .env only)
 2. GitHub login (browser)          5. copies keys into GitHub repo secrets
 3. private repo + pushes the code  6. deploys the Cloudflare Worker + sets the Telegram webhook, 7. test run
Nothing secret is ever committed: .env, models/, data/ are git-ignored and checked before every push."""

import json
import os
import re
import secrets as pysecrets
import shutil
import subprocess
import tempfile

from .publish import _update_env
from .util import CHANNEL, ROOT, env, load_config, load_env

CFG = load_config()
CH = CFG["channel"]
CLOUD_CFG = CFG.get("cloud") or {}  # optional block in channel.json
REPO_NAME = CLOUD_CFG.get("repo") or f"{CHANNEL}-autopilot"
WORKER = CLOUD_CFG.get("worker") or f"{CHANNEL}-webhook"
REMOTE = f"cloud-{CHANNEL}"  # one git remote per channel: the same code can serve several channels
SECRET_KEYS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "YOUTUBE_API_KEY",
    "IG_USER_ID",
    "IG_ACCESS_TOKEN",
    "CF_ACCOUNT_ID",
    "CF_API_TOKEN",
    "PIXABAY_API_KEY",
    "PEXELS_API_KEY",
    "AZURE_SPEECH_KEY",
    "AZURE_SPEECH_REGION",
    "SARVAM_API_KEY",
    "FB_PAGE_ID",
    "FB_PAGE_TOKEN",
]
MUST_IGNORE = [
    ".env",
    ".venv/",
    "models/",
    "data/",
    "state/",
    "engine/.tts_cache/",
    "__pycache__/",
    "*.pyc",
    "*.mp4",
    "client_secret*.json",
]


def say(m):
    print(m, flush=True)


def step(n, m):
    print(f"\n=== {n}. {m} ===", flush=True)


def which(name):
    for c in (name, name + ".exe", name + ".cmd"):
        p = shutil.which(c)
        if p:
            return p
    return None


def run(cmd, check=True, capture=True, inp=None, cwd=ROOT, **kw):
    r = subprocess.run(
        cmd, cwd=cwd, text=True, input=inp, capture_output=capture, encoding="utf-8", errors="replace", **kw
    )
    if check and r.returncode != 0:
        raise SystemExit(f"\n❌ Command failed: {' '.join(cmd[:4])}\n{(r.stderr or r.stdout or '').strip()[:800]}")
    return r


def ask(key, how, required=False):
    v = env(key)
    if v:
        return v
    say(f"\n🔑 {key} is needed.\n{how}")
    while True:
        v = input(f"Paste {key}{' (or press Enter to skip)' if not required else ''}: ").strip()
        if v or not required:
            break
    if v:
        _update_env(key, v)
        os.environ[key] = v
    return v


def push_secret(name, value):
    """Set one GitHub repo secret using your gh login (PC side). Returns True on success."""
    gh = which("gh")
    if not gh:
        return False
    owner = run([gh, "api", "user", "--jq", ".login"], check=False).stdout.strip()
    if not owner:
        return False
    r = run([gh, "secret", "set", name, "-R", f"{owner}/{REPO_NAME}"], inp=value, check=False)
    return r.returncode == 0


def _utc_cron(hhmm, plus=0):
    """Local "HH:MM" (schedule.utc_offset) + minutes -> a daily cron line in UTC."""
    from .util import UTC_OFFSET_MIN

    h, m = map(int, hhmm.split(":"))
    t = (h * 60 + m + plus - UTC_OFFSET_MIN) % 1440
    return f"{t % 60} {t // 60} * * *"


def crons():
    """(daily scout cron, publish cron), UTC. From channel.json: cloud.crons {"daily", "publish"} when given, else
    schedule.scout_at and 3 minutes after schedule.publish_at (or after every schedule.slots time)."""
    c = CLOUD_CFG.get("crons") or {}
    sch = CFG["schedule"]
    slots = sch.get("slots") or [sch["publish_at"]]
    pub = [_utc_cron(x, 3).split() for x in slots]
    publish = f"{pub[0][0]} {','.join(dict.fromkeys(x[1] for x in pub))} * * *"  # slots share the minute
    return c.get("daily") or _utc_cron(sch["scout_at"]), c.get("publish") or publish


def worker_vars(daily_cron):
    """Plain-text Worker variables: the brand texts for the Worker's home / privacy / terms pages."""
    site = CLOUD_CFG.get("site") or {}
    handle = (CH.get("handle") or "").lstrip("@")
    return {
        "BRAND_NAME": CH["name"],
        "PUBLISHER_NAME": site.get("publisher") or f"{CH['name'].replace(' ', '')} Publisher",
        "TAGLINE": site.get("tagline") or CH.get("tagline", ""),
        "ABOUT": site.get("about") or "",
        "INSTAGRAM": site.get("instagram") or handle,
        "CONTACT_EMAIL": site.get("contact_email") or "",
        "DAILY_CRON": daily_cron,
    }


def deploy_worker(npx, repo, tg_secret):
    """Deploys cloud/worker.js as the Telegram webhook Worker and sets its secrets. Returns the Worker URL."""
    wr = [npx, "--yes", "wrangler@4"]
    # Our CF_API_TOKEN is a Workers-AI-only token. wrangler picks it up from the environment AND from a .env file in
    # its working folder, then fails with "No access". So: run wrangler from an empty temp folder, without any
    # Cloudflare token variables -> it uses your browser (OAuth) login, which can deploy Workers.
    wenv = {
        k: v
        for k, v in os.environ.items()
        if k.upper()
        not in (
            "CF_API_TOKEN",
            "CF_ACCOUNT_ID",
            "CLOUDFLARE_API_TOKEN",
            "CLOUDFLARE_ACCOUNT_ID",
            "CF_API_KEY",
            "CF_EMAIL",
            "CLOUDFLARE_API_KEY",
            "CLOUDFLARE_EMAIL",
        )
    }
    # With several channels on one PC a deploy can land in the wrong Cloudflare account (wrangler keeps ONE global
    # login). Three optional guards in channel.json -> cloud:
    #   wrangler_home          this channel's own wrangler login folder, e.g. "~/.mychannel-wrangler"
    #   pin_account            true = write CF_ACCOUNT_ID into wrangler.toml and refuse any other account
    #   cloudflare_login_hint  text that must appear in `wrangler whoami` (part of the account's email / name)
    if CLOUD_CFG.get("wrangler_home"):
        wenv["XDG_CONFIG_HOME"] = os.path.expanduser(CLOUD_CFG["wrangler_home"])
    acct = (env("CF_ACCOUNT_ID") or "").strip() if CLOUD_CFG.get("pin_account") else ""
    if CLOUD_CFG.get("pin_account") and not acct:
        raise SystemExit("❌ CF_ACCOUNT_ID is missing from .env (channel.json -> cloud.pin_account is on).")
    hint = (CLOUD_CFG.get("cloudflare_login_hint") or "").lower()
    wdir = tempfile.mkdtemp(prefix=f"{CHANNEL}_worker_")
    shutil.copyfile(os.path.join(ROOT, "cloud", "worker.js"), os.path.join(wdir, "worker.js"))
    # Cron triggers live here (not in GitHub): GitHub's own schedule runs late or skips. Times are UTC.
    # cloud/worker.js scheduled() compares the firing cron with DAILY_CRON to tell the scout from a publish run.
    daily, publish = crons()
    with open(os.path.join(wdir, "wrangler.toml"), "w", encoding="utf-8") as fh:
        fh.write(
            f'name = "{WORKER}"\nmain = "worker.js"\ncompatibility_date = "2025-01-01"\n'
            + (f'account_id = "{acct}"\n' if acct else "")
            + f"\n[triggers]\ncrons = {json.dumps([daily, publish])}\n\n[vars]\n"
            + "".join(f"{k} = {json.dumps(v, ensure_ascii=False)}\n" for k, v in worker_vars(daily).items())
        )
    W = lambda args, **kw: run(wr + args, check=False, env=wenv, cwd=wdir, **kw)
    for attempt in range(4):
        who = W(["whoami"])
        wtxt = (who.stdout or "") + (who.stderr or "")
        say("\n".join(l for l in wtxt.splitlines() if "@" in l or "Account" in l)[:600])
        token_login = "api token" in wtxt.lower()
        logged_in = who.returncode == 0 and "not authenticated" not in wtxt.lower() and not token_login
        if logged_in and (not acct or acct in wtxt) and (not hint or hint in wtxt.lower()):
            break
        if attempt == 3:
            raise SystemExit(
                "\n❌ Wrong Cloudflare account 3 times. Check the Cloudflare login in your browser and run this again."
            )
        if logged_in or token_login:
            W(["logout"])
        if logged_in:
            say(
                f"❌ This is not the Cloudflare account for {CH['name']} — logged out.\n"
                "   In your browser: dash.cloudflare.com -> Log out -> log in with this channel's account."
            )
            input("   Done? Press Enter: ")
        say("A browser opens -> Cloudflare login -> Allow.")
        W(["login"], capture=False)
    dep = W(["deploy", "worker.js", "--name", WORKER, "--compatibility-date", "2025-01-01"])
    out = (dep.stdout or "") + (dep.stderr or "")
    print(out[-1500:])
    m = re.search(r"https://[a-z0-9.-]+\.workers\.dev", out)
    if dep.returncode != 0 or not m:
        raise SystemExit(
            "\n❌ Worker deploy failed (output above). Every other step is done — after fixing it, just run this command again."
        )
    wurl = m.group(0)
    say(f"✅ Worker live: {wurl}")
    for k, v in (
        ("TG_BOT_TOKEN", env("TELEGRAM_BOT_TOKEN")),
        ("TG_CHAT_ID", env("TELEGRAM_CHAT_ID")),
        ("TG_SECRET", tg_secret),
        ("GH_TOKEN", env("GH_PAT")),
        ("GH_REPO", repo),
    ):
        r = W(["secret", "put", k, "--name", WORKER], inp=v + "\n")
        say(
            ("✅" if r.returncode == 0 else "❌")
            + f" worker secret {k}"
            + ("" if r.returncode == 0 else f": {(r.stderr or r.stdout)[-300:]}")
        )
        if r.returncode != 0:
            raise SystemExit("Could not set the Worker secret — see the output above.")
    return wurl


def redeploy_worker():
    """Update only the Cloudflare Worker (after cloud/worker.js changed):  python autopilot.py worker-deploy"""
    load_env()
    npx, gh = which("npx"), which("gh")
    if not (npx and gh):
        raise SystemExit("npx / gh not found (needs Node.js + the GitHub CLI).")
    owner = run([gh, "api", "user", "--jq", ".login"]).stdout.strip()
    repo = f"{owner}/{REPO_NAME}"
    tg_secret = env("TG_WEBHOOK_SECRET")
    if not tg_secret:
        raise SystemExit("TG_WEBHOOK_SECRET is missing from .env — run cloud-setup first.")
    step(1, "Worker update")
    wurl = deploy_worker(npx, repo, tg_secret)
    from .cloud import webhook_info

    info = webhook_info().get("result", {})
    say(f"webhook: {info.get('url')}  (same URL = nothing else to do)")
    if info.get("url") != wurl:
        from .cloud import set_webhook

        say(json.dumps(set_webhook(wurl, tg_secret)))
    say("\n✅ Worker updated. Tap any button in Telegram — the reply should be instant.")


def main():
    load_env()
    step(1, "Tools check")
    git, gh, npx = which("git"), which("gh"), which("npx")
    missing = [n for n, p in (("Git", git), ("GitHub CLI", gh), ("Node.js (npx)", npx)) if not p]
    if missing:
        say("Install these, then open a NEW terminal and run this again:")
        win = os.name == "nt"
        if not git:
            say("   winget install --id Git.Git -e" if win else "   git:  https://git-scm.com/downloads")
        if not gh:
            say("   winget install --id GitHub.cli -e" if win else "   gh:   https://cli.github.com")
        if not npx:
            say("   winget install --id OpenJS.NodeJS.LTS -e" if win else "   node: https://nodejs.org (LTS)")
        raise SystemExit(1)
    say("✅ found git, gh, npx")

    step(2, "GitHub login")
    st = run([gh, "auth", "status"], check=False)
    if st.returncode != 0:
        say("A browser opens -> GitHub login -> Authorize.")
        run([gh, "auth", "login", "-h", "github.com", "-p", "https", "-w", "-s", "repo,workflow"], capture=False)
    elif "workflow" not in (st.stdout + st.stderr):
        say("Adding the workflow permission (a browser opens)…")
        run([gh, "auth", "refresh", "-h", "github.com", "-s", "workflow"], capture=False, check=False)
    run([gh, "auth", "setup-git"], check=False)
    owner = run([gh, "api", "user", "--jq", ".login"]).stdout.strip()
    repo = f"{owner}/{REPO_NAME}"
    say(f"✅ logged in as {owner}")

    step(3, f"Private repo {repo} + code push")
    if run([gh, "repo", "view", repo], check=False).returncode != 0:
        run([gh, "repo", "create", repo, "--private", "--description", f"{CH['name']} · ShortPilot bot"])
        say("✅ repo created (private)")
    gi = os.path.join(ROOT, ".gitignore")
    have = open(gi, encoding="utf-8").read().splitlines() if os.path.exists(gi) else []
    add = [x for x in MUST_IGNORE if x not in have]
    if add:
        with open(gi, "a", encoding="utf-8") as fh:
            fh.write(("\n" if have and have[-1] else "") + "\n".join(add) + "\n")
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        run([git, "init", "-b", "main"])
    if not run([git, "config", "user.email"], check=False).stdout.strip():
        run([git, "config", "user.name", owner])
        run([git, "config", "user.email", f"{owner}@users.noreply.github.com"])
    run([git, "add", "-A"])
    staged = run([git, "diff", "--cached", "--name-only"]).stdout.split()
    tracked = run([git, "ls-files"]).stdout.split()
    bad = [
        f
        for f in set(staged + tracked)
        if os.path.basename(f) == ".env"
        or os.path.basename(f).startswith("client_secret")
        or f.startswith(("models/", "data/", ".venv/"))
        or "/data/" in f
        or f.endswith(".mp4")
    ]
    if bad:
        raise SystemExit(f"❌ These files must never go to GitHub, push stopped: {bad[:5]}")
    big = [
        f
        for f in staged
        if os.path.isfile(os.path.join(ROOT, f)) and os.path.getsize(os.path.join(ROOT, f)) > 50 * 2**20
    ]
    if big:
        raise SystemExit(f"❌ Files over 50 MB: {big[:5]} — add them to .gitignore, then run this again.")
    if staged:
        run([git, "commit", "-qm", f"{CHANNEL}: cloud version"])
    remotes = run([git, "remote"]).stdout.split()
    url = f"https://github.com/{repo}.git"
    run([git, "remote", "set-url" if REMOTE in remotes else "add", REMOTE, url])
    run([git, "push", REMOTE, "HEAD:main"], capture=False)
    # the workflow reads this to know which channels/<slug>/ pack this repo runs
    run([gh, "variable", "set", "AUTOPILOT_CHANNEL", "--body", CHANNEL, "-R", repo])
    say("✅ code is on GitHub (no secrets were pushed)")

    step(4, "Keys")
    if not env("CLAUDE_CODE_OAUTH_TOKEN"):
        say(
            "Creating a cloud token for your Claude plan. A browser opens -> log in -> the token is printed (sk-ant-oat…)."
        )
        claude = which("claude") or env("CLAUDE_BIN")
        if claude:
            run([claude, "setup-token"], capture=False, check=False)
        else:
            say("claude command not found — run `claude setup-token` in a new terminal.")
    ask(
        "CLAUDE_CODE_OAUTH_TOKEN",
        "Copy the token `claude setup-token` printed above (it starts with sk-ant-oat…).",
        required=True,
    )
    ask("CF_ACCOUNT_ID", "dash.cloudflare.com -> left menu 'Workers & Pages' -> right side 'Account ID' copy.")
    if env("CF_ACCOUNT_ID"):
        ask(
            "CF_API_TOKEN",
            "dash.cloudflare.com/profile/api-tokens -> Create Token -> template 'Workers AI' -> Use template -> Continue -> Create -> token copy.",
        )
    ask(
        "PIXABAY_API_KEY",
        "pixabay.com -> Join (free) -> log in -> open pixabay.com/api/docs/ -> copy the green key next to 'key' under 'Parameters'.",
    )
    ask(
        "GH_PAT",
        (
            "github.com/settings/personal-access-tokens/new  (Fine-grained token)\n"
            f"   Name: {CHANNEL}-bot · Expiration: 1 year · Repository access: Only select -> {REPO_NAME}\n"
            "   Permissions -> Repository: Contents = Read and write, Secrets = Read and write -> Generate -> copy."
        ),
        required=True,
    )

    step(5, "GitHub secrets")
    vals = {k: env(k) for k in SECRET_KEYS if env(k)}
    vals["GH_SECRETS_TOKEN"] = env("GH_PAT")
    fd, tmp = tempfile.mkstemp(suffix=".env")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for k, v in vals.items():
                fh.write(f"{k}={v}\n")
        run([gh, "secret", "set", "-f", tmp, "-R", repo])
    finally:
        os.remove(tmp)
    say(f"✅ {len(vals)} secrets set: " + ", ".join(vals))
    for k in ("CF_ACCOUNT_ID", "PIXABAY_API_KEY"):
        if not env(k):
            say(f"   (note: no {k} — videos will have fewer AI images / stock clips)")

    step(6, "Cloudflare Worker (Telegram buttons -> GitHub)")
    tg_secret = env("TG_WEBHOOK_SECRET") or pysecrets.token_urlsafe(24)
    _update_env("TG_WEBHOOK_SECRET", tg_secret)
    wurl = deploy_worker(npx, repo, tg_secret)
    _update_env("WORKER_URL", wurl)

    step(7, "Telegram webhook + test")
    from .cloud import set_webhook, webhook_info

    say(json.dumps(set_webhook(wurl, tg_secret)))
    info = webhook_info().get("result", {})
    say(f"webhook: {info.get('url')}  pending: {info.get('pending_update_count')}")
    run([gh, "workflow", "run", "bot.yml", "-R", repo, "-f", "task=status"], check=False)
    say(
        "\n🎉 Done! '☁️ Cloud status' should arrive in Telegram within 1–3 minutes.\n"
        f"   Runs: https://github.com/{repo}/actions\n"
        "   Keep the PC bot stopped now (Windows task: powershell -ExecutionPolicy Bypass -File .\\install_task.ps1 -Remove).\n"
        "   Back to PC mode: python autopilot.py telegram-webhook --delete"
    )


if __name__ == "__main__":
    main()
