# Setup: keys and platforms

Each channel keeps its keys in `channels/<name>/.env`. That file is git-ignored, so never commit it.
[`.env.example`](../.env.example) lists every key with a one-line hint. Run `python autopilot.py check` at any time
to see what is installed and which keys are missing.

| Part | Time | Result |
|---|---|---|
| [Script writer](#script-writer) | 2 min | Scripts get written |
| [Telegram](#telegram) | 5 min | Approvals on your phone |
| [YouTube](#youtube) | 15 min | Shorts upload and schedule themselves |
| [Instagram](#instagram) | 25 min | Reels post themselves |
| [Facebook](#facebook-optional) | 10 min | Facebook Reels (optional) |
| [Better topics and visuals](#better-topics-and-visuals-optional) | 10 min | Trending data, AI images, stock footage |
| [Cloud voices](#cloud-voices-optional) | 5 min | Azure / Sarvam TTS |

In the commands below, `py` means your venv Python: `.venv/bin/python` on macOS/Linux and `.venv\Scripts\python.exe`
on Windows.

---

## Script writer

`channel.json → llm.writer` chooses who writes the scripts. `llm.fallback` takes over automatically when the
writer fails, for example when you hit a usage limit.

| Provider | Setup | Cost |
|---|---|---|
| `claude_cli` (default) | Install [Claude Code](https://claude.com/claude-code), run `claude` once and log in, then test with `claude -p "say hi"` | Uses your Claude plan's usage |
| `gemini` | Create a key at [aistudio.google.com/apikey](https://aistudio.google.com/apikey), then set `GEMINI_API_KEY=` | Free tier with daily limits, plus Google Search grounding |
| `anthropic` | Create a key at [console.anthropic.com](https://console.anthropic.com), then set `ANTHROPIC_API_KEY=` | Pay as you go, plus the web search tool |

If `claude` is not on your PATH, point `CLAUDE_BIN=` at it. To judge the providers side by side, run
`py autopilot.py compare --topic "..."`.

## Telegram

1. In Telegram, open **@BotFather** and send `/newbot`. Copy the token into `.env` as `TELEGRAM_BOT_TOKEN=`.
2. Open your bot, tap **Start** and send `hi`.
3. Run `py autopilot.py telegram-setup`. Copy the number it prints into `.env` as `TELEGRAM_CHAT_ID=`.

The bot only ever talks to that one chat id.

## YouTube

Afterwards, every finished video uploads with its title, description and tags. It is scheduled to go public at
your `publish_at` time, and the bot posts the first comment once it is live.

1. **Google Cloud project.** Go to [console.cloud.google.com](https://console.cloud.google.com), signed in with the
   Google account that owns the channel. Create a project and enable **YouTube Data API v3**.
2. **OAuth consent screen** (shown as *Google Auth Platform* in the newer UI). Click **Get started** and pick
   audience **External**. Then open **Audience** and click **Publish app** so the status reads **In production**.
   ⚠️ While the app is in *Testing*, Google expires the login every 7 days and uploads stop.
   If Google asks for a homepage or privacy policy, use your Worker URL's `/`, `/privacy` and `/terms` pages
   ([CLOUD.md](CLOUD.md)).
3. **Desktop client.** Go to **Clients**, create a client of type **Desktop app** and download its JSON. Save it as
   `channels/<name>/client_secret.json` and set `YOUTUBE_CLIENT_SECRET_FILE=client_secret.json`.
4. Run `py autopilot.py youtube-auth`. In the browser, pick the **channel** (not your personal profile). The warning
   "Google hasn't verified this app" is expected for your own app: click **Advanced**, then **Go to …**.
5. Run `py autopilot.py youtube-test`. It uploads an unlisted test video, checks that YouTube did not lock it to
   private, and then deletes it.

For trending-topic scouting, also create an **API key** in the same project and set `YOUTUBE_API_KEY=`. The key is
free.

Uploads are marked *"Altered or synthetic content"* because the videos use an AI voice. YouTube requires this
label for realistic AI content. `publish.ai_label` controls it.

## Instagram

1. Your Instagram account must be **public** and set to **Professional → Creator** (or Business).
2. At [developers.facebook.com](https://developers.facebook.com), go to **My Apps → Create App**, choose use case
   **Other** and type **Business**. Meta rejects app names that contain "Instagram" or "IG".
3. Open the **Instagram** product and go to **API setup with Instagram login → Generate access tokens**. Add your
   account and allow `instagram_business_basic`, `instagram_business_content_publish` and
   `instagram_business_manage_comments`. If asked, accept the tester invite in the Instagram app.
4. Put the token in `.env` as `IG_ACCESS_TOKEN=`.
5. Run `py autopilot.py ig-whoami`. It prints your @handle and saves `IG_USER_ID`.
6. Run `py autopilot.py ig-limit`. If it prints JSON, the publishing permission works.

**Video hosting.** Instagram downloads each video from a public URL. The bot tries hosts in this order:
an optional Azure Blob container, a Cloudflare Quick Tunnel from your own machine, tmpfiles.org, uguu.se, and
litterbox. Test them with `py autopilot.py host-test`. Choose the order yourself with `PUBLIC_HOST=`.

The token lasts 60 days. The bot refreshes it every 30 days by itself.

## Facebook (optional)

Facebook Reels need a Page id and a **Page access token** from the same Meta app. Add the Pages permissions
`pages_manage_posts`, `pages_read_engagement` and `pages_show_list`, plus `read_insights` if you want watch time
in `/stats`. Then set `FB_PAGE_ID=` and `FB_PAGE_TOKEN=`. Leave both empty to skip Facebook.

## Better topics and visuals (optional)

| Key | Where | What it adds |
|---|---|---|
| `YOUTUBE_API_KEY` | Google Cloud → YouTube Data API v3 → Credentials → API key | YouTube trending topics, `/stats` |
| `CF_ACCOUNT_ID`, `CF_API_TOKEN` | Cloudflare dashboard → Account ID, plus an API token from the **Workers AI** template | AI background images (FLUX, ~150/day free) |
| `PIXABAY_API_KEY` | [pixabay.com/api/docs](https://pixabay.com/api/docs/) after a free signup | Real stock footage and photos |
| `PEXELS_API_KEY` | [pexels.com/api](https://www.pexels.com/api/) | More stock footage |

With none of these set, scenes use animated procedural backgrounds. `MEDIA=off` forces that even when keys are set.

## Cloud voices (optional)

| Key | Notes |
|---|---|
| `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION` | Azure AI Speech: 400+ voices in 140+ languages, with a free F0 tier. Then set `voice.tts: "azure"` and `voice.azure_voice` |
| `SARVAM_API_KEY` | Sarvam Bulbul for Indian languages (prepaid). Then set `voice.tts: "sarvam"` |

See [CONFIGURATION.md → Languages and voices](CONFIGURATION.md#languages-and-voices).
