# Dictation — 本機語音輸入（whisper.cpp on AMD ROCm）

按住熱鍵說話，文字直接打進滑鼠游標所在的輸入框。可以選放開按鍵後一次出字，或邊說邊出字，也可以同時寫進 TXT。
辨識全部在本機顯示卡上跑，不連網、不需要帳號。

Push-to-talk dictation for Windows: hold a hotkey, speak, and the text is typed into whatever text box has focus — either after you release the key or live while you speak. Runs [whisper.cpp](https://github.com/ggml-org/whisper.cpp) locally on AMD GPUs via ROCm (prebuilt by [lemonade-sdk](https://github.com/lemonade-sdk/whisper.cpp-rocm)); no internet, no account.

## 功能

- **打進任何輸入框**：瀏覽器、聊天軟體、Word、編輯器都可以。用「貼上」（剪貼簿＋Ctrl+V，結束後還原剪貼簿）或「模擬按鍵」（不碰剪貼簿）。
- **三種出字時機**：
  - 放開後一次出：放開說話鍵才整段辨識、一次輸入。
  - 邊說邊出：A 分段：一停頓就出一段（停頓門檻預設 0.25 秒）；一直沒停頓，講到最長秒數（預設 3 秒）也會在最安靜的地方切開出字。
  - 邊說邊出：B 即時：約每秒重新辨識一次，連續兩次結果一致的部分先打出，不用停頓。
- **標點由 whisper 依語意判斷**：會出現「，」「？」「……」，不用為了標點特意停頓；輸出維持繁體中文。
- **注音等中文輸入法相容**：模擬按鍵送字時暫時切成英文鍵盤配置，打完切回原輸入法，標點不會被輸入法攔下。
- **USB 麥克風熱插拔**：開著時每 2 秒檢查一次，麥克風插上就自動接上。接上後如果 Windows 把預設播放換成麥克風的耳機孔，會自動切回原本的喇叭。
- **轉音檔**：WAV、MP3、FLAC、OGG、M4A。
- 語言：中文（預設）、英文、日文、韓文、自動偵測。

## 需求

| 項目 | 說明 |
|---|---|
| 系統 | Windows 10／11 64 位元 |
| 顯示卡 | AMD Radeon。ROCm 版本有 gfx120X（RX 9000）、gfx110X（RX 7000）、gfx1150、gfx1151；其他顯示卡可用 Vulkan 或 CPU 版 |
| Python | 3.10 以上（測試環境為 3.11） |
| 空間 | 約 2.2 GB（引擎約 0.5 GB、模型 1.6 GB） |

不需要另外安裝 ROCm，引擎壓縮檔已經內附 ROCm 執行環境。

## 安裝

1. 下載 [最新版本](https://github.com/BBQ2077/dictation-whisper-rocm/releases/latest) 的原始碼壓縮檔並解壓，或用 `git clone`。
2. 雙擊 `setup.bat`。它會：
   - 建立 `.venv`，安裝 `numpy`、`sounddevice`
   - 下載 [whisper.cpp v1.8.4 AMD build](https://github.com/lemonade-sdk/whisper.cpp-rocm/releases/tag/v1.8.4)，解壓到 `bin\whisper\`
   - 下載模型 [`ggml-large-v3-turbo.bin`](https://huggingface.co/ggerganov/whisper.cpp) 到 `models\`

   不是 RX 9000 系列的話，在命令列指定引擎：

   ```
   setup.bat -Engine gfx110X      :: RX 7000
   setup.bat -Engine vulkan-x64   :: 其他顯示卡
   setup.bat -Engine cpu-x64      :: 只用 CPU（較慢）
   ```

3. 雙擊 `run.bat` 啟動。

手動安裝也可以：把引擎壓縮檔解壓到 `bin\whisper\`（`bin\whisper\whisper-server.exe` 要存在），模型放在 `models\ggml-large-v3-turbo.bin`，再用任何裝了 `requirements.txt` 的 Python 執行 `ui.py`。

### 指定 Python 或 GPU 參數

`run.bat` 找 Python 的順序：`config\local.bat` → `.venv` → PATH 上的 `pythonw`。
要用別的 Python，或要設定 `HSA_OVERRIDE_GFX_VERSION`（顯示卡型號和 ROCm 套件不完全對應時），把 `config\local.bat.example` 複製成 `config\local.bat` 再修改。

## 操作

| 動作 | 怎麼做 |
|---|---|
| 說一句 | 滑鼠點要打字的輸入框 → 按住 **ScrollLock** → 說話 → 放開，文字出現在游標處 |
| 用按鈕說 | 先點輸入框，再按住 Dictation 的「按住說話」；轉完會自動切回剛才的視窗再輸入 |
| 出字 | 「放開後一次出」／「邊說邊出：A 分段」／「邊說邊出：B 即時」。A 可調「停頓」（0.15～0.6 秒）和「最長」（2～6 秒） |
| 貼上／模擬按鍵 | 一般用「貼上」；不吃 Ctrl+V 的程式改用「模擬按鍵」 |
| 換熱鍵 | 「說話鍵」下拉選單 →「套用」 |
| 改語言 | 「語言」下拉選單（會自動重啟辨識伺服器） |
| 麥克風 | 自動選 ATR2100x；其他麥克風從下拉選單選，選了會記住 |
| 轉音檔 | 「轉選音檔」 |
| 寫入 TXT | 勾「寫進 TXT」，寫入方式可選「追加」「新檔」「不寫」，路徑可改 |
| 其他 | 「複製全文」「存成 TXT」「清空」「開資料夾」 |

- 「目標：」欄位顯示文字會打進哪個視窗。
- 以系統管理員權限執行的視窗，Windows 不允許一般程式送按鍵進去。
- 介面設定存在 `config\ui.json`，下次開啟沿用。

## 邊說邊出字怎麼做的

- 兩種做法都把已打出的文字當成 whisper 的前文提示（prompt），前面再加一句「以下是一段繁體中文的口語內容，有適當的標點符號。」，
  讓分段前後的用詞和標點接得上，也讓輸出維持繁體。
- A 分段：能量 VAD（門檻＝背景音量第 10 百分位 × 3），停頓或達最長秒數就切段送出；段尾逗號先保留，後面還有話才打出，放開時改成句號。
  打出的字不會再改，但每段較短、前後文少，比較容易聽錯字。
- B 即時：LocalAgreement-2（[ufal/whisper_streaming](https://github.com/ufal/whisper_streaming)，Macháček et al. 2023）。
  比對時忽略標點與大小寫，已打出的部分以 difflib 對齊新結果；緩衝超過 8 秒時把已打出的整段移出緩衝。

實測（32 秒連續口語、RX 9070、large-v3-turbo）：

| | 第一次出字 | 出字間隔最長 | 放開後收尾 | 辨識次數／GPU 時間 |
|---|---|---|---|---|
| A 分段（0.25 秒／3 秒） | 3.6 秒 | 3.6 秒 | 0.3 秒 | 14 次／4.0 秒 |
| B 即時（每 1 秒） | 3.3 秒 | 3.0 秒 | 0.5 秒 | 33 次／11.5 秒 |

B 的內容最接近整段一次辨識的結果，說話連續、不太停頓的人建議用 B。

## USB 麥克風工具

| 檔案 | 用途 |
|---|---|
| `tools\mic-redetect.bat` | USB 麥克風插著卻抓不到（「要求裝置描述元失敗」）時雙擊，跳一次系統管理員確認，自動連續重新列舉，成功就停 |
| `tools\speaker.ps1` | `save`／`restore`／`show`：記下並還原預設播放裝置 |
| `tools\usb-watch.ps1` | 記錄 USB 裝置的出現、消失與錯誤碼 |

成因與處理細節見 [docs/MIC-USB.md](docs/MIC-USB.md)。預設針對 ATR2100x，其他麥克風可用參數指定。

## 檔案

| 路徑 | 用途 |
|---|---|
| `ui.py` | 主視窗（Tkinter） |
| `record.py` | 錄音與 whisper-cli 呼叫（可單獨用命令列測試） |
| `live.py` | 邊說邊出字：A 分段、B 即時 |
| `whisper_srv.py` | whisper-server 生命週期：UI 開啟時啟動、關閉時結束 |
| `punct.py` | 中文標點正規化 |
| `hotkey.py` | 全域按住式熱鍵 |
| `textout.py` | 文字注入：剪貼簿貼上／SendInput 模擬按鍵 |
| `winfocus.py` | 追蹤使用中的視窗、切回前景 |
| `run.bat`／`run-console.bat` | 啟動／除錯啟動（保留主控台視窗） |
| `setup.bat`／`setup.ps1` | 安裝 |
| `tests\` | 注入、輸入法、邊說邊出的自動測試（測試音檔說明見 `tests\fixtures\README.md`） |

執行時產生、不在版本庫裡：`bin\`、`models\`、`out\`（辨識結果、錄音暫存）、`logs\`、`config\ui.json`、`config\speaker.txt`。

## 測試

| 測試 | 內容 |
|---|---|
| `tests\inject_test.py` | 前景直接貼上、模擬按鍵、從 Dictation 切回再貼上 |
| `tests\ime_test.py fixed` | 模擬按鍵 × 注音輸入法：送出含全形標點的句子，讀回比對（Chrome 暫存設定檔，不登入、不同步，測完刪除） |
| `tests\live_test.py both` | 錄音檔以實際速度餵入，列出每段出字時間（需要另一個 whisper-server，預設 port 18081） |
| `tests\live_ui_test.py` | 真的 UI × Chrome 輸入框，A／B × 貼上／模擬按鍵四種組合，比對輸入框內容與程式記錄的全文 |

測試用的 whisper-server：

```
set HSA_OVERRIDE_GFX_VERSION=12000000
bin\whisper\whisper-server.exe -m models\ggml-large-v3-turbo.bin -l zh -t 8 --host 127.0.0.1 --port 18081
```

## 授權

MIT。whisper.cpp 與 OpenAI Whisper 模型權重也是 MIT 授權。
