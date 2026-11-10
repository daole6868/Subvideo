"""Shot-change detection (used to snap subtitle timing to cuts and to group
lines into scenes for translation context)."""

from __future__ import annotations

import numpy as np

from ..core.jobs import JobContext
from ..core.project import Project


def detect_shots(project: Project, ctx: JobContext, engine: str = "builtin", threshold: float = 27.0) -> list[int]:
    if engine == "pyscenedetect":
        try:
            return _pyscenedetect(project, threshold)
        except ImportError:
            ctx.warn("Chưa cài scenedetect, dùng bộ phát hiện tích hợp.")
    return _builtin(project, ctx)


def _pyscenedetect(project: Project, threshold: float) -> list[int]:
    from scenedetect import ContentDetector, SceneManager, open_video

    v = open_video(str(project.video_path))
    sm = SceneManager()
    sm.add_detector(ContentDetector(threshold=threshold, min_scene_len=8))
    sm.detect_scenes(video=v)
    shots = []
    for start, _end in sm.get_scene_list():
        sec = getattr(start, "seconds", None)
        if sec is None:
            sec = start.get_seconds()
        if sec > 0:
            shots.append(int(round(sec * 1000)))
    return shots


def _builtin(project: Project, ctx: JobContext) -> list[int]:
    import av

    shots: list[int] = []
    with av.open(str(project.video_path)) as c:
        s = c.streams.video[0]
        s.thread_type = "AUTO"
        prev = None
        diffs: list[float] = []
        dur = project.media.duration_ms or 1
        n = 0
        last_cut = -10_000
        for fr in c.decode(s):
            if fr.time is None:
                continue
            g = fr.reformat(width=96, height=54, format="gray").to_ndarray().astype(np.int16)
            t = int(round(fr.time * 1000))
            if prev is not None:
                d = float(np.abs(g - prev).mean())
                hist_d = float(np.abs(np.histogram(g, 32, (0, 256))[0] - np.histogram(prev, 32, (0, 256))[0]).sum()) / g.size
                recent = diffs[-15:]
                med = float(np.median(recent)) if recent else 0.0
                score = d
                if score > max(22.0, 4.0 * med + 6.0) and hist_d > 0.25 and t - last_cut > 300:
                    shots.append(t)
                    last_cut = t
                diffs.append(d)
            prev = g
            n += 1
            if n % 100 == 0:
                ctx.progress(min(0.99, t / dur), f"Phát hiện cắt cảnh: {t / 1000:.0f}s")
    return shots
