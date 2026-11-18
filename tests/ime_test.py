"""模擬按鍵模式 × 中文輸入法 測試：開一個 Chrome 輸入框，
用 textout 逐字送一段含全形標點的句子，讀回輸入框內容比對。
輸入框內容即時寫進視窗標題，用 GetWindowText 讀回。

用法：python tests/ime_test.py [raw|fixed]
"""
import ctypes
import subprocess
import sys
import tempfile
import time
import urllib.parse
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import textout  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
# 測試用瀏覽器：Chrome + 全新暫存設定檔（不登入、不同步，測完刪除）。
# 不用 Edge：Edge 會自動以 Windows 帳號登入新設定檔並開始同步。
def _find_chrome() -> str:
    import os
    import shutil
    for base in (os.environ.get("PROGRAMFILES", ""), os.environ.get("PROGRAMFILES(X86)", ""),
                 os.environ.get("LOCALAPPDATA", "")):
        p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
        if base and p.exists():
            return str(p)
    return shutil.which("chrome") or "chrome.exe"


BROWSER = _find_chrome()
BROWSER_FLAGS = ["--no-first-run", "--no-default-browser-check", "--disable-sync",
                 "--disable-features=SigninInterceptBubble", "--no-service-autorun",
                 "--password-store=basic"]


def open_box(width=600, height=300):
    """開測試輸入框；回傳 (行程, 視窗, 暫存設定檔資料夾)。"""
    prof = tempfile.mkdtemp(prefix="dictest_")
    url = "data:text/html;charset=utf-8," + urllib.parse.quote(HTML)
    p = subprocess.Popen([BROWSER, f"--user-data-dir={prof}", *BROWSER_FLAGS,
                          f"--window-size={width},{height}", f"--app={url}"])
    h = None
    for _ in range(60):
        h = find_win()
        if h:
            break
        time.sleep(0.25)
    return p, h, prof


def close_box(p, prof):
    import shutil
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
    time.sleep(1.0)
    shutil.rmtree(prof, ignore_errors=True)
SAMPLE = "第一句話。第二句，有逗號！第三句？最後一句。"

HTML = """<title>IMETEST|</title><body style=margin:0>
<textarea id=t autofocus style=width:100%;height:95vh;font-size:20px></textarea>
<script>t.oninput=()=>document.title='IMETEST|'+t.value;t.focus()</script>"""


def find_win():
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(h, buf, 512)
        if buf.value.startswith("IMETEST|") and user32.IsWindowVisible(h):
            found.append(h)
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def title(h):
    buf = ctypes.create_unicode_buffer(2048)
    user32.GetWindowTextW(h, buf, 2048)
    return buf.value.split("|", 1)[1]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "raw"
    p, h, prof = open_box()
    if not h:
        print("找不到測試視窗"); close_box(p, prof); return
    time.sleep(1.5)
    user32.SetForegroundWindow(h)
    time.sleep(0.5)
    if user32.GetForegroundWindow() != h:
        print("前景不是測試視窗，不送字"); close_box(p, prof); return
    hkl = user32.GetKeyboardLayout(user32.GetWindowThreadProcessId(h, None))
    print(f"目標鍵盤配置 HKL=0x{hkl & 0xFFFFFFFF:08X}")

    if mode == "raw":
        textout.send_unicode(SAMPLE)
    else:
        textout.type_text(SAMPLE, method="key")
    time.sleep(1.0)
    got = title(h)
    print("送出:", SAMPLE)
    print("收到:", got)
    after = user32.GetKeyboardLayout(user32.GetWindowThreadProcessId(h, None))
    print(f"打完後 HKL=0x{after & 0xFFFFFFFF:08X}（{'已切回' if after == hkl else '沒切回'}）")
    print("PASS" if got == SAMPLE and after == hkl else "FAIL")
    close_box(p, prof)


if __name__ == "__main__":
    main()
