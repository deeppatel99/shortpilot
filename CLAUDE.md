# ShortPilot: notes for coding agents

Automated faceless short videos: trends → script (episode JSON) → validate → render 9:16 MP4 → publish.
User docs are in `docs/`. The Claude Code skill for *using* ShortPilot is `skills/shortpilot/SKILL.md`.

## Layout

- `autopilot.py`: the CLI and the `Autopilot` bot (approval flow, job queue, Telegram, schedule).
- `pipeline/`: channel resolution, trends, LLM providers, writer, validator, state, publishing, cloud.
- `engine/`: renderers. `html_short.py` (Chromium + `engine/web/*.html` stages) and `make_short.py` (the shared
  core: normalising, TTS, timeline, audio, mux, plus the `classic` Pillow renderer). Renderers run as subprocesses.
- `channels/_template/`: the starter channel pack copied by `new-channel`. User channels live next to it.
- `cloud/`: Cloudflare Worker and the workflow step classifier. `.github/workflows/bot.yml` is the cloud runner.

## Rules

- Nothing channel-specific in code. Per-channel values go in `channel.json` (see `docs/CONFIGURATION.md`).
- Language-aware code keys off `content.language` / `ep["lang"]` (BCP-47). Use `validate.base_lang()`.
- The `pipeline/channel.py` module is standard library only, because renderer subprocesses import it.
- Never read, print or commit `.env`, `client_secret*.json` or `data/`.

## Commands

```bash
PY=.venv/bin/python            # Windows: .venv\Scripts\python.exe
PYTHONUTF8=1 $PY autopilot.py demo                 # smoke test, no keys
for t in tests/test_*.py; do PYTHONUTF8=1 $PY $t; done
$PY tests/fuzz_render.py
$PY -m ruff check . && $PY -m ruff format --check .
```
