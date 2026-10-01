# run_all.ps1 - launch every service in its own window
#
# Ports:
#   8188  ComfyUI          (D:\ComfyUI)      - image generation backend
#   8000  aigc-comic       (E:\aigc-comic)   - legacy manju execute service
#   11434 Ollama                             - local LLM fallback
#   8001  portal                             - demo portal (added in P4)

$Root = Split-Path -Parent $PSScriptRoot
$S = Join-Path $Root "scripts"

$targets = @(
    "start_comfyui.ps1",
    "start_aigc_comic.ps1",
    "start_ollama.ps1"
)

foreach ($t in $targets) {
    $f = Join-Path $S $t
    if (-not (Test-Path $f)) { Write-Host "skip (missing): $t"; continue }
    Write-Host "launching: $t"
    Start-Process powershell -ArgumentList @(
        "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$f`""
    )
}

Write-Host ""
Write-Host "services launching in separate windows."
Write-Host "check:  curl http://127.0.0.1:8188/system_stats"
Write-Host "        curl http://127.0.0.1:8000/health"
Write-Host "        curl http://127.0.0.1:11434/api/tags"
