"""textout.py — 把文字注入「目前有焦點的視窗」，純 ctypes，不需安裝套件。

兩種方式：
  · method="paste"  → 寫入剪貼簿 + 模擬 Ctrl+V（相容性最好，任何輸入框都吃）
  · method="key"    → SendInput KEYEVENTF_UNICODE 逐字注入（不動剪貼簿）

主要用法：
    from textout import type_text
    type_text("你好，世界")            # 預設貼上，並還原原本剪貼簿
    type_text("你好", method="key")    # 逐字注入，不碰剪貼簿
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# Windows 16 個位元組的 handle 一定要明確宣告，否則 ctypes 預設 c_int 會截斷
user32.OpenClipboard.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]

kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalFree.restype = wintypes.HGLOBAL
kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]

GMEM_MOVEABLE = 0x0002

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

VK_CONTROL = 0x11
VK_V = 0x56
VK_ESCAPE = 0x1B

CF_UNICODETEXT = 13


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class INPUT(ctypes.Structure):
    # union 必須含 MOUSEINPUT（三者中最大），sizeof(INPUT) 才會是 x64 的 40 bytes；
    # cbSize 不符時 SendInput 回 0、LastError 87。
    class _I(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    _anonymous_ = ("i",)
    _fields_ = [("type", wintypes.DWORD), ("i", _I)]


user32.SendInput.restype = wintypes.UINT
user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]


# ---------------------------------------------------------------- 低階按鍵
class SendInputError(OSError):
    pass


def _send_in(ki: KEYBDINPUT) -> None:
    inp = INPUT(type=INPUT_KEYBOARD, ki=ki)
    if user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
        err = ctypes.get_last_error()
        raise SendInputError(err, f"SendInput 失敗 (LastError {err})")


def send_key(vk: int, up: bool) -> None:
    _send_in(KEYBDINPUT(wVk=vk, wScan=0,
                        dwFlags=KEYEVENTF_KEYUP if up else 0,
                        time=0, dwExtraInfo=None))


user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.GetKeyboardLayout.restype = ctypes.c_void_p
user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
user32.LoadKeyboardLayoutW.restype = ctypes.c_void_p
user32.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

WM_INPUTLANGCHANGEREQUEST = 0x0050
_IME_LANGS = {0x04, 0x11, 0x12}          # 中文、日文、韓文


def _layout_of(hwnd) -> int:
    tid = user32.GetWindowThreadProcessId(hwnd, None)
    return (user32.GetKeyboardLayout(tid) or 0) & 0xFFFFFFFF


def _switch_layout(hwnd, hkl: int, timeout: float = 0.4) -> bool:
    user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, hkl)
    end = time.time() + timeout
    while time.time() < end:
        if _layout_of(hwnd) == hkl & 0xFFFFFFFF:
            return True
        time.sleep(0.02)
    return False


class _ImeOff:
    """逐字送字期間把前景視窗切到英文鍵盤，結束切回原本的輸入法。
    中文輸入法開著時，KEYEVENTF_UNICODE 送的全形標點會被輸入法攔下，
    標點擠在一起、後面的句子倒序插入（tests/ime_test.py 可重現）。"""

    def __enter__(self):
        self.hwnd = user32.GetForegroundWindow()
        self.orig = _layout_of(self.hwnd) if self.hwnd else 0
        self.switched = False
        if self.orig and (self.orig & 0xFF) in _IME_LANGS:
            en = (user32.LoadKeyboardLayoutW("00000409", 0) or 0) & 0xFFFFFFFF
            if en and en != self.orig:
                self.switched = _switch_layout(self.hwnd, en)
        return self

    def __exit__(self, *exc):
        if self.switched:
            time.sleep(0.05)
            _switch_layout(self.hwnd, self.orig)
        return False


def send_unicode(text: str, delay: float = 0.003) -> None:
    """逐字注入 Unicode 字元（不動剪貼簿）。"""
    for ch in text:
        _send_in(KEYBDINPUT(wVk=0, wScan=ord(ch), dwFlags=KEYEVENTF_UNICODE,
                            time=0, dwExtraInfo=None))
        _send_in(KEYBDINPUT(wVk=0, wScan=ord(ch),
                            dwFlags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                            time=0, dwExtraInfo=None))
        if delay:
            time.sleep(delay)


# ---------------------------------------------------------------- 剪貼簿
def _open_clipboard():
    for _ in range(20):
        if user32.OpenClipboard(0):
            return True
        time.sleep(0.02)
    return False


def set_clipboard(text: str) -> bool:
    """把文字放進剪貼簿（含 GlobalAlloc 記憶體檢查）。"""
    if not _open_clipboard():
        return False
    try:
        user32.EmptyClipboard()
        # CF_UNICODETEXT 需要 NUL 結尾的 wide string
        n_chars = len(text) + 1
        n_bytes = n_chars * ctypes.sizeof(ctypes.c_wchar)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, n_bytes)
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            return False
        try:
            ctypes.memmove(ptr, text.encode("utf-16-le") + b"\x00\x00", n_bytes)
        finally:
            kernel32.GlobalUnlock(h)
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


def get_clipboard_text() -> str | None:
    """讀回剪貼簿文字；不是文字格式（例如複製檔案）則回 None。"""
    if not _open_clipboard():
        return None
    h = 0
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        # 用 GlobalSize 決定要不要讀、讀多少，避免讀進非法位址
        size = kernel32.GlobalSize(h)
        if not size or size > 4 * 1024 * 1024:
            return None
        s = ctypes.wstring_at(ptr, size // ctypes.sizeof(ctypes.c_wchar))
        return s.rstrip("\x00")   # GlobalAlloc 會帶 NUL 尾巴
    finally:
        if h:
            try:
                kernel32.GlobalUnlock(h)
            except Exception:
                pass
        user32.CloseClipboard()


def restore_clipboard(text: str | None, delay: float = 0.6) -> None:
    """延遲還原剪貼簿（等貼上動作完成）。"""
    if text is None:
        return
    time.sleep(delay)
    set_clipboard(text)


# ---------------------------------------------------------------- 對外 API
def paste_via_clipboard(text: str, *, keep_clipboard: bool = True,
                        delay: float = 0.02) -> bool:
    """剪貼簿 + Ctrl+V。keep_clipboard=True 時貼完會還原原本內容。"""
    old = get_clipboard_text() if keep_clipboard else None
    if not set_clipboard(text):
        return False
    time.sleep(0.06)          # 讓目標視窗的貼上選單/快取來得及更新

    send_key(VK_CONTROL, False); time.sleep(delay)
    send_key(VK_V, False);     time.sleep(delay)
    send_key(VK_V, True);      time.sleep(delay)
    send_key(VK_CONTROL, True)

    if keep_clipboard and old is not None:
        threading_restore(old)
    return True


def threading_restore(old: str) -> None:
    import threading

    threading.Thread(target=restore_clipboard, args=(old,), daemon=True).start()


def type_text(text: str, method: str = "paste", *, keep_clipboard: bool = True) -> bool:
    """把文字打到目前焦點的視窗。

    method: "paste" = 剪貼簿貼上（預設，最穩）
            "key"   = Unicode 逐字注入（不動剪貼簿）
    """
    if not text:
        return False
    if method == "key":
        with _ImeOff():
            send_unicode(text)
        return True
    return paste_via_clipboard(text, keep_clipboard=keep_clipboard)


if __name__ == "__main__":
    import sys

    t = " ".join(sys.argv[1:]) or "測試注入 Test 123"
    m = "key" if "--key" in sys.argv else "paste"
    print(f"注入 {len(t)} 字元，method={m}")
    ok = type_text(t, method=m)
    print("結果:", "OK" if ok else "FAILED")