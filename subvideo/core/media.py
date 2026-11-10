"""FFmpeg / FFprobe helpers."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Sequence

from .jobs import Cancelled, JobContext
from .project import MediaInfo

_CREATE_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


def _exe(name: str) -> str:
    return name + ".exe" if sys.platform.startswith("win") else name


@lru_cache(maxsize=4)
def find_tool(name: str) -> str:
    """Locate ffmpeg/ffprobe: settings dir, <app>/bin, then PATH."""
    from ..config import APP_DIR, Settings

    candidates = []
    try:
        d = Settings.get()["ffmpeg_dir"]
        if d:
            candidates.append(Path(d) / _exe(name))
    except Exception:
        pass
    candidates.append(APP_DIR / "bin" / _exe(name))
    for c in candidates:
        if c.exists():
            return str(c)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(
        f"Không tìm thấy {name}. Hãy chạy scripts/setup_tools.py hoặc đặt {_exe(name)} vào thư mục bin/ "
        "hoặc chỉnh đường dẫn trong Cài đặt."
    )


def ffmpeg() -> str:
    return find_tool("ffmpeg")


def ffprobe() -> str:
    return find_tool("ffprobe")


def run(cmd: Sequence[str], cwd: str | None = None, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(
        list(cmd),
        cwd=cwd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        creationflags=_CREATE_NO_WINDOW,
    )
    if check and res.returncode != 0:
        err = (res.stderr or b"").decode("utf-8", "replace")[-3000:]
        raise RuntimeError(f"Lệnh thất bại ({res.returncode}): {' '.join(map(str, cmd[:6]))} ...\n{err}")
    return res


def run_ffmpeg(
    args: Sequence[str],
    ctx: JobContext | None = None,
    duration_ms: int | None = None,
    cwd: str | None = None,
    label: str = "ffmpeg",
) -> None:
    """Run ffmpeg with progress parsing and cancellation."""
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *map(str, args)]
    if ctx:
        ctx.log("ffmpeg " + " ".join(_quote(a) for a in cmd[1:]), "debug")
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=_CREATE_NO_WINDOW,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    import threading

    err_lines: list[str] = []

    def _drain():
        assert proc.stderr is not None
        for line in proc.stderr:
            err_lines.append(line)
            if len(err_lines) > 400:
                del err_lines[:200]

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if ctx is not None:
                if ctx.cancelled():
                    proc.kill()
                    raise Cancelled()
                if duration_ms and line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1])
                        ctx.progress(us / 1000.0 / duration_ms, label)
                    except ValueError:
                        pass
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
    t.join(timeout=2)
    if proc.returncode != 0:
        raise RuntimeError("FFmpeg lỗi:\n" + "".join(err_lines[-40:]))


def _quote(a: str) -> str:
    a = str(a)
    return f'"{a}"' if (" " in a or ";" in a) else a


def probe_json(path: str | os.PathLike) -> dict:
    res = run([ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    return json.loads(res.stdout.decode("utf-8", "replace"))


def _ratio(s: str | None) -> float:
    if not s or s in ("0/0", "N/A"):
        return 0.0
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            return float(a) / float(b) if float(b) else 0.0
        except ValueError:
            return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def probe(path: str | os.PathLike) -> MediaInfo:
    info = probe_json(path)
    mi = MediaInfo(path=str(path))
    fmt = info.get("format", {})
    dur = float(fmt.get("duration", 0) or 0)
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    a = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if v:
        mi.width = int(v.get("width", 0))
        mi.height = int(v.get("height", 0))
        mi.video_codec = v.get("codec_name", "")
        mi.pix_fmt = v.get("pix_fmt", "")
        mi.bit_depth = 10 if any(x in mi.pix_fmt for x in ("10", "12")) else 8
        mi.color_space = v.get("color_space", "") or ""
        mi.color_primaries = v.get("color_primaries", "") or ""
        mi.color_transfer = v.get("color_transfer", "") or ""
        avg = _ratio(v.get("avg_frame_rate"))
        rfr = _ratio(v.get("r_frame_rate"))
        fps = avg or rfr or 25.0
        mi.fps = fps
        rate_str = v.get("avg_frame_rate") if avg else v.get("r_frame_rate")
        try:
            num, den = (rate_str or "25/1").split("/")
            mi.fps_num, mi.fps_den = int(num), int(den) or 1
        except ValueError:
            mi.fps_num, mi.fps_den = int(round(fps * 1000)), 1000
        # VFR heuristic: average differs from the container's base rate
        mi.vfr = bool(avg and rfr and abs(avg - rfr) / max(avg, rfr) > 0.01)
        if not dur:
            dur = float(v.get("duration", 0) or 0)
    if a:
        mi.has_audio = True
        mi.audio_codec = a.get("codec_name", "")
        mi.audio_channels = int(a.get("channels", 2) or 2)
        mi.audio_rate = int(a.get("sample_rate", 48000) or 48000)
        if not dur:
            dur = float(a.get("duration", 0) or 0)
    else:
        mi.has_audio = False
    mi.duration_ms = int(round(dur * 1000))
    return mi


def extract_audio(src: str | os.PathLike, dst: str | os.PathLike, sr: int = 48000, channels: int = 2,
                  ctx: JobContext | None = None, duration_ms: int | None = None, codec: str = "pcm_f32le") -> Path:
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-i", str(src), "-vn", "-map", "0:a:0?", "-ac", str(channels), "-ar", str(sr), "-c:a", codec, str(dst)],
               ctx, duration_ms, label="Trích xuất âm thanh")
    return dst


@lru_cache(maxsize=1)
def ffmpeg_filters() -> set[str]:
    try:
        out = run([ffmpeg(), "-hide_banner", "-filters"]).stdout.decode("utf-8", "replace")
    except Exception:
        return set()
    names = set()
    for line in out.splitlines():
        m = re.match(r"\s*[TSC.]{3}\s+(\w+)\s", line)
        if m:
            names.add(m.group(1))
    return names


@lru_cache(maxsize=1)
def ffmpeg_encoders() -> set[str]:
    try:
        out = run([ffmpeg(), "-hide_banner", "-encoders"]).stdout.decode("utf-8", "replace")
    except Exception:
        return set()
    names = set()
    for line in out.splitlines():
        m = re.match(r"\s*[VAS][F.][S.][X.][B.][D.]\s+(\S+)", line)
        if m:
            names.add(m.group(1))
    return names


@lru_cache(maxsize=1)
def ffmpeg_version() -> tuple[int, int]:
    try:
        out = run([ffmpeg(), "-hide_banner", "-version"]).stdout.decode("utf-8", "replace")
        m = re.search(r"ffmpeg version n?(\d+)\.(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
        if re.search(r"ffmpeg version (N-|git-)", out):
            return (99, 0)  # nightly builds
    except Exception:
        pass
    return (0, 0)


def has_filter(name: str) -> bool:
    return name in ffmpeg_filters()


def has_encoder(name: str) -> bool:
    return name in ffmpeg_encoders()


def grab_frame(video: str | os.PathLike, ms: int, out_png: str | os.PathLike, width: int = 0) -> Path:
    vf = ["-vf", f"scale={width}:-2"] if width else []
    run([ffmpeg(), "-hide_banner", "-y", "-ss", f"{ms / 1000:.3f}", "-i", str(video), "-frames:v", "1", *vf, str(out_png)])
    return Path(out_png)
