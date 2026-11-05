# 构建并测试 C++ 风险引擎（Windows x86-64）。
#
# 前置条件（尚未在本仓库内验证，因为开发机为 macOS）：
#   * Visual Studio 2022 Build Tools（含 MSVC C++ 工作负载）
#   * CMake >= 3.16 并加入 PATH
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File scripts\build_risk_engine.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\build_risk_engine.ps1 -BuildType Debug

param(
    [string]$BuildType = "Release"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location (Join-Path $Root "risk_engine")

$cmake = Get-Command cmake -ErrorAction SilentlyContinue
if (-not $cmake) {
    Write-Error "未找到 cmake。请安装 CMake 3.16+ 并加入 PATH。"
    exit 1
}

Write-Host "==> 配置 ($BuildType)"
cmake -S . -B build -DCMAKE_BUILD_TYPE=$BuildType
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "==> 构建"
cmake --build build --config $BuildType
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# MSVC 是多配置生成器，产物在 build\<Config>\ 下
$exe = Join-Path "build" "$BuildType\rock_risk_tests.exe"
if (-not (Test-Path $exe)) { $exe = "build\rock_risk_tests.exe" }

Write-Host "==> 单元测试"
& $exe
exit $LASTEXITCODE
