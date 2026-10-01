# start_aigc_comic.ps1 - start the legacy manju pipeline service (E:\aigc-comic)
#
# This service is REUSED, NOT MODIFIED.
# It is run with the project venv, which carries its runtime deps
# (edge-tts / Pillow / requests) per requirements\demo_c_runtime.txt.

$ErrorActionPreference = "Stop"

$Root   = Split-Path -Parent $PSScriptRoot
$Legacy = if ($env:AIGC_COMIC_HOME) { $env:AIGC_COMIC_HOME } else { "E:\aigc-comic" }
$Py     = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) { throw "project venv not found. Run scripts\bootstrap_env.ps1 first." }
if (-not (Test-Path (Join-Path $Legacy "app.py"))) { throw "app.py not found under: $Legacy" }

Set-Location $Legacy
Write-Host "starting aigc-comic (legacy, unmodified) -> http://127.0.0.1:8000"
& $Py -m uvicorn app:app --host 127.0.0.1 --port 8000
