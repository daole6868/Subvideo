"""Text utilities for Chinese and Vietnamese."""

from __future__ import annotations

import difflib
import re
import unicodedata
from functools import lru_cache

# ---------------------------------------------------------------- Chinese

ZH_PUNCT = set("，。！？；：、“”‘’（）《》【】…—～·「」『』〈〉,.!?;:\"'()[]<>~-_ 　")
NONVERBAL_ZH = set("啊呀哈嗯哦噢唉哎呃嘿喂呵嘻哼咦呜嗷嘛呢吧啦哇嗯")


def is_cjk(ch: str) -> bool:
    if not ch:
        return False
    o = ord(ch[0])
    return (
        0x4E00 <= o <= 0x9FFF
        or 0x3400 <= o <= 0x4DBF
        or 0x20000 <= o <= 0x2A6DF
        or 0xF900 <= o <= 0xFAFF
    )


def strip_zh_punct(text: str) -> str:
    return "".join(ch for ch in text if ch not in ZH_PUNCT and not ch.isspace())


def zh_units(text: str) -> list[str]:
    """Split Chinese text into 'units': each CJK char, or a run of latin/digits."""
    units: list[str] = []
    buf = ""
    for ch in text:
        if is_cjk(ch):
            if buf:
                units.append(buf)
                buf = ""
            units.append(ch)
        elif ch.isalnum():
            buf += ch
        else:
            if buf:
                units.append(buf)
                buf = ""
    if buf:
        units.append(buf)
    return units


def is_nonverbal_zh(text: str) -> bool:
    core = strip_zh_punct(text)
    return bool(core) and all(ch in NONVERBAL_ZH for ch in core)


@lru_cache(maxsize=1)
def _opencc(conf: str):
    try:
        from opencc import OpenCC  # opencc-python-reimplemented or opencc

        try:
            return OpenCC(conf)
        except Exception:
            return OpenCC(conf + ".json")
    except Exception:
        return None


def to_simplified(text: str) -> str:
    cc = _opencc("t2s")
    return cc.convert(text) if cc else text


@lru_cache(maxsize=65536)
def pinyin_of(ch: str) -> str:
    try:
        from pypinyin import Style, lazy_pinyin

        r = lazy_pinyin(ch, style=Style.NORMAL)
        return r[0] if r else ch
    except Exception:
        return ch


def is_homophone(a: str, b: str) -> bool:
    if a == b:
        return True
    if not (is_cjk(a) and is_cjk(b)):
        return False
    return pinyin_of(a) == pinyin_of(b)


def similarity(a: str, b: str) -> float:
    """0..1 similarity of two strings (punctuation-insensitive)."""
    a2, b2 = strip_zh_punct(a), strip_zh_punct(b)
    if not a2 and not b2:
        return 1.0
    if not a2 or not b2:
        return 0.0
    try:
        from rapidfuzz.fuzz import ratio

        return ratio(a2, b2) / 100.0
    except Exception:
        return difflib.SequenceMatcher(None, a2, b2).ratio()


# ---------------------------------------------------------------- Vietnamese

_DIG = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def _read3(n: int, full: bool) -> list[str]:
    h, t, u = n // 100, (n // 10) % 10, n % 10
    w: list[str] = []
    if h > 0 or full:
        w += [_DIG[h], "trăm"]
    if t == 0:
        if u > 0:
            if h > 0 or full:
                w.append("linh")
            w.append(_DIG[u])
    elif t == 1:
        w.append("mười")
        if u == 5:
            w.append("lăm")
        elif u > 0:
            w.append(_DIG[u])
    else:
        w += [_DIG[t], "mươi"]
        if u == 1:
            w.append("mốt")
        elif u == 4:
            w.append("tư")
        elif u == 5:
            w.append("lăm")
        elif u > 0:
            w.append(_DIG[u])
    return w


