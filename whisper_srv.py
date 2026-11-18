"""whisper-server 生命週期管理。

模型載入一次要 1.9 秒，這是「說完等兩三秒」的主因。
whisper-server 把模型留在記憶體，之後每次推理只要 0.2～0.9 秒。

規則：
  - UI 開 → 一起開（背景起 server）
  - UI 關 → 一起關（server 必須跟著死，不留孤兒）
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "bin" / "whisper" / "whisper-server.exe"
DEFAULT_MODEL = ROOT / "models" / "ggml-large-v3-turbo.bin"

HOST = "127.0.0.1"
PORT = 18080
BASE = f"http://{HOST}:{PORT}"


def _env() -> dict:
    env = dict(os.environ)
    # 需要時（例如顯示卡型號與下載的 ROCm 套件不完全對應）在環境變數設定 HSA_OVERRIDE_GFX_VERSION，
    # run.bat 會從 config\local.bat 帶進來
    return env


def post_inference(base: str, wav_bytes: bytes, *, prompt: str = "",
                   timeout: float = 60.0) -> dict:
    boundary = "----dictationboundary"
    fields = [("response_format", "verbose_json")]
    if prompt:
        fields.append(("prompt", prompt))
    parts = [
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="file"; filename="take.wav"\r\n',
        b"Content-Type: audio/wav\r\n\r\n",
        wav_bytes, b"\r\n",
    ]
    for k, v in fields:
        parts += [f"--{boundary}\r\n".encode(),
                  f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode(),
                  v.encode("utf-8"), b"\r\n"]
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        f"{base}/inference", data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def segments_of(data: dict) -> list[tuple[float, float, str]]:
    out = []
    for s in data.get("segments", []):
        t = s.get("text", "")
        if t.strip():
            out.append((float(s.get("start", 0.0)), float(s.get("end", 0.0)), t))
    return out


class WhisperServer:
    """背景跑 whisper-server，用完必須 close()。"""

    def __init__(self, model: Path = DEFAULT_MODEL, *, language: str = "zh",
                 gpu: bool = True, port: int = PORT) -> None:
        self.model = Path(model)
        self.language = language
        self.gpu = gpu
        self.port = port
        self.base = f"http://{HOST}:{port}"
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()

    # ---------- 狀態 ----------
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def wait_ready(self, timeout: float = 90.0) -> bool:
        """等 /health 回 200。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.alive:
                return False
            try:
                with urllib.request.urlopen(f"{self.base}/health", timeout=2) as r:
                    if r.status == 200:
                        self._ready.set()
                        return True
            except Exception:
                pass
            time.sleep(0.4)
        return False

    # ---------- 啟動 / 關閉 ----------
    def _kill_stale(self) -> None:
        """清掉上次忘記關掉的 whisper-server（避免同一個 port 被占）。"""
        try:
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq whisper-server.exe",
                 "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=15)
            pids = []
            for line in out.stdout.splitlines():
                parts = [p.strip('"') for p in line.split('","')]
                if len(parts) >= 2 and parts[0].lower() == "whisper-server.exe":
                    if parts[1].isdigit():
                        pids.append(parts[1])
            for pid in pids:
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True, timeout=15)
            if pids:
                time.sleep(1.0)
        except Exception:
            pass

    def start(self, *, timeout: float = 90.0) -> bool:
        """起 server（非阻塞，用 wait_ready 等就緒）。"""
        with self._lock:
            if self.alive:
                return True
            if not SERVER.exists():
                raise FileNotFoundError(f"找不到 whisper-server.exe：{SERVER}")
            if not self.model.exists():
                raise FileNotFoundError(f"找不到模型：{self.model}")

            self._kill_stale()

            args = [
                str(SERVER),
                "-m", str(self.model),
                "-l", self.language,
                "-t", "8",
                "--host", HOST,
                "--port", str(self.port),
            ]
            # 注意：不要加 -nt。`-nt` 會把 whisper 的分段壓成一大段，
            # 導致補不出段尾句號（標點會全部黏在一起）。
            if not self.gpu:
                args.append("-ng")

            log_dir = ROOT / "logs"
            log_dir.mkdir(exist_ok=True)
            self._log = open(log_dir / "server.log", "wb")
            self._proc = subprocess.Popen(
                args, stdout=self._log, stderr=subprocess.STDOUT,
                cwd=str(ROOT), env=_env(),
            )
        return self.wait_ready(timeout=timeout)

    def close(self, timeout: float = 6.0) -> None:
        """關掉 server，不留孤兒。"""
        with self._lock:
            proc, self._proc = self._proc, None
        self._ready.clear()
        if proc is None:
            return
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except Exception:
            pass
        try:
            self._log.close()
        except Exception:
            pass

    def restart(self, *, timeout: float = 90.0) -> bool:
        self.close()
        time.sleep(0.5)
        return self.start(timeout=timeout)

    # ---------- 推理 ----------
    def transcribe(self, wav: Path, *, timeout: float = 120.0) -> str:
        """送出 wav，回傳 dict（含 text 與 segments）。"""
        if not self.alive:
            raise RuntimeError("whisper-server 沒在跑")

        boundary = "----dictationboundary"
        wav_bytes = Path(wav).read_bytes()
        parts = [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="take.wav"\r\n',
            b"Content-Type: audio/wav\r\n\r\n",
            wav_bytes,
            f"\r\n--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="response_format"\r\n\r\n',
            b"verbose_json\r\n",
            f"--{boundary}--\r\n".encode(),
        ]
        body = b"".join(parts)
        req = urllib.request.Request(
            f"{self.base}/inference", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def transcribe_bytes(self, wav_bytes: bytes, *, prompt: str = "",
                         timeout: float = 60.0) -> dict:
        """記憶體中的 WAV 直接送出（邊說邊出字用），prompt＝前文提示。"""
        if not self.alive:
            raise RuntimeError("whisper-server 沒在跑")
        return post_inference(self.base, wav_bytes, prompt=prompt, timeout=timeout)

    def transcribe_text(self, wav: Path) -> str:
        """回傳已套標點正規化的中文。"""
        import punct

        data = self.transcribe(wav)
        segs = [s.get("text", "") for s in data.get("segments", [])]
        segs = [s for s in segs if s.strip()]
        if segs:
            return punct.join_segments(segs)
        return punct.polish(data.get("text", ""))