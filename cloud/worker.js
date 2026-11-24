// Telegram webhook -> GitHub Actions  (Cloudflare Workers, free plan)
// Variables / secrets to add in the Worker settings:
//   TG_BOT_TOKEN  your BotFather token           TG_CHAT_ID  your chat id (same as .env)
//   TG_SECRET     printed by: python autopilot.py telegram-webhook --url <this worker url>
//   GH_TOKEN      GitHub fine-grained token: only this channel's repo, permission "Contents: Read and write"
//   GH_REPO       e.g. yourname/mychannel-bot
// Plain variables (written into wrangler.toml by cloud-setup from channels/<slug>/channel.json):
//   BRAND_NAME  PUBLISHER_NAME  TAGLINE  ABOUT  INSTAGRAM  CONTACT_EMAIL  DAILY_CRON

const ACK = {            // instant reply per button (GitHub needs 1-2 min to start a machine)
  approve:  "🎬 #ID: rendering started — a GitHub runner is starting. The video lands here in ~12–15 min. No need to tap again 🙏",
  rerender: "🎬 #ID: re-render started — ~12–15 min.",
  edit:     "✏️ #ID: OK — in a minute the bot asks what to change.",
  skip:     "🔄 #ID: finding another topic — new script in ~4–6 min.",
  reject:   "🗑 #ID: rejecting…",
  pubnow:   "🚀 #ID: publishing now — ~3–8 min (Instagram processes the video).",
  pubsched: "⏰ #ID: scheduling for the next slot…",
  igdone:   "👍 #ID: noted.",
};

// Starts one GitHub Actions run (see .github/workflows/bot.yml for the event types it accepts).
function dispatch(env, eventType, payload) {
  return fetch(`https://api.github.com/repos/${env.GH_REPO}/dispatches`, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GH_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "shortpilot-worker",
      "content-type": "application/json",
    },
    body: JSON.stringify({ event_type: eventType, client_payload: payload }),
  });
}

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") return page(new URL(request.url).pathname, env);
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TG_SECRET)
      return new Response("forbidden", { status: 403 });
    let u;
    try { u = await request.json(); } catch (e) { return new Response("bad request", { status: 400 }); }
    const msg = u.message || (u.callback_query && u.callback_query.message);
    if (!msg || String(msg.chat && msg.chat.id) !== String(env.TG_CHAT_ID)) return new Response("ignored");
    ctx.waitUntil(handle(u, env));
    return new Response("ok");                       // answer Telegram at once; the work happens on GitHub
  },
  // Cron triggers (crons in pipeline/cloud_setup.py). Times are UTC.
  async scheduled(event, env, ctx) {
    const task = event.cron === env.DAILY_CRON ? "daily" : "publish";
    ctx.waitUntil((async () => {
      const r = await dispatch(env, "cron", { task });
      if (r.status !== 204) await tg(env, "sendMessage", { chat_id: env.TG_CHAT_ID, text: `⚠️ Could not send the scheduled ${task} to GitHub (${r.status}).` });
    })());
  },
};

function tg(env, method, body) {
  return fetch(`https://api.telegram.org/bot${env.TG_BOT_TOKEN}/${method}`, {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
  });
}

