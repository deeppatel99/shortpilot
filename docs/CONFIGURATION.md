# Configuration

A channel is the folder `channels/<name>/`. Run `python autopilot.py new-channel <name>` to create one from
[`channels/_template/`](../channels/_template).

| File | What it controls |
|---|---|
| `channel.json` | Identity, content, schedule, writer, voice, look, trend sources, publishing, cloud names |
| `prompts/rank.md` | How the day's candidate topics are scored against your niche |
| `prompts/research.md` | Optional fact-finding step before writing (`llm.researcher`) |
| `prompts/episode.md` | How a script is written: voice, structure, visual types, JSON schema |
| `assets/logo_icon.png` | Square logo shown on the video and in the stage header |
| `assets/chars/host.svg`, `guest.svg` | Optional custom characters, replacing the built-in cast |
| `.env` | Keys and tokens ([SETUP.md](SETUP.md)) |

**Choosing the channel.** `--channel <name>` works on every command. Without it, ShortPilot uses the
`AUTOPILOT_CHANNEL` environment variable, then a file named `.channel` in the project folder, then the only channel
in `channels/`. Folders whose names start with `_` are never picked automatically.

## channel.json

### `channel`: who you are

| Key | Meaning |
|---|---|
| `name`, `handle` | Display name and @handle |
| `brand` | The short name burnt into videos (upper-cased) |
| `tagline` | One line under the handle on the stage footer |
| `niche` | **The most important line.** The ranker and the writer use it to decide what's on-topic |
| `audience` | Who is watching. It sets the tone and the level of detail |
| `default_tags`, `hashtags` | Added to every YouTube upload and caption |
| `disclaimer`, `yt_footer`, `ig_footer` | Fixed text appended to descriptions and captions |
| `stage.handle`, `stage.tagline` | Override what the HTML stage shows |
| `stage.brand_a`, `stage.brand_b` | The two-tone wordmark in the header (e.g. `"MY"` + `"CHANNEL"`) |
| `stage.brand_text` | The name in any script, for non-Latin brands |
| `stage.labels` | The few fixed on-screen words: `{"follow": "Follow", "myth": "everyone says…", "fact": "the truth", "you": "@you"}`. Translate them for other languages |

### `content`: what you make

| Key | Meaning |
|---|---|
| `language` | A BCP-47 code: `en-US`, `en-GB`, `es-MX`, `pt-BR`, `fr-FR`, `it-IT`, `hi-IN`, `ja-JP`, `zh-CN`… Scripts, captions, voice and Google News all follow it |
| `categories` | `{"id": "one-line description"}`, the kinds of videos your channel makes. The writer tags each episode with one |
| `evergreen_flow` | Category ids to cycle through on days without strong news (default: all categories in order) |
| `series` | `{"category id": "LABEL"}`, the small series badge on screen; `series_default` covers the rest |
| `cta` | The spoken and on-screen call to action, e.g. `"Comment below"` |
| `texts.share_line`, `texts.comment_question` | Fallback lines used when the writer leaves them out |

### `schedule`: when

| Key | Meaning |
|---|---|
| `utc_offset` | Your timezone as `"+HH:MM"`/`"-HH:MM"` (`"-05:00"` New York, `"+01:00"` Berlin, `"+05:30"` India). Every time below is local to it |
| `scout_at` | When the daily trend scout runs and the script is sent |
| `publish_at` | When the video goes public |
| `slots` | Several videos a day: `["09:00", "18:00"]` instead of `publish_at` |
| `videos_per_day` | Scripts drafted per scout (start with 1) |

### `llm`: who writes

