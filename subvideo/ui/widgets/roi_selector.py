"""Frame view where the user drags rectangles around the subtitle area(s)."""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

from ..theme import C


def bgr_to_qimage(img: np.ndarray) -> QImage:
    if img.ndim == 2:
        h, w = img.shape
        return QImage(np.ascontiguousarray(img).data, w, h, w, QImage.Format_Grayscale8).copy()
    h, w, _ = img.shape
    rgb = np.ascontiguousarray(img[:, :, ::-1])
    return QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()


class ROISelector(QWidget):
    roisChanged = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 270)
        self.pix: QPixmap | None = None
        self.rois: list[list[float]] = []
        self._drag_start: QPointF | None = None
        self._drag_cur: QPointF | None = None
        self.selected = -1
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)

    def set_frame(self, img_bgr: np.ndarray | None):
        self.pix = QPixmap.fromImage(bgr_to_qimage(img_bgr)) if img_bgr is not None else None
        self.update()

    def set_rois(self, rois: list):
        self.rois = [list(map(float, r)) for r in rois]
        self.update()

    def _img_rect(self) -> QRectF:
        if not self.pix:
            return QRectF(self.rect())
        pw, ph = self.pix.width(), self.pix.height()
        s = min(self.width() / pw, self.height() / ph)
        w, h = pw * s, ph * s
        return QRectF((self.width() - w) / 2, (self.height() - h) / 2, w, h)

    def _to_norm(self, p: QPointF) -> tuple[float, float]:
        r = self._img_rect()
        return (min(1.0, max(0.0, (p.x() - r.x()) / r.width())), min(1.0, max(0.0, (p.y() - r.y()) / r.height())))

    def _from_norm(self, roi) -> QRectF:
        r = self._img_rect()
        x, y, w, h = roi
        return QRectF(r.x() + x * r.width(), r.y() + y * r.height(), w * r.width(), h * r.height())

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#000000"))
        r = self._img_rect()
        if self.pix:
            p.drawPixmap(r.toRect(), self.pix)
        else:
            p.setPen(QColor(C["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Chưa có khung hình — hãy nhập video ở bước 1")
        for i, roi in enumerate(self.rois):
            rr = self._from_norm(roi)
            col = QColor(C["accent"] if i != self.selected else C["warn"])
            fill = QColor(col)
            fill.setAlpha(45)
            p.fillRect(rr, fill)
            p.setPen(QPen(col, 2))
            p.drawRect(rr)
            p.drawText(rr.topLeft() + QPointF(4, -4), f"Vùng {i + 1}")
        if self._drag_start and self._drag_cur:
            p.setPen(QPen(QColor(C["ok"]), 2, Qt.DashLine))
            p.drawRect(QRectF(self._drag_start, self._drag_cur).normalized())
        p.end()

    def mousePressEvent(self, e):
        pos = e.position()
        for i, roi in enumerate(self.rois):
            if self._from_norm(roi).contains(pos):
                self.selected = i
                self.update()
                if e.button() == Qt.RightButton:
                    self.rois.pop(i)
                    self.selected = -1
                    self.roisChanged.emit(self.rois)
                    self.update()
                return
        if e.button() == Qt.LeftButton:
            self._drag_start = pos
            self._drag_cur = pos

    def mouseMoveEvent(self, e):
        if self._drag_start is not None:
            self._drag_cur = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        if self._drag_start is None:
            return
        a = self._to_norm(self._drag_start)
        b = self._to_norm(e.position())
        self._drag_start = self._drag_cur = None
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        if (x1 - x0) > 0.02 and (y1 - y0) > 0.015:
            self.rois.append([round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)])
            self.selected = len(self.rois) - 1
            self.roisChanged.emit(self.rois)
        self.update()

    def remove_selected(self):
        if 0 <= self.selected < len(self.rois):
            self.rois.pop(self.selected)
            self.selected = -1
            self.roisChanged.emit(self.rois)
            self.update()
