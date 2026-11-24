# Security

## Reporting a vulnerability

Please **don't open a public issue** for security problems. Use GitHub's
[private vulnerability reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
on this repository instead. We aim to reply within 7 days.

## Keeping your channel safe

- Keys live in `channels/<name>/.env`, which is git-ignored. Never paste it into issues, logs or chats.
- `cloud-setup` refuses to push `.env`, `client_secret*.json`, models, data or videos, and stores keys as
  GitHub Actions secrets.
- Use fine-grained GitHub tokens limited to the channel's own repo.
- The Telegram bot only answers the single `TELEGRAM_CHAT_ID` you configure, and the Cloudflare Worker checks
  Telegram's secret token header on every request.
