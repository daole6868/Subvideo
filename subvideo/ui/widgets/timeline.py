"""Waveform + multi-track timeline with draggable, millisecond-accurate edges.

Tracks (top to bottom):
  ruler | waveform (vocals) | Thoại (speech timing) | Phụ đề (display timing) | Lồng tiếng (dub placement)
Zoom down to 1 ms per pixel (Ctrl + wheel), scroll with the wheel.
"""

from __future__ import annotations

import bisect
import math

import numpy as np
from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ...core.project import Segment
from ...core.timeutil import ms_to_clock, snap_to_frame
from ..theme import C

RULER_H = 20
TRACK_H = 26
SUB_H = 22
EDGE_PX = 6
TICKS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000, 300000, 600000]


class Timeline(QWidget):
    seekRequested = Signal(int)
    segmentSelected = Signal(int)
    segmentActivated = Signal(int)
    segmentEdited = Signal(int)  # speech or display edges changed (segment object already updated)
    placementMoved = Signal(int)
    viewChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumHeight(190)
        self.setFocusPolicy(Qt.StrongFocus)
        self.mins = np.zeros(0, np.float32)
        self.maxs = np.zeros(0, np.float32)
        self.rate = 200
        self.duration = 60_000
        self.view_start = 0.0
        self.mpp = 20.0  # ms per pixel
        self.playhead = 0
        self.segments: list[Segment] = []
        self._starts: list[int] = []
        self.shots: list[int] = []
        self.events: list[tuple[int, int]] = []
        self.fps = 25.0
        self.selected: int | None = None
        self.follow = True
        self.snap_display_to_frames = True
        self._drag = None  # (kind, seg, edge, offset)
        self._font = QFont("Segoe UI", 8)

    # ----------------------------------------------------------- data
    def set_audio_peaks(self, mins: np.ndarray, maxs: np.ndarray, rate: int, duration_ms: int):
        self.mins, self.maxs, self.rate = mins, maxs, rate
        self.duration = max(1000, int(duration_ms))
        self.update()

    def set_segments(self, segs: list[Segment], fps: float = 25.0):
        self.segments = sorted(segs, key=lambda s: s.start)
        self._starts = [s.start for s in self.segments]
        self.fps = fps or 25.0
        self.update()

    def set_shots(self, shots: list[int]):
        self.shots = sorted(shots or [])
        self.update()

    def set_events(self, events: list[tuple[int, int]]):
        self.events = sorted(events or [])
        self.update()

    def set_playhead(self, ms: int):
        self.playhead = int(ms)
        if self.follow:
            w = self.width() * self.mpp
            if ms < self.view_start or ms > self.view_start + w * 0.92:
                self.view_start = max(0.0, ms - w * 0.1)
                self.viewChanged.emit()
        self.update()

    def select(self, seg_id: int | None, center: bool = True):
        self.selected = seg_id
        if center and seg_id is not None:
            s = next((x for x in self.segments if x.id == seg_id), None)
            if s:
                self.ensure_visible(s.start, s.end)
        self.update()

    def ensure_visible(self, a: int, b: int | None = None):
        b = a if b is None else b
        w = self.width() * self.mpp
        if a < self.view_start or b > self.view_start + w:
            span = b - a
            if span > w * 0.8:
                self.mpp = max(1.0, span / (self.width() * 0.8))
                w = self.width() * self.mpp
            self.view_start = max(0.0, a - (w - span) / 2)
            self.viewChanged.emit()
        self.update()

    def set_zoom(self, mpp: float, anchor_ms: float | None = None):
        anchor_ms = self.playhead if anchor_ms is None else anchor_ms
        frac = (anchor_ms - self.view_start) / max(1.0, self.width() * self.mpp)
        self.mpp = float(min(2000.0, max(1.0, mpp)))
        self.view_start = max(0.0, anchor_ms - frac * self.width() * self.mpp)
        self.viewChanged.emit()
        self.update()

    # --------------------------------------------------------- geometry
    def x_of(self, ms: float) -> float:
        return (ms - self.view_start) / self.mpp

    def ms_at(self, x: float) -> int:
        return int(round(self.view_start + x * self.mpp))

    def _rows(self):
        h = self.height()
        wave_h = max(50, h - RULER_H - TRACK_H * 2 - SUB_H - 8)
        y_wave = RULER_H
        y_speech = y_wave + wave_h + 2
        y_sub = y_speech + TRACK_H + 2
        y_dub = y_sub + SUB_H + 2
        return {"wave": (y_wave, wave_h), "speech": (y_speech, TRACK_H), "sub": (y_sub, SUB_H), "dub": (y_dub, TRACK_H)}

    def _track_at(self, y: float) -> str:
        for k, (y0, h) in self._rows().items():
            if y0 <= y < y0 + h:
                return k
        return "ruler" if y < RULER_H else ""

    def _visible(self) -> list[Segment]:
        a = self.view_start - 60_000
        b = self.view_start + self.width() * self.mpp
        i = max(0, bisect.bisect_left(self._starts, a))
        out = []
        for s in self.segments[i:]:
            if s.start > b:
                break
            out.append(s)
        return out

    # ------------------------------------------------------------ paint
    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(C["panel"]))
        rows = self._rows()
        p.setFont(self._font)
        fm = QFontMetrics(self._font)
        self._paint_ruler(p, w)
        self._paint_wave(p, w, *rows["wave"])
        # OCR events (where the original subtitle is on screen) in the wave area
        y0, wh = rows["wave"]
        for a, b in self.events:
            x0, x1 = self.x_of(a), self.x_of(b)
            if x1 < 0 or x0 > w:
                continue
            p.fillRect(QRectF(x0, y0, x1 - x0, 4), QColor(C["muted"]))
        # shot changes
        pen = QPen(QColor(C["warn"]))
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        for t in self.shots:
            x = self.x_of(t)
            if 0 <= x <= w:
                p.drawLine(QLineF(x, y0, x, y0 + wh))
        # track backgrounds + labels
        for key, label in (("speech", "Thoại"), ("sub", "Phụ đề"), ("dub", "Lồng tiếng")):
            ty, th = rows[key]
            p.fillRect(QRectF(0, ty, w, th), QColor(C["panel2"]))
        vis = self._visible()
        for s in vis:
            self._paint_seg(p, fm, s, rows)
        for key, label in (("speech", "Thoại"), ("sub", "Phụ đề"), ("dub", "Lồng tiếng")):
            ty, th = rows[key]
            lw = fm.horizontalAdvance(label) + 10
            bg = QColor(C["bg"])
            bg.setAlpha(210)
            p.fillRect(QRectF(0, ty + 3, lw, th - 6), bg)
            p.setPen(QColor(C["muted"]))
            p.drawText(QRectF(5, ty, lw, th), Qt.AlignVCenter | Qt.AlignLeft, label)
        # playhead
        x = self.x_of(self.playhead)
        p.setPen(QPen(QColor(C["err"]), 1.5))
        p.drawLine(QLineF(x, 0, x, h))
        p.end()

    def _paint_ruler(self, p: QPainter, w: int):
        p.fillRect(QRectF(0, 0, w, RULER_H), QColor(C["bg"]))
        step = next((t for t in TICKS if t / self.mpp >= 80), TICKS[-1])
        first = math.floor(self.view_start / step) * step
        p.setPen(QColor(C["muted"]))
        t = first
        end = self.view_start + w * self.mpp
        while t <= end:
            x = self.x_of(t)
            p.drawLine(QLineF(x, RULER_H - 6, x, RULER_H))
            label = ms_to_clock(t, with_ms=step < 1000)
            p.drawText(QPointF(x + 3, RULER_H - 7), label)
            sub = step / 5
            for k in range(1, 5):
                xs = self.x_of(t + k * sub)
                p.drawLine(QLineF(xs, RULER_H - 3, xs, RULER_H))
            t += step

    def _paint_wave(self, p: QPainter, w: int, y0: int, wh: int):
        p.fillRect(QRectF(0, y0, w, wh), QColor(C["input"]))
        n = len(self.maxs)
        mid = y0 + wh / 2
        p.setPen(QColor(C["border"]))
        p.drawLine(QLineF(0, mid, w, mid))
        if n == 0:
            return
        xs = np.arange(w + 1, dtype=np.float64)
        t = self.view_start + xs * self.mpp
        idx = np.clip((t / 1000.0 * self.rate).astype(np.int64), 0, n)
        valid = idx[:-1] < n
        lines = []
        amp = wh / 2 * 0.95
        starts = np.minimum(idx[:-1], n - 1)
        if self.mpp * self.rate / 1000.0 >= 1.0:
            end = int(min(n, max(idx[-1], starts[-1] + 1)))
            mx = np.maximum.reduceat(self.maxs[:end], starts)
            mn = np.minimum.reduceat(self.mins[:end], starts)
        else:
            mx = self.maxs[starts]
            mn = self.mins[starts]
        mx = np.clip(mx, -1, 1)
        mn = np.clip(mn, -1, 1)
        for x in range(w):
            if not valid[x]:
                break
            lines.append(QLineF(x, mid - mx[x] * amp, x, mid - mn[x] * amp))
        col = QColor(C["accent"])
        col.setAlpha(200)
        p.setPen(col)
        p.drawLines(lines)

    def _seg_color(self, s: Segment) -> QColor:
        if s.kind == "onscreen":
            return QColor("#6b7280")
        if s.kind in ("nonverbal", "song"):
            return QColor("#8b5cf6")
        if s.flags:
            return QColor(C["warn"])
        if s.reviewed:
            return QColor(C["ok"])
        return QColor(C["accent"])

    def _paint_seg(self, p: QPainter, fm: QFontMetrics, s: Segment, rows):
        w = self.width()
        sel = s.id == self.selected
        # speech
        ty, th = rows["speech"]
        x0, x1 = self.x_of(s.start), self.x_of(s.end)
        if x1 >= 0 and x0 <= w:
            col = self._seg_color(s)
            fill = QColor(col)
            fill.setAlpha(150 if sel else 90)
            r = QRectF(x0, ty + 2, max(2.0, x1 - x0), th - 4)
            p.fillRect(r, fill)
            p.setPen(QPen(col.lighter(140) if sel else col, 2 if sel else 1))
            p.drawRect(r)
            if r.width() > 24:
                p.setPen(QColor(C["text"]))
                p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           fm.elidedText(f"{s.id} {s.zh}", Qt.ElideRight, int(r.width() - 6)))
        # display
        ty, th = rows["sub"]
        a, b = s.display_start, s.display_end
        x0, x1 = self.x_of(a), self.x_of(b)
        if (s.vi_sub or s.zh) and x1 >= 0 and x0 <= w:
            col = QColor("#14b8a6")
            fill = QColor(col)
            fill.setAlpha(130 if sel else 70)
            r = QRectF(x0, ty + 2, max(2.0, x1 - x0), th - 4)
            p.fillRect(r, fill)
            p.setPen(QPen(col, 2 if sel else 1))
            p.drawRect(r)
            if r.width() > 24:
                p.setPen(QColor(C["text"]))
                p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft,
                           fm.elidedText(s.vi_sub or "", Qt.ElideRight, int(r.width() - 6)))
        # dub placement
        if s.place_start is not None and s.has_dub():
            ty, th = rows["dub"]
            span = (s.slow_region[1] - s.slow_region[0]) if s.slow_region else s.place_dur
            x0, x1 = self.x_of(s.place_start), self.x_of(s.place_start + span)
            if x1 >= 0 and x0 <= w:
                if "need_shorten" in s.flags:
                    col = QColor(C["err"])
                elif s.slow_region:
                    col = QColor("#fb923c")
                elif abs(s.stretch - 1.0) > 0.03:
                    col = QColor(C["warn"])
                else:
                    col = QColor(C["ok"])
                fill = QColor(col)
                fill.setAlpha(140 if sel else 80)
                r = QRectF(x0, ty + 2, max(2.0, x1 - x0), th - 4)
                p.fillRect(r, fill)
                p.setPen(QPen(col, 2 if (sel or s.locked) else 1, Qt.DashLine if s.locked else Qt.SolidLine))
                p.drawRect(r)
                if r.width() > 30:
                    tag = f"×{s.stretch:.2f} " if abs(s.stretch - 1) > 0.005 else ""
                    if s.slow_region:
                        tag += f"🎞{s.slow_region[2]:.2f} "
                    p.setPen(QColor(C["text"]))
                    p.drawText(r.adjusted(4, 0, -2, 0), Qt.AlignVCenter | Qt.AlignLeft,
                               fm.elidedText(tag + s.vi_dub, Qt.ElideRight, int(r.width() - 6)))

    # ------------------------------------------------------------ mouse
    def _hit(self, x: float, track: str):
        best = None
        for s in reversed(self._visible()):
            if track == "speech":
                a, b = s.start, s.end
            elif track == "sub":
                a, b = s.display_start, s.display_end
            elif track == "dub" and s.place_start is not None and s.has_dub():
                a = s.place_start
                b = a + ((s.slow_region[1] - s.slow_region[0]) if s.slow_region else s.place_dur)
            else:
                continue
            xa, xb = self.x_of(a), self.x_of(b)
            if track != "dub" and abs(x - xa) <= EDGE_PX:
                return s, "start"
            if track != "dub" and abs(x - xb) <= EDGE_PX:
                return s, "end"
            if xa <= x <= xb and best is None:
                best = (s, "body")
        return best if best else (None, None)

    def mousePressEvent(self, e):
        x, y = e.position().x(), e.position().y()
        track = self._track_at(y)
        if e.button() != Qt.LeftButton:
            return
        if track in ("speech", "sub", "dub"):
            s, part = self._hit(x, track)
            if s is not None:
                self.selected = s.id
                self.segmentSelected.emit(s.id)
                if part in ("start", "end"):
                    self._drag = ("edge", s, track, part)
                elif track == "dub":
                    self._drag = ("move", s, track, self.ms_at(x) - s.place_start)
                self.update()
                return
        self._drag = ("scrub", None, None, None)
        self.seekRequested.emit(max(0, self.ms_at(x)))

    def mouseDoubleClickEvent(self, e):
        track = self._track_at(e.position().y())
        if track in ("speech", "sub", "dub"):
            s, _ = self._hit(e.position().x(), track)
            if s is not None:
                self.segmentActivated.emit(s.id)

    def mouseMoveEvent(self, e):
        x, y = e.position().x(), e.position().y()
        if self._drag is None:
            track = self._track_at(y)
            if track in ("speech", "sub"):
                s, part = self._hit(x, track)
                self.setCursor(Qt.SizeHorCursor if part in ("start", "end") else Qt.ArrowCursor)
            elif track == "dub":
                s, part = self._hit(x, track)
                self.setCursor(Qt.OpenHandCursor if s is not None else Qt.ArrowCursor)
            else:
                self.setCursor(Qt.ArrowCursor)
            return
        kind, s, track, extra = self._drag
        t = max(0, self.ms_at(x))
        if kind == "scrub":
            self.seekRequested.emit(t)
            return
        if kind == "edge":
            if track == "sub" and self.snap_display_to_frames and not (e.modifiers() & Qt.AltModifier):
                t = snap_to_frame(t, self.fps)
            if track == "speech":
                if extra == "start":
                    s.start = min(t, s.end - 40)
                else:
                    s.end = max(t, s.start + 40)
            else:
                if extra == "start":
                    s.disp_start = min(t, s.display_end - 40)
                    if s.disp_end is None:
                        s.disp_end = s.end
                else:
                    s.disp_end = max(t, s.display_start + 40)
                    if s.disp_start is None:
                        s.disp_start = s.start
            self.seekRequested.emit(t)
        elif kind == "move":
            s.place_start = max(0, t - extra)
            s.locked = True
        self.update()

    def mouseReleaseEvent(self, e):
        if self._drag is None:
            return
        kind, s, track, _ = self._drag
        self._drag = None
        if kind == "edge" and s is not None:
            self._starts = [x.start for x in self.segments]
            self.segmentEdited.emit(s.id)
        elif kind == "move" and s is not None:
            self.placementMoved.emit(s.id)

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if e.modifiers() & Qt.ControlModifier:
            factor = 0.8 if d > 0 else 1.25
            self.set_zoom(self.mpp * factor, self.ms_at(e.position().x()))
        else:
            self.view_start = max(0.0, self.view_start - d / 120.0 * self.width() * self.mpp * 0.15)
            self.follow = False
            self.viewChanged.emit()
            self.update()
