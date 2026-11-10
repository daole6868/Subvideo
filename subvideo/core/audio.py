"""Audio IO and DSP helpers (numpy based)."""

from __future__ import annotations

import math
import os
from math import gcd
from pathlib import Path

import numpy as np


def db_to_lin(db: float) -> float:
    if db <= -59.9:
        return 0.0
    return float(10.0 ** (db / 20.0))


def lin_to_db(x: float) -> float:
    return 20.0 * math.log10(max(1e-9, float(x)))


def read_audio(path: str | os.PathLike, mono: bool = False, sr: int | None = None,
               start_ms: int | None = None, end_ms: int | None = None) -> tuple[np.ndarray, int]:
    import soundfile as sf

    with sf.SoundFile(str(path)) as f:
        file_sr = f.samplerate
        start = int((start_ms or 0) * file_sr / 1000)
        stop = int(end_ms * file_sr / 1000) if end_ms is not None else None
        start = max(0, min(start, f.frames))
        f.seek(start)
        frames = (min(stop, f.frames) - start) if stop is not None else -1
        data = f.read(frames=frames, dtype="float32", always_2d=True)
    if mono:
        data = data.mean(axis=1)
    if sr and sr != file_sr:
        data = resample(data, file_sr, sr)
        file_sr = sr
    return data, file_sr


def write_audio(path: str | os.PathLike, data: np.ndarray, sr: int, subtype: str = "FLOAT") -> Path:
    import soundfile as sf

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.stem + ".part" + path.suffix)
    sf.write(str(tmp), np.asarray(data, dtype=np.float32), sr, subtype=subtype)
    os.replace(tmp, path)
    return path


def audio_info(path: str | os.PathLike) -> tuple[int, int, int]:
    """(frames, samplerate, channels)"""
    import soundfile as sf

    i = sf.info(str(path))
    return i.frames, i.samplerate, i.channels


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out:
        return x
    from scipy.signal import resample_poly

    g = gcd(int(sr_in), int(sr_out))
    up, down = sr_out // g, sr_in // g
    return resample_poly(x, up, down, axis=0).astype(np.float32)


def to_mono(x: np.ndarray) -> np.ndarray:
    return x if x.ndim == 1 else x.mean(axis=1)


def to_channels(x: np.ndarray, ch: int) -> np.ndarray:
    if x.ndim == 1:
        x = x[:, None]
    if x.shape[1] == ch:
        return x
    if x.shape[1] == 1:
        return np.repeat(x, ch, axis=1)
    m = x.mean(axis=1, keepdims=True)
    return np.repeat(m, ch, axis=1)


