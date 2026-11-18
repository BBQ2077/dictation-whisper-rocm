"""邊說邊出字自測：把錄好的 WAV 以實際速度（48 kHz、每塊 1024 樣本，和錄音器相同）
餵進 LiveSession，記錄每段文字在第幾秒打出、放開後多久收尾。

用法：python tests/live_test.py [segment|agree|both] [--port 18081] [--wav 檔案]
需要 whisper-server 在指定 port 執行中。
"""
import argparse
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live          # noqa: E402
import whisper_srv   # noqa: E402


def run(mode: str, pcm16: np.ndarray, base: str, **kw) -> dict:
    src = np.repeat(pcm16, 3)                    # 16k → 48k，走和實機一樣的降採樣
    chunk = 1024
    tap = live.Tap()
    pieces: list[tuple[float, str]] = []
    done_ev = threading.Event()
    t0 = time.time()

    def asr(pcm, prompt):
        return whisper_srv.segments_of(
            whisper_srv.post_inference(base, live.to_wav(pcm), prompt=prompt))

    s = live.LiveSession(
        mode=mode, src_rate=48000, pull=tap.pull, transcribe=asr,
        emit=lambda p: pieces.append((time.time() - t0, p)),
        done=lambda full: done_ev.set(), log=print, **kw)
    s.start()
    for i in range(0, len(src), chunk):
        tap.put(src[i:i + chunk].tobytes())
        target = t0 + (i + chunk) / 48000
        dt = target - time.time()
        if dt > 0:
            time.sleep(dt)
    t_release = time.time() - t0
    s.finish()
    done_ev.wait(60)
    t_done = time.time() - t0
    return dict(pieces=pieces, text=s.text, release=t_release, done=t_done,
                calls=s.calls, busy=s.busy_s)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="both")
    ap.add_argument("--port", default="18081")
    ap.add_argument("--wav", default=str(ROOT / "tests" / "fixtures" / "live_sample.wav"))
    ap.add_argument("--pause", type=float, default=0.25)
    ap.add_argument("--max-seg", type=float, default=3.0)
    ap.add_argument("--step", type=float, default=1.0)
    a = ap.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    with wave.open(a.wav) as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    dur = len(pcm) / 16000
    full = "".join(t for _, _, t in whisper_srv.segments_of(
        whisper_srv.post_inference(base, live.to_wav(pcm))))
    print(f"音檔 {dur:.1f}s\n整段一次辨識（參考）：{full}\n")
    modes = ["segment", "agree"] if a.mode == "both" else [a.mode]
    for m in modes:
        kw = dict(pause=a.pause, max_seg=a.max_seg) if m == "segment" else dict(step=a.step)
        r = run(m, pcm, base, **kw)
        print(f"=== {m} {kw} ===")
        for t, p in r["pieces"]:
            print(f"  {t:5.1f}s  {p}")
        gaps = [b[0] - a_[0] for a_, b in zip(r["pieces"], r["pieces"][1:])]
        print(f"  全文：{r['text']}")
        print(f"  第一段出字 {r['pieces'][0][0]:.1f}s；出字間隔最長 {max(gaps, default=0):.1f}s；"
              f"放開後收尾 {r['done'] - r['release']:.2f}s；辨識 {r['calls']} 次共 {r['busy']:.1f}s\n")


if __name__ == "__main__":
    main()
