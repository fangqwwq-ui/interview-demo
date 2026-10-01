# start_comfyui.ps1 - start the existing ComfyUI service (read-only reuse of D:\ComfyUI)
# Do NOT modify anything under $ComfyHome.

$ErrorActionPreference = "Stop"

$ComfyHome = if ($env:COMFYUI_HOME) { $env:COMFYUI_HOME } else { "D:\ComfyUI" }
$Py = Join-Path $ComfyHome "venv\Scripts\python.exe"

if (-not (Test-Path $Py)) { throw "ComfyUI venv python not found: $Py" }
if (-not (Test-Path (Join-Path $ComfyHome "main.py"))) { throw "main.py not found under: $ComfyHome" }

Set-Location $ComfyHome
Write-Host "starting ComfyUI -> http://127.0.0.1:8188"
& $Py main.py --listen 127.0.0.1 --port 8188
