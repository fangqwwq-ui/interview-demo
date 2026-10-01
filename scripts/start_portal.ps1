# Demo D —— 启动统一门户（含指标口径页）
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File E:\interview-demo\scripts\start_portal.ps1
#   → 浏览器打开 http://127.0.0.1:8200
#
# 端口说明：8000 aigc-comic / 8100 Demo B / 8188 ComfyUI / 8200 本门户。

$ErrorActionPreference = "Stop"

$Root = "E:\interview-demo"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Port = 8200

if (-not (Test-Path $VenvPython)) {
    Write-Host "[ERROR] 找不到虚拟环境解释器：$VenvPython" -ForegroundColor Red
    exit 1
}

Write-Host "[1/2] 生成口径数据（metrics / claims / components）..." -ForegroundColor Cyan
Push-Location $Root
try {
    & $VenvPython -c "from common.metrics import registry, claims; from portal import provenance; registry.export_json(); claims.export_json(); provenance.export_json(); print('OK')"
} finally {
    Pop-Location
}

Write-Host "[2/2] 启动门户： http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "      提示：Demo B 界面（:8100）需另开一个窗口启动，本门户会探测其状态" -ForegroundColor DarkGray
Write-Host "      按 Ctrl+C 停止" -ForegroundColor DarkGray

Push-Location $Root
try {
    & $VenvPython -m portal.app
} finally {
    Pop-Location
}
