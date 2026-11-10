"""Generate a synthetic test video: burned-in Chinese subtitles at known frames
and 'speech' (voiced tone bursts) at known times, plus background music."""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]

# (sub_start_s, sub_end_s, text, speech_start_s, speech_end_s)
LINES = [
    (1.00, 3.00, "你好，我叫萧炎。", 1.12, 2.70),
    (3.40, 5.20, "今天天气很好。", 3.52, 4.95),
    (6.00, 8.40, "我们去山上修炼吧！", 6.10, 8.05),
    (9.00, 10.60, "师兄，等等我。", 9.20, 10.40),
]
FPS = 25
DURATION = 12.0
SR = 48000


def _font() -> str:
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    raise FileNotFoundError("Không tìm thấy font tiếng Trung để tạo video thử")


def make_audio(path: Path) -> None:
    import soundfile as sf

    t = np.arange(int(DURATION * SR)) / SR
    music = 0.05 * np.sin(2 * np.pi * 110 * t) + 0.03 * np.sin(2 * np.pi * 165 * t)
    voice = np.zeros_like(t)
    for _, _, _, a, b in LINES:
        m = (t >= a) & (t < b)
        tt = t[m] - a
        syl = (np.sin(np.pi * tt * 4.0) ** 2)  # 4 syllables / s envelope
        voice[m] = 0.4 * syl * (np.sin(2 * np.pi * 220 * tt) + 0.5 * np.sin(2 * np.pi * 440 * tt))
    left = music + voice
    right = music + voice
    sf.write(str(path), np.stack([left, right], axis=1).astype(np.float32), SR, subtype="FLOAT")


def make_video(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    wav = out.with_suffix(".wav")
    make_audio(wav)
    font = _font()
    draws = []
    for a, b, text, _, _ in LINES:
        draws.append(
            f"drawtext=fontfile='{font}':text='{text}':fontsize=44:fontcolor=white:borderw=3:bordercolor=black:"
            f"x=(w-text_w)/2:y=h-90:enable='gte(t,{a})*lt(t,{b})'"
        )
    vf = ",".join(draws)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size=960x540:rate={FPS}:duration={DURATION}",
           "-i", str(wav), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "192k", "-shortest", str(out)]
    subprocess.run(cmd, check=True)
    wav.unlink(missing_ok=True)
    return out


if __name__ == "__main__":
    import sys

    print(make_video(Path(sys.argv[1] if len(sys.argv) > 1 else "synthetic.mp4")))
