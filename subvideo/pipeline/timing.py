"""Pacing solver: decide where every dubbed line is placed and how much it is
stretched, so characters speak at an even, natural rate.

Priority (cheapest perceptual cost first):
  1. translation length budget (done earlier, at translation time)
  2. TTS generated at the right speed (engine supports duration/speed)
  3. borrow silence before (lead) / after (tail) the original line
  4. time-stretch the voice, keeping pitch (limited, e.g. <= 1.20x)
  5. slow the video down locally (limited, e.g. >= 0.85x) - last resort
  6. flag the line "need_shorten" so it can be re-translated shorter
Lines that are shorter than their slot are never slowed down (no drawling);
they keep natural speed and start when the character starts speaking.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..core.project import Project, Segment, TimingSettings
from ..core.textutil import count_vi_syllables


@dataclass
class Placement:
    seg_id: int
    start: int  # source ms
    dur_out: int  # clip duration on the output timeline (ms)
    speed: float  # audio speed factor (>1 faster)
    slow_region: list | None = None  # [a, b, v]
    flags: list = field(default_factory=list)


@dataclass
class TimingReport:
    total: int = 0
    natural: int = 0
    shifted: int = 0
    stretched: int = 0
    video_slowed: int = 0
    need_shorten: int = 0
    added_ms: int = 0
    rates: dict = field(default_factory=dict)  # character -> avg syllables/s

    def text(self) -> str:
        if not self.total:
            return "Không có câu lồng tiếng nào."
        pct = lambda n: f"{n} ({n * 100 // max(1, self.total)}%)"  # noqa: E731
        lines = [
            f"Tổng số câu lồng tiếng: {self.total}",
            f"• Giữ nguyên tốc độ tự nhiên: {pct(self.natural)} (trong đó mượn khoảng lặng: {self.shifted})",
            f"• Co giãn giọng (giữ cao độ): {pct(self.stretched)}",
            f"• Làm chậm video cục bộ: {pct(self.video_slowed)} (video dài thêm {self.added_ms / 1000:.1f}s)",
            f"• Cần rút gọn lời dịch: {pct(self.need_shorten)}",
        ]
        if self.rates:
            lines.append("Tốc độ nói trung bình (âm tiết/giây): " +
                         ", ".join(f"{k}: {v:.1f}" for k, v in self.rates.items()))
        return "\n".join(lines)


def solve(segments: list[Segment], settings: TimingSettings, media_duration_ms: int,
          keep_locked: bool = True) -> tuple[list[Placement], TimingReport]:
    """Greedy left-to-right placement. ``segments`` must have tts_ms filled."""
    segs = sorted([s for s in segments if s.has_dub() and s.tts_ms > 0], key=lambda s: s.start)
    st = settings
    gap = st.min_gap_ms
    out: list[Placement] = []
    rep = TimingReport(total=len(segs))
    prev_end = -10**9
    for i, s in enumerate(segs):
        d = int(s.tts_ms)
        next_start = segs[i + 1].start if i + 1 < len(segs) else media_duration_ms + 2000
        if keep_locked and s.locked and s.place_start is not None:
            spd = max(0.5, s.stretch or 1.0)
            dur_out = int(round(d / spd))
            p = Placement(s.id, int(s.place_start), dur_out, spd, s.slow_region)
            out.append(p)
            prev_end = p.start + (int(s.slow_region[1] - s.slow_region[0]) if s.slow_region else dur_out)
            continue

        lo = max(s.start - st.max_lead_ms, prev_end + gap, 0)
        hard_hi = next_start - gap
        if hard_hi < lo + 200:
            hard_hi = lo + 200  # overlapping speech: allow a minimum window
        soft_hi = min(s.end + st.max_tail_ms, hard_hi)
        pref = max(lo, s.start)

        flags: list[str] = []
        slow = None
        if pref + d <= soft_hi:
            start, speed, dur_out = pref, 1.0, d
            rep.natural += 1
        elif lo + d <= soft_hi:
            start, speed, dur_out = soft_hi - d, 1.0, d
            rep.natural += 1
            rep.shifted += 1
        elif lo + d <= hard_hi:
            start, speed, dur_out = max(lo, min(pref, hard_hi - d)), 1.0, d
            rep.natural += 1
            rep.shifted += 1
        else:
            window = hard_hi - lo
            need = d / max(1, window)
            if need <= st.max_speedup:
                start, speed, dur_out = lo, need, window
                rep.stretched += 1
            else:
                speed = st.max_speedup
                dur_needed = d / speed
                if st.allow_video_slow:
                    v = window / dur_needed
                    if v >= st.max_video_slow:
                        slow = [int(lo), int(hard_hi), round(v, 4)]
                        dur_out = int(round(window / v))
                    else:
                        v = st.max_video_slow
                        slow = [int(lo), int(hard_hi), v]
                        dur_out = int(round(window / v))
                        speed = min(st.hard_max_speedup, d / dur_out)
                        if d / dur_out > st.max_speedup + 1e-3:
                            flags.append("need_shorten")
                    rep.video_slowed += 1
                    rep.added_ms += int(window / slow[2] - window)
                    if speed > 1.001:
                        rep.stretched += 1
                else:
                    speed = min(st.hard_max_speedup, need)
                    dur_out = int(round(d / speed))
                    rep.stretched += 1
                    if need > st.max_speedup + 1e-3:
                        flags.append("need_shorten")
                start = lo
        if speed < 1.0 and speed < st.min_speed:
            speed = 1.0
        if "need_shorten" in flags:
            rep.need_shorten += 1
        p = Placement(s.id, int(start), int(dur_out), float(round(speed, 4)), slow, flags)
        out.append(p)
        prev_end = (slow[1] if slow else start + dur_out)
    return out, rep


def apply(project: Project, placements: list[Placement], report: TimingReport | None = None) -> None:
    by_id = {p.seg_id: p for p in placements}
    rates: dict[str, list[float]] = {}
    for s in project.segments:
        p = by_id.get(s.id)
        s.remove_flag("need_shorten")
        if p is None:
            if not s.has_dub():
                s.place_start, s.slow_region = None, None
            continue
        s.place_start = p.start
        s.place_dur = p.dur_out
        s.stretch = p.speed
        s.slow_region = p.slow_region
        for f in p.flags:
            s.add_flag(f)
        ch = project.character_for_segment(s)
        key = ch.display_name() if ch else (s.speaker or "?")
        syl = count_vi_syllables(s.vi_dub)
        if p.dur_out > 0:
            rates.setdefault(key, []).append(syl / (p.dur_out / 1000.0))
    if report is not None:
        report.rates = {k: sum(v) / len(v) for k, v in rates.items() if v}


def syllable_budget(seg: Segment, prev_end: int | None, next_start: int | None, rate: float,
                    settings: TimingSettings) -> int:
    """Max Vietnamese syllables for a dubbed line so it fits at ``rate`` syll/s."""
    lead = settings.max_lead_ms
    tail = settings.max_tail_ms
    if prev_end is not None:
        lead = max(0, min(lead, seg.start - prev_end - settings.min_gap_ms))
    if next_start is not None:
        tail = max(0, min(tail, next_start - seg.end - settings.min_gap_ms))
    avail = seg.duration + lead + tail * 0.6  # keep a little of the tail for breathing room
    return max(2, int(round(avail / 1000.0 * rate)))


def compute_budgets(project: Project) -> None:
    segs = sorted([s for s in project.segments if s.kind == "speech"], key=lambda s: s.start)
    for i, s in enumerate(segs):
        ch = project.character_for_segment(s)
        rate = (ch.rate if ch and ch.rate else project.timing.default_rate)
        prev_end = segs[i - 1].end if i > 0 else None
        next_start = segs[i + 1].start if i + 1 < len(segs) else None
        s.max_syllables = syllable_budget(s, prev_end, next_start, rate, project.timing)
