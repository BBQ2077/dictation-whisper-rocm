"""Dictation UI — 按住熱鍵說話，文字進到任何輸入框 / TXT。

輸出三選一（可複選）：
  · 注入目前焦點的視窗（瀏覽器、聊天室、Word…）
  · 寫進 TXT
  · 複製到剪貼簿

執行：
  雙擊 run.bat，或 python ui.py（需要 numpy、sounddevice，見 requirements.txt）
"""

from __future__ import annotations

import datetime as dt
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import json

import live
import record as rec_mod
import textout
import whisper_srv
import winfocus
from hotkey import GlobalHotkey

ROOT = Path(__file__).resolve().parent
OUT_DIR = ROOT / "out"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "ui.log"

# ---- 用對的 Python 重開自己 ---------------------------------------------
def _find_python() -> Path | None:
    """裝有 sounddevice 的 Python：環境變數 DICTATION_PYTHON → 專案內 .venv。"""
    cands = [os.environ.get("DICTATION_PYTHON", ""),
             str(ROOT / ".venv" / "Scripts" / "python.exe")]
    for c in cands:
        if c and Path(c).exists():
            return Path(c)
    return None


def _ensure_runtime() -> None:
    """目前的 Python 沒有 sounddevice，就換成找得到的那個重跑一次。"""
    try:
        import sounddevice  # noqa: F401
        return
    except Exception:
        pass
    py = _find_python()
    if py is None:
        return
    try:
        if Path(sys.executable).resolve() == py.resolve():
            return
    except Exception:
        pass
    if "--relayed" in sys.argv:          # 杜絕無限重入
        return
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    subprocess.Popen(
        [str(VENV_PY), str(Path(__file__).resolve()), "--relayed"],
        cwd=str(ROOT), env=env,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    sys.exit(0)


def log(msg: str) -> None:
    """寫進檔案。pythonw 沒有 stdout，print 會憑空消失，所以一律落檔。"""
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass

DEFAULT_TXT = OUT_DIR / "dictation.txt"
SPEAKER_PS1 = ROOT / "tools" / "speaker.ps1"


def _atr_present() -> bool:
    """ATR2100x 的錄音端點是否在線（MMDevices 登錄，DeviceState 1 = active）。"""
    import winreg
    base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture"
    iface = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    return False
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        state, _ = winreg.QueryValueEx(k, "DeviceState")
                    if state != 1:
                        continue
                    with winreg.OpenKey(root, sub + r"\Properties") as p:
                        name, _ = winreg.QueryValueEx(p, iface)
                    if "ATR2100" in str(name):
                        return True
                except OSError:
                    continue
    except OSError:
        return False

TXT_APPEND = "append"   # 追加到同一個檔
TXT_NEW = "new"         # 每次存新檔
TXT_OFF = "off"         # 不寫檔

TIMING_RELEASE = "放開後一次出"
TIMING_SEGMENT = "邊說邊出：A 分段"
TIMING_AGREE = "邊說邊出：B 即時"
SETTINGS_FILE = ROOT / "config" / "ui.json"
SETTINGS_KEYS = ("out_timing", "live_pause", "live_max", "inject_method", "hotkey_name")

HOTKEY_KEYS = ["ScrollLock", "Pause", "F4", "F5", "F6", "F7", "F8", "F9",
               "Insert", "Home", "End", "PageUp", "PageDown", "RAlt", "RCtrl"]

# 狀態 → (符號, 顏色, 標籤, 按鈕文字)
STATES = {
    "idle":      ("○", "#888", "待機",     "按住說話"),
    "recording": ("●", "#d00", "錄音中",   "放開結束"),
    "working":   ("◐", "#07c", "轉文字中", "請稍候"),
}


class DictationApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Dictation · Whisper (ROCm)")
        root.geometry("880x680")
        root.minsize(720, 540)

        self.rec = rec_mod.Recorder()
        self.busy = False
        self._t0 = 0.0
        self._pending: list[str] = []      # 待轉的 wav
        self._uiq: "queue.Queue[tuple]" = queue.Queue()

        # 設定
        self.mic = tk.StringVar(value="")
        self.mic_map: dict[str, int] = {}
        self.lang = tk.StringVar(value="zh")
        self.use_gpu = tk.BooleanVar(value=True)

        self.hotkey_name = tk.StringVar(value="ScrollLock")
        self.do_txt = tk.BooleanVar(value=True)
        self.do_inject = tk.BooleanVar(value=True)
        self.inject_method = tk.StringVar(value="貼上")
        # 出字時機：放開後一次出／邊說邊出（分段 A、即時 B）
        self.out_timing = tk.StringVar(value=TIMING_RELEASE)
        self.live_pause = tk.StringVar(value="0.25")
        self.live_max = tk.StringVar(value="3")
        self._live: live.LiveSession | None = None
        self._live_t0 = 0.0
        self._live_first = True
        self._clip_saved: str | None = None
        self._load_settings()
        self._mic_manual = False           # 使用者手動選過麥克風就不自動換
        self._mic_retry_at = 0.0
        self._atr_seen: bool | None = None
        self.txt_mode = tk.StringVar(value=TXT_APPEND)
        self.txt_path = tk.StringVar(value=str(DEFAULT_TXT))

        self.status = tk.StringVar(value="")

        # 模型常駐：UI 開 → 起 server（背景）；UI 關 → 一起關
        self.srv = whisper_srv.WhisperServer(language="zh")
        self.srv_ok = False
        self._srv_lock = threading.Lock()

        self._build()
        self.refresh_mics()
        self.start_hotkey()
        self._tick()
        threading.Thread(target=self._boot_server, daemon=True).start()
        log("UI 啟動完成")

    # ============================================================ 模型常駐
    def _boot_server(self) -> None:
        """背景把模型載進記憶體。之後每次轉文字只花 0.2～0.9 秒。"""
        try:
            ok = self.srv.start(timeout=180)
        except Exception as exc:
            log(f"server 啟動失敗: {exc}")
            self._uiq.put(("srv", ("error", str(exc))))
            return
        self.srv_ok = ok
        self._uiq.put(("srv", ("ok" if ok else "timeout", "")))

    def _shutdown_server(self) -> None:
        """UI 關閉時把 server 一起帶走，不留孤兒。"""
        try:
            self.srv.close()
            log("server 已關閉")
        except Exception as exc:
            log(f"server 關閉失敗: {exc}")

    def _ensure_server(self) -> str | None:
        """回傳 None 表示可用；否則回傳錯誤描述。"""
        with self._srv_lock:
            if self.srv_ok and self.srv.alive:
                return None
        return "模型還在載入，請再等一下"

    # ============================================================ UI
    def _build(self) -> None:
        # ---- 上方：狀態顯示 ----
        head = ttk.Frame(self.root, padding=(14, 12, 14, 6))
        head.pack(fill="x")

        self.ind = tk.Label(head, text="○", fg="#888", font=("Segoe UI", 22))
        self.ind.pack(side="left")

        box = ttk.Frame(head)
        box.pack(side="left", padx=(10, 0))
        self.lbl_state = tk.Label(box, text="待機", fg="#888",
                                  font=("Microsoft JhengHei", 15, "bold"))
        self.lbl_state.pack(anchor="w")
        self.lbl_hint = tk.Label(box, text="按住 ScrollLock 說話，放開自動輸入",
                                 fg="#666", font=("Microsoft JhengHei", 9))
        self.lbl_hint.pack(anchor="w")

        self.lbl_timer = tk.Label(head, text="", fg="#c60", font=("Consolas", 20))
        self.lbl_timer.pack(side="right")

        # ---- 主按鈕（全寬，狀態會變） ----
        main = ttk.Frame(self.root, padding=(14, 0, 14, 8))
        main.pack(fill="x")

        self.btn_main = tk.Button(
            main, text="按住說話", font=("Microsoft JhengHei", 14, "bold"),
            bg="#2b7", fg="white", activebackground="#198", activeforeground="white",
            relief="flat", bd=0, cursor="hand2", height=2)
        self.btn_main.pack(fill="x")
        self.btn_main.bind("<ButtonPress-1>", lambda e: self.start())
        self.btn_main.bind("<ButtonRelease-1>", lambda e: self.stop())

        # ---- 設定區 ----
        cfg = ttk.LabelFrame(self.root, text="設定", padding=10)
        cfg.pack(fill="x", padx=14)

        # 列 1：麥克風 + 語言 + GPU
        r1 = ttk.Frame(cfg)
        r1.pack(fill="x", pady=(0, 8))
        ttk.Label(r1, text="麥克風", width=6).pack(side="left")
        self.cbo_mic = ttk.Combobox(r1, textvariable=self.mic, width=48, state="readonly")
        self.cbo_mic.pack(side="left", padx=(0, 6))
        self.cbo_mic.bind("<<ComboboxSelected>>", self._on_mic)
        ttk.Button(r1, text="重新掃描", width=10,
                   command=self.refresh_mics).pack(side="left")
        ttk.Label(r1, text="語言", width=5).pack(side="left", padx=(16, 0))
        ttk.Combobox(r1, textvariable=self.lang, width=6, state="readonly",
                     values=["zh", "en", "ja", "ko", "auto"]).pack(side="left")
        ttk.Checkbutton(r1, text="GPU", variable=self.use_gpu).pack(side="left", padx=(10, 0))

        # 列 2：熱鍵
        r2 = ttk.Frame(cfg)
        r2.pack(fill="x", pady=(0, 8))
        ttk.Label(r2, text="說話鍵", width=6).pack(side="left")
        self.cbo_key = ttk.Combobox(r2, textvariable=self.hotkey_name, width=12,
                                    state="readonly", values=HOTKEY_KEYS)
        self.cbo_key.pack(side="left")
        self.cbo_key.bind("<<ComboboxSelected>>", lambda e: self.start_hotkey())
        ttk.Button(r2, text="套用", width=6, command=self.start_hotkey).pack(side="left", padx=(6, 0))
        self.lbl_key = ttk.Label(r2, text="", foreground="#070")
        self.lbl_key.pack(side="left", padx=(12, 0))
        self.lbl_mic_err = ttk.Label(r1, text="", foreground="#c00")
        self.lbl_mic_err.pack(side="left", padx=(10, 0))

        # 列 3：輸出
        r3 = ttk.Frame(cfg)
        r3.pack(fill="x", pady=(0, 8))
        ttk.Label(r3, text="輸出", width=6).pack(side="left")
        ttk.Checkbutton(r3, text="打進游標所在的輸入框",
                        variable=self.do_inject).pack(side="left")
        ttk.Combobox(r3, textvariable=self.inject_method, width=8, state="readonly",
                     values=["貼上", "模擬按鍵"]).pack(side="left", padx=(4, 14))
        ttk.Checkbutton(r3, text="寫進 TXT", variable=self.do_txt).pack(side="left")
        self.lbl_out = ttk.Label(r3, text="目標：（先在要輸入的地方點一下）",
                                 foreground="#666")
        self.lbl_out.pack(side="left", padx=(12, 0))

        # 列 3b：出字時機
        r3b = ttk.Frame(cfg)
        r3b.pack(fill="x", pady=(0, 8))
        ttk.Label(r3b, text="出字", width=6).pack(side="left")
        cbo_t = ttk.Combobox(r3b, textvariable=self.out_timing, width=16, state="readonly",
                             values=[TIMING_RELEASE, TIMING_SEGMENT, TIMING_AGREE])
        cbo_t.pack(side="left")
        cbo_t.bind("<<ComboboxSelected>>", lambda e: self._on_timing())
        self.lbl_pause = ttk.Label(r3b, text="停頓(秒)")
        self.cbo_pause = ttk.Combobox(r3b, textvariable=self.live_pause, width=5, state="readonly",
                                      values=["0.15", "0.2", "0.25", "0.3", "0.4", "0.6"])
        self.lbl_max = ttk.Label(r3b, text="最長(秒)")
        self.cbo_max = ttk.Combobox(r3b, textvariable=self.live_max, width=4, state="readonly",
                                    values=["2", "3", "4", "5", "6"])
        for w in (self.cbo_pause, self.cbo_max):
            w.bind("<<ComboboxSelected>>", lambda e: self._save_settings())
        self.lbl_timing = ttk.Label(r3b, text="", foreground="#666")
        self._on_timing(save=False)

        # 列 4：TXT 路徑
        r4 = ttk.Frame(cfg)
        r4.pack(fill="x", pady=(0, 8))
        ttk.Label(r4, text="TXT 路徑", width=8).pack(side="left")
        ttk.Entry(r4, textvariable=self.txt_path).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(r4, text="瀏覽", width=6,
                   command=self.pick_txt).pack(side="left")
        ttk.Label(r4, text="寫入", width=5).pack(side="left", padx=(12, 0))
        ttk.Radiobutton(r4, text="追加", value=TXT_APPEND,
                        variable=self.txt_mode).pack(side="left")
        ttk.Radiobutton(r4, text="新檔", value=TXT_NEW,
                        variable=self.txt_mode).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(r4, text="不寫", value=TXT_OFF,
                        variable=self.txt_mode).pack(side="left", padx=(8, 0))

        # ---- 狀態列 ----
        st = ttk.Frame(self.root, padding=(14, 8, 14, 4))
        st.pack(fill="x")
        ttk.Label(st, textvariable=self.status, foreground="#075").pack(side="left")

        # ---- 文字區 ----
        mid = ttk.Frame(self.root, padding=(14, 0))
        mid.pack(fill="both", expand=True)
        self.text = tk.Text(mid, wrap="word", font=("Microsoft JhengHei", 11),
                            undo=True, padx=8, pady=6, height=8)
        self.text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(mid, command=self.text.yview)
        sb.pack(side="right", fill="y")
        self.text.config(yscrollcommand=sb.set)

        # ---- 功能鈕 ----
        btns = ttk.Frame(self.root, padding=(14, 8, 14, 14))
        btns.pack(fill="x")
        for label, cmd in [
            ("轉選音檔", self.pick_media),
            ("複製全文", self.copy_all),
            ("存成 TXT", self.save_txt),
            ("清空", self.clear),
            ("開資料夾", self.open_folder),
        ]:
            ttk.Button(btns, text=label, command=cmd).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="結束", command=self.root.destroy).pack(side="right")

    # ============================================================ 設定存檔
    def _load_settings(self) -> None:
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if data.get("inject_method") == "逐字":
            data["inject_method"] = "模擬按鍵"
        for k in SETTINGS_KEYS:
            if isinstance(data.get(k), str) and data[k]:
                getattr(self, k).set(data[k])
        for k in SETTINGS_KEYS:
            getattr(self, k).trace_add("write", lambda *_: self._save_settings())

    def _save_settings(self) -> None:
        try:
            SETTINGS_FILE.parent.mkdir(exist_ok=True)
            SETTINGS_FILE.write_text(
                json.dumps({k: getattr(self, k).get() for k in SETTINGS_KEYS},
                           ensure_ascii=False, indent=1), encoding="utf-8")
        except Exception as exc:
            log(f"設定存檔失敗: {exc}")

    def _on_timing(self, save: bool = True) -> None:
        """依出字時機顯示對應的參數。"""
        for w in (self.lbl_pause, self.cbo_pause, self.lbl_max, self.cbo_max, self.lbl_timing):
            w.pack_forget()
        t = self.out_timing.get()
        if t == TIMING_SEGMENT:
            self.lbl_pause.pack(side="left", padx=(12, 2))
            self.cbo_pause.pack(side="left")
            self.lbl_max.pack(side="left", padx=(10, 2))
            self.cbo_max.pack(side="left")
            self.lbl_timing.config(text="說話中一停頓就出一段；一直沒停頓，到「最長」秒數也會出")
        elif t == TIMING_AGREE:
            self.lbl_timing.config(text="說話中約每秒出字，不必停頓；前後文最完整")
        else:
            self.lbl_timing.config(text="放開說話鍵後整段一次辨識、一次輸入")
        self.lbl_timing.pack(side="left", padx=(12, 0))
        if save:
            self._save_settings()

    # ============================================================ 麥克風
    @staticmethod
    def _reinit_portaudio() -> None:
        """PortAudio 只在初始化時列一次裝置，之後插拔都看不到
        （python-sounddevice issue #47）。重新初始化才會重列。"""
        import sounddevice as sd
        try:
            sd._terminate()
        except Exception:
            pass
        sd._initialize()

    def refresh_mics(self, quiet: bool = False) -> bool:
        """重列錄音裝置；回傳是否找到 ATR2100。失敗會顯示在畫面上（pythonw 沒有 stderr）。"""
        if self.rec.recording:
            return True
        try:
            import sounddevice as sd
            self._reinit_portaudio()
            raw = sd.query_devices()
            apis = [a["name"] for a in sd.query_hostapis()]
            devs = [(i, f'{d["name"]} · {apis[d["hostapi"]]}') for i, d in enumerate(raw)
                    if d.get("max_input_channels", 0) > 0]
        except Exception as exc:
            log(f"refresh_mics 失敗: {exc}\n{traceback.format_exc()}")
            self.mic_map = {}
            self.cbo_mic["values"] = ["(讀取失敗)"]
            self.cbo_mic.set("(讀取失敗)")
            self.lbl_mic_err.config(
                text=f"讀不到麥克風：{exc}",
                foreground="#c00")
            self.set_status("麥克風讀取失敗，詳見 logs/ui.log")
            return False

        if not devs:
            self.mic_map = {}
            self.cbo_mic["values"] = ["(沒有錄音裝置)"]
            self.cbo_mic.set("(沒有錄音裝置)")
            self.lbl_mic_err.config(text="系統沒有任何錄音裝置（麥克風沒插好？）",
                                    foreground="#c00")
            self.set_status("沒有錄音裝置")
            return False

        prev = self.mic.get().split("] ", 1)[-1]     # 之前選的（不含索引，插拔後索引會變）
        self.mic_map = {}
        labels = []
        for idx, name in devs:
            name = " ".join(str(name).split())      # 去掉換行/多餘空白
            label = f"[{idx}] {name}"
            self.mic_map[label] = idx
            labels.append(label)
        self.cbo_mic["values"] = labels

        atr = [l for l in labels if "ATR2100" in l]
        want = None
        if self._mic_manual and prev:
            want = next((l for l in labels if l.split("] ", 1)[-1] == prev), None)
        if want is None and atr:
            # ATR2100x 端點偏好：WDM-KS（實測最靈敏）→ WASAPI → 其他
            for api in ("WDM-KS", "WASAPI"):
                want = next((l for l in atr if api in l), None)
                if want:
                    break
            want = want or atr[0]
        if want is None:
            want = next((l for l in labels if l.split("] ", 1)[-1] == prev), labels[0])

        self.cbo_mic.set(want)
        self._apply_mic()
        if atr:
            self.lbl_mic_err.config(text="", foreground="#c00")
            self.set_status(f"麥克風就緒：{want}")
        else:
            self.lbl_mic_err.config(text="ATR2100x 未偵測到，插上後會自動接上",
                                    foreground="#c60")
        if not quiet or atr:
            log(f"refresh_mics: input devices={len(devs)}, 選 {want}")
        return bool(atr)

    def _on_mic(self, _e=None) -> None:
        self._mic_manual = True
        self._apply_mic()
        self.set_status(f"麥克風切換為：{self.mic.get()}")

    def _apply_mic(self) -> None:
        idx = self.mic_map.get(self.mic.get())
        if idx is not None:
            self.rec.device = idx

    # ============================================================ 全域熱鍵
    def start_hotkey(self) -> None:
        old = getattr(self, "hk", None)
        if old:
            try:
                old.stop()
            except Exception:
                pass
            self.hk = None
        try:
            self.hk = GlobalHotkey(self.hotkey_name.get(),
                                   on_down=lambda: self._uiq.put(("down", None)),
                                   on_up=lambda: self._uiq.put(("up", None)))
            self.hk.start()
            self.lbl_key.config(text=f"✓ {self.hotkey_name.get()} 生效中")
        except Exception as exc:
            self.hk = None
            self.lbl_key.config(text=f"✗ {exc}")

    def stop_hotkey(self) -> None:
        old = getattr(self, "hk", None)
        if old:
            try:
                old.stop()
            except Exception:
                pass
            self.hk = None

    # ============================================================ 錄音
    def start(self) -> None:
        if self.busy or self.rec.recording:
            return
        winfocus.track()
        log("start: 錄音開始")
        tap = self._live_begin()
        self.rec.tap = tap
        try:
            self.rec.start()
        except Exception as exc:
            # 裝置可能剛被插拔過（索引已變）：重列一次再試
            log(f"start 失敗，重列裝置後再試: {exc}")
            self.refresh_mics(quiet=True)
            try:
                self.rec.start()
            except Exception as exc2:
                self._live_abort()
                messagebox.showerror("錄音失敗", f"{exc2}\n\n可能原因：麥克風沒插好或被占用。")
                return
        self._t0 = time.time()
        if self._live:
            self._live.start()
        self.set_state("recording")

    # ---------- 邊說邊出字 ----------
    def _live_begin(self) -> "live.Tap | None":
        t = self.out_timing.get()
        if t == TIMING_RELEASE:
            return None
        if self._ensure_server() is not None:
            self.set_status("模型還在載入，這次改用放開後一次出")
            return None
        lang = self.lang.get()
        tap = live.Tap()
        try:
            pause, max_seg = float(self.live_pause.get()), float(self.live_max.get())
        except ValueError:
            pause, max_seg = 0.25, 3.0
        self._live = live.LiveSession(
            mode="segment" if t == TIMING_SEGMENT else "agree",
            src_rate=rec_mod.DEVICE_SAMPLE_RATE, pull=tap.pull,
            transcribe=self._live_asr,
            emit=lambda p: self._uiq.put(("piece", p)),
            done=lambda full: self._uiq.put(("live_done", full)),
            pause=pause, max_seg=max_seg,
            default_prompt=live.DEFAULT_PROMPT if lang == "zh" else "",
            log=log)
        self._live_t0 = time.time()
        self._live_first = True
        # 貼上模式：連續多次貼上，剪貼簿只在整句結束後還原一次
        self._clip_saved = None
        if self.do_inject.get() and self.inject_method.get() == "貼上":
            try:
                self._clip_saved = textout.get_clipboard_text()
            except Exception:
                self._clip_saved = None
        return tap

    def _live_abort(self) -> None:
        self.rec.tap = None
        self._live = None

    def _live_asr(self, pcm, prompt: str):
        data = self.srv.transcribe_bytes(live.to_wav(pcm), prompt=prompt)
        return whisper_srv.segments_of(data)

    def _live_piece(self, piece: str) -> None:
        if self._live_first:
            self._live_first = False
            if self.text.get("1.0", "end-1c").strip():
                self.text.insert("end", "\n")
        self.text.insert("end", piece)
        self.text.see("end")
        if self.do_inject.get():
            self.set_status(self._inject(piece, keep_clipboard=False))

    def _live_done(self, full: str) -> None:
        took = time.time() - self._live_t0
        sess, self._live = self._live, None
        self.busy = False
        if self._clip_saved is not None:
            textout.threading_restore(self._clip_saved)
            self._clip_saved = None
        if sess:
            log(f"live[{sess.mode}] {len(full)} 字，辨識 {sess.calls} 次 {sess.busy_s:.1f}s")
        self._finish(full, took, inject=False, show=False)

    def stop(self) -> None:
        if not self.rec.recording:
            return
        if self._live:
            self.rec.stop()             # 音訊已同步複製到 tap
            self.rec.tap = None
            self.busy = True
            self._live.finish()
            self.set_state("working", "收尾中…")
            return
        wav = self.rec.stop()
        if wav is None:
            self.set_state("idle", "沒有錄到聲音")
            return
        self._pending.append(str(wav))
        self.set_state("working", f"錄音結束，排隊轉文字（{len(self._pending)}）…")
        self._pump()

    def _pump(self) -> None:
        if self.busy or not self._pending:
            return
        wav = Path(self._pending.pop(0))
        self.busy = True
        # 主執行緒先把 Tk 變數讀出來，子執行緒才不會炸
        lang = self.lang.get()
        use_gpu = self.use_gpu.get()
        self.set_state("working", f"轉文字中… {wav.name}")
        threading.Thread(target=self._worker, args=(wav, lang, use_gpu),
                         daemon=True).start()

    def _worker(self, wav: Path, lang: str, use_gpu: bool) -> None:
        t0 = time.time()
        try:
            # 語言或 GPU 設定跟 server 當時不一樣 → 重啟 server（只比較需要時）
            with self._srv_lock:
                need_restart = (self.srv_ok
                                and (self.srv.language != lang
                                     or self.srv.gpu != use_gpu))
            if need_restart:
                log(f"重啟 server：lang {self.srv.language}→{lang}, gpu {self.srv.gpu}→{use_gpu}")
                self.srv.language, self.srv.gpu = lang, use_gpu
                self.srv_ok = self.srv.restart(timeout=180)

            if self.srv_ok and self.srv.alive:
                text = self.srv.transcribe_text(wav)
                if text.strip():
                    self._uiq.put(("done", (text, time.time() - t0)))
                    return
                log("server 回傳空白，改用 whisper-cli")

            text = rec_mod.transcribe(wav, language=lang, gpu=use_gpu)
            import punct
            self._uiq.put(("done", (punct.polish(text), time.time() - t0)))
        except Exception as exc:
            self._uiq.put(("error", exc))

    # ============================================================ 主執行緒輪詢
    def _tick(self) -> None:
        # 1. 處理子執行緒送來的訊息
        drained = False
        while True:
            try:
                kind, payload = self._uiq.get_nowait()
            except queue.Empty:
                break
            drained = True
            if kind == "down":
                self.start()
            elif kind == "up":
                self.stop()
            elif kind == "piece":
                self._live_piece(payload)
            elif kind == "live_done":
                self._live_done(payload)
            elif kind == "done":
                text, took = payload
                self.busy = False
                self._finish(text, took)
            elif kind == "error":
                self.busy = False
                self._failed(payload)
            elif kind == "srv":
                state, detail = payload
                if state == "ok":
                    self.srv_ok = True
                    self.set_status("模型已載入，可以開始說話")
                    log("server 就緒")
                else:
                    self.srv_ok = False
                    self.set_status(f"模型載入失敗：{detail}")
                    log(f"server 未就緒: {state} {detail}")

        # 2. 追蹤使用者正在用的視窗（輸入框所在處）
        try:
            winfocus.track()
            t = winfocus.target()
            if t:
                self.lbl_out.config(text=f"目標：{t[1][:36]}")
        except Exception:
            pass

        # 3. 每 2 秒看一次 ATR2100x 在不在（讀登錄，不碰 PortAudio）；
        #    剛接上 → 重列裝置 + 把被搶走的預設喇叭切回來
        if not self.rec.recording and time.time() >= self._mic_retry_at:
            self._mic_retry_at = time.time() + 2.0
            present = _atr_present()
            if present != self._atr_seen:
                first = self._atr_seen is None
                self._atr_seen = present
                if present:
                    if not first:
                        log("ATR2100x 接上")
                        self.refresh_mics(quiet=True)
                    self._restore_speaker()
                elif not first:
                    log("ATR2100x 拔除")
                    self.lbl_mic_err.config(text="ATR2100x 未連線，插上後會自動接上",
                                            foreground="#c60")

        # 錄音計時
        if self.rec.recording:
            self.lbl_timer.config(text=f"{time.time() - self._t0:5.1f}s")
        else:
            self.lbl_timer.config(text="")

        # 3. 排隊轉下一個
        if not drained and not self.busy and self._pending:
            self._pump()

        self.root.after(100, self._tick)

    # ============================================================ 完成處理
    def _finish(self, text: str, took: float, *, inject: bool = True, show: bool = True) -> None:
        if not text.strip():
            self.set_state("idle", f"沒聽到內容（{took:.1f}s）")
            return

        if show:
            cur = self.text.get("1.0", "end-1c")
            if cur.strip():
                self.text.insert("end", "\n")
            self.text.insert("end", text)
            self.text.see("end")

        done = [f"完成 {took:.1f}s · {len(text)} 字"]

        if inject and self.do_inject.get():
            done.append(self._inject(text))

        if self.do_txt.get() and self.txt_mode.get() != TXT_OFF:
            try:
                p = self._write(text)
                done.append(f"→ {Path(p).name}")
            except Exception as exc:
                done.append(f"寫檔失敗: {exc}")

        self.set_state("idle", " · ".join(done))

    def _restore_speaker(self) -> None:
        """ATR2100x 接上時 Windows 可能把預設播放換成它的耳機孔 → 切回記下的喇叭。"""
        def run():
            time.sleep(2.0)       # 等 Windows 先完成切換
            try:
                p = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                     "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                     f"& '{SPEAKER_PS1}' restore"],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                log(f"speaker restore: {(p.stdout or p.stderr).strip()}")
            except Exception as exc:
                log(f"speaker restore 失敗: {exc}")
        threading.Thread(target=run, daemon=True).start()

    def _inject(self, text: str, *, keep_clipboard: bool = True) -> str:
        """打進游標所在的輸入框。前景是別的程式 → 直接打；
        前景是 Dictation 自己（點了它的按鈕）→ 先切回剛才的視窗再打。"""
        try:
            if winfocus.foreground_is_mine():
                if not winfocus.bring_back():
                    return "輸入框：找不到目標視窗（先在要輸入的地方點一下）"
                time.sleep(0.08)
            method = "paste" if self.inject_method.get() == "貼上" else "key"
            textout.type_text(text, method=method, keep_clipboard=keep_clipboard)
            t = winfocus.target()
            log(f"inject[{method}] → {t[1] if t else '?'}: {len(text)} 字")
            return "已輸入"
        except Exception as exc:
            log(f"inject 失敗: {exc}\n{traceback.format_exc()}")
            return f"輸入框失敗：{exc}"

    def _failed(self, exc: Exception) -> None:
        self.set_state("idle", "轉文字失敗")
        messagebox.showerror("轉文字失敗", str(exc))

    def _log_stderr(self, err: str) -> None:
        if not err.strip():
            return
        try:
            p = ROOT / "logs" / "whisper.log"
            p.parent.mkdir(exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(f"[{dt.datetime.now():%Y-%m-%d %H:%M:%S}]\n{err}\n")
        except Exception:
            pass

    # ============================================================ 狀態
    def set_state(self, state: str, detail: str = "") -> None:
        mark, color, label, btn_text = STATES.get(state, STATES["idle"])
        self.ind.config(text=mark, fg=color)
        self.lbl_state.config(text=label, fg=color)
        self.btn_main.config(text=btn_text)
        if detail:
            self.status.set(detail)
        elif state == "idle":
            hint = f"按住 {self.hotkey_name.get()} 說話，放開自動輸入"
            self.lbl_hint.config(text=hint)

    def set_status(self, msg: str) -> None:
        self.status.set(msg)

    # ============================================================ 輸出
    def _write(self, text: str) -> Path:
        OUT_DIR.mkdir(exist_ok=True)
        raw = self.txt_path.get().strip()
        path = Path(raw).expanduser() if raw else DEFAULT_TXT
        if self.txt_mode.get() == TXT_NEW:
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            path = path.with_name(f"{path.stem}-{stamp}{path.suffix or '.txt'}")
        path.parent.mkdir(parents=True, exist_ok=True)

        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        block = f"[{stamp}] {text}\n"
        if path.exists():
            old = path.read_text(encoding="utf-8")
            if old and not old.endswith("\n"):
                block = "\n" + block
        with path.open("a", encoding="utf-8") as f:
            f.write(block)
        self.txt_path.set(str(path))
        return path

    def pick_txt(self) -> None:
        p = filedialog.asksaveasfilename(
            title="TXT 位置", defaultextension=".txt",
            initialfile="dictation.txt", initialdir=str(OUT_DIR))
        if p:
            self.txt_path.set(p)

    def save_txt(self) -> None:
        text = self.text.get("1.0", "end-1c").strip()
        if not text:
            messagebox.showinfo("沒有內容", "文字區是空的。")
            return
        path = Path(self.txt_path.get().strip()).expanduser() or DEFAULT_TXT
        try:
            if self.txt_mode.get() == TXT_NEW:
                stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
                path = path.with_name(f"{path.stem}-{stamp}{path.suffix or '.txt'}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text + "\n", encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("寫入失敗", str(exc))
            return
        self.txt_path.set(str(path))
        self.set_status(f"已寫入 {path}")

    def copy_all(self) -> None:
        text = self.text.get("1.0", "end-1c")
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status("已複製到剪貼簿")

    def clear(self) -> None:
        self.text.delete("1.0", "end")

    def open_folder(self) -> None:
        import subprocess
        p = Path(self.txt_path.get().strip()).expanduser() or DEFAULT_TXT
        p = p if p.parent.exists() else OUT_DIR
        subprocess.Popen(["explorer", str(p)])

    def pick_media(self) -> None:
        p = filedialog.askopenfilename(
            title="選音檔（不必經過麥克風）",
            filetypes=[("音檔", "*.wav *.mp3 *.flac *.ogg *.m4a"), ("全部", "*.*")],
            initialdir=str(ROOT))
        if not p:
            return
        src = Path(p)
        wav = src
        if src.suffix.lower() != ".wav":
            wav = OUT_DIR / "from_media.wav"
            OUT_DIR.mkdir(exist_ok=True)
            import subprocess
            try:
                subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                                "-i", str(src), "-ar", str(rec_mod.SAMPLE_RATE),
                                "-ac", "1", str(wav)], check=True)
            except Exception as exc:
                messagebox.showerror("轉檔失敗", f"{exc}\n\n需要 ffmpeg。")
                return
        self._pending.append(str(wav))
        self._pump()


def main() -> None:
    try:
        root = tk.Tk()
        app = DictationApp(root)
    except Exception:
        log("啟動失敗:\n" + traceback.format_exc())
        raise
    try:
        root.mainloop()
    finally:
        try:
            app.stop_hotkey()
        except Exception:
            pass
        try:
            app._shutdown_server()   # 模型跟著 UI 一起關
        except Exception:
            pass
        log("UI 結束")


if __name__ == "__main__":
    main()