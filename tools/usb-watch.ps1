# USB 插拔監測：每 0.5 秒比對一次 USB 裝置清單，有變化就記下（含「無法辨識的 USB 裝置」）
param([int]$Seconds = 180)
$Root = Split-Path -Parent $PSScriptRoot
$Log = Join-Path $Root 'logs\usbwatch.log'
function Snap {
    $h = @{}
    Get-CimInstance Win32_PnPEntity -Filter "PNPDeviceID LIKE 'USB%' OR PNPDeviceID LIKE 'SWD\\MMDEVAPI%'" |
        ForEach-Object { $h[$_.PNPDeviceID] = "$($_.Name) [err=$($_.ConfigManagerErrorCode)]" }
    $h
}
function W($m) { $l = '[{0:HH:mm:ss.f}] {1}' -f (Get-Date), $m; $l; Add-Content $Log $l -Encoding UTF8 }
W "=== 監測開始 $Seconds 秒 ==="
$prev = Snap
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
    Start-Sleep -Milliseconds 500
    $cur = Snap
    foreach ($k in $cur.Keys) {
        if (-not $prev.ContainsKey($k)) { W "+ 出現 $($cur[$k])  $k" }
        elseif ($prev[$k] -ne $cur[$k]) { W "~ 變化 $($cur[$k])  $k" }
    }
    foreach ($k in $prev.Keys) { if (-not $cur.ContainsKey($k)) { W "- 消失 $($prev[$k])  $k" } }
    $prev = $cur
}
W '=== 監測結束 ==='
