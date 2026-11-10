"""End-to-end run on a synthetic video (no GPU / no API needed).

OCR (RapidOCR) -> fusion -> beep TTS -> timing -> mix -> export -> exact preview.
"""

import importlib.util
import json
import shutil
import subprocess

import pytest

from subvideo.core.jobs import JobContext
from subvideo.core.project import Character, Project
from subvideo.pipeline.stages import run_stage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or importlib.util.find_spec("rapidocr") is None or importlib.util.find_spec("av") is None,
    reason="needs ffmpeg, PyAV and rapidocr",
)


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    from tests.synthetic import LINES, make_video

    try:
        from tests.synthetic import _font

        _font()
    except FileNotFoundError:
        pytest.skip("no CJK font to render the test video")
    d = tmp_path_factory.mktemp("e2e")
    video = make_video(d / "synthetic.mp4")
    p = Project.create(d / "proj")
    ctx = JobContext()
    run_stage("import_media", p, {"file": str(video)}, ctx)
    run_stage("extract", p, {}, ctx)
    run_stage("separate", p, {"engine": "none"}, ctx)
    p.ocr.rois = [[0.1, 0.78, 0.8, 0.2]]
    p.save()
    return p, LINES


def test_ocr_frame_exact(project):
    p, lines = project
    run_stage("ocr", p, {}, JobContext())
    events = json.loads(p.ocr_json.read_text("utf-8"))["events"]
    assert len(events) == len(lines)
    for e, (a, b, text, _, _) in zip(events, lines):
        assert e["start"] == int(a * 1000) and e["end"] == int(b * 1000)  # exact frames
        assert e["text"][:2] == text[:2]


def test_fusion_ocr_only_refines_to_voice(project):
    p, lines = project
    run_stage("fusion", p, {"mode": "ocr", "detect_shots": False}, JobContext())
    assert len(p.segments) == len(lines)
    for s, (_, _, _, va, vb) in zip(sorted(p.segments, key=lambda x: x.start), lines):
        assert abs(s.start - va * 1000) <= 60, (s.start, va)
        assert abs(s.end - vb * 1000) <= 60, (s.end, vb)


def test_dub_mix_export(project, tmp_path):
    p, lines = project
    p.characters = [Character(id="char01", name_vi="A", speaker_labels=[])]
    texts = ["Xin chào, ta là Tiêu Viêm.", "Hôm nay trời đẹp quá.",
             "Chúng ta cùng nhau lên ngọn núi phía xa kia để tu luyện thật chăm chỉ, rồi luyện thêm kiếm pháp nữa đi nào!",
             "Sư huynh, đợi muội với."]
    for s, t in zip(sorted(p.segments, key=lambda x: x.start), texts):
        s.vi_sub = s.vi_dub = t
    p.set_param("tts", "engine", "beep")
    p.export.cover = "blur"
    p.export.video_mode = "x264"
    p.export.preset = "ultrafast"
    ctx = JobContext()
    run_stage("tts", p, {}, ctx)
    msg = run_stage("timing", p, {}, ctx)
    assert "Tổng số câu lồng tiếng: 4" in msg
    assert p.slow_regions(), "the long line should slow the video down"
    run_stage("mix", p, {}, ctx)
    assert p.mix_wav.exists() and p.mix_src_wav.exists()
    out = tmp_path / "out.mp4"
    run_stage("export", p, {"output": str(out)}, ctx)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout.strip()
    expected = (p.media.duration_ms + sum((b - a) * (1 / v - 1) for a, b, v in p.slow_regions())) / 1000
    assert abs(float(probe) - expected) < 0.1
    prev = run_stage("preview_clip", p, {"start_ms": 2000, "end_ms": 5000}, ctx)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", prev.strip()],
                           capture_output=True, text=True).stdout.strip()
    assert abs(float(probe) - 3.0) < 0.15
