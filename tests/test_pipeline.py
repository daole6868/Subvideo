import numpy as np

from subvideo.core.project import Segment, TimingSettings
from subvideo.pipeline.fusion import Char, align_units, chars_to_text, match_ocr_line, split_chars
from subvideo.pipeline.llm import parse_json
from subvideo.pipeline.mixer import _gate, soft_limit
from subvideo.pipeline.timemap import TimeMap
from subvideo.pipeline.timing import solve, syllable_budget


# ------------------------------------------------------------- time map

def test_timemap_forward_inverse():
    tm = TimeMap([(1000, 2000, 0.5), (5000, 6000, 0.8)])
    assert tm.out(500) == 500
    assert tm.out(1500) == 2000  # half-way through a 2x-long region
    assert tm.out(2000) == 3000
    assert abs(tm.out(6000) - (6000 + 1000 + 250)) < 1e-6
    for t in (0, 999, 1500, 3000, 5500, 7000):
        assert abs(tm.inv(tm.out(t)) - t) < 1e-6
    segs = tm.segments(8000)
    assert segs[0] == (0.0, 1000.0, 1.0) and segs[1] == (1000.0, 2000.0, 0.5)


def test_timemap_ffmpeg_expr_matches_python():
    tm = TimeMap([(1000, 2000, 0.5), (5000, 6000, 0.8)])
    expr = tm.ffmpeg_pts_expr()
    body = expr[1:-4]  # strip "(" ... ")/TB"

    def lt(a, b):
        return a < b

    def iff(c, a, b):
        return a if c else b

    for t in (0.5, 1.5, 3.0, 5.5, 7.0):
        py = body.replace("if(", "iff(").replace("T", str(t))
        assert abs(eval(py) - tm.out(t * 1000) / 1000) < 1e-5


# --------------------------------------------------------------- timing

def _seg(i, a, b, tts, text="một hai ba"):
    return Segment(id=i, start=a, end=b, vi_dub=text, tts_ms=tts, tts_file="x.wav")


def test_solver_natural_and_shift():
    st = TimingSettings()
    segs = [_seg(1, 1000, 2000, 900), _seg(2, 3000, 4000, 1300)]
    pl, rep = solve(segs, st, 10000)
    assert pl[0].start == 1000 and pl[0].speed == 1.0
    assert pl[1].speed == 1.0 and pl[1].start + pl[1].dur_out <= 4000 + st.max_tail_ms
    assert rep.natural == 2


def test_solver_stretch_then_slow_video():
    st = TimingSettings(max_lead_ms=0, max_tail_ms=0, min_gap_ms=0)
    segs = [_seg(1, 1000, 2000, 1100), _seg(2, 2000, 3000, 2000), _seg(3, 3000, 4000, 500)]
    pl, rep = solve(segs, st, 10000)
    by = {p.seg_id: p for p in pl}
    assert abs(by[1].speed - 1.1) < 1e-3  # stretched within the window
    assert by[2].slow_region is not None  # needed more than max_speedup
    assert by[2].slow_region[2] >= st.max_video_slow
    assert rep.video_slowed == 1


def test_solver_flags_need_shorten():
    st = TimingSettings(max_lead_ms=0, max_tail_ms=0, min_gap_ms=0, allow_video_slow=False)
    segs = [_seg(1, 1000, 2000, 3000), _seg(2, 2000, 3000, 500)]
    pl, rep = solve(segs, st, 10000)
    assert "need_shorten" in pl[0].flags and pl[0].speed <= st.hard_max_speedup


def test_syllable_budget():
    st = TimingSettings()
    s = Segment(start=1000, end=3000)
    assert syllable_budget(s, None, None, 5.0, st) == round((2000 + 200 + 300) / 1000 * 5)


# --------------------------------------------------------------- fusion

def test_align_and_match_with_homophones():
    cand = [Char(c, 1000 + i * 200, 1200 + i * 200) for i, c in enumerate("你好我叫小言")]
    r = match_ocr_line("你好，我叫萧炎。", cand)
    assert r is not None
    assert r.start == 1000 and r.end == 1000 + 6 * 200
    assert r.equal == 4 and r.homophone == 2


def test_match_rejects_unrelated():
    cand = [Char(c, i * 100, i * 100 + 90) for i, c in enumerate("今天天气很好")]
    assert match_ocr_line("师兄等等我", cand) is None


def test_split_chars_on_pause_and_punct():
    chars = [Char(c, i * 200, i * 200 + 180, "S0", 0) for i, c in enumerate("你好我叫萧炎")]
    chars[1].punct = "。"
    for c in chars[2:]:
        c.s += 1000
        c.e += 1000
    groups = split_chars(chars)
    assert ["".join(c.c for c in g) for g in groups] == ["你好", "我叫萧炎"]
    assert chars_to_text(groups[0]) == "你好"


def test_align_units_gaps():
    pairs = align_units(list("山上上修"), list("山上修"))
    assert sum(1 for a, b in pairs if a is not None and b is None) == 1


# --------------------------------------------------------------- misc

def test_parse_json_variants():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('blah {"lines": []} trailing') == {"lines": []}


def test_loudness_gate_and_limiter():
    sr = 48000
    t = np.arange(sr * 3) / sr
    x = (0.1 * np.sin(2 * np.pi * 1000 * t)).astype(np.float64)
    from subvideo.pipeline.mixer import _lufs_array

    l1 = _lufs_array(np.stack([x, x], axis=1))
    l2 = _lufs_array(np.stack([2 * x, 2 * x], axis=1))
    assert abs((l2 - l1) - 6.02) < 0.1
    y = soft_limit(np.array([0.5, 0.95, 1.5, -2.0], dtype=np.float32))
    assert np.all(np.abs(y) <= 10 ** (-1 / 20) + 1e-6) and y[0] == 0.5
    assert _gate(np.array([1e-9] * 10)) == -70.0
