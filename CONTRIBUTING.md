# Contributing to ShortPilot

Thanks for helping! Bug fixes, new visual types, stages, voices, languages and publishers are all welcome.

## Development setup

```bash
git clone https://github.com/OWNER/shortpilot && cd shortpilot
bash setup.sh                       # Windows: powershell -ExecutionPolicy Bypass -File .\setup.ps1
.venv/bin/python autopilot.py demo  # renders channels/_template/data/videos/demo.mp4
```

## Tests

The tests run offline. They use fakes for Claude, Telegram, Instagram and YouTube, and never touch a real `.env`
or `data/` folder.

```bash
export PYTHONUTF8=1
for t in tests/test_*.py; do .venv/bin/python "$t" || break; done   # each prints ... OK
.venv/bin/python tests/fuzz_render.py                               # random junk must never crash the renderer
```

`test_flow.py`, `test_cloud.py` and `test_publish.py` need one rendered `.mp4` in
`channels/_template/data/videos/`, so run `autopilot.py demo` once first. Alternatively, set
`AUTOPILOT_TEST_VIDEOS` to a folder that already has one.

## Style

- `ruff check .` and `ruff format .` (config in `pyproject.toml`, line length 120).
- Keep dependencies minimal: the standard library first, then what is already in `requirements.txt`.
- User-facing text (Telegram, CLI) is short, plain English.
- Nothing channel-specific in the engine. If a value differs between channels, it belongs in `channel.json`.
- Comments explain *why*, not *what*.

## Good first contributions

| Idea | Where |
|---|---|
| A TikTok publisher | `pipeline/publish.py` and the `publish` flow in `autopilot.py` |
| A new visual type | [docs/ARCHITECTURE.md → Adding a visual type](docs/ARCHITECTURE.md#adding-a-visual-type) |
| A new stage (look) | Copy `engine/web/stage.html` and select it with `render.stage` |
| Speaking-speed numbers for your language | `CHARS_PER_SEC` in `pipeline/validate.py` |
| Default cast voices for a language | `CAST_VOICE` in `engine/html_short.py` |
| Translated stage labels | Examples in `docs/CONFIGURATION.md` |

## Pull requests

1. Fork the repo, then create a branch from `main`.
2. Make your change, with a test if it adds logic.
3. Run the tests and ruff.
4. Open the PR, saying what changed and why. Add a screenshot or a short clip for anything visual.

Never include keys, tokens, `.env` files, personal channel data or rendered videos in a PR.
