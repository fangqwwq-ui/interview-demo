# bootstrap_env.ps1 - create the project venv and install all dependencies
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_env.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap_env.ps1 -SkipTorch
#
# Notes:
#   - pip mirror: Tsinghua is unusable on this machine, Aliyun works.
#   - pip MUST use --no-cache-dir: the sandbox bulk-delete guard blocks pip's
#     cache cleanup and the install then dies with SystemExit: 1.
#
#   - Demo A's vision/ASR no longer needs torch at all (ONNX + CTranslate2 stack,
#     ~500 MB instead of ~2.7 GB). It is installed by a SEPARATE script because
#     it has strict ordering constraints:
#         python scripts\setup_demo_a_vision.py
#     Do NOT put vision.txt back into the pip -r list below — insightface and
#     faster-whisper both pull the CPU-only `onnxruntime`, which silently
#     overwrites the GPU build (and the sandbox blocks the uninstall needed to
#     repair it). See requirements/vision.txt for details.
#
#   - torch (CPU) is still needed by sentence-transformers for the bge encoder.

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

Write-Host "== [1/6] virtualenv =="
if (-not (Test-Path $VenvPy)) {
    & $BasePy -m venv $Venv
    Write-Host "created: $Venv"
} else {
    Write-Host "exists, skip: $Venv"
}

Write-Host "== [2/6] pip tooling =="
& $VenvPy -m pip install --upgrade pip setuptools wheel -i $Aliyun --no-cache-dir

Write-Host "== [3/6] base =="
& $VenvPy -m pip install -r (Join-Path $Root "requirements\base.txt") -i $Aliyun --no-cache-dir

# torch: CPU build is enough for sentence-transformers (the bge encoder runs on
# CPU in this project). The cu128 wheel is ~2.7 GB at ~343 KB/s == ~137 min, and
# Demo A no longer needs it at all (ONNX + CTranslate2 instead).
if ($SkipTorch) {
    Write-Host "== [4/6] torch SKIPPED (-SkipTorch) =="
    Write-Host "   NOTE: nlp.txt needs torch; it will be skipped too."
} else {
    Write-Host "== [4/6] torch (CPU) =="
    & $VenvPy -m pip install torch --no-cache-dir -i $Aliyun
}

Write-Host "== [5/6] nlp / audio / demo_c_runtime =="
$files = @("audio.txt", "demo_c_runtime.txt")
if (-not $SkipTorch) {
    $files = @("nlp.txt", "audio.txt", "demo_c_runtime.txt")
} else {
    Write-Host "   (SkipTorch: nlp.txt skipped - it requires torch)"
}
foreach ($f in $files) {
    Write-Host "   -> $f"
    & $VenvPy -m pip install -r (Join-Path $Root "requirements\$f") -i $Aliyun --no-cache-dir
}

Write-Host "== [6/6] dev =="
& $VenvPy -m pip install -r (Join-Path $Root "requirements\dev.txt") -i $Aliyun --no-cache-dir

Write-Host ""
Write-Host "== NEXT: Demo A vision/ASR (ONNX stack, no torch needed) =="
Write-Host "   & $VenvPy scripts\setup_demo_a_vision.py"
Write-Host "   (must be a separate step - ordering matters, see requirements\vision.txt)"

Write-Host ""
Write-Host "== verify =="
& $VenvPy -c "import sys; print('python ', sys.version.split()[0])"
& $VenvPy -c "import torch; print('torch  ', torch.__version__, '| cuda', torch.cuda.is_available())"
& $VenvPy -c "import faiss, sentence_transformers, fastapi; print('faiss ', faiss.__version__)"
& $VenvPy -c "import onnxruntime as o; p=o.get_available_providers(); print('ort    ', o.__version__, '| CUDA', 'CUDAExecutionProvider' in p)"

Write-Host ""
Write-Host "done."
Write-Host "activate with:  $Venv\Scripts\Activate.ps1"