"""live.py — 邊說邊出字（按住說話期間就陸續打出文字）。

兩種做法，UI 可切換：
  A. 分段（segment）：偵測短暫停頓（預設 0.25 秒）就把那段送去辨識並打出；
     一段超過上限（預設 3 秒）沒有停頓，就在其中最安靜的位置強制切開。
     打出去的字不會再改。
  B. 即時（agree）：每隔 step 秒把「尚未定案的音訊」整段重新辨識，
     連續兩次結果相同的開頭部分才打出（LocalAgreement-2，
     Macháček et al. 2023「Turning Whisper into Real-Time Transcription System」，
     github.com/ufal/whisper_streaming）。已定案的整段會從緩衝區移除，
     並當作下一次辨識的前文提示。

兩者都把「已打出的文字」當前文提示（whisper 的 prompt）送出，
讓分段前後的用詞、繁簡與標點一致。

與 UI 無關，可單獨測試：tests/live_test.py 用錄好的 WAV 以實際速度餵入。
"""
from __future__ import annotations

import io
import queue
import threading
import time
import wave
from typing import Callable

import numpy as np

import punct

DEBUG = False
SR = 16000
FRAME = 480                       # 30 ms
PUNCT_END = "。！？…，、；：」』）】"
SENT_END = "。！？…"

# 第一段沒有前文時用的提示：讓輸出維持繁體並帶標點
DEFAULT_PROMPT = "以下是一段繁體中文的口語內容，有適當的標點符號。"

Segments = list[tuple[float, float, str]]          # (start 秒, end 秒, 文字)
Transcribe = Callable[[np.ndarray, str], Segments]


# ---------------------------------------------------------------- 音訊
class Resampler:
    """裝置取樣率 int16 位元組 → 16 kHz int16；跨 chunk 保留餘數，不產生接縫。"""

    def __init__(self, src_rate: int) -> None:
        self.src = src_rate
        self.rest = np.zeros(0, np.int16)

    def feed(self, raw: bytes) -> np.ndarray:
        x = np.concatenate([self.rest, np.frombuffer(raw, np.int16)])
        if self.src == SR:
            self.rest = np.zeros(0, np.int16)
            return x
        if self.src % SR == 0:                      # 48k → 16k：每 3 點取平均
            k = self.src // SR
            n = len(x) // k
            self.rest = x[n * k:]
            return x[:n * k].reshape(n, k).astype(np.float32).mean(1).astype(np.int16)
        # 其他取樣率：線性內插（整段處理，餘數不保留）
        self.rest = np.zeros(0, np.int16)
        m = int(round(len(x) * SR / self.src))
        if m <= 0:
            return np.zeros(0, np.int16)
        idx = np.linspace(0, len(x) - 1, m)
        return np.interp(idx, np.arange(len(x)), x.astype(np.float32)).astype(np.int16)


def to_wav(pcm: np.ndarray) -> bytes:
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.astype(np.int16).tobytes())
    return b.getvalue()


class Vad:
    """以能量判斷有沒有在說話。門檻＝背景噪音（音量第 10 百分位）× 3，至少 100。"""

    def __init__(self) -> None:
        self.hist: list[float] = []

    def rms(self, pcm: np.ndarray) -> np.ndarray:
        n = len(pcm) // FRAME
        if n == 0:
            return np.zeros(0)
        f = pcm[:n * FRAME].astype(np.float32).reshape(n, FRAME)
        return np.sqrt((f ** 2).mean(1))

    def threshold(self) -> float:
        if len(self.hist) < 20:
            return 150.0
        return max(float(np.percentile(self.hist[-2000:], 10)) * 3.0, 100.0)

    def push(self, r: np.ndarray) -> None:
        self.hist.extend(float(v) for v in r)


# ---------------------------------------------------------------- 文字
def _tail(text: str, n: int = 160) -> str:
    return text[-n:]


def _clean(t: str) -> str:
    """標點正規化 + 去掉短音段常被補出的語助詞「呃」。"""
    t = punct.tidy(t).replace("呃，", "").replace("呃", "")
    return t.lstrip("，、")


