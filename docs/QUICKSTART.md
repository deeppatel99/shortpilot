# Quickstart

From zero to your first video approved in Telegram, in about 15 minutes. The commands below use macOS/Linux
paths. On Windows, use `.venv\Scripts\python.exe` instead of `.venv/bin/python`.

## 1. Install (5 min)

You need Git, and also FFmpeg on macOS/Linux (`brew install ffmpeg` / `sudo apt install ffmpeg`). The setup script
handles everything else.

```bash
git clone https://github.com/OWNER/shortpilot && cd shortpilot
bash setup.sh                                          # macOS / Linux
powershell -ExecutionPolicy Bypass -File .\setup.ps1   # Windows (also installs Python 3.12 and FFmpeg)
```

It creates `.venv`, installs the Python packages and Chromium, and downloads the Kokoro voice model (~350 MB) into
`models/`.

## 2. Render the demo (1 min)

```bash
.venv/bin/python autopilot.py demo
```

✅ `channels/_template/data/videos/demo.mp4` is a 30-second short. No keys or LLM were needed. If it fails, run
`.venv/bin/python autopilot.py check` and fix whatever it marks with ❌.

## 3. Create your channel (3 min)

```bash
.venv/bin/python autopilot.py new-channel mychannel
```

This copies `channels/_template/` to `channels/mychannel/` along with its own `.env`. Open
`channels/mychannel/channel.json` and set at least these keys:

| Key | Example |
|---|---|
| `channel.name`, `handle`, `brand` | `"Space Bites"`, `"@spacebites"`, `"SPACE BITES"` |
| `channel.niche` | `"Mind-blowing space facts explained simply"` |
| `channel.audience` | `"teenagers and adults who like science, no background needed"` |
| `content.language` | `"en-US"`, `"es-MX"`, `"pt-BR"`, `"hi-IN"`… |
| `content.categories` | 3–6 kinds of videos your channel makes, each with one line describing it |
| `schedule.utc_offset`, `scout_at`, `publish_at` | `"-05:00"`, `"08:00"`, `"12:00"` |

Replace `channels/mychannel/assets/logo_icon.png` with your logo (square PNG, 512×512 is plenty).

## 4. Your first real video (3 min)

The script is written by the [Claude Code](https://claude.com/claude-code) CLI on your Claude plan. Install it,
then run `claude` once to log in. If you'd rather use a free Gemini key instead, see
[SETUP.md → Script writer](SETUP.md#script-writer).

```bash
.venv/bin/python autopilot.py make --topic "why airplane windows are round"
```

✅ The episode JSON is saved in `channels/mychannel/episodes/` and the video in `channels/mychannel/data/videos/`.

## 5. Approve from Telegram (5 min)

1. In Telegram, open **@BotFather** and send `/newbot`. Give it a name and a username that ends in `bot`.
2. Paste the token into `channels/mychannel/.env` as `TELEGRAM_BOT_TOKEN=...`.
3. Open your new bot, tap **Start** and send `hi`.
4. Run `.venv/bin/python autopilot.py telegram-setup`. It prints your chat id. Put it in `.env` as `TELEGRAM_CHAT_ID=...`.
5. Start the bot: `./run_serve.sh` (Windows: `run_serve.bat`). Telegram says *"My Channel · ShortPilot online"*.
6. Send `/new why cats purr`. The script and its sources arrive in 2–4 minutes. Tap **✅ Approve & render**.

✅ The video preview arrives with publish buttons.

## Next

- Auto-posting to YouTube, Instagram and Facebook: [SETUP.md](SETUP.md)
- Every setting in `channel.json`: [CONFIGURATION.md](CONFIGURATION.md)
- Turn your PC off and run in the cloud for free: [CLOUD.md](CLOUD.md)
- What each button does: [OPERATIONS.md](OPERATIONS.md)
