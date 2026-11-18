"""注入自測：另一個行程開輸入框視窗，本行程分別測
  A. 目標已在前景 → 直接貼上 / 逐字
  B. 本行程自己的視窗搶到前景 → bring_back() 切回再貼上
結果由輸入框行程寫到 out/inject_result.txt。
"""
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import textout  # noqa: E402
import winfocus  # noqa: E402

RESULT = ROOT / "out" / "inject_result.txt"

TARGET = r'''
import tkinter as tk, sys
r = tk.Tk(); r.title("inject-target"); r.geometry("420x120+200+200")
e = tk.Entry(r, font=("Microsoft JhengHei", 12)); e.pack(fill="x", padx=10, pady=30)
r.after(200, lambda: (r.lift(), r.focus_force(), e.focus_set()))
def dump():
    open(sys.argv[1], "w", encoding="utf-8").write(e.get()); r.destroy()
r.after(int(sys.argv[2]), dump)
r.mainloop()
'''

def main():
    RESULT.unlink(missing_ok=True)
    p = subprocess.Popen([sys.executable, "-c", TARGET, str(RESULT), "4500"])
    time.sleep(1.2)
    winfocus.track()
    print("追蹤到:", winfocus.target())

    # A. 目標在前景
    textout.type_text("甲貼上，", method="paste")
    time.sleep(0.4)
    textout.type_text("乙逐字。", method="key")
    time.sleep(0.4)

    # B. 自己的視窗搶前景，再切回去
    import tkinter as tk
    me = tk.Tk(); me.title("Dictation-test"); me.geometry("200x80+700+200")
    me.update(); me.lift(); me.focus_force(); me.update()
    time.sleep(0.4); me.update()
    print("前景是自己:", winfocus.foreground_is_mine())
    print("切回:", winfocus.bring_back())
    time.sleep(0.1)
    textout.type_text("丙切回OK", method="paste")
    time.sleep(0.3)
    me.destroy()

    p.wait(timeout=10)
    got = RESULT.read_text(encoding="utf-8") if RESULT.exists() else ""
    want = "甲貼上，乙逐字。丙切回OK"
    print("輸入框內容:", repr(got))
    print("PASS" if got == want else f"FAIL（預期 {want!r}）")


if __name__ == "__main__":
    main()
