"""Hán-Việt conversion for Chinese names and terms.

Uses dictionaries in QuickTranslator format ("中文=nghĩa", alternatives separated
by "/" or "|"), placed in the folder configured in Settings → "Thư mục từ điển":
  * Names.txt / Names2.txt          - proper names (highest priority)
  * ChinesePhienAmWords.txt         - per-character Hán-Việt readings
Without dictionaries the LLM's suggestion is used.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


def _parse(path: Path) -> dict[str, str]:
    d: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return d
    for line in text.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().split("/")[0].split("|")[0].strip()
        if k and v:
            d[k] = v
    return d


@lru_cache(maxsize=2)
def load(dict_dir: str) -> tuple[dict, dict]:
    names: dict[str, str] = {}
    chars: dict[str, str] = {}
    if dict_dir:
        base = Path(dict_dir)
        for fn in ("Names2.txt", "Names.txt"):
            if (base / fn).exists():
                for k, v in _parse(base / fn).items():
                    names.setdefault(k, v)
        for fn in ("ChinesePhienAmWords.txt", "HanViet.txt"):
            if (base / fn).exists():
                chars.update(_parse(base / fn))
    return names, chars


def _dict_dir() -> str:
    try:
        from ..config import Settings

        return Settings.get()["dict_dir"] or ""
    except Exception:
        return ""


def convert(text: str, dict_dir: str | None = None) -> str:
    """Longest-match conversion; returns '' if no dictionary covers the text."""
    names, chars = load(dict_dir if dict_dir is not None else _dict_dir())
    if not names and not chars:
        return ""
    if text in names:
        return names[text]
    out: list[str] = []
    i = 0
    maxlen = 8
    while i < len(text):
        for L in range(min(maxlen, len(text) - i), 0, -1):
            piece = text[i:i + L]
            if piece in names:
                out.append(names[piece])
                i += L
                break
            if L == 1:
                out.append(chars.get(piece, ""))
                i += 1
        else:
            i += 1
    if any(not w for w in out):
        return ""
    return " ".join(w[:1].upper() + w[1:] for w in " ".join(out).split())


def available() -> bool:
    names, chars = load(_dict_dir())
    return bool(names or chars)
