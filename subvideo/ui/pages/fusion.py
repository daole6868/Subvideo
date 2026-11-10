"""Step 5 — fuse OCR (what we see) with ASR (what we hear) into final lines."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFormLayout, QGridLayout, QMessageBox

from ..widgets.common import PathEdit, button, card, combo, combo_value, hint, hrow, primary, set_combo, spin, stat_box
from ..widgets.segment_table import SegmentTable
from .base import BasePage

MODES = [("auto", "Tự động (OCR + nghe nếu có cả hai)"),
         ("fused", "Kết hợp chữ trên hình + tiếng nói (chính xác nhất)"),
         ("ocr", "Chỉ chữ trên hình (căn theo năng lượng giọng)"),
         ("asr", "Chỉ tiếng nói (video không có phụ đề)")]


class FusionPage(BasePage):
    step_id = "fusion"
    title = "Hợp nhất phụ đề"
    icon = "🔗"
    subtitle = ("Ghép chữ đọc từ hình (đúng chữ) với chữ nghe được (đúng thời điểm từng chữ), rồi tinh chỉnh mép câu theo "
                "năng lượng giọng nói (~10 ms). Câu nào hình và tiếng không khớp sẽ được đánh dấu để bạn xem lại.")

    def build(self):
        grid = QGridLayout()
        g, lay = card("Cách ghép")
        form = QFormLayout()
        self.mode = combo(MODES, "auto")
        self.disp = combo([("speech", "Theo tiếng nói (khuyên dùng)"), ("ocr", "Giữ đúng thời gian phụ đề gốc")], "speech")
        self.maxc = spin(6, 40, 16, " chữ")
        self.shots = QCheckBox("Phát hiện cắt cảnh để phụ đề không vắt qua cảnh")
        self.shots.setChecked(True)
        self.subfile = PathEdit(mode="file", filter="Phụ đề (*.srt *.ass *.vtt)", placeholder="(tuỳ chọn) dùng file phụ đề thay cho OCR")
        form.addRow("Chế độ:", self.mode)
        form.addRow("Thời gian hiển thị:", self.disp)
        form.addRow("Tối đa mỗi câu (tiếng Trung):", self.maxc)
        form.addRow("File phụ đề gốc:", self.subfile)
        lay.addLayout(form)
        lay.addWidget(self.shots)
        b = self.action(primary("▶ Hợp nhất"))
        b.clicked.connect(self._run)
        lay.addWidget(b, 0, Qt.AlignLeft)
        grid.addWidget(g, 0, 0)

        g2, l2 = card("Quy tắc hiển thị phụ đề")
        f2 = QFormLayout()
        self.lead = spin(0, 500, 0, " ms", 10)
        self.linger = spin(0, 1500, 300, " ms", 50)
        self.mind = spin(200, 3000, 700, " ms", 50)
        self.maxd = spin(2000, 15000, 7000, " ms", 500)
        self.gap = spin(0, 10, 2, " khung")
        self.snap = spin(0, 1000, 250, " ms", 10)
        self.cpl = spin(20, 80, 42, " ký tự")
        self.lines = spin(1, 3, 2, " dòng")
        f2.addRow("Hiện sớm trước lời:", self.lead)
        f2.addRow("Giữ thêm sau lời:", self.linger)
        f2.addRow("Thời lượng tối thiểu:", self.mind)
        f2.addRow("Thời lượng tối đa:", self.maxd)
        f2.addRow("Khoảng cách tối thiểu:", self.gap)
        f2.addRow("Hút vào điểm cắt cảnh trong:", self.snap)
        f2.addRow("Ký tự mỗi dòng (Việt):", self.cpl)
        f2.addRow("Số dòng tối đa:", self.lines)
        l2.addLayout(f2)
        b2 = self.action(button("Chỉ tính lại thời gian hiển thị"))
        b2.clicked.connect(lambda: self.run("display_times", {"keep_manual": False}, "Tính lại thời gian hiển thị"))
        l2.addWidget(b2, 0, Qt.AlignLeft)
        grid.addWidget(g2, 0, 1)
        self.body.addLayout(grid)

        stats = hrow()
        self.s_total = stat_box("Tổng số dòng")
        self.s_fused = stat_box("Khớp hình + tiếng")
        self.s_flag = stat_box("Cần xem lại")
        self.s_screen = stat_box("Chữ trên hình (không lồng)")
        for f, _ in (self.s_total, self.s_fused, self.s_flag, self.s_screen):
            stats.addWidget(f)
        self.body.addLayout(stats)
        self.table = SegmentTable(columns=["id", "start", "end", "who", "zh", "flags"])
        self.table.setMinimumHeight(340)
        self.body.addWidget(self.table, 1)
        self.body.addWidget(hint("Nhấp đúp để sửa trực tiếp. Sửa chi tiết, nghe từng câu và kéo mép thời gian ở tab “Chỉnh sửa & căn chỉnh”."))

    def load_project(self, project):
        self.table.smodel.set_project(project)
        if not project:
            return
        set_combo(self.mode, project.param("fusion", "mode", "auto"))
        set_combo(self.disp, project.param("fusion", "display_from", "speech"))
        self.maxc.setValue(int(project.param("fusion", "max_chars", 16)))
        self.shots.setChecked(bool(project.param("fusion", "detect_shots", True)))
        self.subfile.setText(project.param("fusion", "subtitle_file", ""))
        d = project.display
        self.lead.setValue(d.lead_in_ms)
        self.linger.setValue(d.linger_ms)
        self.mind.setValue(d.min_duration_ms)
        self.maxd.setValue(d.max_duration_ms)
        self.gap.setValue(d.min_gap_frames)
        self.snap.setValue(d.snap_shot_ms)
        self.cpl.setValue(d.max_chars_line)
        self.lines.setValue(d.max_lines)
        segs = project.segments
        self.s_total[1].setText(str(len(segs)))
        self.s_fused[1].setText(str(sum(1 for s in segs if s.zh_source == "fused")))
        self.s_flag[1].setText(str(sum(1 for s in segs if s.flags)))
        self.s_screen[1].setText(str(sum(1 for s in segs if s.kind == "onscreen")))

    def save_project(self, project):
        project.set_param("fusion", "mode", combo_value(self.mode))
        project.set_param("fusion", "display_from", combo_value(self.disp))
        project.set_param("fusion", "max_chars", self.maxc.value())
        project.set_param("fusion", "detect_shots", self.shots.isChecked())
        project.set_param("fusion", "subtitle_file", self.subfile.text())
        d = project.display
        d.lead_in_ms, d.linger_ms = self.lead.value(), self.linger.value()
        d.min_duration_ms, d.max_duration_ms = self.mind.value(), self.maxd.value()
        d.min_gap_frames, d.snap_shot_ms = self.gap.value(), self.snap.value()
        d.max_chars_line, d.max_lines = self.cpl.value(), self.lines.value()

    def _run(self):
        p = self.project
        if p and any(s.vi_sub for s in p.segments):
            r = QMessageBox.question(self, "Hợp nhất lại?",
                                     "Dự án đã có bản dịch. Hợp nhất lại sẽ tạo lại danh sách câu "
                                     "(câu nào giữ nguyên chữ Trung sẽ giữ bản dịch; bản cũ được sao lưu project.json.bak).\n\nTiếp tục?")
            if r != QMessageBox.Yes:
                return
        self.run("fusion", {}, "Hợp nhất phụ đề")
