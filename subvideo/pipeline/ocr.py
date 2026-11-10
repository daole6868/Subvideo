"""Frame-accurate hard-subtitle extraction from user-selected regions (ROI).

Every frame is decoded (exact PTS, so variable-frame-rate videos are handled),
only the ROI is analysed: a cheap text mask (bright text + dark outline, optional
colour filter) is compared frame-to-frame to find the exact frame where each
subtitle appears / disappears.  OCR runs only once per stable subtitle (on a
settled frame), which is ~100x faster than OCR-ing every frame.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..core.jobs import JobContext
from ..core.project import OCRSettings, Project
from ..core.textutil import is_cjk, similarity, to_simplified

MASK_W = 320


# ------------------------------------------------------------------ masks

def _cv2():
    import cv2

    return cv2


def text_mask(y: np.ndarray, u: np.ndarray | None, v: np.ndarray | None, st: OCRSettings) -> np.ndarray:
    """Boolean mask of probable subtitle-text pixels on (downscaled) planes."""
    cv2 = _cv2()
    thr = st.luma_thr
    if st.text_color == "yellow":
        bright = y >= int(thr * 0.72)
    else:
        bright = y >= thr
    m = bright
    if st.need_outline:
        dark = (y <= 90).astype(np.uint8)
        dark = cv2.dilate(dark, np.ones((3, 3), np.uint8), iterations=2).astype(bool)
        m = m & dark
    if u is not None and v is not None:
        du = u.astype(np.int16) - 128
        dv = v.astype(np.int16) - 128
        tol = st.color_tol
        if st.text_color == "white":
            m = m & (np.abs(du) < tol // 2 + 8) & (np.abs(dv) < tol // 2 + 8)
        elif st.text_color == "yellow":
            m = m & (du < -tol // 3) & (dv > -10)
        elif st.text_color == "custom":
            r, g, b = st.custom_rgb
            cy = 0.299 * r + 0.587 * g + 0.114 * b
            cu = -0.169 * r - 0.331 * g + 0.5 * b
            cv_ = 0.5 * r - 0.419 * g - 0.081 * b
            m = (np.abs(y.astype(np.int16) - cy) < tol * 1.5) & (np.abs(du - cu) < tol) & (np.abs(dv - cv_) < tol)
            if st.need_outline:
                dark = (y <= 90).astype(np.uint8)
                m = m & cv2.dilate(dark, np.ones((3, 3), np.uint8), iterations=2).astype(bool)
    return m


def mask_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Shift-tolerant overlap of two masks, 0..1."""
    sa, sb = int(a.sum()), int(b.sum())
    if sa == 0 and sb == 0:
        return 1.0
    if sa == 0 or sb == 0:
        return 0.0
    cv2 = _cv2()
    k = np.ones((3, 3), np.uint8)
    ad = cv2.dilate(a.astype(np.uint8), k).astype(bool)
    bd = cv2.dilate(b.astype(np.uint8), k).astype(bool)
    inter = int((a & bd).sum()) + int((b & ad).sum())
    return inter / float(sa + sb)