def envelope_db(mono: np.ndarray, sr: int, hop_ms: float = 5.0, win_ms: float = 20.0) -> np.ndarray:
    """RMS envelope in dBFS, one value per hop."""
    hop = max(1, int(sr * hop_ms / 1000))
    win = max(hop, int(sr * win_ms / 1000))
    x = mono.astype(np.float64)
    c = np.concatenate([[0.0], np.cumsum(x * x)])
    n = len(x)
    centers = np.arange(0, n, hop)
    a = np.clip(centers - win // 2, 0, n)
    b = np.clip(centers + win // 2, 0, n)
    energy = (c[b] - c[a]) / np.maximum(1, b - a)
    return (10.0 * np.log10(energy + 1e-12)).astype(np.float32)


def refine_boundaries(env: np.ndarray, hop_ms: float, start_ms: int, end_ms: int,
                      search_ms: int = 150, pad_ms: int = 10, context_ms: int = 1500,
                      start_window: tuple[int, int] | None = None, end_window: tuple[int, int] | None = None,
                      lo_ms: int | None = None, hi_ms: int | None = None) -> tuple[int, int]:
    """Snap a speech segment's start/end to real voice onset/offset.

    ``env`` is the dB envelope of the (separated) vocal track.
    ``start_window`` / ``end_window`` = (ms before, ms after) to search around the
    boundary (default ±search_ms). ``lo_ms``/``hi_ms`` bound the search (neighbour lines).
    Returns refined (start_ms, end_ms); falls back to the input when unsure.
    """
    if len(env) == 0 or end_ms <= start_ms:
        return start_ms, end_ms
    n = len(env)
    sw = start_window or (search_ms, search_ms)
    ew = end_window or (search_ms, search_ms)
    lo = lo_ms if lo_ms is not None else -10**9
    hi = hi_ms if hi_ms is not None else 10**12

    def idx(ms):
        return int(min(n - 1, max(0, round(ms / hop_ms))))

    c0, c1 = idx(start_ms - context_ms), idx(end_ms + context_ms)
    local = env[c0:c1 + 1]
    inner = env[idx(start_ms):idx(end_ms) + 1]
    if len(local) < 5 or len(inner) < 2:
        return start_ms, end_ms
    floor = float(np.percentile(local, 10))
    level = float(np.percentile(inner, 90))
    if level - floor < 6.0:  # not enough contrast to decide
        return start_ms, end_ms
    thr = floor + max(6.0, 0.30 * (level - floor))
    above = env >= thr

    s0, s1 = idx(max(lo, start_ms - sw[0])), idx(min(start_ms + sw[1], end_ms))
    new_start = start_ms
    for i in range(s0, s1 + 1):
        j = min(n, i + 4)
        if above[i] and above[i:j].mean() >= 0.75:
            new_start = int(max(0, i * hop_ms - pad_ms))
            break

    e0, e1 = idx(max(end_ms - ew[0], new_start)), idx(min(hi, end_ms + ew[1]))
    new_end = end_ms
    for i in range(e1, e0 - 1, -1):
        j = max(0, i - 3)
        if above[i] and above[j:i + 1].mean() >= 0.75:
            new_end = int((i + 1) * hop_ms + pad_ms)
            break
    if new_end - new_start < 80:
        return start_ms, end_ms
    return new_start, new_end


def energy_vad(mono: np.ndarray, sr: int, min_speech_ms: int = 200, min_silence_ms: int = 300,
               pad_ms: int = 60, rel_db: float = 18.0) -> list[tuple[int, int]]:
    """Simple energy-based VAD (fallback when no neural VAD is installed)."""
    hop = 10.0
    env = envelope_db(mono, sr, hop_ms=hop, win_ms=30)
    if len(env) == 0:
        return []
    floor = float(np.percentile(env, 15))
    peak = float(np.percentile(env, 97))
    thr = max(floor + 8.0, peak - rel_db)
    act = env >= thr
    segs: list[list[int]] = []
    i, n = 0, len(act)
    while i < n:
        if act[i]:
            j = i
            while j < n and act[j]:
                j += 1
            segs.append([int(i * hop), int(j * hop)])
            i = j
        else:
            i += 1
    merged: list[list[int]] = []
    for s in segs:
        if merged and s[0] - merged[-1][1] < min_silence_ms:
            merged[-1][1] = s[1]
        else:
            merged.append(s)
    total = int(len(mono) * 1000 / sr)
    out = []
    for s, e in merged:
        if e - s >= min_speech_ms:
            out.append((max(0, s - pad_ms), min(total, e + pad_ms)))
    return out


def trim_silence(x: np.ndarray, sr: int, thr_db: float = -45.0, pad_ms: int = 25) -> tuple[int, int]:
    """Return (start_sample, end_sample) of the non-silent part."""
    mono = to_mono(x)
    if len(mono) == 0:
        return 0, 0
    env = envelope_db(mono, sr, hop_ms=5, win_ms=15)
    peak = float(env.max()) if len(env) else -120
    thr = max(thr_db, peak - 45.0)
    idx = np.where(env >= thr)[0]
    if len(idx) == 0:
        return 0, len(mono)
    hop = int(sr * 0.005)
    pad = int(sr * pad_ms / 1000)
    s = max(0, idx[0] * hop - pad)
    e = min(len(mono), (idx[-1] + 1) * hop + pad)
    return int(s), int(e)


def apply_fades(x: np.ndarray, sr: int, fade_in_ms: float = 5, fade_out_ms: float = 8) -> np.ndarray:
    y = np.array(x, dtype=np.float32, copy=True)
    n = len(y)
    fi = min(n, int(sr * fade_in_ms / 1000))
    fo = min(n, int(sr * fade_out_ms / 1000))
    if fi > 1:
        ramp = np.linspace(0, 1, fi, dtype=np.float32)
        y[:fi] *= ramp if y.ndim == 1 else ramp[:, None]
    if fo > 1:
        ramp = np.linspace(1, 0, fo, dtype=np.float32)
        y[n - fo:] *= ramp if y.ndim == 1 else ramp[:, None]
    return y


def compute_peaks(mono: np.ndarray, sr: int, rate: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """Min/max per bucket (rate buckets per second) for waveform drawing."""
    hop = max(1, sr // rate)
    n = len(mono) // hop
    if n == 0:
        return np.zeros(0, np.float32), np.zeros(0, np.float32)
    x = mono[: n * hop].reshape(n, hop)
    return x.min(axis=1).astype(np.float32), x.max(axis=1).astype(np.float32)


def load_or_make_peaks(audio_path: Path, cache_path: Path, rate: int = 200) -> tuple[np.ndarray, np.ndarray]:
    if cache_path.exists() and cache_path.stat().st_mtime >= audio_path.stat().st_mtime:
        d = np.load(cache_path)
        return d["mins"], d["maxs"]
    import soundfile as sf

    mins, maxs = [], []
    with sf.SoundFile(str(audio_path)) as f:
        sr = f.samplerate
        hop = max(1, sr // rate)
        block = hop * rate * 30  # 30 s blocks
        while True:
            data = f.read(frames=block, dtype="float32", always_2d=True)
            if len(data) == 0:
                break
            mono = data.mean(axis=1)
            a, b = compute_peaks(mono, sr, rate)
            mins.append(a)
            maxs.append(b)
    mi = np.concatenate(mins) if mins else np.zeros(0, np.float32)
    ma = np.concatenate(maxs) if maxs else np.zeros(0, np.float32)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache_path, mins=mi, maxs=ma)
    return mi, ma
