#!/usr/bin/env bash
# ShortPilot - one-time setup for macOS and Linux (Windows: setup.ps1).   Run from this folder:
#   bash setup.sh
# Uses Python 3.10-3.13: brand-new Pythons often have no ready-made wheels yet for onnxruntime/numpy.
set -u
cd "$(dirname "$0")"
ok()   { printf '  \033[32m[OK]\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m[!!]\033[0m %s\n' "$1"; }
fail() { printf '  \033[31m[XX]\033[0m %s\n' "$1"; exit 1; }
step() { printf '\n\033[36m%s\033[0m\n' "$1"; }

step "1) Python 3.10-3.13"
PY=""
for v in python3.12 python3.11 python3.13 python3.10 python3; do
  if command -v "$v" >/dev/null 2>&1 && "$v" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,13) else 1)'; then PY="$v"; break; fi
done
[ -n "$PY" ] || fail "Install Python 3.12 (macOS: brew install python@3.12 · Ubuntu: sudo apt install python3.12 python3.12-venv) and run again."
ok "using $($PY --version)"

step "2) Virtual env + packages"
[ -d .venv ] || "$PY" -m venv .venv || fail "venv creation failed (Ubuntu: sudo apt install python3-venv)"
VPY=.venv/bin/python
"$VPY" -m pip install --upgrade pip --quiet
"$VPY" -m pip install -r requirements.txt || fail "pip install failed - see the error above."
"$VPY" -m playwright install chromium || warn "Chromium download failed - set PW_CHROMIUM=/path/to/chrome, or use render.engine \"classic\""
[ "$(uname)" = "Linux" ] && { "$VPY" -m playwright install-deps chromium 2>/dev/null || warn "Chromium system libraries: run  sudo .venv/bin/python -m playwright install-deps chromium"; }
"$VPY" -c "import kokoro_onnx, numpy, PIL, soundfile, requests" || fail "packages installed but import failed."
ok "packages installed"

step "3) FFmpeg"
if command -v ffmpeg >/dev/null 2>&1; then ok "ffmpeg found"
else warn "ffmpeg not found. macOS: brew install ffmpeg · Ubuntu/Debian: sudo apt install ffmpeg"; fi

step "4) Voice model (Kokoro, ~350 MB, one time)"
mkdir -p models
BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
  min=$([ "$f" = kokoro-v1.0.onnx ] && echo 300000000 || echo 25000000)
  if [ -f "models/$f" ] && [ "$(wc -c < "models/$f")" -ge "$min" ]; then ok "$f present"
  else echo "  downloading $f ..."; curl -fL --progress-bar "$BASE/$f" -o "models/$f" || fail "download failed: $f"; ok "$f downloaded"; fi
done

step "5) Claude Code CLI (writes scripts with your Claude plan)"
if command -v claude >/dev/null 2>&1; then ok "claude found - run 'claude' once and log in if you haven't"
else warn "claude not found. Install:  curl -fsSL https://claude.ai/install.sh | bash   then run 'claude' once and log in."; fi

step "6) Check"
export PYTHONUTF8=1
for d in channels/*/; do
  n=$(basename "$d"); case "$n" in _*) continue;; esac
  [ -f "$d/channel.json" ] || continue
  [ -f "$d/.env" ] || { cp .env.example "$d/.env"; warn "channels/$n/.env created - fill in your keys"; }
  printf '\n--- %s\n' "$n"; "$VPY" autopilot.py check --channel "$n"
done
printf '\n\033[36mNext:  .venv/bin/python autopilot.py demo              (renders a sample video, no keys needed)\033[0m\n'
printf '\033[36mThen:  .venv/bin/python autopilot.py new-channel <name>  -> docs/QUICKSTART.md\033[0m\n'
