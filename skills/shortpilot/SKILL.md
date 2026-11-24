---
name: shortpilot
description: Make, set up and run automated faceless short videos (YouTube Shorts, Instagram Reels, Facebook Reels) with ShortPilot. Use when the user wants to make a short or reel about a topic, start a faceless channel, configure a niche/language/voice/schedule, connect Telegram/YouTube/Instagram, move the bot to the cloud, or debug a failed render or publish.
---

# ShortPilot

ShortPilot is a Python project. It turns a topic into a researched script (episode JSON), renders an animated
9:16 MP4, and publishes it after the user approves in Telegram. You drive it with `autopilot.py`. Always run
commands from the ShortPilot project root, the folder that contains `autopilot.py`.

## 0. Find or get the project

1. If the current folder (or a parent) has `autopilot.py` and `channels/`, use it.
2. Otherwise ask where to put it, then run `git clone https://github.com/deeppatel99/shortpilot` and `cd` into it.

Python is `.venv/bin/python` (macOS/Linux) or `.venv\Scripts\python.exe` (Windows). Call it `PY` below.
If `.venv` is missing, run the setup first (step 1).

## 1. Install (once)

- macOS/Linux: `bash setup.sh`. Windows: `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.
- Then run `PY autopilot.py check` and fix every ❌ it shows before going further.
- Smoke test without keys: `PY autopilot.py demo`. Report the path of the MP4 it prints.

## 2. Create a channel

Ask for what's missing: the **niche** (what the videos are about), the **audience**, the **language** and the
**timezone**. Then:

```bash
PY autopilot.py new-channel <slug>
```

Edit `channels/<slug>/channel.json`:
- `channel.name`, `handle`, `brand`, `tagline`, `niche`, `audience`
- `content.language`: a BCP-47 code (`en-US`, `es-MX`, `pt-BR`, `hi-IN`, `ja-JP`…)
- `content.categories`: 3–6 `{"id": "one-line description"}` entries that fit the niche. Also set
  `evergreen_flow`, `series` and `cta`, translated
- `schedule.utc_offset`, `scout_at`, `publish_at`
- `voice.voice`: a Kokoro voice for that language (see `docs/CONFIGURATION.md#languages-and-voices`). Use
  `voice.tts: "azure"` for languages Kokoro lacks
- `channel.stage.labels` and `render.cast_names`: translate them for non-English channels
- `trends.geo` and `trends.news_queries`: the market and 3–6 searches that fit the niche

Keep the JSON valid. Don't touch `prompts/*.md` unless the user asks for a different content style.

If more than one channel exists, add `--channel <slug>` to every command.

## 3. Make a video

```bash
PY autopilot.py make --topic "<topic>" [--angle "<angle>"] [--hook "<first line>"]
```

This writes and validates `channels/<slug>/episodes/<name>.json`, then renders
`channels/<slug>/data/videos/<name>.mp4`. Rendering takes 1–6 minutes, so run it in the background if you can.

- To review a script before rendering, use `PY autopilot.py write --topic "..."`, show the user the scenes, then
  run `PY autopilot.py render <episode.json>`.
- To edit a script, change the episode JSON (keep `tts` spoken-form and `cap` on-screen form in step), then render
  again. Visual types and fields are listed in `docs/ARCHITECTURE.md#visual-types`.
- Topic ideas: `PY autopilot.py pick -n 5` (AI-ranked from today's trends).

After a render, report the MP4 path, the duration, and the title/description from the episode JSON.

## 4. Approvals and publishing

Follow `docs/QUICKSTART.md` (Telegram) and `docs/SETUP.md` (YouTube, Instagram, Facebook). Keys go in
`channels/<slug>/.env`. **Never print, commit or paste the contents of `.env` or `client_secret*.json`.**
The user has to do the browser logins (`youtube-auth`, Meta token) themselves. Tell them exactly what to click.

- 24/7 on this machine: `./run_serve.sh` (Windows `run_serve.bat`)
- Cloud, so the PC can be off: `PY autopilot.py cloud-setup` (interactive, see `docs/CLOUD.md`). Before running
  it, confirm with the user, because it creates a GitHub repo and a Cloudflare Worker on their accounts.

Publishing is outward-facing. Only publish (`publish <id> --now`, or turning on `publish.auto`) when the user
explicitly asks.

## 5. Troubleshooting

| Symptom | Look at |
|---|---|
| Anything | `PY autopilot.py check` |
| Render failed | `channels/<slug>/data/logs/render_<id>.log`, or run `PY autopilot.py render <episode.json>` to see the error |
| Validation errors | The writer repairs twice. If it still fails, fix the episode JSON by hand using the error text |
| Chromium missing | `PY -m playwright install chromium`, or set `render.engine: "classic"` |
| Script never comes | `claude -p "hi"` works? Otherwise set `GEMINI_API_KEY` and `llm.fallback: "gemini"` |
| Video too long/short | `CHARS_PER_SEC` in `pipeline/validate.py` for the language |
| Bot logs | `channels/<slug>/data/logs/autopilot.log` |

More: `docs/OPERATIONS.md`.

## Ground rules

- Work inside the project. Don't install global packages beyond what the setup scripts do.
- Scripts must cite real sources. Don't invent facts or URLs for an episode.
- Run the offline tests after code changes: `PY tests/test_units.py` (all `tests/test_*.py` before a PR).
