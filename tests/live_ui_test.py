"""邊說邊出字 × UI 整合測試。
- 真的 DictationApp（Tk），錄音器換成「以實際速度播放 WAV」的假錄音器
- whisper 用測試用 server（預設 port 18081），不碰使用者正在用的 Dictation
- 目標輸入框：Chrome textarea（暫存設定檔、不登入不同步，內容同步到視窗標題讀回）
- 檢查：輸入框內容 == 程式記下的全文；貼上模式結束後剪貼簿還原

用法：python tests/live_ui_test.py [--port 18081] [--seconds 16]
"""
import argparse
import ctypes
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import wave
from ctypes import wintypes
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import ui           # noqa: E402
import whisper_srv  # noqa: E402
import textout      # noqa: E402
from ime_test import close_box, open_box, title  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
ARGS = None
PCM = None


class FakeServer:
    def __init__(self, *a, **k):
        self.language, self.gpu, self.alive = "zh", True, True
        self.base = f"http://127.0.0.1:{ARGS.port}"

    def start(self, **k):
        return True

    def close(self, *a, **k):
        pass

    def transcribe_bytes(self, wav_bytes, *, prompt="", timeout=60.0):
        return whisper_srv.post_inference(self.base, wav_bytes, prompt=prompt)


class FakeRecorder:
    """以實際速度把 WAV（16k → 48k）送進 tap，和真的錄音器一樣每塊 1024 樣本。"""

    def __init__(self, *a, **k):
        self.device, self.tap = None, None
        self._run = False
        self._th = None

    def start(self):
        self._run = True
        self._th = threading.Thread(target=self._play, daemon=True)
        self._th.start()

    def _play(self):
        src = np.repeat(PCM, 3)
        t0 = time.time()
        for i in range(0, len(src), 1024):
            if not self._run:
                return
            tap = self.tap
            if tap is not None:
                tap.put(src[i:i + 1024].tobytes())
            dt = t0 + (i + 1024) / 48000 - time.time()
            if dt > 0:
                time.sleep(dt)

    def stop(self):
        self._run = False
        if self._th:
            self._th.join()
        return None

    @property
    def recording(self):
        return self._run


def main():
    global ARGS, PCM
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="18081")
    ap.add_argument("--seconds", type=float, default=16)
    ap.add_argument("--only", default="")
    ARGS = ap.parse_args()
    with wave.open(str(ROOT / "tests" / "fixtures" / "live_sample.wav")) as w:
        PCM = np.frombuffer(w.readframes(w.getnframes()), np.int16)[:int(ARGS.seconds * 16000)]

    ui.whisper_srv.WhisperServer = FakeServer
    ui.rec_mod.Recorder = FakeRecorder
    ui.SETTINGS_FILE = Path(tempfile.mkdtemp()) / "ui.json"
    ui.DictationApp._restore_speaker = lambda self: None
    ui.DictationApp.refresh_mics = lambda self, quiet=False: True

    browser, h, prof = open_box(900, 300)
    assert h, "找不到測試視窗"
    time.sleep(1.5)

    root = ui.tk.Tk()
    app = ui.DictationApp(root)
    app.srv_ok = True
    app.do_txt.set(False)
    app.stop_hotkey()

    # 安全閥：前景不是測試輸入框就不送字（避免打進使用者正在用的視窗）
    lost = []
    real_inject = app._inject

    def guarded(text, **kw):
        if user32.GetForegroundWindow() != h:
            lost.append(text)
            return "前景不是測試視窗，略過"
        return real_inject(text, **kw)
    app._inject = guarded

    only = [c for c in sys.argv if c.startswith("--only=")]
    cases = [(ui.TIMING_SEGMENT, "貼上"), (ui.TIMING_AGREE, "模擬按鍵"),
             (ui.TIMING_AGREE, "貼上"), (ui.TIMING_SEGMENT, "模擬按鍵")]
    if only:
        m = only[0].split("=", 1)[1]
        cases = [c for c in cases if c[1] == m]
    results = []
    marker = f"剪貼簿原內容-{time.time():.0f}"

    def clear_box():
        user32.SetForegroundWindow(h)
        time.sleep(0.3)
        # Ctrl+A、Delete
        for vk, up in [(0x11, 0), (0x41, 0), (0x41, 1), (0x11, 1), (0x2E, 0), (0x2E, 1)]:
            textout.send_key(vk, bool(up))
            time.sleep(0.02)
        time.sleep(0.3)

    def run_case(i=0):
        if i >= len(cases):
            root.after(300, root.destroy)
            return
        timing, method = cases[i]
        clear_box()
        textout.set_clipboard(marker)
        app.text.delete("1.0", "end")
        app.out_timing.set(timing)
        app.inject_method.set(method)
        user32.SetForegroundWindow(h)
        time.sleep(0.3)
        app.start()
        stamps = []
        orig_piece = app._live_piece

        def watch(p):
            stamps.append(time.time())
            orig_piece(p)
        app._live_piece = watch
        t0 = time.time()

        def release():
            app.stop()
            t_rel = time.time()

            def wait_done():
                if app._live is not None or app.busy:
                    root.after(100, wait_done)
                    return
                time.sleep(1.0)   # 剪貼簿延遲還原
                full = app.text.get("1.0", "end-1c")
                got = title(h)
                clip = textout.get_clipboard_text()
                first = stamps[0] - t0 if stamps else -1
                if lost:
                    print(f"  （{timing} / {method}：前景被切走，{len(lost)} 段沒送）")
                    lost.clear()
                results.append(dict(case=f"{timing} / {method}", ok=(got == full and bool(full)),
                                    clip_ok=(clip == marker), first=first,
                                    pieces=len(stamps), tail=time.time() - 1.0 - t_rel,
                                    full=full, got=got))
                app._live_piece = orig_piece
                root.after(200, lambda: run_case(i + 1))
            root.after(100, wait_done)
        root.after(int(len(PCM) / 16 + 300), release)

    root.after(1500, run_case)
    root.mainloop()
    close_box(browser, prof)

    allok = True
    for r in results:
        allok &= r["ok"] and r["clip_ok"]
        print(f"[{'PASS' if r['ok'] and r['clip_ok'] else 'FAIL'}] {r['case']}：說話中出字 {r['pieces']} 次，"
              f"第一次 {r['first']:.1f}s，放開後收尾 {r['tail']:.1f}s，剪貼簿{'已還原' if r['clip_ok'] else '沒還原'}")
        print(f"    輸入框：{r['got']}")
        if not r["ok"]:
            print(f"    程式全文：{r['full']}")
    print("ALL PASS" if allok and len(results) == len(cases) else "有失敗")


if __name__ == "__main__":
    main()
