"""Audio mixer shared by preview and export (so what you hear is what you get).

Tracks:
  * dub      - dubbed Vietnamese clips placed by the timing solver
  * orig     - original voice stem (vocals) — kept for non-verbal/song lines
  * bgm      - background music + sound effects stem (instrumental)
Gains are in dB (<= -60 dB means muted).  Ducking lowers bgm (and optionally the
original voice) while a dubbed line is playing.  Loudness is normalised with a
streaming ITU-R BS.1770 meter, then a soft limiter keeps peaks below -1 dBFS.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from ..core.audio import audio_info, db_to_lin, read_audio, resample, to_channels, write_audio
from ..core.jobs import JobContext
from ..core.project import Project
from ..core.textutil import short_hash
from .stretch import fit_length, stretch
from .timemap import TimeMap

SR = 48000
CTRL_HZ = 100  # control envelope rate


# ------------------------------------------------------------------ helpers

def time_map_for(project: Project, timeline: str = "output") -> TimeMap:
    if timeline == "source":
        return TimeMap([])
    return TimeMap(project.slow_regions())


def retime_audio(src: Path, dst: Path, tm: TimeMap, ctx: JobContext | None = None) -> Path:
    """Stretch the parts of a stem that fall in slowed-down video regions."""
    import soundfile as sf

    frames, sr, ch = audio_info(src)
    dur_ms = frames * 1000.0 / sr
    pieces = tm.segments(dur_ms)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part.wav")
    with sf.SoundFile(str(src)) as fin, sf.SoundFile(str(tmp), "w", samplerate=sr, channels=ch, subtype="FLOAT") as fout:
        for k, (a, b, v) in enumerate(pieces):
            i0, i1 = int(round(a * sr / 1000)), int(round(b * sr / 1000))
            i1 = min(i1, frames)
            if i1 <= i0:
                continue
            fin.seek(i0)
            if v >= 0.9999:
                remaining = i1 - i0
                while remaining > 0:
                    n = min(remaining, sr * 20)
                    fout.write(fin.read(n, dtype="float32", always_2d=True))
                    remaining -= n
            else:
                x = fin.read(i1 - i0, dtype="float32", always_2d=True)
                target = int(round((i1 - i0) / v))
                y = stretch(x, sr, v, target_samples=target)
                fout.write(to_channels(y, ch))
            if ctx:
                ctx.progress((k + 1) / len(pieces), f"Giãn âm thanh theo video: {src.name}")
    tmp.replace(dst)
    return dst


def _intervals_to_env(intervals: list[tuple[float, float]], n: int, ramp: int = 2) -> np.ndarray:
    env = np.zeros(n, np.float32)
    for a, b in intervals:
        i0, i1 = max(0, int(a * CTRL_HZ / 1000)), min(n, int(math.ceil(b * CTRL_HZ / 1000)))
        if i1 > i0:
            env[i0:i1] = 1.0
    if ramp > 0:
        k = np.ones(2 * ramp + 1, np.float32) / (2 * ramp + 1)
        env = np.convolve(env, k, mode="same").astype(np.float32)
    return env


def _attack_release(x: np.ndarray, attack_ms: float = 40, release_ms: float = 350) -> np.ndarray:
    a_up = 1 - math.exp(-1000.0 / (attack_ms * CTRL_HZ))
    a_dn = 1 - math.exp(-1000.0 / (release_ms * CTRL_HZ))
    y = np.zeros_like(x)
    v = 0.0
    # look-ahead: start ducking slightly before the line
    look = int(attack_ms * CTRL_HZ / 1000)
    xa = np.concatenate([x[look:], np.zeros(look, x.dtype)]) if look else x
    xm = np.maximum(x, xa)
    for i in range(len(xm)):
        t = xm[i]
        v += (t - v) * (a_up if t > v else a_dn)
        y[i] = v
    return y


class _Track:
    def __init__(self, path: Path):
        import soundfile as sf

        self.f = sf.SoundFile(str(path))
        self.sr = self.f.samplerate
        self.ch = self.f.channels
        self.frames = self.f.frames

    def read(self, start: int, n: int) -> np.ndarray:
        """Read n samples at SR starting at output sample ``start``."""
        if self.sr == SR:
            if start >= self.frames:
                return np.zeros((n, 2), np.float32)
            self.f.seek(start)
            x = self.f.read(min(n, self.frames - start), dtype="float32", always_2d=True)
        else:
            s0 = int(start * self.sr / SR)
            if s0 >= self.frames:
                return np.zeros((n, 2), np.float32)
            self.f.seek(s0)
            x = self.f.read(int(n * self.sr / SR) + 2, dtype="float32", always_2d=True)
            x = resample(x, self.sr, SR)
        x = to_channels(x, 2)
        if len(x) < n:
            x = np.concatenate([x, np.zeros((n - len(x), 2), np.float32)])
        return x[:n]

    def close(self):
        self.f.close()


# --------------------------------------------------------------- loudness

_K1 = ([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585])
_K2 = ([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621])


def measure_lufs(path: Path) -> float:
    """Integrated loudness (ITU-R BS.1770-4) of a 48 kHz file, streamed."""
    import soundfile as sf
    from scipy.signal import lfilter, lfilter_zi

    with sf.SoundFile(str(path)) as f:
        sr, ch = f.samplerate, f.channels
        if sr != 48000:
            data, _ = read_audio(path, sr=48000)
            return _lufs_array(data)
        z1 = [lfilter_zi(*_K1) * 0.0 for _ in range(ch)]
        z2 = [lfilter_zi(*_K2) * 0.0 for _ in range(ch)]
        sub = 4800  # 100 ms
        ms_sub: list[float] = []
        carry = np.zeros((0, ch), np.float32)
        while True:
            x = f.read(sr * 10, dtype="float64", always_2d=True)
            if len(x) == 0:
                break
            y = np.empty_like(x)
            for c in range(ch):
                t, z1[c] = lfilter(_K1[0], _K1[1], x[:, c], zi=z1[c])
                y[:, c], z2[c] = lfilter(_K2[0], _K2[1], t, zi=z2[c])
            y = np.concatenate([carry, y])
            nsub = len(y) // sub
            if nsub:
                blk = y[: nsub * sub].reshape(nsub, sub, ch)
                ms_sub.extend((blk ** 2).mean(axis=1).sum(axis=1).tolist())
            carry = y[nsub * sub:]
    return _gate(np.array(ms_sub))


def _lufs_array(x: np.ndarray) -> float:
    from scipy.signal import lfilter

    x = to_channels(x, x.shape[1] if x.ndim == 2 else 1).astype(np.float64)
    y = np.stack([lfilter(_K2[0], _K2[1], lfilter(_K1[0], _K1[1], x[:, c])) for c in range(x.shape[1])], axis=1)
    sub = 4800
    nsub = len(y) // sub
    blk = y[: nsub * sub].reshape(nsub, sub, y.shape[1])
    return _gate((blk ** 2).mean(axis=1).sum(axis=1))


def _gate(ms_sub: np.ndarray) -> float:
    if len(ms_sub) < 4:
        return -70.0
    # 400 ms blocks with 75 % overlap = mean of 4 consecutive 100 ms sub-blocks
    blocks = np.convolve(ms_sub, np.ones(4) / 4, mode="valid")
    lk = -0.691 + 10 * np.log10(blocks + 1e-12)
    g1 = blocks[lk > -70]
    if len(g1) == 0:
        return -70.0
    rel = -0.691 + 10 * np.log10(g1.mean()) - 10
    g2 = blocks[(lk > -70) & (lk > rel)]
    if len(g2) == 0:
        return -70.0
    return float(-0.691 + 10 * np.log10(g2.mean()))


def soft_limit(x: np.ndarray, ceiling_db: float = -1.0) -> np.ndarray:
    thr = 10 ** (ceiling_db / 20)
    knee = thr * 0.9
    a = np.abs(x)
    over = a > knee
    if not over.any():
        return x
    y = x.copy()
    span = thr - knee
    y[over] = np.sign(x[over]) * (knee + span * np.tanh((a[over] - knee) / span))
    return y


# ---------------------------------------------------------------- the mix

def mix_key(project: Project, timeline: str) -> str:
    m = project.mix
    clips = [(s.id, s.final_file or s.tts_file, s.final_key or s.tts_key, s.place_start, s.place_dur, s.gain_db,
              s.keep_original, s.kind, s.slow_region) for s in project.segments]
    chars = [(c.id, c.gain_db, tuple(c.speaker_labels)) for c in project.characters]
    return short_hash(m.__dict__, clips, chars, timeline, project.slow_regions() if timeline == "output" else None)


def mix_is_current(project: Project, timeline: str = "output") -> bool:
    out = project.mix_wav if timeline == "output" else project.mix_src_wav
    man = out.with_suffix(".json")
    if not out.exists() or not man.exists():
        return False
    try:
        return json.loads(man.read_text("utf-8")).get("key") == mix_key(project, timeline)
    except Exception:
        return False


def render_mix(project: Project, ctx: JobContext, timeline: str = "output", out_path: Path | None = None) -> Path:
    m = project.mix
    tm = time_map_for(project, timeline)
    out_path = Path(out_path or (project.mix_wav if timeline == "output" else project.mix_src_wav))
    src_dur = project.media.duration_ms
    if not src_dur and project.original_wav.exists():
        fr, sr0, _ = audio_info(project.original_wav)
        src_dur = int(fr * 1000 / sr0)
    out_dur = int(math.ceil(tm.out(src_dur)))
    n_total = int(out_dur * SR / 1000)

    dub_lin = db_to_lin(m.dub_db)
    orig_lin = db_to_lin(m.orig_db)
    bgm_lin = db_to_lin(m.bgm_db)
    keep_lin = db_to_lin(m.keep_db)

    have_stems = project.has_stems()
    use_original_direct = (not have_stems) or (dub_lin == 0 and abs(m.orig_db) < 0.01 and abs(m.bgm_db) < 0.01)
    if not have_stems and m.orig_db <= -59 and m.mode != "sub":
        ctx.warn("Chưa tách giọng gốc: giọng Trung vẫn còn trong âm thanh nền. Hãy chạy bước 'Tách giọng'.")

    # --- source tracks (retimed if needed) ---
    def prepared(path: Path, name: str) -> Path:
        if tm.identity:
            return path
        key = short_hash(tm.regions, path.stat().st_mtime)
        dst = project.path("render", f"{name}_rt_{key}.wav")
        if not dst.exists():
            with ctx.sub(0.0, 0.3):
                retime_audio(path, dst, tm, ctx)
        return dst

    tracks: dict[str, _Track] = {}
    if use_original_direct:
        if not project.original_wav.exists():
            raise FileNotFoundError("Chưa có audio gốc (audio/original.wav). Hãy chạy bước tách âm thanh.")
        tracks["bgm"] = _Track(prepared(project.original_wav, "original"))
        bgm_base = bgm_lin if not have_stems else 1.0
        orig_base = 0.0
    else:
        tracks["bgm"] = _Track(prepared(project.instrumental_wav, "instrumental"))
        tracks["orig"] = _Track(prepared(project.vocals_wav, "vocals"))
        bgm_base, orig_base = bgm_lin, orig_lin

    # --- dub clips ---
    clips: list[tuple[int, np.ndarray]] = []
    dub_intervals: list[tuple[float, float]] = []
    keep_intervals: list[tuple[float, float]] = []
    for s in project.segments:
        if s.keep_original or (m.keep_nonverbal and s.kind in ("nonverbal", "song")):
            keep_intervals.append((tm.out(s.start), tm.out(s.end)))
        if dub_lin == 0 or not s.has_dub() or s.place_start is None:
            continue
        rel = s.final_file or (s.tts_file if abs(s.stretch - 1.0) <= 0.01 else "")  # natural clip only if unstretched
        f = project.abs(rel) if rel else None
        if f is None or not f.is_file():
            continue
        x, sr = read_audio(f, mono=True, sr=SR)
        ch = project.character_for_segment(s)
        g = dub_lin * db_to_lin(s.gain_db + (ch.gain_db if ch else 0.0))
        o0 = tm.out(s.place_start)
        st = int(round(o0 * SR / 1000))
        clips.append((st, x * g))
        dub_intervals.append((o0, o0 + len(x) * 1000.0 / SR))
    clips.sort(key=lambda c: c[0])

    n_ctrl = int(math.ceil(out_dur * CTRL_HZ / 1000)) + 2
    dub_env = _attack_release(_intervals_to_env(dub_intervals, n_ctrl, ramp=0)) if (m.ducking and dub_intervals) else np.zeros(n_ctrl, np.float32)
    duck_gain = db_to_lin(-abs(m.duck_db)) if m.ducking else 1.0
    duck_curve = 1.0 - (1.0 - duck_gain) * dub_env  # 1 -> duck_gain
    keep_env = _intervals_to_env(keep_intervals, n_ctrl, ramp=3) if keep_intervals else np.zeros(n_ctrl, np.float32)
    bgm_ctrl = bgm_base * duck_curve
    orig_ctrl = orig_base * (duck_curve if m.duck_orig else 1.0)
    if "orig" in tracks:
        orig_ctrl = np.maximum(orig_ctrl, keep_lin * keep_env)
    ctrl_t = np.arange(n_ctrl, dtype=np.float64) * (SR / CTRL_HZ)

    # --- block render ---
    import soundfile as sf

    tmp = out_path.with_name(out_path.stem + ".pre.wav")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    block = SR * 10
    ci = 0
    active: list[tuple[int, np.ndarray]] = []
    with sf.SoundFile(str(tmp), "w", samplerate=SR, channels=2, subtype="FLOAT") as fout:
        pos = 0
        while pos < n_total:
            n = min(block, n_total - pos)
            t = np.arange(pos, pos + n, dtype=np.float64)
            acc = tracks["bgm"].read(pos, n) * np.interp(t, ctrl_t, bgm_ctrl).astype(np.float32)[:, None]
            if "orig" in tracks:
                g = np.interp(t, ctrl_t, orig_ctrl).astype(np.float32)
                if g.any():
                    acc += tracks["orig"].read(pos, n) * g[:, None]
            # dub clips overlapping this block
            while ci < len(clips) and clips[ci][0] < pos + n:
                active.append(clips[ci])
                ci += 1
            still = []
            for st, x in active:
                a = max(pos, st)
                b = min(pos + n, st + len(x))
                if b > a:
                    acc[a - pos:b - pos] += x[a - st:b - st, None]
                if st + len(x) > pos + n:
                    still.append((st, x))
            active = still
            fout.write(acc)
            pos += n
            ctx.progress(0.3 + 0.4 * pos / max(1, n_total), "Đang trộn âm")
    for tr in tracks.values():
        tr.close()

    # --- loudness normalisation + limiter ---
    gain = 1.0
    measured = None
    if m.loudness and m.loudness < 0:
        ctx.progress(0.72, "Đo độ to (LUFS)")
        measured = measure_lufs(tmp)
        if measured > -69:
            gain = db_to_lin(max(-20.0, min(20.0, m.loudness - measured)))
    with sf.SoundFile(str(tmp)) as fin, sf.SoundFile(str(out_path.with_name(out_path.stem + ".part.wav")), "w",
                                                    samplerate=SR, channels=2, subtype="FLOAT") as fout:
        done = 0
        while True:
            x = fin.read(block, dtype="float32", always_2d=True)
            if len(x) == 0:
                break
            fout.write(soft_limit(x * gain))
            done += len(x)
            ctx.progress(0.75 + 0.25 * done / max(1, n_total), "Chuẩn hoá độ to")
    out_path.with_name(out_path.stem + ".part.wav").replace(out_path)
    tmp.unlink(missing_ok=True)
    man = {"key": mix_key(project, timeline), "timeline": timeline, "duration_ms": out_dur,
           "measured_lufs": measured, "gain_db": 20 * math.log10(gain) if gain > 0 else None,
           "clips": len(clips)}
    out_path.with_suffix(".json").write_text(json.dumps(man, ensure_ascii=False, indent=1), "utf-8")
    ctx.log(f"Đã trộn {len(clips)} câu lồng tiếng → {out_path.name}"
            + (f" (độ to gốc {measured:.1f} LUFS → {m.loudness:.0f} LUFS)" if measured is not None else ""))
    return out_path


def render_final_clips(project: Project, ctx: JobContext, synth=None) -> int:
    """Create the placed clip for every dubbed line: regenerate with an exact
    duration when the engine supports it, otherwise time-stretch."""
    from ..core.audio import trim_silence

    segs = [s for s in project.segments if s.has_dub() and s.tts_file and s.place_start is not None]
    done = 0
    for k, s in enumerate(segs):
        key = short_hash(s.tts_key, round(s.stretch, 4), s.place_dur)
        out = project.path("tts", "final", f"{s.id:05d}_{key}.wav")
        if s.final_key == key and out.exists():
            continue
        src = project.abs(s.tts_file)
        if not src.exists():
            continue
        x, sr = read_audio(src, mono=True, sr=SR)
        i0, i1 = trim_silence(x, SR)
        x = x[i0:i1]
        target = int(round(s.place_dur * SR / 1000)) if s.place_dur else None
        y = None
        if synth is not None and abs(s.stretch - 1.0) > 0.03 and target:
            try:
                y = synth(s, s.place_dur / 1000.0)  # regenerate at exact duration
                if y is not None:
                    y = fit_length(y, target, sr=SR)
            except Exception as e:  # fall back to stretching
                ctx.warn(f"Câu {s.id}: tạo lại theo thời lượng thất bại ({e}); dùng co giãn.")
                y = None
        if y is None:
            y = stretch(x, SR, s.stretch, target_samples=target) if abs(s.stretch - 1.0) > 0.003 else (
                fit_length(x, target, sr=SR) if target else x)
        from ..core.audio import apply_fades

        for old in out.parent.glob(f"{s.id:05d}_*.wav"):
            if old != out:
                old.unlink(missing_ok=True)
        write_audio(out, apply_fades(y, SR, 3, 10), SR)
        s.final_file = project.rel(out)
        s.final_key = key
        done += 1
        ctx.progress((k + 1) / max(1, len(segs)), f"Dựng câu lồng tiếng {k + 1}/{len(segs)}")
    return done
