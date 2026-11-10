"""Pitch-preserving time stretch.

Preference: rubberband CLI with the R3 ("finer") engine > ffmpeg rubberband
filter > ffmpeg atempo.  All methods take/return float32 numpy audio.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..core import media
from ..core.audio import read_audio, write_audio

_CREATE_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


@lru_cache(maxsize=1)
def rubberband_cli() -> str | None:
    try:
        from ..config import APP_DIR, Settings

        p = Settings.get()["rubberband_path"]
        if p and Path(p).exists():
            return p
        exe = "rubberband.exe" if sys.platform.startswith("win") else "rubberband"
        cand = APP_DIR / "bin" / exe
        if cand.exists():
            return str(cand)
    except Exception:
        pass
    return shutil.which("rubberband") or shutil.which("rubberband-r3")


def method() -> str:
    if rubberband_cli():
        return "rubberband-r3"
    if media.has_filter("rubberband"):
        return "ffmpeg-rubberband"
    return "atempo"


def _atempo_chain(speed: float) -> str:
    parts = []
    s = speed
    while s > 2.0:
        parts.append("atempo=2.0")
        s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def stretch(x: np.ndarray, sr: int, speed: float, target_samples: int | None = None) -> np.ndarray:
    """Change tempo by ``speed`` (>1 faster) keeping pitch."""
    if abs(speed - 1.0) < 1e-3 or len(x) == 0:
        y = x
    else:
        with tempfile.TemporaryDirectory(prefix="sv_stretch_") as td:
            src = Path(td) / "in.wav"
            dst = Path(td) / "out.wav"
            write_audio(src, x, sr)
            m = method()
            if m == "rubberband-r3":
                cmd = [rubberband_cli(), "-3", "-q", "-T", f"{speed:.6f}", str(src), str(dst)]
                r = subprocess.run(cmd, capture_output=True, creationflags=_CREATE_NO_WINDOW)
                if r.returncode != 0:  # older rubberband without -3
                    cmd = [rubberband_cli(), "-q", "-c", "6", "-T", f"{speed:.6f}", str(src), str(dst)]
                    subprocess.run(cmd, capture_output=True, check=True, creationflags=_CREATE_NO_WINDOW)
            elif m == "ffmpeg-rubberband":
                media.run([media.ffmpeg(), "-hide_banner", "-y", "-i", str(src), "-af",
                           f"rubberband=tempo={speed:.6f}:pitchq=quality:transients=smooth:window=standard",
                           "-c:a", "pcm_f32le", str(dst)])
            else:
                media.run([media.ffmpeg(), "-hide_banner", "-y", "-i", str(src), "-af", _atempo_chain(speed),
                           "-c:a", "pcm_f32le", str(dst)])
            y, _ = read_audio(dst)
            if x.ndim == 1:
                y = y.mean(axis=1)
    if target_samples is not None:
        y = fit_length(y, target_samples)
    return y.astype(np.float32)


def fit_length(y: np.ndarray, n: int, fade_ms: float = 8.0, sr: int = 48000) -> np.ndarray:
    """Pad with silence or trim (with a short fade) to exactly n samples."""
    if len(y) == n:
        return y
    if len(y) > n:
        y = np.array(y[:n], copy=True)
        f = min(n, int(sr * fade_ms / 1000))
        if f > 1:
            ramp = np.linspace(1, 0, f, dtype=np.float32)
            y[-f:] *= ramp if y.ndim == 1 else ramp[:, None]
        return y
    pad_shape = (n - len(y),) + y.shape[1:]
    return np.concatenate([y, np.zeros(pad_shape, dtype=y.dtype)])
