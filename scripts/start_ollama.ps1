# start_ollama.ps1 - start the local LLM fallback channel
#
# Ollama is NOT installed on this machine yet.
# Install from https://ollama.com/download , then:
#   ollama pull qwen3:8b

$ErrorActionPreference = "Stop"

$Exe = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
if (-not (Test-Path $Exe)) {
    $cmd = Get-Command ollama -ErrorAction SilentlyContinue
    if ($cmd) {
        $Exe = $cmd.Source
    } else {
        throw "Ollama not installed. Get it from https://ollama.com/download then rerun."
    }
}

Write-Host "starting ollama -> http://127.0.0.1:11434"
& $Exe serve
