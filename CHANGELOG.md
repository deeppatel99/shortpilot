# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org).

## [1.0.0] - 2026-11-24

### Added
- End-to-end pipeline: trend scouting → LLM ranking → script writing with sources → validation and auto-repair
  → rendering → publishing to YouTube Shorts, Instagram Reels and Facebook Reels.
- Two renderers: `html` (animated HTML stages in two looks, with a two-person cast) and `classic` (Pillow).
- Any-language channels via `content.language`, with Kokoro (local), Azure Speech and Sarvam voices.
- Telegram approvals, a full auto mode, scheduling with `publishAt`, and crash recovery.
- A free cloud mode on GitHub Actions + Cloudflare Workers, set up with `cloud-setup`.
- Multi-channel support: `new-channel`, with channel packs in `channels/<name>/`.
- `demo` command: renders a sample video without any keys.
- A Claude Code plugin and the `shortpilot` skill, also installable with `npx skills add` for Codex, Cursor,
  Gemini CLI and other Agent Skills tools.
- `setup.bat`: double-click install for Windows that ends by playing the demo video.
- A "Start here" path for non-coders, and a Hindi README (`README.hi.md`).
- An offline test suite with fakes for Claude, Telegram, Instagram and YouTube.
