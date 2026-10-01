# Demo B —— 启动三路对照演示服务
#
# 位置说明：本工程实体在 E:\interview-demo，C 盘工作区只放指针与 memory。
#
# 用法：
#   powershell -ExecutionPolicy Bypass -File E:\interview-demo\scripts\start_demo_b.ps1
#   → 浏览器打开 http://127.0.0.1:8100
#
# 为什么端口是 8100：
#   8000 被既有资产 aigc-comic 占用，8188 被 ComfyUI 占用，避开冲突。

$ErrorActionPreference = "Stop"

$Root = "E:\interview-demo"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$Port = 8100

if (-not (Test-Path $VenvPython)) {
    Write-Host "[ERROR] 找不到虚拟环境解释器：$VenvPython" -ForegroundColor Red
    Write-Host "        请先运行 scripts\bootstrap_env.ps1" -ForegroundColor Yellow
    exit 1
}

# 索引不存在时先构建（幂等，成本约 1 秒）
$BuildInfo = Join-Path $Root "data\index\demo_b\build_info.json"
if (-not (Test-Path $BuildInfo)) {
    Write-Host "[1/2] 索引不存在，先执行语料清洗与索引构建 ..." -ForegroundColor Cyan
    & $VenvPython -m demo_b_copywriting.ingest
    & $VenvPython -m demo_b_copywriting.build_index
} else {
    Write-Host "[1/2] 索引已存在，跳过构建" -ForegroundColor DarkGray
}

Write-Host "[2/2] 启动服务： http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "      按 Ctrl+C 停止" -ForegroundColor DarkGray

Push-Location $Root
try {
    & $VenvPython -m demo_b_copywriting.app
} finally {
    Pop-Location
}