| Key | Meaning |
|---|---|
| `writer`, `ranker` | `claude_cli` (default), `gemini` or `anthropic` |
| `researcher` | Optional separate fact-finding call before writing (`""` = off) |
| `fallback` | Used automatically when the main provider fails |
| `claude_model`, `gemini_model`, `anthropic_model` | Model override per provider (`""` = the provider's default) |

### `voice`: how it sounds

| Key | Meaning |
|---|---|
| `tts` | `kokoro` (local, free), `azure` or `sarvam` |
| `voice` | The host's Kokoro voice, e.g. `af_heart`. Blends work: `af_heart*0.7+af_bella*0.3` |
| `speed` | 1.0 is natural. Shorts reward pace, so 1.1–1.2 works well |
| `azure_voice`, `sarvam_voice` | Voice names for the cloud engines |
| `guest_voice` | `{"kokoro": "am_michael", "azure": "..."}`, the second character's voice (default: picked per language) |

### `render`: how it looks

| Key | Meaning |
|---|---|
| `engine` | `html` (animated stages with the cast, the default) or `classic` (Pillow motion graphics, no browser needed) |
| `stage` | `stage` (notebook look) or `editorial` (print look). The env var `STAGE` overrides it for one render |
| `cast_names` | `{"host": "Alex", "guest": "Sam"}`, the names on the speech bubbles |
| `jobs` | Parallel render workers; `0` sizes them from free RAM |

### `trends`: where topics come from

| Key | Meaning |
|---|---|
| `geo` | Country for Google Trends and Google News (`US`, `GB`, `MX`, `BR`, `IN`, `JP`…) |
| `google_trends`, `youtube_trending` | Switch each source on or off (YouTube needs `YOUTUBE_API_KEY`) |
| `youtube_category` | YouTube category id for trending (`28` Science & Tech, `26` Howto, `27` Education, `24` Entertainment) |
| `news_queries` | Google News searches that fit your niche |
| `world_news_queries` | The same, in the US/English edition (useful when `geo` is a smaller market) |
| `news_days` | How many days back news counts as fresh |
| `hacker_news`, `github_queries` | For tech channels: HN lists (`front_page`) and GitHub repo searches |

### `publish`: where it goes

| Key | Meaning |
|---|---|
| `auto` | `false`: you approve every script (recommended). `true`: everything posts itself. `"cron"`: only the daily scout runs on its own, and `/new` still asks |
| `auto_preview` | In auto mode, still send the finished video to Telegram |
| `instagram` | Post Reels |
| `youtube_mode` | `auto` (use the API once `youtube-test` has passed), `api` or `manual` (the bot sends you the file and text) |
| `ai_label` | Tick YouTube's "altered or synthetic content" box |

### `cloud`: cloud mode names

| Key | Meaning |
|---|---|
| `repo`, `worker` | The private GitHub repo and Cloudflare Worker that `cloud-setup` creates |
| `crons` | Optional `{"daily": "...", "publish": [...]}` UTC overrides; by default they come from `schedule` |
| `site.*` | Text for the Worker's home, privacy and terms pages (Google's OAuth screen asks for them) |
| `wrangler_home`, `pin_account`, `cloudflare_login_hint` | Guards for people who run several Cloudflare accounts |

## Languages and voices

1. Set `content.language`, e.g. `"es-MX"`.
2. Pick a voice that speaks it:

| Language | Kokoro voices (f = female, m = male) | Azure example |
|---|---|---|
| English US / UK | `af_heart`, `af_bella`, `am_michael` / `bf_emma`, `bm_george` | `en-US-AvaMultilingualNeural` |
| Spanish | `ef_dora`, `em_alex` | `es-MX-DaliaNeural` |
| French | `ff_siwis` | `fr-FR-DeniseNeural` |
| Hindi | `hf_alpha`, `hf_beta`, `hm_omega` | `hi-IN-SwaraNeural` |
| Italian | `if_sara`, `im_nicola` | `it-IT-ElsaNeural` |
| Japanese | `jf_alpha`, `jm_kumo` | `ja-JP-NanamiNeural` |
| Portuguese (BR) | `pf_dora`, `pm_alex` | `pt-BR-FranciscaNeural` |
| Mandarin | `zf_xiaobei`, `zm_yunjian` | `zh-CN-XiaoxiaoNeural` |
| Anything else | not available in Kokoro, so set `voice.tts: "azure"` | [140+ languages](https://learn.microsoft.com/azure/ai-services/speech-service/language-support) |

3. Translate `content.cta`, `content.texts`, `content.series` and `channel.stage.labels`.
4. Set `trends.geo` and `trends.news_queries` for your market.
5. Optionally, rewrite the prompts in your language. The English prompts work for any `{{LANGUAGE}}`, because they
   tell the writer which language to use.

Speaking speed varies by language. The validator's length check uses a per-language characters-per-second table
(`pipeline/validate.py → CHARS_PER_SEC`). Add your language there if its estimates are off.

Hindi channels: the voice text (`tts`) must be in Devanagari, while captions may use any script. The validator
enforces this.

## Prompts

The prompts are plain Markdown with placeholders, and editing them is the main way to change the content:

| Placeholder | Filled with |
|---|---|
| `{{CHANNEL}}`, `{{BRAND}}`, `{{NICHE}}`, `{{AUDIENCE}}` | From `channel` |
| `{{LANGUAGE}}` | `content.language` |
| `{{CATEGORIES}}` | `content.categories` as a list |
| `{{TODAY}}` | Today's date in your timezone |

`prompts/episode.md` also documents the episode JSON and the visual types. Keep its JSON schema section in step
with the renderer ([ARCHITECTURE.md](ARCHITECTURE.md#episode-json)).
