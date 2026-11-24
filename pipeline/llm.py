"""LLM providers behind one interface: complete(prompt, web=False) -> text.

claude_cli : `claude -p` (Claude Code). Uses your Claude Pro/Max login — no API key, counts against plan limits.
             web=True lets Claude use its WebSearch/WebFetch tools for research.
anthropic  : Claude API (paid, separate from Pro). Needs ANTHROPIC_API_KEY. web=True adds the web_search tool.
gemini     : Gemini API (free tier available). Needs GEMINI_API_KEY. web=True adds Google Search grounding.
"""

import json
import os
import shutil
import subprocess
import sys

from .util import DATA, env, http, log


class LLMError(RuntimeError):
    pass


class ClaudeCLI:
    name = "claude_cli"

    def __init__(self, model=None):
        self.model = model or None
        self.exe = (
            env("CLAUDE_BIN") or shutil.which("claude") or shutil.which("claude.cmd") or shutil.which("claude.exe")
        )
        if not self.exe:
            raise LLMError(
                "Claude Code CLI not found. Install it and run `claude` once to log in with your Pro account."
            )
        self.cwd = os.path.join(DATA, "llm_sandbox")
        os.makedirs(self.cwd, exist_ok=True)  # empty dir: no CLAUDE.md / hooks picked up

    def complete(self, prompt, web=False, timeout=900):
        cmd = [*([sys.executable] if self.exe.endswith(".py") else []), self.exe, "-p", "--output-format", "json"]
        if web:
            cmd += ["--allowedTools", "WebSearch,WebFetch"]
        if self.model:
            cmd += ["--model", self.model]
        r = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self.cwd,
            timeout=timeout,
        )
        out = (r.stdout or "").strip()
        try:
            data = json.loads(out.splitlines()[-1] if out else "{}")
        except json.JSONDecodeError:
            raise LLMError(f"claude -p returned non-JSON (exit {r.returncode}): {out[:400]} {r.stderr[:400]}") from None
        if r.returncode != 0 or data.get("is_error"):
            raise LLMError(f"claude -p failed: {data.get('result') or r.stderr[:400]}")
        if data.get("total_cost_usd") is not None:
            log.info(
                f"claude -p done ({data.get('num_turns', '?')} turns, est. ${data['total_cost_usd']:.3f} of plan usage)"
            )
        return data.get("result", "")


class AnthropicAPI:
    name = "anthropic"

    def __init__(self, model=None):
        self.key = env("ANTHROPIC_API_KEY", required=True)
        self.model = model or "claude-sonnet-5-5"

    def complete(self, prompt, web=False, timeout=600):
        body = {"model": self.model, "max_tokens": 8000, "messages": [{"role": "user", "content": prompt}]}
        if web:
            body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 6}]
        r = http(
            "POST",
            "https://api.anthropic.com/v1/messages",
            json=body,
            timeout=timeout,
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        )
        if r.status_code != 200:
            raise LLMError(f"Anthropic API {r.status_code}: {r.text[:400]}")
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


class Gemini:
    name = "gemini"

    def __init__(self, model=None):
        self.key = env("GEMINI_API_KEY", required=True)
        self.model = model or "gemini-flash-latest"

    def complete(self, prompt, web=False, timeout=600):
        body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.8}}
        if web:
            body["tools"] = [{"google_search": {}}]
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        r = http(
            "POST",
            url,
            json=body,
            timeout=timeout,
            headers={"x-goog-api-key": self.key, "content-type": "application/json"},
        )
        if r.status_code != 200:
            raise LLMError(f"Gemini {r.status_code}: {r.text[:400]}")
        cand = (r.json().get("candidates") or [{}])[0]
        text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []))
        chunks = cand.get("groundingMetadata", {}).get("groundingChunks", [])
        if web and chunks:  # surface Google Search sources to the writer
            text += "\n\nSOURCES:\n" + "\n".join(
                f"- {c.get('web', {}).get('title', '')}: {c.get('web', {}).get('uri', '')}" for c in chunks[:12]
            )
        return text


def get_llm(provider, cfg):
    llm_cfg = cfg.get("llm", {})
    if provider == "claude_cli":
        return ClaudeCLI(llm_cfg.get("claude_model"))
    if provider == "anthropic":
        return AnthropicAPI(llm_cfg.get("anthropic_model"))
    if provider == "gemini":
        return Gemini(llm_cfg.get("gemini_model"))
    raise LLMError(f"unknown LLM provider {provider}")
