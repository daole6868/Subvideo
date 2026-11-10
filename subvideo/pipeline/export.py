"""Final render with FFmpeg.

The exact same mix (render/mix.wav) and subtitle file (render/subs_vi.ass) used
by the preview are used here, so the export sounds and looks identical.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..core import media
from ..core.jobs import JobContext
from ..core.project import Project
from ..core.subtitles import build_cues, write_ass, write_srt
from .mixer import mix_is_current, render_mix, time_map_for
from .timemap import TimeMap

VIDEO_PRESETS = {
    "copy": "Giữ nguyên hình gốc (không nén lại, nhanh nhất)",
    "x264": "H.264 chất lượng cao (x264, tương thích mọi nơi)",
    "x265": "H.265/HEVC chất lượng rất cao, file nhỏ (x265)",
    "nvenc_h264": "H.264 bằng GPU NVIDIA (nhanh)",
    "nvenc_hevc": "H.265 bằng GPU NVIDIA (nhanh, file nhỏ)",
    "av1": "AV1 (SVT-AV1, file nhỏ nhất, chậm)",
}


def write_project_subs(project: Project, timeline: str = "output", offset_ms: int = 0,
                       out_ass: Path | None = None, out_srt: Path | None = None) -> tuple[Path, Path | None]:
    tm = time_map_for(project, timeline)
    cues = build_cues(project.segments, "vi_sub", project.display, project.style.bilingual,
                      None if tm.identity else tm.out, offset_ms)
    out_ass = out_ass or (project.subs_ass if timeline == "output" else project.subs_src_ass)
    write_ass(out_ass, cues, project.style, project.media.width, project.media.height)
    srt = None
    if out_srt is not None:
        srt = write_srt(out_srt, cues)
    return out_ass, srt


def cover_intervals(project: Project, tm: TimeMap, offset_ms: int = 0, merge_gap_ms: int = 800) -> list[tuple[float, float]]:
    """Output-timeline intervals (seconds) where the original hard subtitle is visible.

    Uses the exact OCR appearance times (ocr/ocr.json); falls back to the lines' display times."""
    import json

    items = []
    if project.ocr_json.exists():
        try:
            for e in json.loads(project.ocr_json.read_text("utf-8")).get("events", []):
                items.append((tm.out(e["start"]) - offset_ms - 40, tm.out(e["end"]) - offset_ms + 80))
        except Exception:
            items = []
    if not items:
        for s in project.segments:
            if s.zh_ocr or s.zh_source in ("ocr", "fused"):
                items.append((tm.out(s.display_start) - offset_ms - 40, tm.out(s.display_end) - offset_ms + 80))
    items.sort()
    merged: list[list[float]] = []
    for a, b in items:
        if merged and a - merged[-1][1] < merge_gap_ms:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(max(0.0, a) / 1000.0, b / 1000.0) for a, b in merged if b > 0]


def _even(v: float) -> int:
    v = int(round(v))
    return v - (v % 2)


def build_video_filters(project: Project, tm: TimeMap, burn: bool, ass_name: str | None,
                        offset_src_s: float = 0.0, offset_out_s: float = 0.0, scale_h: int = 0) -> list[str]:
    """Returns a list of filter_complex lines producing [vout] from [0:v]."""
    ex = project.export
    mi = project.media
    chain = []
    cur = "0:v"
    idx = 0

    def nxt():
        nonlocal idx
        idx += 1
        return f"v{idx}"

    if not tm.identity or offset_src_s or offset_out_s:
        o = nxt()
        trim = ",trim=start=0" if offset_out_s else ""
        chain.append(f"[{cur}]setpts='{tm.ffmpeg_pts_expr(offset_src_s, offset_out_s)}'{trim},fps={mi.fps_num}/{mi.fps_den}[{o}]")
        cur = o
    if ex.cover != "none" and project.ocr.rois:
        W, H = mi.width or 1920, mi.height or 1080
        intervals = cover_intervals(project, tm, int(offset_out_s * 1000)) if ex.cover_when == "subtitle" else []
        enable = ""
        if ex.cover_when == "subtitle":
            if intervals:
                enable = ":enable='" + "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in intervals) + "'"
            else:
                enable = None  # nothing to cover
        if enable is not None:
            for k, (x, y, w, h) in enumerate(project.ocr.rois):
                px, py = _even(x * W), _even(y * H)
                pw, ph = max(2, _even(w * W)), max(2, _even(h * H))
                if ex.cover == "box":
                    o = nxt()
                    chain.append(f"[{cur}]drawbox=x={px}:y={py}:w={pw}:h={ph}:color=black@0.82:t=fill{enable}[{o}]")
                    cur = o
                else:
                    a, b, c, o = nxt(), nxt(), nxt(), nxt()
                    chain.append(f"[{cur}]split[{a}][{b}]")
                    chain.append(f"[{b}]crop={pw}:{ph}:{px}:{py},boxblur=luma_radius=min(h\\,w)/6:luma_power=3[{c}]")
                    chain.append(f"[{a}][{c}]overlay={px}:{py}{enable}[{o}]")
                    cur = o
    if burn and ass_name:
        o = nxt()
        chain.append(f"[{cur}]subtitles={ass_name}:fontsdir=fonts[{o}]")
        cur = o
    if scale_h:
        o = nxt()
        chain.append(f"[{cur}]scale=-2:{scale_h}[{o}]")
        cur = o
    if not chain:
        return []
    chain[-1] = chain[-1].rsplit("[", 1)[0] + "[vout]"
    return chain


