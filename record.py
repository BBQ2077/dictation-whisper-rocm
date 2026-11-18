"""Dictation 錄音引擎：錄音 → WAV → whisper.cpp(ROCm) 轉文字。

可單獨自測：
    python record.py --list
    python record.py --seconds 5
    python record.py --wav out/sine.wav
"""

from __future__ import annotations

import queue
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BIN = ROOT / "bin" / "whisper" / "whisper-cli.exe"
DEFAULT_MODEL = ROOT / "models" / "ggml-large-v3-turbo.bin"

# 對裝置擷取用裝置原生格式；寫檔時降採樣到 whisper 要的 16 kHz
DEVICE_SAMPLE_RATE = 48000
SAMPLE_RATE = 16000
CHANNELS = 1
CHUNK = 1024
TAIL_SECONDS = 0.8  # 放開後再補錄一點，避免尾音被切掉

# 預設錄音裝置（None = 用系統預設）。UI 啟動後會依裝置清單自動選；CLI 可用 --device 指定。
DEFAULT_DEVICE: int | None = None


class Recorder:
    """錄音器。start() 非阻塞，stop() 回傳 WAV 路徑。"""

    def __init__(self, device: int | None = DEFAULT_DEVICE) -> None:
        self._q: "queue.Queue[bytes]" = queue.Queue()
        self._stream = None
        self._frames: list[bytes] = []
        self._wave_path: Path | None = None
        self._lock = threading.Lock()
        self.device = device
        self.tap = None              # live.Tap；None＝只錄成 WAV

    # ---------- 裝置 ----------
    @staticmethod
    def devices() -> list[tuple[int, str]]:
        """回傳 (index, 名稱)，只列有輸入能力的裝置。"""
        import sounddevice as sd

        out = []
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                out.append((i, d["name"]))
        return out

    # ---------- 錄音 ----------
    def start(self) -> None:
        import sounddevice as sd

        with self._lock:
            if self._stream is not None:
                return
            self._frames = []
            self._q = queue.Queue()
            self._stream = sd.RawInputStream(
                samplerate=DEVICE_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK,
                device=self.device,
                callback=self._on_audio,
            )
            self._stream.start()

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if status:
            print(f"[audio] {status}", file=sys.stderr)
        b = bytes(indata)
        self._q.put(b)
        tap = self.tap
        if tap is not None:          # 邊說邊出字：同一份音訊複製給 live.LiveSession
            tap.put(b)

    def _drain(self, timeout: float) -> None:
        """把 callback 推進 queue 的尾巴吸進 frames。"""
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                return
            try:
                self._frames.append(self._q.get(timeout=min(remain, 0.15)))
            except queue.Empty:
                return

    def stop(self) -> Path | None:
        """停止錄音，寫入暫存 WAV（16 kHz mono），回傳路徑。"""
        with self._lock:
            stream, self._stream = self._stream, None
        if stream is None:
            return None

        stream.stop()
        stream.close()
        self._drain(TAIL_SECONDS)  # 先吸乾 queue 裡已錄到的尾巴

        data = b"".join(self._frames)
        self._frames = []
        if not data:
            return None

        out_dir = ROOT / "out"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / "take.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(CHANNELS)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(_resample_to_16k(data))
        self._wave_path = path
        return path

    @property
    def recording(self) -> bool:
        return self._stream is not None


# ---------- 轉文字 ----------
def _resample_to_16k(pcm: bytes) -> bytes:
    """把 DEVICE_SAMPLE_RATE 的 int16 PCM 線性降採樣到 16 kHz。"""
    if DEVICE_SAMPLE_RATE == SAMPLE_RATE:
        return pcm

    import numpy as np

    src = np.frombuffer(pcm, dtype=np.int16)
    if src.size == 0:
        return b""
    dst_len = int(round(src.size * SAMPLE_RATE / DEVICE_SAMPLE_RATE))
    idx = np.linspace(0, src.size - 1, dst_len)
    resampled = np.interp(idx, np.arange(src.size), src.astype(np.float32))
    return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()


def transcribe(
    wav: Path,
    *,
    model: Path = DEFAULT_MODEL,
    language: str = "zh",
    gpu: bool = True,
    on_stderr=None,
) -> str:
    """呼叫 whisper-cli，回傳帶標點的純文字。

    注意：不加 -nt。`-nt` 會連標點符號一起丟掉，
    所以時間軸改由 _clean() 用正則去掉，標點保留。
    """
    if not BIN.exists():
        raise FileNotFoundError(f"找不到 whisper-cli.exe：{BIN}")
    if not model.exists():
        raise FileNotFoundError(f"找不到模型：{model}")

    cmd = [
        str(BIN),
        "-m", str(model),
        "-f", str(wav),
        "-l", language,
        "-t", "8",
        "-ml", "0",        # 不限制每段字數，避免長句被切掉
    ]
    if not gpu:
        cmd.append("-ng")

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=_env())
    if proc.returncode != 0:
        raise RuntimeError(f"whisper 失敗 (code {proc.returncode})：\n{proc.stderr[-1200:]}")
    if on_stderr:
        on_stderr(proc.stderr)
    return _clean(proc.stdout)


def _env() -> dict:
    import os

    env = dict(os.environ)
    # 需要時（例如顯示卡型號與下載的 ROCm 套件不完全對應）在環境變數設定 HSA_OVERRIDE_GFX_VERSION，
    # run.bat 會從 config\local.bat 帶進來
    return env


def _clean(stdout: str) -> str:
    """去掉時間軸前綴與進度訊息，保留標點符號。"""
    import re

    ts = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}")
    lines = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        line = ts.sub("", line)                       # 去掉時間區間
        line = line.replace("[", " ").replace("]", " ")  # 去掉剩下的中括號
        line = re.sub(r"\s+", " ", line).strip()
        if not line or line.startswith("whisper_"):
            continue
        if line.startswith("(") and line.endswith(")"):   # (timings) 之類
            continue
        lines.append(line)
    text = " ".join(lines)
    return text.strip(" ,，")


# ---------- CLI 自測 ----------
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="列出錄音裝置")
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--device", type=int, default=DEFAULT_DEVICE)
    ap.add_argument("--wav", type=str, default=None, help="直接轉這個 wav，不錄音")
    ap.add_argument("--no-gpu", action="store_true")
    args = ap.parse_args()

    if args.list:
        print("錄音裝置：")
        for i, name in Recorder.devices():
            print(f"  [{i}] {name}")
        sys.exit(0)

    if args.wav:
        wav = Path(args.wav)
        print("wav:", wav)
    else:
        rec = Recorder(device=args.device)
        print(f"開始錄 {args.seconds}s … 請說話（device={args.device}）")
        rec.start()
        time.sleep(args.seconds)
        wav = rec.stop()
        print("wav:", wav)

    if not wav or not Path(wav).exists():
        print("沒有音訊，略過轉文字")
        sys.exit(1)

    t0 = time.time()
    text = transcribe(wav, gpu=not args.no_gpu)
    print(f"轉文字耗時 {time.time() - t0:.1f}s")
    print("文字：", text)