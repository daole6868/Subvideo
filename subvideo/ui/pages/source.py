"""Step 1 — project & video source (local file or download)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox)

from ...core.timeutil import ms_to_clock
from ..widgets.common import button, card, combo, combo_value, hint, primary
from .base import BasePage


class SourcePage(BasePage):
    step_id = "source"
    title = "Dự án & tải video"
    icon = "🎞"
    subtitle = ("Chọn video trên máy hoặc dán link (YouTube, Bilibili, TikTok, Douyin, Facebook…). "
                "Video có DRM (iQIYI/Youku/WeTV trả phí) không tải được.")

    def build(self):
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        # --- local file
        g1, l1 = card("Video trên máy")
        self.copy_cb = QCheckBox("Sao chép video vào thư mục dự án (an toàn khi di chuyển dự án)")
        b_local = self.action(primary("📂 Chọn file video…"))
        b_local.clicked.connect(self._pick_local)
        l1.addWidget(hint("Hỗ trợ mp4, mkv, mov, flv, webm, ts… Nên dùng bản chất lượng cao nhất có thể."))
        l1.addWidget(self.copy_cb)
        l1.addWidget(b_local, 0, Qt.AlignLeft)
        grid.addWidget(g1, 0, 0)
        # --- download
        g2, l2 = card("Tải từ đường link")
        self.url = QLineEdit()
        self.url.setPlaceholderText("https://www.bilibili.com/video/… hoặc https://www.youtube.com/watch?v=…")
        form = QFormLayout()
        self.cookies = combo([("", "Không dùng"), ("chrome", "Chrome"), ("edge", "Edge"), ("firefox", "Firefox"),
                              ("brave", "Brave"), ("opera", "Opera")], "")
        self.quality = combo([(0, "Tốt nhất"), (2160, "≤ 4K"), (1080, "≤ 1080p"), (720, "≤ 720p")], 0)
        self.subs_cb = QCheckBox("Tải kèm phụ đề có sẵn (nếu có) — dùng thay cho OCR")
        self.subs_cb.setChecked(True)
        form.addRow("Đường link:", self.url)
        form.addRow("Cookie trình duyệt:", self.cookies)
        form.addRow("Chất lượng:", self.quality)
        l2.addLayout(form)
        l2.addWidget(self.subs_cb)
        l2.addWidget(hint("Cookie giúp tải video cần đăng nhập / chất lượng cao bằng tài khoản của chính bạn "
                          "(Bilibili 1080p+). YouTube cần cài Deno hoặc Node.js để giải mã."))
        b_dl = self.action(primary("⬇ Tải video"))
        b_dl.clicked.connect(self._download)
        l2.addWidget(b_dl, 0, Qt.AlignLeft)
        grid.addWidget(g2, 0, 1)
        self.body.addLayout(grid)
        # --- info
        g3, l3 = card("Thông tin video")
        h = QHBoxLayout()
        self.thumb = QLabel()
        self.thumb.setFixedSize(384, 216)
        self.thumb.setStyleSheet("background:#000; border-radius:8px;")
        self.thumb.setAlignment(Qt.AlignCenter)
        self.info = QLabel("Chưa có video.")
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.info.setWordWrap(True)
        h.addWidget(self.thumb)
        h.addWidget(self.info, 1)
        l3.addLayout(h)
        self.subs_list = QListWidget()
        self.subs_list.setMaximumHeight(90)
        self.subs_hint = hint("Phụ đề tải kèm: chọn một file rồi bấm “Dùng làm phụ đề gốc” (bỏ qua OCR, ghép với tiếng nói).")
        b_use = self.action(button("Dùng làm phụ đề gốc"))
        b_use.clicked.connect(self._use_subs)
        l3.addWidget(self.subs_hint)
        l3.addWidget(self.subs_list)
        l3.addWidget(b_use, 0, Qt.AlignLeft)
        self.body.addWidget(g3)
        # --- recent
        g4, l4 = card("Dự án gần đây")
        self.recent = QListWidget()
        self.recent.setMaximumHeight(140)
        self.recent.itemDoubleClicked.connect(lambda it: self.main.open_project(it.data(Qt.UserRole)))
        l4.addWidget(self.recent)
        l4.addWidget(hint("Nhấp đúp để mở. Mỗi tập phim nên là một dự án; hồ sơ nhân vật có thể nhập lại giữa các tập."))
        self.body.addWidget(g4)
        self.body.addStretch(1)

    def on_show(self):
        self._fill_recent()

    def _fill_recent(self):
        self.recent.clear()
        for p in self.main.settings["recent_projects"]:
            it = QListWidgetItem(f"📁 {Path(p).name}   —   {p}")
            it.setData(Qt.UserRole, p)
            self.recent.addItem(it)

    def load_project(self, project):
        self._fill_recent()
        self.subs_list.clear()
        if not project or not project.media.path:
            self.info.setText("Chưa có video. Hãy chọn file hoặc tải từ link." if project else
                              "Hãy tạo dự án mới (＋ Dự án mới) hoặc mở dự án có sẵn.")
            self.thumb.clear()
            return
        m = project.media
        vfr = "<br><b style='color:#f5b94a'>⚠ Video có khung hình biến thiên (VFR)</b> — SubVideo dùng mốc thời gian thực của từng khung nên vẫn chính xác." if m.vfr else ""
        self.info.setText(
            f"<b>{Path(m.path).name}</b><br>Thời lượng: {ms_to_clock(m.duration_ms)}<br>"
            f"Hình: {m.width}×{m.height}, {m.fps:.3f} fps, {m.video_codec} {m.pix_fmt} ({m.bit_depth}-bit)<br>"
            f"Âm thanh: {m.audio_codec or 'không có'} {m.audio_channels} kênh {m.audio_rate} Hz"
            + (f"<br>Nguồn: {project.source_url}" if project.source_url else "") + vfr)
        self._thumb(project)
        for s in project.param("source", "downloaded_subs", []) or []:
            it = QListWidgetItem(Path(s).name)
            it.setData(Qt.UserRole, s)
            self.subs_list.addItem(it)
        self.subs_list.setVisible(self.subs_list.count() > 0)
        self.subs_hint.setVisible(self.subs_list.count() > 0)

    def _thumb(self, project):
        try:
            from ...core.media import grab_frame

            out = project.path("cache", "thumb.jpg")
            if not out.exists():
                grab_frame(project.video_path, min(5000, project.media.duration_ms // 3), out, width=640)
            pm = QPixmap(str(out))
            self.thumb.setPixmap(pm.scaled(self.thumb.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        except Exception:
            self.thumb.setText("(không tạo được ảnh xem trước)")

    def _ensure_project(self) -> bool:
        if self.project:
            return True
        self.main.new_project_dialog()
        return self.project is not None

    def _pick_local(self):
        if not self._ensure_project():
            return
        f, _ = QFileDialog.getOpenFileName(self, "Chọn video", "", "Video (*.mp4 *.mkv *.mov *.flv *.webm *.ts *.avi *.m4v);;Tất cả (*)")
        if not f:
            return
        self.project.path("cache", "thumb.jpg").unlink(missing_ok=True)
        self.run("import_media", {"file": f, "copy": self.copy_cb.isChecked()}, "Nhập video")

    def _download(self):
        url = self.url.text().strip()
        if not url:
            QMessageBox.information(self, "Thiếu link", "Hãy dán đường link video.")
            return
        if not self._ensure_project():
            return
        self.project.path("cache", "thumb.jpg").unlink(missing_ok=True)
        self.run("download", {"url": url, "cookies_browser": combo_value(self.cookies),
                              "max_height": combo_value(self.quality), "subtitles": self.subs_cb.isChecked()},
                 "Tải video")

    def _use_subs(self):
        it = self.subs_list.currentItem()
        if not it or not self.project:
            return
        path = str(self.project.abs(it.data(Qt.UserRole)))
        self.project.set_param("fusion", "subtitle_file", path)
        self.main.save_project(quiet=True)
        QMessageBox.information(self, "Đã chọn phụ đề", "Đã đặt file phụ đề này làm nguồn chữ ở bước Hợp nhất "
                                "(thay cho OCR).\nBạn vẫn nên chạy bước Nghe để căn thời gian chính xác theo tiếng nói.")
