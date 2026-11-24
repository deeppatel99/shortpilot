# ShortPilot - one-time Windows setup (the same for every channel in channels\).  Run in PowerShell from this folder:
#   powershell -ExecutionPolicy Bypass -File .\setup.ps1
# Uses Python 3.12 on purpose: brand-new Pythons (3.14/3.15) often have no ready-made wheels yet for onnxruntime/numpy.
$ErrorActionPreference = "Continue"
Set-Location $PSScriptRoot
function Ok($m) { Write-Host "  [OK] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  [!!] $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "  [XX] $m" -ForegroundColor Red; exit 1 }

function Find-Py {
  foreach ($v in @("3.12", "3.11", "3.13")) {
    try { $o = & py "-$v" --version 2>&1 | Out-String; if ($LASTEXITCODE -eq 0 -and $o -match "Python $([regex]::Escape($v))") { return $v } } catch {}
  }
  return $null
}

Write-Host "`n1) Python 3.12" -ForegroundColor Cyan
$pv = Find-Py
if (-not $pv) {
  if (Get-Command py -ErrorAction SilentlyContinue) { Warn "Installing Python 3.12 with the Python install manager (py install 3.12)..."; & py install 3.12 }
  $pv = Find-Py
  if (-not $pv) { Warn "Trying winget..."; winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements; $pv = Find-Py }
  if (-not $pv) { Fail "Could not get Python 3.12. Install it from python.org (3.12.x), reopen PowerShell, run setup again." }
}
Ok "using Python $pv (py -$pv)"

Write-Host "`n2) Virtual env + packages" -ForegroundColor Cyan
if (Test-Path ".venv\pyvenv.cfg") {
  $cfg = Get-Content ".venv\pyvenv.cfg" -Raw
  if ($cfg -notmatch "version\s*=\s*3\.1[0-3]\.") { Warn "Existing .venv uses an unsupported Python - recreating it"; Remove-Item -Recurse -Force ".venv" }
}
if (-not (Test-Path ".venv")) { & py "-$pv" -m venv .venv; if ($LASTEXITCODE -ne 0) { Fail "venv creation failed" } }
& .\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Fail "pip install failed - see the red error above (or paste it into Claude Code)." }
# Chromium for the HTML renderer (render.engine "html")
& .\.venv\Scripts\python.exe -m playwright install chromium
if ($LASTEXITCODE -ne 0) { Warn "playwright could not download Chromium - set PW_CHROMIUM to a chrome.exe, or use render.engine classic" }
& .\.venv\Scripts\python.exe -c "import kokoro_onnx, numpy, PIL, soundfile, requests; print('imports ok')"
if ($LASTEXITCODE -ne 0) { Fail "packages installed but import failed - see the error above." }
Ok "packages installed"

Write-Host "`n3) FFmpeg" -ForegroundColor Cyan
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Ok "ffmpeg found" }
else {
  Warn "ffmpeg not found. Installing with winget (Gyan.FFmpeg)..."
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  Warn "Close and reopen PowerShell after this script so ffmpeg is on PATH."
}

Write-Host "`n4) Voice model (Kokoro, ~350 MB, one time)" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path models | Out-Null
$base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
$sizes = @{ "kokoro-v1.0.onnx" = 300MB; "voices-v1.0.bin" = 25MB }
$ProgressPreference = "SilentlyContinue"
foreach ($f in $sizes.Keys) {
  $p = "models\$f"
  if ((Test-Path $p) -and ((Get-Item $p).Length -ge $sizes[$f])) { Ok "$f present" }
  else {
    if (Test-Path $p) { Remove-Item $p -Force }
    Write-Host "  downloading $f ..."; Invoke-WebRequest "$base/$f" -OutFile $p; Ok "$f downloaded"
  }
}
Remove-Item "models\.keep" -ErrorAction SilentlyContinue

Write-Host "`n5) Claude Code CLI (writes scripts with your Claude plan)" -ForegroundColor Cyan
if (Get-Command claude -ErrorAction SilentlyContinue) { Ok "claude found - run 'claude' once and log in if you haven't" }
else { Warn "claude not found. Install:  irm https://claude.ai/install.ps1 | iex   then run 'claude' once and log in." }

Write-Host "`n6) .env" -ForegroundColor Cyan
# every channel keeps its own keys: channels\<name>\.env
$chans = @(Get-ChildItem channels -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch "^_" -and (Test-Path "$($_.FullName)\channel.json") })
foreach ($c in $chans) {
  $e = Join-Path $c.FullName ".env"
  if (-not (Test-Path $e)) { Copy-Item ".env.example" $e; Warn "channels\$($c.Name)\.env created - fill TELEGRAM_BOT_TOKEN etc." } else { Ok "channels\$($c.Name)\.env exists" }
}

Write-Host "`n7) Check" -ForegroundColor Cyan
$env:PYTHONUTF8 = "1"
foreach ($c in $chans) { Write-Host "`n--- $($c.Name)" -ForegroundColor Cyan; & .\.venv\Scripts\python.exe autopilot.py check --channel $c.Name }
Write-Host "`nNext:  .\.venv\Scripts\python.exe autopilot.py demo      (renders a sample video, no keys needed)" -ForegroundColor Cyan
Write-Host "Then:  .\.venv\Scripts\python.exe autopilot.py new-channel <name>   -> docs\QUICKSTART.md" -ForegroundColor Cyan