async function handle(u, env) {
  try {
    if (u.callback_query) {
      const cq = u.callback_query, m = cq.message;
      const [action, id] = String(cq.data || "").split(":");
      await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: "✅ Got it" });
      // remove the buttons right away -> no double taps, no extra GitHub runs
      await tg(env, "editMessageReplyMarkup", { chat_id: m.chat.id, message_id: m.message_id, reply_markup: { inline_keyboard: [] } });
      const text = (ACK[action] || "⏳ Kaam shuru…").replace("ID", id || "");
      await tg(env, "sendMessage", { chat_id: m.chat.id, text });
    } else if (u.message && u.message.text) {
      const t = u.message.text.trim();
      let text = "⏳ Got it — working on GitHub (reply in 1–2 min).";
      if (t.startsWith("/new")) text = "✍️ Writing the script — script + sources land here in ~4–6 min.";
      else if (t.startsWith("/daily")) text = "🔎 Trending topics + script — ~5–7 min.";
      else if (t.startsWith("/render")) text = "🎬 Render shuru — ~12–15 min.";
      else if (t.startsWith("/status") || t.startsWith("/help") || t.startsWith("/start")) text = "";
      if (text) await tg(env, "sendMessage", { chat_id: u.message.chat.id, text });
    }
    const r = await dispatch(env, "telegram", { update: u });
    if (r.status !== 204) {
      const t = (await r.text()).slice(0, 200);
      await tg(env, "sendMessage", { chat_id: env.TG_CHAT_ID, text: `⚠️ Could not trigger GitHub (${r.status}): ${t}\nCheck the Worker's GH_TOKEN / GH_REPO.` });
    }
  } catch (e) {
    await tg(env, "sendMessage", { chat_id: env.TG_CHAT_ID, text: `⚠️ Worker error: ${e}` });
  }
}

// ---------------------------------------------------------------- public pages (needed for the Google OAuth app)
const CSS = "body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;max-width:720px;margin:40px auto;padding:0 16px;line-height:1.6;color:#111;background:#fff}h1{margin-bottom:4px}small{color:#666}a{color:#3b5bdb}";
function html(env, title, body) {
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title><style>${CSS}</style></head><body>${body}<hr><small>${env.BRAND_NAME} · <a href="/">Home</a> · <a href="/privacy">Privacy</a> · <a href="/terms">Terms</a> · Contact: ${env.CONTACT_EMAIL}</small></body></html>`,
    { headers: { "content-type": "text/html; charset=utf-8" } });
}
function page(path, env) {
  const N = env.BRAND_NAME, P = env.PUBLISHER_NAME;
  if (path === "/privacy") return html(env, `${N} – Privacy Policy`, `<h1>Privacy Policy</h1><small>Last updated: 28 October 2026</small>
<p><b>${P}</b> is a private tool used only by the owner of the ${N} channels to publish their own videos to their own YouTube channel, Instagram account and Facebook Page.</p>
<h3>What data the app accesses</h3><p>With the owner's permission (Google OAuth), the app uses the YouTube Data API to upload videos, set titles/descriptions/schedule and post a comment on the owner's own channel. With the owner's permission (Meta login), it uses the Instagram and Facebook Graph APIs to publish Reels to the owner's own Instagram account and Facebook Page and to reply to comments on those posts. It does not collect or store any data about viewers or other users.</p>
<h3>Storage</h3><p>The access tokens are stored encrypted as GitHub Actions secrets in the owner's private repository and are used only to perform the actions above. No data is sold or shared with third parties.</p>
<h3>Revoking access and data deletion</h3><p>Access can be revoked at any time at <a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a> (Google) and in Facebook Settings &gt; Apps and Websites or Instagram Settings &gt; Apps and websites (Meta). The app keeps no user data, so revoking access removes everything; for any other deletion request write to the owner via the Instagram account above.</p>
<p>The app's use of information received from Google APIs adheres to the Google API Services User Data Policy, including the Limited Use requirements. YouTube API usage is subject to the <a href="https://www.youtube.com/t/terms">YouTube Terms of Service</a> and <a href="https://policies.google.com/privacy">Google Privacy Policy</a>.</p>`);
  if (path === "/terms") return html(env, `${N} – Terms`, `<h1>Terms of Service</h1><small>Last updated: 22 October 2026</small>
<p>${P} is a personal publishing tool for the ${N} channels and is not offered to the public. By using it you agree to the <a href="https://www.youtube.com/t/terms">YouTube Terms of Service</a>. The tool is provided as is, without warranty.</p>`);
  if (path === "/" || path === "") return html(env, N, `<h1>${N}</h1><p><b>${env.TAGLINE}</b> ${env.ABOUT}</p>
<p>Instagram: <a href="https://instagram.com/${env.INSTAGRAM}">@${env.INSTAGRAM}</a></p><p>This site also hosts <b>${P}</b>, the private tool the channel uses to publish its own videos.</p>`);
  return new Response("not found", { status: 404 });
}
