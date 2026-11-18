"""中文標點正規化。

Whisper 中文輸出有兩個毛病：
  1. 標點是半形（, ? ! : ;），中文該用全形（，？！：；）
  2. 段尾常常沒有句號（模型預測就是這樣結束）

這個模組負責把兩件事補齊，讓文字直接可讀。
"""
from __future__ import annotations

import re

_HALF2FULL = {
    ",": "，",
    "?": "？",
    "!": "！",
    ":": "：",
    ";": "；",
}

# 句尾標點：有這些就不補句號
CLOSERS = "。！？…」』）】"
# 前面的字若已是這些，也不需要補句號
CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
_CJK_SPACE = re.compile(rf"(?<=[{CJK}])\s+(?=[{CJK}])")
_CJK_CHAR = re.compile(rf"[{CJK}]")


def _is_cjk(ch: str) -> bool:
    return bool(ch) and bool(_CJK_CHAR.match(ch))


def to_fullwidth(text: str) -> str:
    """半形標點 → 全形。

    只換「前面是中文字」的標點：
      「很好,我們」→「很好，我們」✅
      「VS Code, and」→ 保持半形 ✅（英文句子不該用全形逗號）
      「1,234 元」→ 保持半形 ✅（千分位）
    """
    chars = list(text)
    for i, ch in enumerate(chars):
        full = _HALF2FULL.get(ch)
        if not full:
            continue
        prev = chars[i - 1] if i > 0 else ""
        if _is_cjk(prev):
            chars[i] = full
    return "".join(chars)


def squeeze_cjk_space(text: str) -> str:
    """拿掉中文與中文之間的空格（英文單字之間保留）。"""
    return _CJK_SPACE.sub("", text)


def tidy(text: str) -> str:
    """單段文字正規化：全形標點 + 去中文間多餘空白。"""
    text = to_fullwidth(text)
    text = squeeze_cjk_space(text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def join_segments(segments: list[str]) -> str:
    """把 whisper 的分段組合成一段流暢中文。

    每段若結尾沒有標點就補「。」；段與段之間不加空格。
    """
    out: list[str] = []
    for raw in segments:
        seg = tidy(raw)
        if not seg:
            continue
        if seg[-1] not in CLOSERS:
            seg += "。"
        out.append(seg)
    return "".join(out).strip()


def polish(text: str) -> str:
    """整段文字（whisper 的 text 欄位）正規化。

    用在拿不到分段的情況：全形標點 + 去中文空格，
    並在句尾補上句號。
    """
    text = tidy(text)
    if not text:
        return text
    if text[-1] not in CLOSERS:
        text += "。"
    return text