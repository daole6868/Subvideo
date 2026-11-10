import numpy as np
import pytest

from subvideo.core.audio import energy_vad, envelope_db, refine_boundaries
from subvideo.core.project import Character, DisplayRules, Project, Segment
from subvideo.core.subtitles import Cue, build_cues, compute_display_times, parse_srt_text, write_ass, write_srt, read_srt
from subvideo.core.textutil import (count_vi_syllables, is_homophone, is_nonverbal_zh, normalize_vi_for_tts, read_int_vi,
                                    similarity, strip_zh_punct, wrap_lines, zh_units)
from subvideo.core.timeutil import ms_to_ass, ms_to_clock, ms_to_srt, parse_time, snap_to_frame
from subvideo.core.project import SubtitleStyle


# ---------------------------------------------------------------- time

def test_time_formats():
    assert ms_to_srt(3_723_456) == "01:02:03,456"
    assert ms_to_ass(3_723_456) == "1:02:03.46"
    assert ms_to_clock(83_500) == "01:23.500"
    assert parse_time("01:23.5") == 83_500
    assert parse_time("1:02:03,456") == 3_723_456
    assert parse_time("1500ms") == 1500
    assert parse_time("2.25") == 2250


def test_snap_to_frame():
    assert snap_to_frame(1019, 25) == 1000
    assert snap_to_frame(1021, 25) == 1040
    assert snap_to_frame(1001, 25, "ceil") == 1040
    assert snap_to_frame(1039, 25, "floor") == 1000


# ---------------------------------------------------------------- text

@pytest.mark.parametrize("n,txt", [
    (0, "không"), (5, "năm"), (10, "mười"), (15, "mười lăm"), (21, "hai mươi mốt"), (24, "hai mươi tư"),
    (105, "một trăm linh năm"), (1000, "một nghìn"), (2024, "hai nghìn không trăm hai mươi tư"),
    (1_000_005, "một triệu không trăm linh năm"), (3_000_000_000, "ba tỷ"),
])
def test_read_int_vi(n, txt):
    assert read_int_vi(n) == txt


def test_vi_syllables_and_normalize():
    assert count_vi_syllables("Xin chào, ta là Tiêu Viêm.") == 6
    assert count_vi_syllables("Năm 2024") == 1 + 7
    assert "phần trăm" in normalize_vi_for_tts("50%")
    assert normalize_vi_for_tts("1.000.000 đồng") == "một triệu đồng"


def test_zh_helpers():
    assert zh_units("你好ABC 123，世界") == ["你", "好", "ABC", "123", "世", "界"]
    assert strip_zh_punct("你好，世界！") == "你好世界"
    assert is_homophone("萧", "小")
    assert not is_homophone("萧", "大")
    assert is_nonverbal_zh("啊啊！")
    assert not is_nonverbal_zh("你好")
    assert similarity("你好，世界", "你好世界") == 1.0


def test_wrap_lines():
    t = wrap_lines("Hôm nay chúng ta sẽ cùng nhau lên núi tu luyện võ công thật chăm chỉ nhé", 42, 2)
    lines = t.split("\n")
    assert len(lines) == 2 and all(len(l) <= 42 for l in lines)
    assert wrap_lines("Ngắn thôi", 42) == "Ngắn thôi"


# --------------------------------------------------------------- project

def test_project_roundtrip(tmp_path):
    p = Project.create(tmp_path / "p")
    p.segments = [Segment(id=1, start=100, end=900, zh="你好", vi_sub="Chào", vi_dub="Chào", speaker="SPEAKER_00")]
    p.characters = [Character(id="char01", name_vi="A", speaker_labels=["SPEAKER_00"])]
    p.save()
    q = Project.load(tmp_path / "p")
    assert q.segments[0].zh == "你好"
    assert q.character_for_segment(q.segments[0]).name_vi == "A"
    assert q.segments[0].has_dub()
    q.mark("fusion", "done")
    q.mark("translate", "done")
    q.mark("fusion", "done")
    assert q.steps["translate"].status == "stale"