def video_codec_args(project: Project, mode: str, preview: bool = False) -> list[str]:
    ex = project.export
    mi = project.media
    if preview:
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
    ten_bit = mi.bit_depth >= 10
    if mode == "x264":
        args = ["-c:v", "libx264", "-preset", ex.preset or "slow", "-crf", str(ex.crf), "-pix_fmt", "yuv420p"]
    elif mode == "x265":
        args = ["-c:v", "libx265", "-preset", ex.preset or "slow", "-crf", str(ex.crf + 1),
                "-pix_fmt", "yuv420p10le" if ten_bit else "yuv420p"]
        if ex.container == "mp4":
            args += ["-tag:v", "hvc1"]
    elif mode == "nvenc_h264":
        args = ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", str(ex.crf + 2), "-b:v", "0",
                "-pix_fmt", "yuv420p"]
    elif mode == "nvenc_hevc":
        args = ["-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", str(ex.crf + 2), "-b:v", "0",
                "-pix_fmt", "p010le" if ten_bit else "yuv420p"]
        if ex.container == "mp4":
            args += ["-tag:v", "hvc1"]
    elif mode == "av1":
        args = ["-c:v", "libsvtav1", "-preset", "5", "-crf", str(ex.crf + 10),
                "-pix_fmt", "yuv420p10le" if ten_bit else "yuv420p"]
    else:
        args = ["-c:v", "libx264", "-preset", "slow", "-crf", str(ex.crf), "-pix_fmt", "yuv420p"]
    for k, v in (("-colorspace", mi.color_space), ("-color_primaries", mi.color_primaries), ("-color_trc", mi.color_transfer)):
        if v and v not in ("unknown", "reserved"):
            args += [k, v]
    return args


def audio_codec_args(project: Project, preview: bool = False) -> list[str]:
    ex = project.export
    if preview:
        return ["-c:a", "aac", "-b:a", "192k"]
    if ex.audio_codec == "flac" and ex.container == "mkv":
        return ["-c:a", "flac"]
    if ex.audio_codec == "opus" and ex.container == "mkv":
        return ["-c:a", "libopus", "-b:a", "256k"]
    return ["-c:a", "aac", "-b:a", ex.audio_bitrate or "320k"]


def _filter_script_args(script_path: Path) -> list[str]:
    major, _ = media.ffmpeg_version()
    if major >= 7:
        return ["-/filter_complex", str(script_path)]
    return ["-filter_complex_script", str(script_path)]


def default_output(project: Project) -> Path:
    base = Path(project.export.output) if project.export.output else project.path("export", f"{project.name}_vi.{project.export.container}")
    return base


