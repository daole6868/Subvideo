"""Step 11 — high quality export."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QCheckBox, QColorDialog, QFontComboBox, QFormLayout, QGridLayout, QLineEdit, QMessageBox, QPushButton

from ...core import media
from ...core.timeutil import ms_to_clock, parse_time
from ...pipeline.export import VIDEO_PRESETS, default_output
from ..widgets.common import PathEdit, button, card, combo, combo_value, dspin, hint, hrow, open_path, primary, set_combo, spin
from .base import BasePage


class ColorButton(QPushButton):
    def __init__(self, color: str = "#FFFFFF"):
        super().__init__()
        self.color = color
        self.setFixedWidth(60)
        self.clicked.connect(self._pick)
        self._paint()

    def _paint(self):
        self.setStyleSheet(f"background:{self.color}; border:1px solid #888; border-radius:6px; min-height:22px;")

    def _pick(self):
        c = QColorDialog.getColor(QColor(self.color), self)
        if c.isValid():
            self.color = c.name().upper()
            self._paint()

    def set(self, color: str):
        self.color = color
        self._paint()


class ExportPage(BasePage):
    step_id = "export"
    title = "Xuất video"
    icon = "📦"
    subtitle = ("Xuất video chất lượng cao bằng FFmpeg với đúng bản trộn âm và phụ đề bạn đã nghe/xem. "
                "Có thể xem trước chính xác một đoạn 20 giây trước khi xuất toàn bộ.")

    def build(self):
        grid = QGridLayout()
        g, lay = card("Hình ảnh")
        f = QFormLayout()
        self.container = combo([("mp4", "MP4 (phổ biến)"), ("mkv", "MKV (nhiều track, phụ đề ASS)")])
        self.vmode = combo(list(VIDEO_PRESETS.items()))
        self.crf = spin(10, 35, 17)
        self.crf.setToolTip("Số càng nhỏ chất lượng càng cao (17 ≈ gần như không phân biệt được với gốc)")
        self.preset = combo([("medium", "medium"), ("slow", "slow (khuyên dùng)"), ("slower", "slower"), ("veryslow", "veryslow")], "slow")
        f.addRow("Định dạng:", self.container)
        f.addRow("Bộ mã hoá:", self.vmode)
        f.addRow("Chất lượng (CRF/CQ):", self.crf)
        f.addRow("Độ kỹ khi nén:", self.preset)
        lay.addLayout(f)
        self.enc_hint = hint("")
        lay.addWidget(self.enc_hint)
        grid.addWidget(g, 0, 0)

        g2, l2 = card("Phụ đề")
        f2 = QFormLayout()
        self.subs = combo([("burn", "In cứng vào hình"), ("soft", "Phụ đề rời (bật/tắt được)"), ("both", "Cả hai"), ("none", "Không")])
        self.bilingual = QCheckBox("Song ngữ (tiếng Trung nhỏ phía trên)")
        self.font = QFontComboBox()
        self.size = spin(0, 200, 0)
        self.size.setSpecialValueText("Tự động")
        self.c_text = ColorButton("#FFFFFF")
        self.c_out = ColorButton("#000000")
        self.outline = dspin(0, 8, 2.5, 0.5, 1)
        self.shadow = dspin(0, 6, 1, 0.5, 1)
        self.box = QCheckBox("Nền hộp tối sau chữ")
        self.margin = spin(0, 400, 0)
        self.margin.setSpecialValueText("Tự động")
        f2.addRow("Kiểu phụ đề:", self.subs)
        f2.addRow("", self.bilingual)
        f2.addRow("Phông chữ:", self.font)
        f2.addRow("Cỡ chữ:", self.size)
        f2.addRow("Màu chữ / viền:", hrow(self.c_text, self.c_out, None))
        f2.addRow("Độ dày viền / bóng:", hrow(self.outline, self.shadow, None))
        f2.addRow("", self.box)
        f2.addRow("Cách mép dưới:", self.margin)
        l2.addLayout(f2)
        grid.addWidget(g2, 0, 1)

        g3, l3 = card("Phụ đề gốc tiếng Trung trên hình")
        f3 = QFormLayout()
        self.cover = combo([("none", "Giữ nguyên"), ("blur", "Làm mờ vùng phụ đề gốc"), ("box", "Phủ dải tối")])
        self.cover_when = combo([("subtitle", "Chỉ lúc có phụ đề gốc"), ("always", "Suốt video")])
        f3.addRow("Xử lý:", self.cover)
        f3.addRow("Khi nào:", self.cover_when)
        l3.addLayout(f3)
        l3.addWidget(hint("Dùng vùng đã chọn ở bước OCR. Muốn xoá sạch bằng AI (inpainting) có thể dùng công cụ ngoài như "
                          "video-subtitle-remover rồi nhập lại video."))
        grid.addWidget(g3, 1, 0)

        g4, l4 = card("Âm thanh && tệp ra")
        f4 = QFormLayout()
        self.acodec = combo([("aac", "AAC 320 kbps"), ("flac", "FLAC lossless (MKV)"), ("opus", "Opus 256 kbps (MKV)")])
        self.extra_orig = QCheckBox("Thêm track âm thanh gốc (người xem tự chọn)")
        self.srt = QCheckBox("Lưu kèm file .srt/.ass")
        self.out = PathEdit(mode="save", filter="Video (*.mp4 *.mkv)")
        f4.addRow("Âm thanh:", self.acodec)
        f4.addRow("", self.extra_orig)
        f4.addRow("", self.srt)
        f4.addRow("Lưu tại:", self.out)
        l4.addLayout(f4)
        grid.addWidget(g4, 1, 1)
        self.body.addLayout(grid)

        g5, l5 = card("Xuất")
        self.prev_at = QLineEdit("00:00.000")
        self.prev_at.setMaximumWidth(120)
        b_prev = self.action(button("👁 Xem trước chính xác 20 giây từ"))
        b_prev.clicked.connect(self._preview)
        b_exp = self.action(primary("📦 Xuất video"))
        b_exp.clicked.connect(self._export)
        b_open = button("📂 Mở thư mục xuất")
        b_open.clicked.connect(lambda: self.project and open_path(self.project.path("export")))
        l5.addLayout(hrow(b_prev, self.prev_at, None, b_open, b_exp))
        l5.addWidget(hint("Xem trước dùng đúng chuỗi xử lý của bản xuất (giãn video, che phụ đề gốc, phụ đề, bản trộn) ở chất lượng nhanh."))
        self.body.addWidget(g5)
        self.body.addStretch(1)

    def load_project(self, project):
        try:
            nv = media.has_encoder("h264_nvenc")
            self.enc_hint.setText("GPU NVIDIA: " + ("có NVENC ✔" if nv else "không thấy NVENC trong FFmpeg") +
                                  "  •  “Giữ nguyên hình” chỉ dùng được khi không in phụ đề, không che, không giãn video.")
        except Exception:
            pass
        if not project:
            return
        e, st = project.export, project.style
        set_combo(self.container, e.container)
        set_combo(self.vmode, e.video_mode)
        self.crf.setValue(e.crf)
        set_combo(self.preset, e.preset)
        set_combo(self.subs, e.subs)
        self.bilingual.setChecked(st.bilingual)
        self.font.setCurrentText(st.font)
        self.size.setValue(st.size)
        self.c_text.set(st.primary)
        self.c_out.set(st.outline_color)
        self.outline.setValue(st.outline)
        self.shadow.setValue(st.shadow)
        self.box.setChecked(st.box)
        self.margin.setValue(st.margin_v)
        set_combo(self.cover, e.cover)
        set_combo(self.cover_when, e.cover_when)
        set_combo(self.acodec, e.audio_codec)
        self.extra_orig.setChecked("original" in e.extra_tracks)
        self.srt.setChecked(e.export_srt)
        self.out.setText(e.output or str(default_output(project)))

    def save_project(self, project):
        e, st = project.export, project.style
        e.container = combo_value(self.container)
        e.video_mode = combo_value(self.vmode)
        e.crf = self.crf.value()
        e.preset = combo_value(self.preset)
        e.subs = combo_value(self.subs)
        st.bilingual = self.bilingual.isChecked()
        st.font = self.font.currentText()
        st.size = self.size.value()
        st.primary, st.outline_color = self.c_text.color, self.c_out.color
        st.outline, st.shadow = self.outline.value(), self.shadow.value()
        st.box = self.box.isChecked()
        st.margin_v = self.margin.value()
        e.cover = combo_value(self.cover)
        e.cover_when = combo_value(self.cover_when)
        e.audio_codec = combo_value(self.acodec)
        e.extra_tracks = ["original"] if self.extra_orig.isChecked() else []
        e.export_srt = self.srt.isChecked()
        out = self.out.text()
        if out:
            suffix = "." + e.container
            if Path(out).suffix.lower() != suffix:
                out = str(Path(out).with_suffix(suffix))
                self.out.setText(out)
        e.output = out

    def _preview(self):
        try:
            t0 = parse_time(self.prev_at.text())
        except ValueError:
            t0 = 0

        def done(ok, res):
            if ok:
                open_path(res.strip())
            else:
                QMessageBox.warning(self, "Xem trước lỗi", res)

        self.run("preview_clip", {"start_ms": t0, "end_ms": t0 + 20000}, "Xem trước chính xác", done)

    def _export(self):
        p = self.project
        if not p:
            return
        if not p.segments:
            if QMessageBox.question(self, "Chưa có phụ đề", "Dự án chưa có câu nào. Vẫn xuất?") != QMessageBox.Yes:
                return

        def done(ok, res):
            if ok:
                r = QMessageBox.information(self, "Xuất xong", res + "\n\nMở thư mục chứa video?",
                                            QMessageBox.Open | QMessageBox.Close)
                if r == QMessageBox.Open:
                    open_path(Path(self.project.export.output or default_output(self.project)).parent)
            else:
                QMessageBox.warning(self, "Xuất lỗi", res)

        self.run("export", {}, "Xuất video", done)

    def set_preview_time(self, ms: int):
        self.prev_at.setText(ms_to_clock(ms))
