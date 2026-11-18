# USB 麥克風重新偵測（預設 Audio-Technica ATR2100x-USB）
# 點一次自動由輕到重：移除握手失敗的裝置並重新掃描（連續重試 120 秒）→ 重新掃描硬體 → 重啟麥克風所在的 USB 控制器（最多 3 次）
# 抓到就停。全部用 Windows 內建 pnputil，需要系統管理員權限（會跳一次 UAC）。
# -DryRun：不需管理員，只顯示狀態和會執行的動作。
# 其他麥克風：-MicName <音訊端點名稱的一部分> -MicId 'USB\VID_xxxx&PID_xxxx\*'
param(
    [switch]$DryRun,
    [string]$MicName = 'ATR2100',
    [string]$MicId = 'USB\VID_0909&PID_004D\*'
)

$ErrorActionPreference = 'SilentlyContinue'
$Host.UI.RawUI.WindowTitle = '麥克風重新偵測'
$Root = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $Root 'logs\redetect.log'

# ---------- 提權 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $DryRun) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"",
        '-MicName', "`"$MicName`"", '-MicId', "`"$MicId`"")
    exit
}

function Say([string]$m, [string]$color = 'Gray') {
    $line = '[{0:HH:mm:ss}] {1}' -f (Get-Date), $m
    Write-Host $line -ForegroundColor $color
    New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
    Add-Content -Path $Log -Value ('[{0:yyyy-MM-dd}] {1}' -f (Get-Date), $line) -Encoding UTF8
}

function Test-MicReady {
    [bool](Get-PnpDevice -Class AudioEndpoint -PresentOnly |
        Where-Object { $_.FriendlyName -match $MicName -and $_.Status -eq 'OK' })
}

function Wait-Mic([int]$sec) {
    $end = (Get-Date).AddSeconds($sec)
    while ((Get-Date) -lt $end) {
        if (Test-MicReady) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Get-ProblemDevices {
    Get-PnpDevice -PresentOnly | Where-Object {
        $_.Status -ne 'OK' -and (
            $_.InstanceId -like $MicId -or
            $_.InstanceId -like 'USB\VID_0000&PID_000*' -or
            $_.FriendlyName -match 'Unknown USB|未知的 USB|無法辨識|Device Descriptor|描述元')
    }
}

function Get-MicController {
    # 從「最近一次接上的 ATR2100x」往上找：USB 裝置 → 根集線器 → xHCI 控制器
    $last = Get-PnpDevice | Where-Object { $_.InstanceId -like $MicId } | ForEach-Object {
        [pscustomobject]@{
            Id   = $_.InstanceId
            When = (Get-PnpDeviceProperty -InstanceId $_.InstanceId -KeyName DEVPKEY_Device_LastArrivalDate).Data
        }
    } | Sort-Object When -Descending | Select-Object -First 1
    if (-not $last) { return $null }
    $id = $last.Id
    for ($i = 0; $i -lt 4; $i++) {
        $id = (Get-PnpDeviceProperty -InstanceId $id -KeyName DEVPKEY_Device_Parent).Data
        if (-not $id) { return $null }
        if ($id -like 'PCI\*') { return $id }
    }
    return $null
}

function Run-Pnp([string[]]$a) {
    if ($DryRun) { Say ("（試跑）pnputil " + ($a -join ' ')) 'DarkGray'; return }
    $out = (& pnputil.exe @a 2>&1 | Where-Object { "$_".Trim() }) -join ' / '
    Say "  pnputil → $out (exit $LASTEXITCODE)" 'DarkGray'
}

function Done([bool]$ok, [string]$how) {
    if ($ok) {
        Say "麥克風已就緒（$how）。Dictation 會在 3 秒內自動接上。" 'Green'
        # Windows 會在新端點出現後才把預設播放換過去，等一下再切回喇叭
        Start-Sleep -Milliseconds 1500
        $sp = Join-Path $PSScriptRoot 'speaker.ps1'
        if (-not $DryRun) { Say ((& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $sp restore) -join ' ') }
        Start-Sleep -Seconds 3
    } else {
        Say '軟體方式都試過仍抓不到：請實體拔插一次，或換另一個 USB 孔。' 'Yellow'
        Read-Host '按 Enter 關閉'
    }
    exit
}

# ---------- 開始 ----------
Say ('開始' + $(if ($DryRun) { '（試跑，不實際執行）' } else { '' })) 'Cyan'

if (Test-MicReady) { Done $true '本來就在線' }

# 第 1 步：插著但握手失敗（未知的 USB 裝置 / 要求裝置描述元失敗）
#   部分晶片組 USB 孔與 USB 2.0 裝置偶爾才握手成功 → 連續重新列舉，等於自動重插，成功就停。
#   單數次 restart-device，雙數次 remove-device + scan-devices（換方式重來）。
$MaxSec = 120
$bad = @(Get-ProblemDevices)
if ($bad.Count) {
    $deadline = (Get-Date).AddSeconds($MaxSec)
    $n = 0
    while ((Get-Date) -lt $deadline) {
        if (Test-MicReady) { Done $true "第 1 步，第 $n 次重試成功" }
        $bad = @(Get-ProblemDevices)
        if (-not $bad.Count) {            # 正在重新列舉中，等它出結果
            if (Wait-Mic 2) { Done $true "第 1 步，第 $n 次重試成功" }
            continue
        }
        $n++
        foreach ($d in $bad) {
            # 主要做法：移除握手失敗的那一個裝置再掃描（只動這一個裝置，同控制器上的滑鼠等不會斷線）
            if ($n % 2) {
                Say "第 1 步（第 $n 次）：移除後重新掃描 $($d.FriendlyName)"
                Run-Pnp @('/remove-device', $d.InstanceId)
                Run-Pnp @('/scan-devices')
            } else {
                Say "第 1 步（第 $n 次）：重新列舉 $($d.FriendlyName)"
                Run-Pnp @('/restart-device', $d.InstanceId)
            }
        }
        if ($DryRun) { break }
        if (Wait-Mic 4) { Done $true "第 1 步，第 $n 次重試成功" }
    }
    Say "第 1 步：$MaxSec 秒內重試 $n 次都沒握手成功" 'Yellow'
} else {
    Say '第 1 步：沒有握手失敗的裝置，略過'
}

# 第 2 步：重新掃描硬體
Say '第 2 步：重新掃描硬體'
Run-Pnp @('/scan-devices')
if (Wait-Mic 6) { Done $true '第 2 步' }

# 第 3 步：重啟麥克風所在的 USB 控制器（同控制器上的滑鼠等裝置會斷線 2～3 秒）
$ctrl = Get-MicController
if (-not $ctrl) {
    Say '第 3 步：找不到麥克風所在的控制器，略過' 'Yellow'
    Done $false ''
}
$ctrlName = (Get-PnpDevice -InstanceId $ctrl).FriendlyName
for ($n = 1; $n -le 3; $n++) {
    Say "第 3 步（$n/3）：重啟 USB 控制器 $ctrlName（滑鼠可能斷 2～3 秒）"
    Run-Pnp @('/restart-device', $ctrl)
    if ($DryRun) { break }
    if (Wait-Mic 10) { Done $true "第 3 步第 $n 次" }
}
Done $false ''