def _hyp(segs: Segments, final: bool) -> tuple[str, list[tuple[int, float]]]:
    """分段 → 一串文字；回傳 (文字, [(此段結束的字元位置, 此段結束秒數)])。
    只用 whisper 自己的標點：分段位置每次重新辨識都會變，
    在段與段之間自動補句號會讓前後兩次結果對不上、遲遲無法定案。
    final 時才在全文最後補句號。"""
    out = ""
    marks: list[tuple[int, float]] = []
    segs = [(s, e, _clean(t)) for s, e, t in segs]
    segs = [x for x in segs if x[2]]
    for s, e, t in segs:
        out += t
        marks.append((len(out), e))
    if final and out and out[-1] not in PUNCT_END:
        out += "。"
    return out, marks


def _norm(text: str) -> tuple[str, list[int]]:
    """比對用：只留文字、數字（英文轉小寫），回傳 (正規化字串, 每個字在原文的位置)。"""
    out, pos = [], []
    for i, ch in enumerate(text):
        if ch.isalnum():
            out.append(ch.lower())
            pos.append(i)
    return "".join(out), pos


def _align(done: str, hyp: str) -> int:
    """已打出的（正規化）done 對應到新結果 hyp 的哪個位置之後。"""
    if not done:
        return 0
    if hyp.startswith(done):
        return len(done)
    import difflib
    sm = difflib.SequenceMatcher(None, done, hyp[:len(done) + 8], autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size]
    if not blocks:
        return min(len(done), len(hyp))
    last = blocks[-1]
    return min(len(hyp), last.b + last.size + (len(done) - (last.a + last.size)))


def _common(a: str, b: str) -> str:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    s = a[:n]
    # 不要停在英文單字／數字中間
    if n < len(a) and n < len(b) and s and s[-1].isascii() and s[-1].isalnum() \
            and a[n].isascii() and a[n].isalnum():
        while s and s[-1].isascii() and s[-1].isalnum():
            s = s[:-1]
    return s


_HALLU = ("字幕", "訂閱", "订阅", "點贊", "点赞", "感謝觀看", "谢谢观看", "謝謝收看", "明鏡")


def _hallucination(t: str) -> bool:
    return any(h in t for h in _HALLU) and len(t) < 30


