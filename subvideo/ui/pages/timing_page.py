"""Step 9 — pacing: stretch voice / borrow silence / slow video."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFormLayout, QGridLayout, QLabel

from ...pipeline.stretch import method as stretch_method
from ..widgets.common import card, dspin, hint, primary, spin
from ..widgets.segment_table import SegmentTable
from .base import BasePage


class TimingPage(BasePage):
    step_id = "timing"
    title = "Căn nhịp"
    icon = "⏱"
    subtitle = ("Đặt từng câu lồng tiếng vào đúng chỗ nhân vật nói, ưu tiên giữ tốc độ tự nhiên: mượn khoảng lặng trước/sau → "
                "tạo lại giọng đúng thời lượng → co giãn giọng (giữ cao độ) → làm chậm video cục bộ (cuối cùng). "
                "Câu vẫn quá dài sẽ được đánh dấu để rút gọn lời dịch.")

    def build(self):
        grid = QGridLayout()
        g, lay = card("Giới hạn")
        f = QFormLayout()
        self.lead = spin(0, 1000, 200, " ms", 10)
        self.tail = spin(0, 2000, 500, " ms", 10)
        self.gap = spin(0, 500, 80, " ms", 10)
        self.maxsp = dspin(1.0, 1.6, 1.20, 0.01)
        self.hardsp = dspin(1.0, 2.0, 1.35, 0.01)
        self.minsp = dspin(0.7, 1.0, 1.0, 0.01)
        self.regen = QCheckBox("Tạo lại giọng đúng thời lượng thay vì kéo giãn (OmniVoice)")
        self.vslow = QCheckBox("Cho phép làm chậm video ở đoạn câu quá dài")
        self.maxvs = dspin(0.6, 1.0, 0.85, 0.01)
        self.rate = dspin(3.0, 8.0, 5.0, 0.1, 1, " âm tiết/giây")
        f.addRow("Được nói sớm hơn tối đa:", self.lead)
        f.addRow("Được nói kéo dài sau tối đa:", self.tail)
        f.addRow("Khoảng nghỉ tối thiểu giữa 2 câu:", self.gap)
        f.addRow("Nói nhanh hơn tối đa (tự nhiên):", self.maxsp)
        f.addRow("Nói nhanh hơn tối đa (bắt buộc):", self.hardsp)
        f.addRow("Cho phép nói chậm hơn tới:", self.minsp)
        f.addRow("", self.regen)
        f.addRow("", self.vslow)
        f.addRow("Tốc độ video chậm nhất:", self.maxvs)
        f.addRow("Nhịp nói mặc định:", self.rate)
        lay.addLayout(f)
        self.method_lbl = hint("")
        lay.addWidget(self.method_lbl)
        b = self.action(primary("▶ Căn nhịp"))
        b.clicked.connect(lambda: self.run("timing", {}, "Căn nhịp"))
        lay.addWidget(b, 0, Qt.AlignLeft)
        grid.addWidget(g, 0, 0)
        g2, l2 = card("Báo cáo")
        self.report = QLabel("Chưa căn nhịp.")
        self.report.setWordWrap(True)
        self.report.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l2.addWidget(self.report)
        l2.addWidget(hint("Màu trên dòng thời gian (tab Chỉnh sửa): xanh = tự nhiên, vàng = co giãn giọng, cam = làm chậm video, "
                          "đỏ = cần rút gọn lời. Kéo khối lồng tiếng để tự đặt vị trí (khoá câu)."))
        l2.addStretch(1)
        grid.addWidget(g2, 0, 1)
        self.body.addLayout(grid)
        self.table = SegmentTable(columns=["id", "start", "end", "who", "vi_dub", "syl", "pace", "flags"])
        self.table.setMinimumHeight(360)
        self.body.addWidget(self.table, 1)

    def load_project(self, project):
        self.table.smodel.set_project(project)
        try:
            self.method_lbl.setText("Co giãn giọng bằng: " + {"rubberband-r3": "Rubber Band R3 (chất lượng cao nhất)",
                                                              "ffmpeg-rubberband": "Rubber Band (qua FFmpeg)",
                                                              "atempo": "FFmpeg atempo (nên cài Rubber Band)"}[stretch_method()])
        except Exception:
            pass
        if not project:
            return
        t = project.timing
        self.lead.setValue(t.max_lead_ms)
        self.tail.setValue(t.max_tail_ms)
        self.gap.setValue(t.min_gap_ms)
        self.maxsp.setValue(t.max_speedup)
        self.hardsp.setValue(t.hard_max_speedup)
        self.minsp.setValue(t.min_speed)
        self.regen.setChecked(t.regen_with_duration)
        self.vslow.setChecked(t.allow_video_slow)
        self.maxvs.setValue(t.max_video_slow)
        self.rate.setValue(t.default_rate)
        self.report.setText(project.param("timing", "report", "Chưa căn nhịp."))

    def save_project(self, project):
        t = project.timing
        t.max_lead_ms, t.max_tail_ms, t.min_gap_ms = self.lead.value(), self.tail.value(), self.gap.value()
        t.max_speedup, t.hard_max_speedup, t.min_speed = self.maxsp.value(), self.hardsp.value(), self.minsp.value()
        t.regen_with_duration = self.regen.isChecked()
        t.allow_video_slow = self.vslow.isChecked()
        t.max_video_slow = self.maxvs.value()
        t.default_rate = self.rate.value()
