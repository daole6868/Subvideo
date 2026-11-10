"""Step 4 — read burned-in subtitles from a user-selected region."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFormLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox, QSlider, QSplitter, QVBoxLayout, QWidget)

from ...core.timeutil import ms_to_clock
from ...pipeline.ocr import analyze_frame, load_events, make_engine, read_frame_at
from ..widgets.common import button, card, combo, combo_value, dspin, hint, hrow, primary, set_combo, spin
from ..widgets.roi_selector import ROISelector, bgr_to_qimage
from .base import BasePage

COLORS = [("white", "Chữ trắng"), ("yellow", "Chữ vàng"), ("auto", "Chữ sáng (mọi màu)"), ("custom", "Màu tự chọn")]


class OCRPage(BasePage):
    step_id = "ocr"
    title = "Quét phụ đề (OCR)"
    icon = "🔍"
    subtitle = ("Kéo chuột chọn vùng có phụ đề (có thể chọn nhiều vùng, nhấp phải để xoá). SubVideo xem TỪNG khung hình "
                "trong vùng này để biết chính xác khung nào phụ đề xuất hiện/biến mất, rồi đọc chữ một lần cho mỗi câu.")
    scroll = True

    def build(self):
        self._engine = None
        top = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.roi = ROISelector()
        self.roi.setMinimumHeight(360)
        self.roi.roisChanged.connect(self._rois_changed)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.time_lbl = QLabel("00:00.000")
        self.slider.valueChanged.connect(lambda v: self.time_lbl.setText(ms_to_clock(v)))
        self._frame_timer = QTimer(self)
        self._frame_timer.setSingleShot(True)
        self._frame_timer.timeout.connect(self._load_frame)
        self.slider.valueChanged.connect(lambda _: self._frame_timer.start(180))
        b_prev = button("◀ 1s")
        b_next = button("1s ▶")
        b_prev.clicked.connect(lambda: self.slider.setValue(self.slider.value() - 1000))
        b_next.clicked.connect(lambda: self.slider.setValue(self.slider.value() + 1000))
        b_del = button("Xoá vùng đang chọn")
        b_del.clicked.connect(self.roi.remove_selected)
        b_bottom = button("Gợi ý: đáy khung hình", "Đặt nhanh một vùng ở 1/5 dưới khung hình")
        b_bottom.clicked.connect(self._suggest)
        ll.addWidget(self.roi, 1)
        ll.addLayout(hrow(b_prev, self.slider, b_next, self.time_lbl))
        ll.addLayout(hrow(b_bottom, b_del, None))
        top.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        g, lay = card("Nhận biết chữ phụ đề")
        form = QFormLayout()
        self.color = combo(COLORS, "white")
        self.luma = spin(80, 255, 190)
        self.outline = QCheckBox("Chữ có viền tối (đa số phim)")
        self.change = dspin(0.05, 0.9, 0.35, 0.05)
        self.minf = spin(1, 10, 3)
        self.step = spin(1, 5, 1)
        self.engine = combo([("rapidocr", "RapidOCR (PP-OCR, nhẹ, có sẵn mô hình)"), ("paddleocr", "PaddleOCR 3 (chính xác hơn, nặng)")])
        self.simpl = QCheckBox("Đổi phồn thể → giản thể")
        self.cjk = QCheckBox("Chỉ giữ dòng có chữ Hán (bỏ dòng tiếng Anh)")
        form.addRow("Màu chữ:", self.color)
        form.addRow("Độ sáng tối thiểu:", self.luma)
        form.addRow("", self.outline)
        form.addRow("Ngưỡng đổi câu:", self.change)
        form.addRow("Số khung tối thiểu:", self.minf)
        form.addRow("Bước khung (1 = mọi khung):", self.step)
        form.addRow("Bộ đọc chữ:", self.engine)
        lay.addLayout(form)
        lay.addWidget(self.simpl)
        lay.addWidget(self.cjk)
        b_test = button("🧪 Thử tại khung này")
        b_test.clicked.connect(self._test)
        lay.addWidget(b_test)
        self.mask_lbl = QLabel()
        self.mask_lbl.setMinimumHeight(60)
        self.mask_lbl.setAlignment(Qt.AlignCenter)
        self.test_out = hint("Bấm “Thử” để xem vùng chữ được nhận biết (màu trắng) và chữ đọc được.")
        lay.addWidget(self.mask_lbl)
        lay.addWidget(self.test_out)
        b_run = self.action(primary("▶ Quét toàn bộ video"))
        b_run.clicked.connect(self._run)
        lay.addWidget(b_run)
        lay.addWidget(hint("Độ chính xác thời gian = 1 khung hình (40 ms ở 25 fps). Bước Hợp nhất sẽ ghép với tiếng nói "
                           "để có mốc chính xác tới vài mili giây."))
        rl.addWidget(g)
        rl.addStretch(1)
        top.addWidget(right)
        top.setSizes([760, 420])
        self.body.addWidget(top)

        g2, l2 = card("Các dòng phụ đề đọc được")
        self.events = QListWidget()
        self.events.setIconSize(QSize(260, 40))
        self.events.setMinimumHeight(260)
        self.events.itemDoubleClicked.connect(lambda it: self.slider.setValue(int(it.data(Qt.UserRole)) + 80))
        self.ev_summary = hint("")
        l2.addWidget(self.ev_summary)
        l2.addWidget(self.events)
        self.body.addWidget(g2)

    # ------------------------------------------------------------------
    def load_project(self, project):
        self.events.clear()
        if not project:
            self.roi.set_frame(None)
            return
        o = project.ocr
        set_combo(self.color, o.text_color)
        self.luma.setValue(o.luma_thr)
        self.outline.setChecked(o.need_outline)
        self.change.setValue(o.change_thr)
        self.minf.setValue(o.min_frames)
        self.step.setValue(o.frame_step)
        set_combo(self.engine, o.engine)
        self.simpl.setChecked(o.to_simplified)
        self.cjk.setChecked(bool(project.param("ocr", "cjk_only", True)))
        self.roi.set_rois(o.rois)
        dur = max(1000, project.media.duration_ms)
        self.slider.blockSignals(True)
        self.slider.setRange(0, dur)
        if self.slider.value() == 0:
            self.slider.setValue(min(dur // 3, 60_000))
        self.slider.blockSignals(False)
        self.time_lbl.setText(ms_to_clock(self.slider.value()))
        self._loaded_video = None
        evs = load_events(project)
        self.ev_summary.setText(f"{len(evs)} dòng" if evs else "Chưa quét.")
        for e in evs[:3000]:
            it = QListWidgetItem(f"{ms_to_clock(e.start)} → {ms_to_clock(e.end)}   {e.text}   ({e.conf:.0%})")
            it.setData(Qt.UserRole, e.start)
            if e.crop:
                pm = QPixmap(str(project.abs(e.crop)))
                if not pm.isNull():
                    it.setIcon(QIcon(pm))
            self.events.addItem(it)

    def on_show(self):
        if self.project and self.project.media.path and getattr(self, "_loaded_video", None) != self.project.media.path:
            self._load_frame()

    def save_project(self, project):
        o = project.ocr
        o.text_color = combo_value(self.color)
        o.luma_thr = self.luma.value()
        o.need_outline = self.outline.isChecked()
        o.change_thr = self.change.value()
        o.min_frames = self.minf.value()
        o.frame_step = self.step.value()
        o.engine = combo_value(self.engine)
        o.to_simplified = self.simpl.isChecked()
        o.rois = self.roi.rois
        project.set_param("ocr", "cjk_only", self.cjk.isChecked())

    def _rois_changed(self, rois):
        if self.project:
            self.project.ocr.rois = rois

    def _suggest(self):
        self.roi.set_rois([[0.08, 0.78, 0.84, 0.18]])
        self._rois_changed(self.roi.rois)

    def _load_frame(self):
        p = self.project
        if not p or not p.media.path or not p.video_path.exists():
            return
        try:
            img, t = read_frame_at(str(p.video_path), self.slider.value())
            self.roi.set_frame(img)
            self._loaded_video = p.media.path
        except Exception as e:
            self.test_out.setText(f"Không đọc được khung hình: {e}")

    def _test(self):
        p = self.project
        if not p:
            return
        self.save_project(p)
        if not p.ocr.rois:
            QMessageBox.information(self, "Chưa chọn vùng", "Hãy kéo chuột trên khung hình để chọn vùng phụ đề.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            if self._engine is None or self._engine.name != p.ocr.engine:
                self._engine = make_engine(p.ocr.engine, self.cjk.isChecked())
            idx = max(0, self.roi.selected)
            r = analyze_frame(str(p.video_path), self.slider.value(), p.ocr, self._engine, idx)
            pm = QPixmap.fromImage(bgr_to_qimage(r["mask"]))
            self.mask_lbl.setPixmap(pm.scaledToWidth(min(400, max(100, pm.width())), Qt.SmoothTransformation))
            self.test_out.setText(f"Chữ đọc được: <b>{r['text'] or '(không có)'}</b> — độ tin cậy {r['conf']:.0%} — "
                                  f"điểm ảnh chữ: {r['pixels']} ({r['ratio']:.1%}). Nếu vùng trắng không trùng chữ, "
                                  "chỉnh màu chữ / độ sáng / viền.")
        except Exception as e:
            self.test_out.setText(f"Lỗi: {e}")
        finally:
            QApplication.restoreOverrideCursor()

    def _run(self):
        if not self.roi.rois:
            QMessageBox.information(self, "Chưa chọn vùng", "Hãy kéo chuột trên khung hình để chọn vùng phụ đề.")
            return
        self.run("ocr", {}, "Quét phụ đề (OCR)")