def test_slow_regions_merge(tmp_path):
    p = Project.create(tmp_path / "p")
    a = Segment(id=1, start=0, end=1000, vi_dub="x", slow_region=[0, 1000, 0.9])
    b = Segment(id=2, start=900, end=2000, vi_dub="y", slow_region=[900, 2000, 0.9])
    p.segments = [a, b]
    regs = p.slow_regions()
    assert regs[0] == (0, 1000, 0.9) and regs[1][0] == 1000


# --------------------------------------------------------------- audio

def _tone(sr, total_s, spans):
    t = np.arange(int(total_s * sr)) / sr
    x = 0.002 * np.random.default_rng(0).standard_normal(len(t))
    for a, b in spans:
        m = (t >= a) & (t < b)
        x[m] += 0.5 * np.sin(2 * np.pi * 300 * t[m])
    return x.astype(np.float32)


def test_refine_boundaries_snaps_to_voice():
    sr = 16000
    x = _tone(sr, 4.0, [(1.2, 2.5)])
    env = envelope_db(x, sr, hop_ms=5, win_ms=20)
    s, e = refine_boundaries(env, 5.0, 1100, 2600, search_ms=150)
    assert abs(s - 1200) <= 25 and abs(e - 2500) <= 30
    s, e = refine_boundaries(env, 5.0, 900, 2900, start_window=(300, 700), end_window=(900, 300))
    assert abs(s - 1200) <= 25 and abs(e - 2500) <= 30


def test_energy_vad():
    sr = 16000
    x = _tone(sr, 5.0, [(0.5, 1.5), (2.5, 4.0)])
    regs = energy_vad(x, sr)
    assert len(regs) == 2
    assert abs(regs[0][0] - 500) < 100 and abs(regs[1][1] - 4000) < 100


# ------------------------------------------------------------- subtitles

def test_display_times_rules():
    segs = [Segment(id=1, start=1000, end=2000), Segment(id=2, start=2100, end=3000)]
    compute_display_times(segs, 25.0, DisplayRules(), shots=[])
    a, b = segs
    assert a.disp_start == 1000
    assert a.disp_end <= b.disp_start - 80 + 1  # 2 frames gap at 25 fps
    assert b.disp_end == 3320  # linger 300 ms then snapped to frame
    for s in segs:
        assert s.disp_start % 40 == 0 and s.disp_end % 40 == 0


def test_display_snaps_to_shot():
    segs = [Segment(id=1, start=1000, end=2000)]
    compute_display_times(segs, 25.0, DisplayRules(), shots=[2200])
    assert segs[0].disp_end == 2200


def test_srt_ass_roundtrip(tmp_path):
    cues = [Cue(1000, 2500, "Xin chào"), Cue(3000, 4000, "Tạm biệt", secondary="再见")]
    write_srt(tmp_path / "a.srt", cues)
    back = read_srt(tmp_path / "a.srt")
    assert back[0].start == 1000 and back[0].end == 2500 and back[0].text == "Xin chào"
    write_ass(tmp_path / "a.ass", cues, SubtitleStyle(), 1920, 1080)
    txt = (tmp_path / "a.ass").read_text("utf-8-sig")
    assert "Dialogue: 0,0:00:01.00,0:00:02.50" in txt and "{\\rChinese}再见" in txt
    vtt = parse_srt_text("WEBVTT\n\n00:01.000 --> 00:02.000\nHello\n")
    assert vtt[0].start == 1000 and vtt[0].text == "Hello"


def test_build_cues_time_map():
    from subvideo.pipeline.timemap import TimeMap

    segs = [Segment(id=1, start=5000, end=6000, vi_sub="A", disp_start=5000, disp_end=6000)]
    tm = TimeMap([(1000, 2000, 0.5)])  # +1000 ms after 2 s
    cues = build_cues(segs, time_map=tm.out)
    assert cues[0].start == 6000 and cues[0].end == 7000
