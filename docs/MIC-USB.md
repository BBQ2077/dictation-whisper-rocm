# USB 麥克風抓不到：成因與處理

適用：USB 麥克風（以 Audio-Technica ATR2100x-USB 為例）插上後燈亮、有供電，Windows 卻認不到，要反覆拔插才成功。

## 1. 症狀

- 裝置管理員出現 `未知的 USB 裝置 (要求裝置描述元失敗)`／`Unknown USB Device (Device Descriptor Request Failed)`，代碼 43，
  ID 形如 `USB\VID_0000&PID_0002\...`。
- 代表電氣連線已建立，**USB 列舉（讀取裝置描述元）失敗**；驅動還沒載入，所以不是驅動問題。
- 一旦列舉成功就穩定可用；多試幾次總會成功一次。

用 `tools/usb-watch.ps1 -Seconds 180` 可以把插拔過程中裝置的出現、消失、錯誤碼逐一記到 `logs/usbwatch.log`。

## 2. 可能原因（依可能性排序）

1. **晶片組 USB 孔與裝置的列舉相容性／訊號品質**
   - 社群多例：AMD B550/X570 晶片組孔上的 USB 2.0 裝置出現「device descriptor request failed」，
     同裝置接 CPU 直出孔正常（MSI 論壇 B550-A PRO 討論串、r/Amd「USB issues on B550/X570」）。
   - 前面板孔經機殼內部排線接到主機板，線長與接點增加，列舉時序與訊號裕度更差。
   - 「多試幾次總會成功」符合邊緣訊號品質的特徵。
2. **Windows USB 堆疊更新**：對照症狀開始的時間與 Windows Update、USB 驅動版本。
3. **麥克風 USB-C 座或線材磨損**：插緊後燈恆亮、成功後穩定，可能性較低。

## 3. 處理

### 一鍵重新偵測：`tools/mic-redetect.bat`

雙擊執行，會跳一次系統管理員確認。由輕到重，抓到就停：

1. 有握手失敗的裝置時，120 秒內輪流：
   - `pnputil /remove-device <該裝置>` + `pnputil /scan-devices`（主要做法，只動這一個裝置，同控制器上的滑鼠等不會斷線）
   - `pnputil /restart-device <該裝置>`
2. `pnputil /scan-devices`
3. 重啟麥克風所在的 USB 控制器（最多 3 次；同控制器上的裝置會斷線 2～3 秒）

都失敗才請你實體拔插一次。完成後自動把預設播放切回記下的喇叭。紀錄寫在 `logs/redetect.log`。

- 只看狀態不執行：`powershell -ExecutionPolicy Bypass -File tools\mic-redetect.ps1 -DryRun`
- 其他麥克風：加 `-MicName <音訊裝置名稱的一部分> -MicId 'USB\VID_xxxx&PID_xxxx\*'`

各做法單獨使用的效果：

| 做法 | 對「描述元失敗」的效果 |
|---|---|
| `pnputil /remove-device <失敗裝置>` 後再掃描 | 實際救回過 |
| `pnputil /scan-devices` 單獨使用 | 無效 |
| `pnputil /restart-device <失敗裝置>` | 會觸發重新列舉，不一定成功 |

不建議連同麥克風的舊裝置紀錄一起移除：會讓 Windows 重建音訊端點，並把它設成預設播放裝置。

### 接上後喇叭被換掉：`tools/speaker.ps1`

ATR2100x 沒有 USB 序號，Windows 依插孔位置辨識；換一個孔就是新裝置，Windows 會把新音訊裝置設成預設播放（麥克風的耳機孔）。

- `speaker.ps1 save`：記下目前的預設播放裝置（存到 `config/speaker.txt`）
- `speaker.ps1 restore`：預設播放被換成麥克風時切回
- `speaker.ps1 show`：顯示目前狀態

Dictation 開著時會自動做 `restore`。固定插同一個孔，Windows 就不會一直建立新裝置。

### Dictation 自動接上

Dictation 每 2 秒讀一次 MMDevices 登錄判斷麥克風是否在線，系統一認到就重新初始化 PortAudio、重列裝置並選用，不用按「重新掃描」。

## 4. 還可以試的方向

1. **埠電源循環（Port Cycle）**：`IOCTL_USB_HUB_CYCLE_PORT`（0x220444）對麥克風所在的 hub 埠下指令，比 `restart-device` 更接近實體拔插。
   可用 ctypes 自寫（開啟 `GUID_DEVINTERFACE_USB_HUB` 介面，設定 `USB_CYCLE_PORT_PARAMS.ConnectionIndex`，需要系統管理員權限），
   或用 Uwe Sieber 的 `RestartUsbPort`（https://www.uwe-sieber.de/misc_tools_e.html）。
2. **對照系統更新**：Windows Update 紀錄（`WindowsUpdateClient` 事件 19）、AMD 晶片組驅動與 BIOS 版本。
3. **背景自動修復**：登入時以系統管理員權限常駐的工作排程，偵測到握手失敗就自動跑第 1 步。
