# 預設播放裝置：記住 / 還原
#   speaker.ps1 save     記下目前的預設播放裝置（不是 ATR2100x 時才記）
#   speaker.ps1 restore  預設播放被換成 ATR2100x 時，切回記下的那個
#   speaker.ps1 show     顯示目前預設與記下的裝置
# ATR2100x 沒有 USB 序號，換插孔就被 Windows 當成新裝置並設為預設播放；這裡負責切回來。
param([ValidateSet('save', 'restore', 'show')][string]$Mode = 'show')

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Cfg = Join-Path $Root 'config\speaker.txt'
$Grab = 'ATR2100'

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
class MMDeviceEnumeratorCo {}

[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
    [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
}

[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    [PreserveSig] int Activate(IntPtr iid, int ctx, IntPtr p, out IntPtr o);
    [PreserveSig] int OpenPropertyStore(int access, out IntPtr ps);
    [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
}

[ComImport, Guid("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")]
class PolicyConfigCo {}

// 只用到 SetDefaultEndpoint；前面的方法只是佔 vtable 位置
[ComImport, Guid("F8679F50-850A-41CF-9C72-430F290290C8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPolicyConfig {
    [PreserveSig] int GetMixFormat();
    [PreserveSig] int GetDeviceFormat();
    [PreserveSig] int ResetDeviceFormat();
    [PreserveSig] int SetDeviceFormat();
    [PreserveSig] int GetProcessingPeriod();
    [PreserveSig] int SetProcessingPeriod();
    [PreserveSig] int GetShareMode();
    [PreserveSig] int SetShareMode();
    [PreserveSig] int GetPropertyValue();
    [PreserveSig] int SetPropertyValue();
    [PreserveSig] int SetDefaultEndpoint([MarshalAs(UnmanagedType.LPWStr)] string id, int role);
}

public static class AudioDefault {
    public static string Get() {
        var en = (IMMDeviceEnumerator)new MMDeviceEnumeratorCo();
        IMMDevice d;
        if (en.GetDefaultAudioEndpoint(0, 1, out d) != 0 || d == null) return null;   // eRender, eMultimedia
        string id; d.GetId(out id); return id;
    }
    public static void Set(string id) {
        var pc = (IPolicyConfig)new PolicyConfigCo();
        for (int role = 0; role < 3; role++) {                                          // console / multimedia / communications
            int hr = pc.SetDefaultEndpoint(id, role);
            if (hr != 0) throw new Exception("SetDefaultEndpoint 0x" + hr.ToString("X8"));
        }
    }
}
'@

function Get-EndpointName([string]$id) {
    if (-not $id) { return '' }
    $guid = ($id -split '\.')[-1]
    $p = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$guid\Properties" -ErrorAction SilentlyContinue
    if (-not $p) { return $id }
    return "$($p.'{a45c254e-df1c-4efd-8020-67d146a850e0},2') ($($p.'{b3f8fa53-0004-438e-9003-51a46e139bfc},6'))"
}

function Test-Active([string]$id) {
    $guid = ($id -split '\.')[-1]
    (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render\$guid" -ErrorAction SilentlyContinue).DeviceState -eq 1
}

$cur = [AudioDefault]::Get()
$curName = Get-EndpointName $cur
$saved = if (Test-Path $Cfg) { (Get-Content $Cfg -Encoding UTF8 -TotalCount 1).Trim() } else { '' }

switch ($Mode) {
    'show' {
        "目前預設：$curName"
        "記下的　：$(if ($saved) { Get-EndpointName $saved } else { '（尚未記）' })"
    }
    'save' {
        if ($curName -match $Grab) { "目前預設是 $curName，不記"; exit 1 }
        New-Item -ItemType Directory -Force -Path (Split-Path $Cfg) | Out-Null
        Set-Content -Path $Cfg -Value $cur -Encoding UTF8
        "已記下：$curName"
    }
    'restore' {
        if ($curName -notmatch $Grab) { "預設是 $curName，不用切"; exit 0 }
        if (-not $saved) { "還沒記下要切回哪個裝置（先跑 save）"; exit 2 }
        if (-not (Test-Active $saved)) { "記下的裝置目前不在：$(Get-EndpointName $saved)"; exit 3 }
        [AudioDefault]::Set($saved)
        "已切回：$(Get-EndpointName ([AudioDefault]::Get()))"
    }
}
