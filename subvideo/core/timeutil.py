"""Time helpers. Internal unit: integer milliseconds."""

from __future__ import annotations

import math
import re


def ms_to_srt(ms: int) -> str:
    ms = max(0, int(round(ms)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{msec:03d}"


def ms_to_ass(ms: int) -> str:
    """ASS uses centiseconds: H:MM:SS.cc"""
    cs = max(0, int(round(ms / 10.0)))
    h, rem = divmod(cs, 360_000)
    m, rem = divmod(rem, 6000)
    s, c = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{c:02d}"


def ms_to_clock(ms: int, with_ms: bool = True) -> str:
    """Human display: MM:SS.mmm or H:MM:SS.mmm"""
    neg = ms < 0
    ms = abs(int(round(ms)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, msec = divmod(rem, 1000)
    core = f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"
    if with_ms:
        core += f".{msec:03d}"
    return ("-" if neg else "") + core


_TIME_RE = re.compile(r"^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?\s*$")


def parse_time(text: str) -> int:
    """Parse '1:02:03.456', '02:03,4', '83.5' (seconds) or '1234ms' to ms."""
    t = text.strip().lower()
    if t.endswith("ms"):
        return int(float(t[:-2]))
    m = _TIME_RE.match(t)
    if m:
        h = int(m.group(1) or 0)
        mi = int(m.group(2))
        s = int(m.group(3))
        frac = m.group(4) or "0"
        msec = int(frac.ljust(3, "0")[:3])
        return ((h * 60 + mi) * 60 + s) * 1000 + msec
    return int(round(float(t.replace(",", ".")) * 1000))


def srt_to_ms(text: str) -> int:
    return parse_time(text.replace(",", "."))


# ---------------------------------------------------------------- frames

def frame_duration_ms(fps: float) -> float:
    return 1000.0 / fps if fps > 0 else 40.0


def frame_at(ms: float, fps: float) -> int:
    """Index of the frame displayed at time ``ms`` (CFR assumption)."""
    return int(math.floor(ms * fps / 1000.0 + 1e-6))


def frame_start_ms(frame: int, fps: float) -> int:
    return int(round(frame * 1000.0 / fps))


def snap_to_frame(ms: float, fps: float, mode: str = "nearest") -> int:
    """Snap a time to a frame boundary. mode: nearest | floor | ceil"""
    if fps <= 0:
        return int(round(ms))
    f = ms * fps / 1000.0
    if mode == "floor":
        n = math.floor(f + 1e-6)
    elif mode == "ceil":
        n = math.ceil(f - 1e-6)
    else:
        n = round(f)
    return int(round(n * 1000.0 / fps))


def overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))
