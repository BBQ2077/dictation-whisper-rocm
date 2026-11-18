"""winfocus.py — 追蹤「使用者正在用的視窗」，並把文字注入該視窗。

核心問題：
  Dictation UI 是 topmost/前景視窗，直接 SendInput 只會打到它自己的文字框。
  解法是記下「按下熱鍵前的最後一個非 Dictation 視窗」，注入前把它叫回前景。

API：
  focus.remember()      # 在開始錄音時呼叫，記下目前真正在用的視窗
  focus.restore()       # 注入前呼叫，把該視窗拉回前景
  focus.target()        # 回傳 (hwnd, title)，沒有就 None
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

u = ctypes.WinDLL("user32", use_last_error=True)

u.GetWindowTextLengthW.argtypes = [wintypes.HWND]
u.GetWindowTextLengthW.restype = ctypes.c_int
u.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u.GetForegroundWindow.restype = wintypes.HWND
u.IsWindowVisible.argtypes = [wintypes.HWND]
u.IsWindowVisible.restype = wintypes.BOOL
u.IsIconic.argtypes = [wintypes.HWND]
u.IsIconic.restype = wintypes.BOOL
u.SetForegroundWindow.argtypes = [wintypes.HWND]
u.SetForegroundWindow.restype = wintypes.BOOL
u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
u.BringWindowToTop.argtypes = [wintypes.HWND]
u.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
u.GetWindowLongW.restype = ctypes.c_long
u.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
u.IsWindow.argtypes = [wintypes.HWND]
u.IsWindow.restype = wintypes.BOOL
u.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_void_p]

GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
SW_RESTORE = 9
SW_SHOW = 5

# 永遠不要注入的視窗
_DENY = ("dictation", "progman", "shell_traywnd", "shell_secondarytraywnd",
         "cabinetwclass", "applicationframewindow", "multitaskingviewframe",
         "windows.ui.core.corewindow", "xaml_windowname")

_last_target: tuple | None = None   # (hwnd, title)


# ---------------------------------------------------------------- 基本查詢
def title_of(hwnd: int) -> str:
    n = u.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 2)
    u.GetWindowTextW(hwnd, b, n + 2)
    return b.value


def class_of(hwnd: int) -> str:
    b = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, b, 256)
    return b.value


def _is_real_window(hwnd: int) -> bool:
    if not hwnd or not u.IsWindowVisible(hwnd):
        return False
    c = class_of(hwnd).lower()
    t = title_of(hwnd).lower()
    if any(x in c for x in _DENY) or any(x in t for x in _DENY):
        return False
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    if r.right - r.left < 300 or r.bottom - r.top < 200:
        return False
    return bool(title_of(hwnd).strip())


# ---------------------------------------------------------------- 持續追蹤
import os as _os

_MY_PID = _os.getpid()


def pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def is_mine(hwnd: int) -> bool:
    return bool(hwnd) and pid_of(hwnd) == _MY_PID


def track() -> None:
    """定時呼叫：前景若是別人的視窗就記下來（Dictation 自己的視窗不算）。

    使用者在輸入框點一下，那個視窗就成了前景；之後即使點了 Dictation 的按鈕，
    記錄的仍是剛才的輸入框所在視窗。
    """
    global _last_target
    h = u.GetForegroundWindow()
    if not h or is_mine(h) or not u.IsWindowVisible(h):
        return
    c = class_of(h).lower()
    if c in ("shell_traywnd", "shell_secondarytraywnd", "progman", "workerw",
             "multitaskingviewframe", "foregroundstaging", "xamlexplorerhostislandwindow"):
        return
    _last_target = (h, title_of(h))


def foreground_is_mine() -> bool:
    return is_mine(u.GetForegroundWindow())


def bring_back() -> bool:
    """把最後追蹤到的視窗拉回前景（只在前景是 Dictation 自己時需要）。
    前景屬於本程式時，SetForegroundWindow 不受 foreground lock 限制。"""
    if not _last_target:
        return False
    hwnd = _last_target[0]
    if not u.IsWindow(hwnd):
        return False
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, SW_RESTORE)
    u.SetForegroundWindow(hwnd)
    for _ in range(15):
        if u.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.02)
    return u.GetForegroundWindow() == hwnd


# ---------------------------------------------------------------- 對外 API
def remember() -> None:
    """記下目前的「真正在用的視窗」。在開始錄音時呼叫。"""
    global _last_target
    h = u.GetForegroundWindow()
    if _is_real_window(h):
        # 前景是別人的視窗 → 這就是使用者要打字的地方
        _last_target = (h, title_of(h))
        return
    # 前景是 Dictation 自己 → 用使用者手動指定的，否則往後找
    if _last_target and _is_real_window(_last_target[0]):
        return
    for cand in _all_windows():
        if _is_real_window(cand):
            _last_target = (cand, title_of(cand))
            return


def target() -> tuple | None:
    """回傳 (hwnd, title)；沒有可用的就 None。"""
    if _last_target and u.IsWindow(_last_target[0]):
        return _last_target
    return None


def restore() -> bool:
    """把記錄的視窗拉回前景，讓後續 SendInput 打到它。"""
    global _last_target
    if not _last_target:
        remember()
    if not _last_target:
        return False

    hwnd, _t = _last_target
    if not _is_real_window(hwnd):
        remember()
        if not _last_target:
            return False
        hwnd = _last_target[0]

    try:
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, SW_RESTORE)
        else:
            u.ShowWindow(hwnd, SW_SHOW)
        u.BringWindowToTop(hwnd)

        # Windows 的 foreground lock：用 ALT 鍵 trick 解除
        u.keybd_event(0x12, 0, 0, 0)          # ALT down
        u.keybd_event(0x12, 0, 2, 0)          # ALT up
        ok = u.SetForegroundWindow(hwnd)
        time.sleep(0.12)
        # 有些視窗第一次會被拒，再試一次
        if u.GetForegroundWindow() != hwnd:
            u.BringWindowToTop(hwnd)
            u.SetForegroundWindow(hwnd)
            time.sleep(0.12)
        return u.GetForegroundWindow() == hwnd
    except Exception:
        return False


def _all_windows():
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    out: list[int] = []

    def cb(hwnd, _):
        out.append(hwnd)
        return True

    u.EnumWindows(EnumProc(cb), 0)
    return out


def list_candidates() -> list[tuple[int, str, str]]:
    """列出所有可注入的視窗 (hwnd, title, class)。"""
    out = []
    for h in _all_windows():
        if _is_real_window(h):
            out.append((h, title_of(h), class_of(h)))
    return out


def use(hwnd: int) -> bool:
    """手動指定目標視窗。"""
    global _last_target
    if not _is_real_window(hwnd):
        return False
    _last_target = (hwnd, title_of(hwnd))
    return True


def use_title(keyword: str) -> bool:
    """用標題關鍵字指定目標視窗，例如 '記事本' 或 'Chrome'。"""
    kw = keyword.strip().lower()
    if not kw:
        return False
    for hwnd, title, _cls in list_candidates():
        if kw in title.lower():
            return use(hwnd)
    # 再試 class 名（Notepad 的 class 是 "Notepad"，標題可能還沒變）
    for hwnd, title, cls in list_candidates():
        if kw in cls.lower():
            return use(hwnd)
    return False


def describe() -> str:
    t = target()
    if not t:
        return "（沒有記錄到目標視窗）"
    return f"{t[1][:40]}  (hwnd={t[0]})"


if __name__ == "__main__":
    import sys

    print("前景視窗:", title_of(u.GetForegroundWindow()))
    print("記錄目標:", describe())
    if "--type" in sys.argv:
        import textout
        remember()
        print("3 秒後切到目標視窗並注入…")
        time.sleep(3)
        ok = restore()
        print("切回前景:", ok, "->", title_of(u.GetForegroundWindow()))
        time.sleep(0.3)
        r = textout.type_text("注入測試 12345", method="paste")
        print("注入:", "OK" if r else "FAILED")