def read_int_vi(n: int) -> str:
    if n == 0:
        return "không"
    neg = n < 0
    n = abs(n)
    units = ["", "nghìn", "triệu", "tỷ"]
    groups = []
    while n > 0:
        groups.append(n % 1000)
        n //= 1000
    words: list[str] = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        words += _read3(g, full=i < len(groups) - 1)
        if i > 0:
            unit = units[i] if i <= 3 else units[i - 3] + " tỷ"
            words.append(unit)
    out = " ".join(words)
    return ("âm " + out) if neg else out


def _read_digits(s: str) -> str:
    return " ".join(_DIG[int(c)] for c in s)


_NUM_RE = re.compile(r"\d{1,3}(?:\.\d{3})+(?!\d)|\d+(?:[.,]\d+)?")


def _num_to_words(tok: str) -> str:
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", tok):
        return read_int_vi(int(tok.replace(".", "")))
    if "," in tok or "." in tok:
        a, b = re.split(r"[.,]", tok, maxsplit=1)
        return f"{read_int_vi(int(a))} phẩy {_read_digits(b) if b.startswith('0') else read_int_vi(int(b))}"
    if len(tok) > 1 and tok.startswith("0"):
        return _read_digits(tok)  # phone numbers, codes
    return read_int_vi(int(tok))


_SYMBOLS = {
    "%": " phần trăm",
    "&": " và ",
    "+": " cộng ",
    "=": " bằng ",
    "$": " đô la",
    "°C": " độ C",
    "°": " độ",
    "km/h": " ki lô mét trên giờ",
    "kg": " ki lô gam",
    "km": " ki lô mét",
}


def normalize_vi_for_tts(text: str) -> str:
    """Make Vietnamese text TTS-friendly: numbers to words, symbols, spacing."""
    t = unicodedata.normalize("NFC", text)
    t = re.sub(r"(\d{1,2}):(\d{2})", lambda m: f"{read_int_vi(int(m.group(1)))} giờ {read_int_vi(int(m.group(2)))}" if m.group(2) != "00" else f"{read_int_vi(int(m.group(1)))} giờ", t)
    for k in sorted(_SYMBOLS, key=len, reverse=True):
        t = t.replace(k, _SYMBOLS[k])
    t = _NUM_RE.sub(lambda m: " " + _num_to_words(m.group(0)) + " ", t)
    t = t.replace("…", "...").replace("“", '"').replace("”", '"')
    t = re.sub(r"[\[\]{}<>]", " ", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def count_vi_syllables(text: str, normalize: bool = True) -> int:
    """Vietnamese syllables ~= space separated words (numbers expanded)."""
    if not text:
        return 0
    t = normalize_vi_for_tts(text) if normalize else text
    return len(_WORD_RE.findall(t))


def wrap_lines(text: str, max_chars: int = 42, max_lines: int = 2) -> str:
    """Balanced line break for subtitles (Vietnamese). Returns text with \\n."""
    text = " ".join(text.replace("\n", " ").split())
    if len(text) <= max_chars or max_lines <= 1:
        return text
    words = text.split(" ")
    if max_lines == 2:
        best, best_cost = None, 1e9
        for i in range(1, len(words)):
            a = " ".join(words[:i])
            b = " ".join(words[i:])
            over = max(0, len(a) - max_chars) + max(0, len(b) - max_chars)
            cost = over * 10 + abs(len(a) - len(b))
            prev = words[i - 1]
            if prev.endswith((",", ".", "!", "?", ";", ":")):
                cost -= 6  # prefer breaking after punctuation
            if len(b) < len(a) - 6:
                cost += 2  # prefer bottom-heavy pyramid slightly less
            if cost < best_cost:
                best, best_cost = (a, b), cost
        return "\n".join(best) if best else text
    # generic greedy for >2 lines
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > max_chars and len(lines) < max_lines - 1:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    lines.append(cur)
    return "\n".join(lines)


def cps(text: str, duration_ms: int) -> float:
    n = len(text.replace("\n", "").replace(" ", ""))
    return n / max(0.001, duration_ms / 1000.0)


def short_hash(*parts) -> str:
    import hashlib

    h = hashlib.sha1()
    for p in parts:
        h.update(repr(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()[:12]
