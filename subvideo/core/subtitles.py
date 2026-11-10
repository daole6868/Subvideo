"""Subtitle timing rules and SRT / ASS reading & writing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .project import DisplayRules, Segment, SubtitleStyle
from .textutil import wrap_lines
from .timeutil import frame_duration_ms, ms_to_ass, ms_to_srt, snap_to_frame, srt_to_ms


@dataclass
class Cue:
    start: int
    end: int
    text: str
    secondary: str = ""  # e.g. Chinese line for bilingual output
    speaker: str = ""


# ----------------------------------------------------------- display timing

def compute_display_times(segments: list[Segment], fps: float, rules: DisplayRules,
                          shots: list[int] | None = None, only_missing: bool = False) -> None:
    """Derive frame-accurate subtitle display times from speech times.

    Rules (similar to professional timed-text guidelines):
    * start at speech onset minus lead-in, snapped to a frame
    * linger after speech ends, but keep ``min_gap_frames`` before the next cue
    * honour minimum / maximum duration
    * snap to a nearby shot change so subtitles do not flash across cuts
    """
    segs = sorted([s for s in segments if s.kind != "nonverbal" or s.zh], key=lambda s: s.start)
    fd = frame_duration_ms(fps)
    gap = rules.min_gap_frames * fd
    shots = sorted(shots or [])

    def near_shot(t: float) -> int | None:
        if not shots:
            return None
        import bisect

        i = bisect.bisect_left(shots, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(shots):
                d = abs(shots[j] - t)
                if d <= rules.snap_shot_ms and (best is None or d < abs(best - t)):
                    best = shots[j]
        return best

    for i, s in enumerate(segs):
        if only_missing and s.disp_start is not None and s.disp_end is not None:
            continue
        ds = s.start - rules.lead_in_ms
        de = s.end + rules.linger_ms
        sh = near_shot(ds)
        if sh is not None and sh <= s.start + 120:
            ds = sh
        de = max(de, ds + rules.min_duration_ms)
        de = min(de, ds + max(rules.max_duration_ms, s.end - ds))
        limit = None
        if i + 1 < len(segs):
            nxt = segs[i + 1]
            nxt_start = nxt.start - rules.lead_in_ms
            if nxt.disp_start is not None and only_missing:
                nxt_start = nxt.disp_start
            nsh = near_shot(nxt_start)
            if nsh is not None and nsh <= nxt.start + 120:
                nxt_start = nsh
            nxt_start = snap_to_frame(max(0, nxt_start), fps, "floor")
            limit = nxt_start - gap
            if de > limit:
                de = max(limit, min(s.end, nxt_start - fd), ds + fd)
        sh = near_shot(de)
        if sh is not None and sh > ds + rules.min_duration_ms * 0.5 and sh >= s.end - 80:
            if limit is None or sh <= limit:
                de = sh
        # start on the frame at/before the voice, end on the frame at/after the linger
        ds_f = snap_to_frame(max(0, ds), fps, "floor")
        de_f = snap_to_frame(de, fps, "ceil")
        if limit is not None and de_f > limit and limit > ds_f:
            de_f = snap_to_frame(limit, fps, "floor")
        if de_f <= ds_f:
            de_f = snap_to_frame(ds_f + fd, fps, "nearest")
        s.disp_start, s.disp_end = int(ds_f), int(de_f)


# ------------------------------------------------------------- cue building

def build_cues(segments: Iterable[Segment], field: str = "vi_sub", rules: DisplayRules | None = None,
               bilingual: bool = False, time_map: Callable[[float], float] | None = None,
               offset_ms: int = 0) -> list[Cue]:
    rules = rules or DisplayRules()
    cues: list[Cue] = []
    for s in sorted(segments, key=lambda x: x.display_start):
        text = getattr(s, field, "") or ""
        if field == "vi_sub" and not text.strip() and s.kind == "onscreen":
            text = s.vi_dub
        if not text.strip():
            continue
        st, en = s.display_start, s.display_end
        if time_map is not None:
            st, en = int(round(time_map(st))), int(round(time_map(en)))
        st -= offset_ms
        en -= offset_ms
        if en <= 0:
            continue
        cues.append(Cue(max(0, st), en, wrap_lines(text, rules.max_chars_line, rules.max_lines),
                        s.zh if bilingual else "", s.character or s.speaker))
    return cues


# --------------------------------------------------------------------- SRT

def write_srt(path: str | Path, cues: list[Cue]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, c in enumerate(cues, 1):
        text = c.text if not c.secondary else c.secondary + "\n" + c.text
        lines += [str(i), f"{ms_to_srt(c.start)} --> {ms_to_srt(c.end)}", text, ""]
    path.write_text("\n".join(lines), encoding="utf-8-sig")
    return path


_SRT_TIME = re.compile(r"((?:\d+:)?\d+:\d+[,.]\d+)\s*-->\s*((?:\d+:)?\d+:\d+[,.]\d+)")


def read_srt(path: str | Path) -> list[Cue]:
    return parse_srt_text(Path(path).read_text(encoding="utf-8-sig", errors="replace"))


def parse_srt_text(text: str) -> list[Cue]:
    """Parse SRT or WebVTT text."""
    cues = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        lines = [l for l in block.split("\n") if l.strip() != ""]
        for k, l in enumerate(lines):
            m = _SRT_TIME.search(l)
            if m:
                body = "\n".join(lines[k + 1:])
                body = re.sub(r"<[^>]+>", "", body)
                cues.append(Cue(srt_to_ms(m.group(1)), srt_to_ms(m.group(2)), body.strip()))
                break
    return cues


# --------------------------------------------------------------------- ASS

def _ass_color(hex_rgb: str, alpha: int = 0) -> str:
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_escape(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", "\\N")


def style_metrics(style: SubtitleStyle, width: int, height: int) -> tuple[int, int]:
    h = height or 1080
    size = style.size or max(16, int(round(h * 0.055)))
    margin = style.margin_v or max(10, int(round(h * 0.045)))
    return size, margin


def write_ass(path: str | Path, cues: list[Cue], style: SubtitleStyle, width: int, height: int) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = width or 1920, height or 1080
    size, margin = style_metrics(style, w, h)
    zh_size = max(12, int(round(size * style.zh_size_ratio)))
    border_style = 3 if style.box else 1
    back = _ass_color("#000000", 0x60 if style.box else 0x80)
    bold = -1 if style.bold else 0
    hdr = [
        "[Script Info]",
        "; Generated by SubVideo",
        "ScriptType: v4.00+",
        f"PlayResX: {w}",
        f"PlayResY: {h}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding",
        f"Style: Default,{style.font},{size},{_ass_color(style.primary)},&H000000FF,{_ass_color(style.outline_color)},"
        f"{back},{bold},0,0,0,100,100,0,0,{border_style},{style.outline},{style.shadow},2,{int(w * 0.05)},"
        f"{int(w * 0.05)},{margin},1",
        f"Style: Chinese,{style.font},{zh_size},&H00E0E0E0,&H000000FF,{_ass_color(style.outline_color)},"
        f"{back},0,0,0,0,100,100,0,0,{border_style},{max(1.0, style.outline * 0.8)},{style.shadow},2,{int(w * 0.05)},"
        f"{int(w * 0.05)},{margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    ev = []
    for c in cues:
        text = _ass_escape(c.text)
        if c.secondary:
            text = "{\\rChinese}" + _ass_escape(c.secondary) + "\\N{\\rDefault}" + text
        ev.append(f"Dialogue: 0,{ms_to_ass(c.start)},{ms_to_ass(c.end)},Default,{c.speaker},0,0,0,,{text}")
    path.write_text("\n".join(hdr + ev) + "\n", encoding="utf-8-sig")
    return path


_ASS_DLG = re.compile(r"^Dialogue:\s*[^,]*,([^,]+),([^,]+),[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,(.*)$")


def read_ass(path: str | Path) -> list[Cue]:
    from .timeutil import parse_time

    cues = []
    for line in Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
        m = _ASS_DLG.match(line.strip())
        if m:
            txt = re.sub(r"\{[^}]*\}", "", m.group(3)).replace("\\N", "\n").replace("\\n", "\n")
            cues.append(Cue(parse_time(m.group(1)), parse_time(m.group(2)), txt.strip()))
    return cues


def read_subtitles(path: str | Path) -> list[Cue]:
    p = Path(path)
    if p.suffix.lower() in (".ass", ".ssa"):
        return read_ass(p)
    return read_srt(p)
