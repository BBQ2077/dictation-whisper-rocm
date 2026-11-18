"""hotkey.py — 全域按鍵監聽（純 ctypes，不需套件）。

用 Win32 GetAsyncKeyState 輪詢，不攔截、不干擾其他程式的按鍵。
支援：按住模式（key down 開始、key up 結束）。

用法：
    hk = GlobalHotkey("ScrollLock", on_down=callback, on_up=callback)
    hk.start()      # 背景執行緒
    hk.stop()
"""

from __future__ import annotations

import ctypes
import threading
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)

# 常見鍵的虚拟键码
KEY_NAMES = {
    "ScrollLock": 0x91,
    "Pause": 0x13,
    "CapsLock": 0x14,
    "NumLock": 0x90,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23,
    "PageUp": 0x21, "PageDown": 0x22,
    "PrintScreen": 0x2C,
    "RAlt": 0xA5, "LCtrl": 0xA2, "RCtrl": 0xA3, "LShift": 0xA0, "RShift": 0xA1,
    "Esc": 0x1B, "Tab": 0x09, "CapsLock ": 0x14,
}


class GlobalHotkey:
    """按住式全域熱鍵。on_down / on_up 為 callback(無參數)。"""

    def __init__(self, key: str = "ScrollLock",
                 on_down=None, on_up=None, poll: float = 0.02):
        if isinstance(key, str):
            vk = KEY_NAMES.get(key)
            if vk is None:
                raise ValueError(f"未知按鍵 {key!r}，可用：{', '.join(KEY_NAMES)}")
            self.vk = vk
            self.key_name = key
        else:
            self.vk = int(key)
            self.key_name = f"0x{self.vk:02X}"

        self.on_down = on_down
        self.on_up = on_up
        self.poll = poll
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._held = False

    # ---------- 狀態 ----------
    def _is_down(self) -> bool:
        # GetAsyncKeyState 的高位元 (0x8000) = 目前是否按下
        return bool(user32.GetAsyncKeyState(self.vk) & 0x8000)

    def is_held(self) -> bool:
        return self._held

    # ---------- 背景執行 ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="hotkey")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        t, self._thread = self._thread, None
        if t and t.is_alive():
            t.join(timeout=1.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            down = self._is_down()
            if down and not self._held:
                self._held = True
                self._fire(self.on_down)
            elif not down and self._held:
                self._held = False
                self._fire(self.on_up)
            time.sleep(self.poll)

    @staticmethod
    def _fire(fn) -> None:
        if fn is None:
            return
        try:
            fn()
        except Exception as exc:  # 熱鍵 callback 掛了不能拖垮整條 thread
            print(f"[hotkey] callback error: {exc}", flush=True)


def flash_led(vk: int, times: int = 1, on_ms: int = 60) -> None:
    """閃一下鍵盤指示燈（Signal virtual key）。"""
    for _ in range(times):
        user32.keybd_event(vk, 0x45, 0, 0)
        time.sleep(on_ms / 1000)
        user32.keybd_event(vk, 0x45, 2, 0)
        time.sleep(on_ms / 1000)


if __name__ == "__main__":
    import sys

    key = sys.argv[1] if len(sys.argv) > 1 else "ScrollLock"
    print(f"監聽 {key}，按住 2 秒測試（Ctrl+C 結束）")

    hk = GlobalHotkey(key,
                      on_down=lambda: print(f"[{time.strftime('%H:%M:%S')}] ▼ DOWN"),
                      on_up=lambda: print(f"[{time.strftime('%H:%M:%S')}] ▲ UP"))
    hk.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        hk.stop()
        print("結束")