def export_video(project: Project, ctx: JobContext, range_ms: tuple[int, int] | None = None,
                 output: Path | None = None, preview: bool = False, proxy_height: int = 0) -> Path:
    """Render the final video (or an exact preview of ``range_ms`` on the output timeline)."""
    ex = project.export
    mi = project.media
    if not project.video_path.exists():
        raise FileNotFoundError(f"Không thấy video gốc: {project.video_path}")
    tm = time_map_for(project, "output")

    # 1. audio mix (identical to preview)
    if not mix_is_current(project, "output"):
        with ctx.sub(0.0, 0.35):
            render_mix(project, ctx, "output")
    out_dur = int(tm.out(mi.duration_ms))

    # 2. subtitles
    render_dir = project.path("render")
    fonts = render_dir / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    _copy_fonts(fonts)
    t0, t1 = (range_ms if range_ms else (0, out_dur))
    t0, t1 = max(0, int(t0)), min(out_dur, int(t1))
    is_range = range_ms is not None
    ass_name = "subs_vi.ass" if not is_range else "subs_vi_preview.ass"
    write_project_subs(project, "output", offset_ms=t0 if is_range else 0, out_ass=render_dir / ass_name)

    if output is None:
        output = render_dir / "preview.mp4" if is_range else default_output(project)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    burn = ex.subs in ("burn", "both")
    soft = ex.subs in ("soft", "both") and not preview
    mode = "x264" if preview else ex.video_mode
    need_filters = (not tm.identity) or burn or (ex.cover != "none" and project.ocr.rois) or is_range or proxy_height
    if mode == "copy" and need_filters:
        ctx.warn("Chế độ 'Giữ nguyên hình' không dùng được khi in phụ đề/che phụ đề/giãn video — chuyển sang x264.")
        mode = "x264"

    args: list[str] = []
    offset_src_s = 0.0
    if is_range:
        s0 = max(0.0, tm.inv(t0) - 2000)  # seek a bit earlier, frames are mapped by setpts
        offset_src_s = s0 / 1000.0
        args += ["-ss", f"{offset_src_s:.3f}"]
    args += ["-i", str(project.video_path)]
    if is_range:
        args += ["-ss", f"{t0 / 1000:.3f}"]
    args += ["-i", str(project.mix_wav)]
    n_inputs = 2
    extra_maps = []
    if not preview and "original" in ex.extra_tracks and project.original_wav.exists():
        from .mixer import retime_audio
        from ..core.textutil import short_hash

        orig = project.original_wav
        if not tm.identity:
            orig = project.path("render", f"original_rt_{short_hash(tm.regions, orig.stat().st_mtime)}.wav")
            if not orig.exists():
                retime_audio(project.original_wav, orig, tm, ctx)
        args += ["-i", str(orig)]
        extra_maps.append((n_inputs, "Âm thanh gốc", "chi"))
        n_inputs += 1
    srt_path = None
    if soft:
        srt_path = render_dir / "subs_vi.srt"
        write_project_subs(project, "output", out_ass=render_dir / "subs_vi.ass", out_srt=srt_path)
        sub_in = render_dir / ("subs_vi.ass" if ex.container == "mkv" else "subs_vi.srt")
        args += ["-i", str(sub_in)]
        sub_idx = n_inputs
        n_inputs += 1

    filters = build_video_filters(project, tm, burn, ass_name, offset_src_s if is_range else 0.0,
                                  (t0 / 1000.0) if is_range else 0.0, proxy_height)
    script = None
    if filters:
        script = render_dir / "filters.txt"
        script.write_text(";\n".join(filters), encoding="utf-8")
        args += _filter_script_args(script)
        args += ["-map", "[vout]"]
    else:
        args += ["-map", "0:v:0"]
    args += ["-map", "1:a:0"]
    for i, _, _ in extra_maps:
        args += ["-map", f"{i}:a:0"]
    if soft:
        args += ["-map", f"{sub_idx}:s:0"]

    if mode == "copy":
        args += ["-c:v", "copy"]
    else:
        args += video_codec_args(project, mode, preview)
    args += audio_codec_args(project, preview)
    args += ["-ar", "48000"]
    args += ["-metadata:s:a:0", "language=vie", "-metadata:s:a:0", f"title={_track_title(project)}"]
    for k, (_, title, lang) in enumerate(extra_maps, start=1):
        args += [f"-metadata:s:a:{k}", f"language={lang}", f"-metadata:s:a:{k}", f"title={title}"]
    if soft:
        args += ["-c:s", "ass" if ex.container == "mkv" else "mov_text", "-metadata:s:s:0", "language=vie"]
    if is_range:
        args += ["-t", f"{(t1 - t0) / 1000:.3f}"]
    else:
        args += ["-t", f"{out_dur / 1000:.3f}"]
    if output.suffix.lower() in (".mp4", ".mov", ".m4v"):
        args += ["-movflags", "+faststart"]
    args += ["-max_muxing_queue_size", "4096", str(output)]

    ctx.log(("Xem trước chính xác" if is_range else "Xuất video") + f" → {output}")
    with ctx.sub(0.35, 1.0):
        media.run_ffmpeg(args, ctx, duration_ms=(t1 - t0), cwd=str(render_dir), label="Đang xuất video")
    if not is_range and ex.export_srt:
        write_project_subs(project, "output", out_ass=output.with_suffix(".ass"), out_srt=output.with_suffix(".srt"))
    return output


def _track_title(project: Project) -> str:
    return {"sub": "Âm thanh gốc", "narration": "Thuyết minh", "dub": "Lồng tiếng Việt"}.get(project.mix.mode, "Tiếng Việt")


def _copy_fonts(dst: Path) -> None:
    """Copy bundled fonts (assets/fonts) so libass finds the same font everywhere."""
    from ..config import APP_DIR

    src = APP_DIR / "assets" / "fonts"
    if src.is_dir():
        for f in src.iterdir():
            if f.suffix.lower() in (".ttf", ".otf", ".ttc") and not (dst / f.name).exists():
                shutil.copy2(f, dst / f.name)