def containment(small: np.ndarray, big: np.ndarray) -> float:
    s = int(small.sum())
    if s == 0:
        return 1.0
    cv2 = _cv2()
    bd = cv2.dilate(big.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
    return int((small & bd).sum()) / float(s)


# ------------------------------------------------------------ OCR engines

class OCREngine:
    name = "base"

    def recognize(self, img_bgr: np.ndarray) -> tuple[str, float, list]:
        raise NotImplementedError


def _join_lines(items: list[tuple[list, str, float]], cjk_only: bool) -> tuple[str, float, list]:
    """items: (box_points, text, score). Join top-to-bottom, left-to-right."""
    rows = []
    for box, txt, sc in items:
        if not txt or not txt.strip():
            continue
        if cjk_only and not any(is_cjk(c) for c in txt):
            continue
        pts = np.asarray(box, dtype=np.float32).reshape(-1, 2) if box is not None and len(box) else np.zeros((1, 2))
        rows.append((float(pts[:, 1].mean()), float(pts[:, 0].min()), float(pts[:, 1].max() - pts[:, 1].min()), txt.strip(), float(sc)))
    if not rows:
        return "", 0.0, []
    rows.sort(key=lambda r: (r[0], r[1]))
    # group into lines by vertical centre
    lines: list[list] = []
    for r in rows:
        if lines and abs(lines[-1][-1][0] - r[0]) < max(8.0, 0.5 * r[2]):
            lines[-1].append(r)
        else:
            lines.append([r])
    texts, scores = [], []
    for ln in lines:
        ln.sort(key=lambda r: r[1])
        texts.append("".join(r[3] for r in ln) if all(is_cjk(r[3][0]) for r in ln) else " ".join(r[3] for r in ln))
        scores += [r[4] for r in ln]
    return "\n".join(texts), float(np.mean(scores)) if scores else 0.0, [r[:3] for r in rows]


class RapidOCREngine(OCREngine):
    """RapidOCR 3.x (ONNX Runtime; default PP-OCR models ship inside the wheel)."""

    name = "rapidocr"

    def __init__(self, cjk_only: bool = True):
        self.cjk_only = cjk_only
        try:
            from rapidocr import RapidOCR  # rapidocr >= 2

            params = {"Global.log_level": "warning"}
            try:
                from ..config import Settings

                md = Settings.get()["models_dir"]
                if md:
                    Path(md, "rapidocr").mkdir(parents=True, exist_ok=True)
                    params["Global.model_root_dir"] = str(Path(md, "rapidocr"))
            except Exception:
                pass
            try:
                self.engine = RapidOCR(params=params)
            except Exception:
                self.engine = RapidOCR()
            self.v2 = True
        except ImportError:
            from rapidocr_onnxruntime import RapidOCR  # legacy 1.x package

            self.engine = RapidOCR()
            self.v2 = False

    def recognize(self, img_bgr):
        res = self.engine(img_bgr)
        items = []
        if self.v2 or hasattr(res, "txts"):
            boxes = getattr(res, "boxes", None)
            txts = getattr(res, "txts", None) or ()
            scores = getattr(res, "scores", None) or ()
            for i, t in enumerate(txts):
                box = boxes[i] if boxes is not None and i < len(boxes) else None
                items.append((box, t, scores[i] if i < len(scores) else 0.0))
        else:
            result = res[0] if isinstance(res, tuple) else res
            for r in result or []:
                items.append((r[0], r[1], r[2]))
        return _join_lines(items, self.cjk_only)


class PaddleOCREngine(OCREngine):
    """PaddleOCR 3.x (models downloaded on first use; set PADDLE_PDX_MODEL_SOURCE=modelscope if HF is blocked)."""

    name = "paddleocr"

    def __init__(self, cjk_only: bool = True):
        from paddleocr import PaddleOCR

        self.cjk_only = cjk_only
        self.ocr = PaddleOCR(lang="ch", use_doc_orientation_classify=False, use_doc_unwarping=False,
                             use_textline_orientation=False)

    def recognize(self, img_bgr):
        items = []
        for res in self.ocr.predict(img_bgr) or []:
            texts = list(res["rec_texts"])
            scores = list(res["rec_scores"])
            polys = list(res["rec_polys"])
            for i, t in enumerate(texts):
                items.append((polys[i] if i < len(polys) else None, t, scores[i] if i < len(scores) else 0.0))
        return _join_lines(items, self.cjk_only)


def make_engine(name: str, cjk_only: bool = True) -> OCREngine:
    if name == "paddleocr":
        return PaddleOCREngine(cjk_only)
    return RapidOCREngine(cjk_only)


def prepare_for_ocr(crop_bgr: np.ndarray) -> np.ndarray:
    cv2 = _cv2()
    h = crop_bgr.shape[0]
    if h < 64:
        f = 64.0 / max(1, h)
        crop_bgr = cv2.resize(crop_bgr, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    # pad: detectors dislike text touching the border
    return cv2.copyMakeBorder(crop_bgr, 12, 12, 12, 12, cv2.BORDER_REPLICATE)


# ----------------------------------------------------------- frame access

@dataclass
class ROIRect:
    x0: int
    y0: int
    x1: int
    y1: int

    @classmethod
    def from_norm(cls, r, w: int, h: int) -> "ROIRect":
        x, y, rw, rh = r
        x0 = max(0, int(x * w)) & ~1
        y0 = max(0, int(y * h)) & ~1
        x1 = min(w, int((x + rw) * w)) & ~1
        y1 = min(h, int((y + rh) * h)) & ~1
        return cls(x0, y0, max(x0 + 2, x1), max(y0 + 2, y1))


def _planes(frame, roi: ROIRect, sw: int, sh: int):
    """Downscaled Y, U, V planes of the ROI from a PyAV frame (cheap)."""
    cv2 = _cv2()
    arr = None
    fmt = frame.format.name
    if fmt in ("yuv420p", "yuvj420p"):
        arr = frame.to_ndarray()
        H = frame.height
        W = frame.width
        Y = arr[:H]
        U = arr[H:H + H // 4].reshape(H // 2, W // 2)
        V = arr[H + H // 4:].reshape(H // 2, W // 2)
    else:
        yuv = frame.reformat(format="yuv420p").to_ndarray()
        H, W = frame.height, frame.width
        Y = yuv[:H]
        U = yuv[H:H + H // 4].reshape(H // 2, W // 2)
        V = yuv[H + H // 4:].reshape(H // 2, W // 2)
    y = Y[roi.y0:roi.y1, roi.x0:roi.x1]
    u = U[roi.y0 // 2:roi.y1 // 2, roi.x0 // 2:roi.x1 // 2]
    v = V[roi.y0 // 2:roi.y1 // 2, roi.x0 // 2:roi.x1 // 2]
    ys = cv2.resize(y, (sw, sh), interpolation=cv2.INTER_AREA)
    us = cv2.resize(u, (sw, sh), interpolation=cv2.INTER_LINEAR)
    vs = cv2.resize(v, (sw, sh), interpolation=cv2.INTER_LINEAR)
    return ys, us, vs


def _bgr_crop(frame, roi: ROIRect) -> np.ndarray:
    img = frame.to_ndarray(format="bgr24")
    return np.ascontiguousarray(img[roi.y0:roi.y1, roi.x0:roi.x1])


def read_frame_at(video: str, t_ms: int):
    """Return (bgr full frame, actual time ms) of the frame shown at t_ms."""
    import av

    with av.open(str(video)) as c:
        s = c.streams.video[0]
        tb = float(s.time_base)
        c.seek(int(max(0, t_ms / 1000.0 - 1.0) / tb), stream=s, backward=True, any_frame=False)
        last = None
        for fr in c.decode(s):
            if fr.time is None:
                continue
            if fr.time * 1000 > t_ms + 1:
                break
            last = fr
        if last is None:
            raise RuntimeError("Không đọc được khung hình")
        return last.to_ndarray(format="bgr24"), int(round(last.time * 1000))


def analyze_frame(video: str, t_ms: int, st: OCRSettings, engine: OCREngine | None = None, roi_index: int = 0):
    """For the UI 'test' button: crop, mask preview and OCR text at a time."""
    cv2 = _cv2()
    img, real_t = read_frame_at(video, t_ms)
    H, W = img.shape[:2]
    if not st.rois:
        raise ValueError("Chưa chọn vùng phụ đề")
    roi = ROIRect.from_norm(st.rois[roi_index], W, H)
    crop = img[roi.y0:roi.y1, roi.x0:roi.x1]
    yuv = cv2.cvtColor(crop, cv2.COLOR_BGR2YCrCb)
    y, cr, cb = yuv[..., 0], yuv[..., 1], yuv[..., 2]
    m = text_mask(y, cb, cr, st)
    text, conf = "", 0.0
    if engine is not None:
        text, conf, _ = engine.recognize(prepare_for_ocr(crop))
        if st.to_simplified:
            text = to_simplified(text)
    return {"crop": crop, "mask": (m * 255).astype(np.uint8), "text": text, "conf": conf, "time": real_t,
            "pixels": int(m.sum()), "ratio": float(m.mean())}


# ------------------------------------------------------------- the scanner

@dataclass
class _Run:
    roi: int
    start: float
    start_idx: int
    last_mask: np.ndarray
    ref_mask: np.ndarray | None = None
    frames: int = 1
    crop: np.ndarray | None = None
    first_mask: np.ndarray | None = None
    end: float = 0.0


@dataclass
class OCREvent:
    id: int
    roi: int
    start: int
    end: int
    text: str = ""
    conf: float = 0.0
    crop: str = ""
    frames: int = 0
    extra: dict = field(default_factory=dict)


def scan_video(project: Project, ctx: JobContext, engine_name: str | None = None, cjk_only: bool = True,
               range_ms: tuple[int, int] | None = None) -> list[OCREvent]:
    import av

    st = project.ocr
    if not st.rois:
        raise ValueError("Chưa chọn vùng phụ đề (ROI). Hãy kéo chọn vùng trên khung hình ở tab OCR.")
    video = str(project.video_path)
    crops_dir = project.path("ocr", "crops")
    crops_dir.mkdir(parents=True, exist_ok=True)
    for old in crops_dir.glob("*.jpg"):
        old.unlink()

    container = av.open(video)
    vs = container.streams.video[0]
    vs.thread_type = "AUTO"
    W, H = vs.codec_context.width, vs.codec_context.height
    rois = [ROIRect.from_norm(r, W, H) for r in st.rois]
    sizes = []
    for r in rois:
        rw, rh = r.x1 - r.x0, r.y1 - r.y0
        sw = min(MASK_W, rw)
        sh = max(8, int(round(rh * sw / max(1, rw))))
        sizes.append((sw, sh))
    min_px = [max(12, int(0.002 * sw * sh)) for sw, sh in sizes]
    dur_ms = project.media.duration_ms or (float(vs.duration * vs.time_base) * 1000 if vs.duration else 0)
    fd = 1000.0 / (project.media.fps or 25.0)
    if range_ms:
        container.seek(int(max(0, range_ms[0] / 1000.0 - 1) / float(vs.time_base)), stream=vs, backward=True)

    runs: list[_Run | None] = [None] * len(rois)
    finished: list[_Run] = []
    fragments: list[_Run] = []
    settle = max(2, st.min_frames)
    same_thr = 1.0 - st.change_thr
    idx = 0
    last_t = 0.0

    def close(k: int, t_end: float):
        r = runs[k]
        if r is None:
            return
        r.end = t_end
        (finished if r.frames >= settle and r.crop is not None else fragments).append(r)
        runs[k] = None

    for frame in container.decode(vs):
        if frame.time is None:
            continue
        t = frame.time * 1000.0
        if range_ms and t < range_ms[0]:
            continue
        if range_ms and t > range_ms[1]:
            break
        idx += 1
        if st.frame_step > 1 and idx % st.frame_step:
            continue
        last_t = t
        for k, roi in enumerate(rois):
            ys, us, vv = _planes(frame, roi, *sizes[k])
            m = text_mask(ys, us, vv, st)
            has = int(m.sum()) >= min_px[k]
            r = runs[k]
            if r is None:
                if has:
                    runs[k] = _Run(k, t, idx, m, first_mask=m)
                continue
            sim = mask_similarity(m, r.last_mask) if has else 0.0
            sim_ref = mask_similarity(m, r.ref_mask) if (has and r.ref_mask is not None) else sim
            if has and sim >= same_thr and sim_ref >= same_thr * 0.8:
                r.frames += 1
                r.last_mask = m
                if r.frames == settle:
                    r.ref_mask = m
                    r.crop = _bgr_crop(frame, roi)
            else:
                close(k, t)
                if has:
                    runs[k] = _Run(k, t, idx, m, first_mask=m)
        if idx % 50 == 0 and dur_ms:
            span = (range_ms[1] - range_ms[0]) if range_ms else dur_ms
            base = range_ms[0] if range_ms else 0
            ctx.progress(0.7 * (t - base) / max(1.0, span), f"Quét khung hình {idx} ({t / 1000:.0f}s)")
    for k in range(len(rois)):
        close(k, last_t + fd)
    container.close()

    # merge fade-in/out fragments into neighbouring stable runs
    finished.sort(key=lambda r: (r.roi, r.start))
    fragments.sort(key=lambda r: (r.roi, r.start))
    for fr in fragments:
        for r in finished:
            if r.roi != fr.roi or r.ref_mask is None:
                continue
            if abs(fr.end - r.start) < fd * 0.6 and containment(fr.last_mask, r.ref_mask) >= 0.7:
                r.start = fr.start  # fade-in: subtitle visible from the fragment start
                break
            if abs(fr.start - r.end) < fd * 0.6 and containment(fr.first_mask, r.ref_mask) >= 0.7:
                r.end = fr.end  # fade-out
                break

    # OCR one settled frame per run
    ctx.progress(0.7, "Nhận dạng chữ (OCR)…", force=True)
    engine = make_engine(engine_name or st.engine, cjk_only)
    events: list[OCREvent] = []
    for n, r in enumerate(finished):
        text, conf, _ = engine.recognize(prepare_for_ocr(r.crop))
        if st.to_simplified:
            text = to_simplified(text)
        text = text.strip()
        if text:
            ev = OCREvent(len(events) + 1, r.roi, int(round(r.start)), int(round(r.end)), text, conf, frames=r.frames)
            fn = crops_dir / f"e_{ev.id:05d}.jpg"
            _cv2().imwrite(str(fn), r.crop)
            ev.crop = project.rel(fn)
            events.append(ev)
        ctx.progress(0.7 + 0.28 * (n + 1) / max(1, len(finished)), f"OCR {n + 1}/{len(finished)}")
    events = merge_events(events, fd)
    save_events(project, events)
    return events


def merge_events(events: list[OCREvent], frame_ms: float) -> list[OCREvent]:
    """Merge consecutive events with the same text split by mask flicker."""
    out: list[OCREvent] = []
    for e in sorted(events, key=lambda e: (e.roi, e.start)):
        if out and out[-1].roi == e.roi and e.start - out[-1].end <= frame_ms * 2.5 and similarity(out[-1].text, e.text) >= 0.8:
            p = out[-1]
            if e.conf > p.conf:
                p.text, p.conf, p.crop = e.text, e.conf, e.crop
            p.end = max(p.end, e.end)
            p.frames += e.frames
            continue
        out.append(e)
    for i, e in enumerate(out, 1):
        e.id = i
    return out


def save_events(project: Project, events: list[OCREvent]) -> None:
    data = {"rois": project.ocr.rois, "fps": project.media.fps,
            "events": [e.__dict__ for e in events]}
    project.ocr_json.parent.mkdir(parents=True, exist_ok=True)
    project.ocr_json.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")


def load_events(project: Project) -> list[OCREvent]:
    if not project.ocr_json.exists():
        return []
    d = json.loads(project.ocr_json.read_text("utf-8"))
    return [OCREvent(**{k: v for k, v in e.items() if k in OCREvent.__dataclass_fields__}) for e in d.get("events", [])]
