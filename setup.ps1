# Dictation 安裝：建立 .venv、安裝套件、下載 whisper.cpp 引擎與模型。已存在的部分會略過。
#   -Engine  gfx120X（RX 9000，預設）／gfx110X（RX 7000）／gfx1150／gfx1151／vulkan-x64／cpu-x64
#   -Model   ggml-large-v3-turbo（預設）／ggml-medium／ggml-small …（https://huggingface.co/ggerganov/whisper.cpp）
param(
    [string]$Engine = 'gfx120X',
    [string]$Model = 'ggml-large-v3-turbo'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$Root = $PSScriptRoot
$WhisperVer = 'v1.8.4'

function Step([string]$m) { Write-Host "`n== $m" -ForegroundColor Cyan }

# 1. Python 虛擬環境
Step 'Python 環境（.venv）'
$venvPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    if (Get-Command py -ErrorAction SilentlyContinue) { $pyExe = 'py'; $pyArgs = @('-3') }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyExe = 'python'; $pyArgs = @() }
    else { throw '找不到 Python 3.10 以上，請先到 https://www.python.org/downloads/ 安裝（勾選 Add to PATH）。' }
    & $pyExe @pyArgs -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE) { throw '建立 .venv 失敗' }
}
& $venvPy -m pip install --disable-pip-version-check -q -r (Join-Path $Root 'requirements.txt')
& $venvPy -c "import sys, numpy, sounddevice; print('Python', sys.version.split()[0], '/ numpy', numpy.__version__, '/ sounddevice', sounddevice.__version__)"

# 2. whisper.cpp 引擎（lemonade-sdk/whisper.cpp-rocm）
Step "whisper.cpp 引擎（$Engine）"
$binDir = Join-Path $Root 'bin\whisper'
if (Test-Path (Join-Path $binDir 'whisper-server.exe')) {
    Write-Host '已存在，略過'
} else {
    $name = if ($Engine -like 'gfx*') { "whisper-$WhisperVer-windows-rocm-$Engine.zip" } else { "whisper-$WhisperVer-windows-$Engine.zip" }
    $url = "https://github.com/lemonade-sdk/whisper.cpp-rocm/releases/download/$WhisperVer/$name"
    $zip = Join-Path $env:TEMP $name
    Write-Host "下載 $url"
    curl.exe -L --fail -o $zip $url
    if ($LASTEXITCODE) { throw "下載失敗：$url" }
    New-Item -ItemType Directory -Force -Path $binDir | Out-Null
    Expand-Archive -Path $zip -DestinationPath $binDir -Force
    Remove-Item $zip
    if (-not (Test-Path (Join-Path $binDir 'whisper-server.exe'))) { throw "解壓後找不到 bin\whisper\whisper-server.exe" }
}

# 3. 模型
Step "模型（$Model）"
$modelDir = Join-Path $Root 'models'
$modelFile = Join-Path $modelDir "$Model.bin"
if (Test-Path $modelFile) {
    Write-Host '已存在，略過'
} else {
    New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
    $url = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/$Model.bin"
    Write-Host "下載 $url"
    curl.exe -L --fail -o "$modelFile.part" $url
    if ($LASTEXITCODE) { throw "下載失敗：$url" }
    Move-Item "$modelFile.part" $modelFile
}
if ($Model -ne 'ggml-large-v3-turbo') {
    Write-Host "注意：程式預設讀 models\ggml-large-v3-turbo.bin，換模型要改 whisper_srv.py 與 record.py 的 DEFAULT_MODEL。" -ForegroundColor Yellow
}

Step '完成：雙擊 run.bat 啟動'
