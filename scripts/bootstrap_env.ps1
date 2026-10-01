# bootstrap_env.ps1 - create the project venv and install all dependencies
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_env.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_env.ps1 -SkipTorch
#
# Notes:
#   - pip mirror: Tsinghua is unusable on this machine, Aliyun works.
#   - torch MUST come from the cu128 index: RTX 5070 Laptop is Blackwell (sm_120),
#     older wheels fail with "no kernel image is available".

param(
    [switch]$SkipTorch
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$BasePy = "C:\Users\18751\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if (-not (Test-Path $BasePy)) {
    $BasePy = "C:\Python314\python.exe"
}
if (-not (Test-Path $BasePy)) { throw "No usable base python found." }

$Venv    = Join-Path $Root ".venv"
$VenvPy  = Join-Path $Venv "Scripts\python.exe"
$Aliyun  = "https://mirrors.aliyun.com/pypi/simple/"
$TorchIdx = "https://download.pytorch.org/whl/cu128"

Write-Host "== [1/6] virtualenv =="
if (-not (Test-Path $VenvPy)) {
    & $BasePy -m venv $Venv
    Write-Host "created: $Venv"
} else {
    Write-Host "exists, skip: $Venv"
}

Write-Host "== [2/6] pip tooling =="
& $VenvPy -m pip install --upgrade pip setuptools wheel -i $Aliyun

Write-Host "== [3/6] base =="
& $VenvPy -m pip install -r (Join-Path $Root "requirements\base.txt") -i $Aliyun

if ($SkipTorch) {
    Write-Host "== [4/6] torch SKIPPED (-SkipTorch) =="
} else {
    Write-Host "== [4/6] torch cu128 (~2.5 GB, required for RTX 5070 / sm_120) =="
    & $VenvPy -m pip install torch torchvision --index-url $TorchIdx
}

Write-Host "== [5/6] nlp / audio / demo_c_runtime =="
# NOTE: nlp.txt (sentence-transformers) and vision.txt both depend on torch.
# Installing them before torch would pull a CPU-only wheel from the mirror and
# conflict with the cu128 build. They are therefore gated behind -SkipTorch.
$files = @("audio.txt", "demo_c_runtime.txt")
if (-not $SkipTorch) {
    $files = @("nlp.txt", "audio.txt", "demo_c_runtime.txt", "vision.txt")
} else {
    Write-Host "   (SkipTorch: nlp.txt and vision.txt skipped - run again without -SkipTorch to install them)"
}
foreach ($f in $files) {
    Write-Host "   -> $f"
    & $VenvPy -m pip install -r (Join-Path $Root "requirements\$f") -i $Aliyun
}

Write-Host "== [6/6] dev =="
& $VenvPy -m pip install -r (Join-Path $Root "requirements\dev.txt") -i $Aliyun

Write-Host ""
Write-Host "== verify =="
& $VenvPy -c "import sys; print('python ', sys.version.split()[0])"
& $VenvPy -c "import torch; print('torch  ', torch.__version__, '| cuda', torch.cuda.is_available(), '|', (torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU'))"
& $VenvPy -c "import faiss, sentence_transformers, fastapi; print('faiss', faiss.__version__)"

Write-Host ""
Write-Host "done."
Write-Host "activate with:  $Venv\Scripts\Activate.ps1"