# ---------------------------------------------------------------- 主體
class LiveSession:
    """pull() 取新音訊（裝置取樣率 int16 bytes，沒有就回 b""）；
    emit(piece) 每定案一段文字呼叫一次（在本類別的工作執行緒上）；
    finish() 後會把剩下的音訊處理完，done(全文) 收尾。"""

    def __init__(self, *, mode: str, src_rate: int,
                 pull: Callable[[], bytes], transcribe: Transcribe,
                 emit: Callable[[str], None], done: Callable[[str], None],
                 pause: float = 0.25, max_seg: float = 3.0, step: float = 1.0,
                 default_prompt: str = DEFAULT_PROMPT,
                 log: Callable[[str], None] = lambda m: None) -> None:
        assert mode in ("segment", "agree")
        self.mode = mode
        self.pull, self.transcribe = pull, transcribe
        self.emit_cb, self.done_cb, self.log = emit, done, log
        self.pause, self.max_seg, self.step = pause, max_seg, step
        self.default_prompt = default_prompt
        self.hold = ""
        self.rs = Resampler(src_rate)
        self.vad = Vad()
        self.text = ""                    # 已打出的全文
        self._stop = threading.Event()
        self._th = threading.Thread(target=self._run, daemon=True)
        # 緩衝
        self.buf = np.zeros(0, np.int16)
        self.rms = np.zeros(0)
        # agree 模式狀態
        self.done_n = ""
        self.done_raw = ""
        self.prev_tail = ""
        self.last_run_len = 0
        self.calls = 0
        self.busy_s = 0.0

    # ---------- 外部 ----------
    def start(self) -> None:
        self._th.start()

    def finish(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        self._th.join(timeout)

    # ---------- 共用 ----------
    def _take(self) -> bool:
        raw = self.pull()
        if not raw:
            return False
        x = self.rs.feed(raw)
        if len(x):
            self.buf = np.concatenate([self.buf, x])
            n_old = len(self.rms)
            self.rms = self.vad.rms(self.buf)
            self.vad.push(self.rms[n_old:])
        return True

    def _drop(self, n: int) -> None:
        """從緩衝區開頭丟掉 n 個樣本（對齊 frame）。"""
        n = (n // FRAME) * FRAME
        self.buf = self.buf[n:]
        self.rms = self.rms[n // FRAME:]

    def _prompt(self) -> str:
        return self._with_hint(self.text)

    def _with_hint(self, before: str) -> str:
        """前文提示＝標點說明句 + 已打出的最後一段。
        只給前文時，前文若剛好沒標點，之後就一路都不出標點。"""
        return (self.default_prompt + _tail(before, 120)).strip()

    def _asr(self, pcm: np.ndarray, prompt: str) -> Segments:
        t0 = time.time()
        try:
            return self.transcribe(pcm, prompt)
        finally:
            self.calls += 1
            self.busy_s += time.time() - t0

    def _emit(self, piece: str) -> None:
        if not piece:
            return
        self.text += piece
        self.emit_cb(piece)

    def _voiced(self, r: np.ndarray) -> np.ndarray:
        return r > self.vad.threshold()

    def _run(self) -> None:
        try:
            if self.mode == "segment":
                self._run_segment()
            else:
                self._run_agree()
            if self.text and (self.hold or self.text[-1] not in PUNCT_END):
                self._emit("。")
        except Exception as exc:       # noqa: BLE001
            self.log(f"live 失敗: {exc!r}")
        finally:
            self.done_cb(self.text)

    # ---------- A. 分段 ----------
    def _run_segment(self) -> None:
        pause_f = max(1, int(round(self.pause / 0.03)))
        max_f = int(self.max_seg / 0.03)
        while True:
            stopping = self._stop.is_set()
            got = self._take()
            if stopping and not got:
                # 放開：剩下的整段送出
                self._cut(len(self.rms), final=True)
                return
            if not got:
                time.sleep(0.02)
                continue
            v = self._voiced(self.rms)
            if not v.any():
                # 開頭全是靜音：只留最後 0.3 秒
                if len(self.rms) > 20:
                    self._drop((len(self.rms) - 10) * FRAME)
                continue
            first = int(np.argmax(v))
            # 從最後一個有聲音的 frame 往後數靜音
            last = len(v) - 1 - int(np.argmax(v[::-1]))
            silent = len(v) - 1 - last
            voiced_f = int(v.sum())
            if silent >= pause_f and voiced_f >= 8:
                self._cut(last + 1 + min(silent, 5), final=False, start=first)
            elif len(self.rms) - first >= max_f:
                lo = first + max_f // 2
                cut = lo + int(np.argmin(self.rms[lo:]))
                self._cut(cut + 1, final=False, start=first, forced=True)

    def _cut(self, end_f: int, *, final: bool, start: int = 0, forced: bool = False) -> None:
        start = max(0, start - 5)                      # 前面留 0.15 秒
        pcm = self.buf[start * FRAME:end_f * FRAME]
        r = self.rms[start:end_f]
        self._drop(end_f * FRAME)
        if len(r) == 0 or int(self._voiced(r).sum()) < 6:
            return
        segs = self._asr(pcm, self._prompt())
        t = "".join(_clean(x[2]) for x in segs)
        if not t or _hallucination(t):
            return
        if self.hold:                          # 上一段停頓留下的逗號，跟這段一起打出
            if t[0] not in PUNCT_END:
                t = self.hold + t
            self.hold = ""
        if t[-1] in "，、；：":                 # 結尾逗號先不打：若之後沒有下一段，改成句號
            self.hold = t[-1]
            t = t[:-1]
        elif not forced and t[-1] not in PUNCT_END:
            if final:
                t += "。"
            else:
                self.hold = "，"
        if t:
            self._emit(t)

    # ---------- B. 即時 ----------
    def _run_agree(self) -> None:
        step_n = int(self.step * SR)
        while True:
            stopping = self._stop.is_set()
            got = self._take()
            if stopping and not got:
                self._agree_final()
                return
            if not got:
                time.sleep(0.02)
                continue
            if len(self.buf) - self.last_run_len < step_n:
                continue
            v = self._voiced(self.rms)
            if not v.any():
                if len(self.rms) > 20:
                    self._drop((len(self.rms) - 10) * FRAME)
                    self.last_run_len = len(self.buf)
                continue
            self.last_run_len = len(self.buf)
            self._agree_step()

    # 狀態（正規化字串，忽略標點與大小寫）：
    #   done_n：這個緩衝區已打出的部分（以最新一次辨識的寫法記）
    #   prev_tail：上一次辨識在 done_n 之後的部分，用來和這一次比對
    def _agree_prompt(self) -> str:
        before = self.text[:len(self.text) - len(self.done_raw)] if self.done_raw else self.text
        return self._with_hint(before)

    def _agree_step(self) -> None:
        segs = self._asr(self.buf, self._agree_prompt())
        hyp, marks = _hyp(segs, final=False)
        if not hyp or _hallucination(hyp):
            return
        if DEBUG:
            self.log(f"[{len(self.buf) / SR:4.1f}s] {hyp}")
        nh, pos = _norm(hyp)
        off = _align(self.done_n, nh)
        tail = nh[off:]
        agree = _common(tail, self.prev_tail)
        if agree:
            a, b = off, off + len(agree)
            start = pos[a - 1] + 1 if a > 0 else 0
            piece = hyp[start:pos[b - 1] + 1]
            self._emit(piece)
            self.done_raw += piece
            off = b
        self.done_n = nh[:off]
        self.prev_tail = nh[off:]
        self._trim(marks, nh, pos, off)

    def _trim(self, marks, nh: str, pos: list[int], off: int) -> None:
        """緩衝區超過 8 秒：把已完全打出的整段從緩衝區移除。"""
        if len(self.buf) < 8 * SR:
            return
        done_raw_end = pos[off - 1] + 1 if off > 0 else 0
        cut = None
        for raw_end, end_s in marks[:-1]:
            if raw_end <= done_raw_end + 1:
                cut = (raw_end, end_s)
        if cut is None:
            if len(self.buf) > 20 * SR:              # 一直沒有段落邊界：保留最後 4 秒
                self.log("live: 緩衝過長，強制截斷")
                self._drop(len(self.buf) - 4 * SR)
                self.done_n = self.prev_tail = self.done_raw = ""
                self.last_run_len = len(self.buf)
            return
        raw_end, end_s = cut
        n_cut = sum(1 for p in pos if p < raw_end)   # 被移除的正規化字數
        self._drop(int(end_s * SR))
        self.done_n = self.done_n[n_cut:]
        # done_raw：已打出、仍留在緩衝區的原文（用來算前文提示）
        keep = len(self.done_n)
        self.done_raw = self._raw_tail_of(keep)
        self.last_run_len = len(self.buf)

    def _raw_tail_of(self, n_norm: int) -> str:
        """self.text 結尾含 n_norm 個文字（不含標點）的那一段。"""
        if n_norm <= 0:
            return ""
        cnt = 0
        for i in range(len(self.text) - 1, -1, -1):
            if self.text[i].isalnum():
                cnt += 1
                if cnt == n_norm:
                    return self.text[i:]
        return self.text

    def _agree_final(self) -> None:
        if len(self.rms) == 0 or not self._voiced(self.rms).any():
            return
        segs = self._asr(self.buf, self._agree_prompt())
        hyp, _ = _hyp(segs, final=True)
        if not hyp or _hallucination(hyp):
            return
        nh, pos = _norm(hyp)
        off = _align(self.done_n, nh)
        start = pos[off - 1] + 1 if off > 0 else 0
        self._emit(hyp[start:])


# ---------------------------------------------------------------- 錄音接頭
class Tap:
    """接在 Recorder 上，複製一份即時音訊給 LiveSession。"""

    def __init__(self) -> None:
        self.q: "queue.Queue[bytes]" = queue.Queue()

    def put(self, b: bytes) -> None:
        self.q.put(b)

    def pull(self) -> bytes:
        out = []
        while True:
            try:
                out.append(self.q.get_nowait())
            except queue.Empty:
                break
        return b"".join(out)
