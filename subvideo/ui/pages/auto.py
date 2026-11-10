"""Automatic run: every step up to (and NOT including) export."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout, QLabel, QListWidget, QListWidgetItem,
                               QMessageBox)

from ...core.project import Project
from ...pipeline.llm import PROVIDERS
from ...pipeline.stages import AUTO_STEPS
from ...pipeline.transcribe import ASR_ENGINES
from ...pipeline.tts import ENGINES
from ..widgets.common import button, card, combo, combo_value, hint, hrow, primary, set_combo
from .base import BasePage


class AutoPage(BasePage):
    step_id = None
    title = "Tự động"
    icon = "⚡"
    subtitle = ("Chạy liên tục mọi bước từ đầu tới Trộn âm rồi DỪNG — không bao giờ tự xuất video. "
                "Sau đó bạn kiểm tra ở tab Chỉnh sửa và tự bấm Xuất.")

    def build(self):
        grid = QGridLayout()
        g, lay = card("Các bước sẽ chạy")
        self.checks: dict[str, QCheckBox] = {}
        self.opts: dict[str, QComboBox] = {}
        for key, label in AUTO_STEPS:
            cb = QCheckBox(label.replace("&", "&&"))
            cb.setChecked(True)
            self.checks[key] = cb
            row = [cb, None]
            if key == "transcribe":
                self.opts["asr"] = combo(ASR_ENGINES)
                row = [cb, self.opts["asr"], None]
            elif key == "translate":
                self.opts["provider"] = combo(PROVIDERS)
                row = [cb, self.opts["provider"], None]
            elif key == "tts":
                self.opts["tts"] = combo(ENGINES)
                row = [cb, self.opts["tts"], None]
            elif key == "ocr":
                self.ocr_note = QLabel("")
                self.ocr_note.setObjectName("Hint")
                row = [cb, self.ocr_note, None]
            lay.addLayout(hrow(*row))
        for c in self.opts.values():
            c.setMaximumWidth(330)
        lay.addStretch(1)
        grid.addWidget(g, 0, 0)

        g2, l2 = card("Chạy")
        self.start_from = combo([("", "Từ đầu")] + list(AUTO_STEPS))
        self.loop = QCheckBox("Tự rút gọn câu quá dài, lồng lại, căn nhịp lại")
        self.loop.setChecked(True)
        l2.addLayout(hrow(QLabel("Bắt đầu từ:"), self.start_from, None))
        l2.addWidget(self.loop)
        b = self.action(primary("⚡ Chạy tự động (dừng trước khi Xuất)"))
        b.setMinimumHeight(44)
        b.clicked.connect(self._run_current)
        l2.addWidget(b)
        l2.addWidget(hint("• Bỏ chọn “Quét phụ đề (OCR)” nếu video không có phụ đề cứng.\n"
                          "• “Phân tích nhân vật” chỉ chạy khi dự án chưa có hồ sơ (để không ghi đè hồ sơ bạn đã sửa).\n"
                          "• Có thể bấm ■ Dừng trên thanh trên cùng bất kỳ lúc nào; kết quả các bước đã xong được giữ lại."))
        self.steps_view = QListWidget()
        self.steps_view.setMinimumHeight(220)
        l2.addWidget(QLabel("Tiến trình:"))
        l2.addWidget(self.steps_view)
        grid.addWidget(g2, 0, 1)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)

        g3, l3 = card("Hàng đợi nhiều dự án (nhiều tập)")
        self.queue = QListWidget()
        self.queue.setMaximumHeight(150)
        b_add = button("＋ Thêm dự án…")
        b_add.clicked.connect(self._add_queue)
        b_del = button("Xoá khỏi hàng đợi")
        b_del.clicked.connect(lambda: self.queue.takeItem(self.queue.currentRow()))
        b_runq = self.action(button("⚡ Chạy tự động cho cả hàng đợi"))
        b_runq.clicked.connect(self._run_queue)
        l3.addWidget(self.queue)
        l3.addLayout(hrow(b_add, b_del, None, b_runq))
        l3.addWidget(hint("Mỗi dự án dùng thiết lập riêng đã lưu (vùng OCR, hồ sơ nhân vật…). Nhập hồ sơ nhân vật của tập 1 "
                          "cho các tập sau ở bước Nhân vật để dịch nhất quán."))
        self.body.addWidget(g3)
        self.body.addStretch(1)
        self.main.jobs.log.connect(self._on_log)

    def load_project(self, project):
        s = self.main.settings
        set_combo(self.opts["asr"], (project.param("transcribe", "engine") if project else None) or s.default("asr"))
        set_combo(self.opts["provider"], (project.param("translate", "provider") if project else None) or s["translate_provider"])
        set_combo(self.opts["tts"], (project.param("tts", "engine") if project else None) or s.default("tts"))
        has_roi = bool(project and project.ocr.rois)
        self.ocr_note.setText("" if has_roi else "(chưa chọn vùng phụ đề — sẽ bỏ qua)")
        auto = project.param("auto", "steps") if project else None
        if auto:
            for k, cb in self.checks.items():
                cb.setChecked(k in auto)

    def save_project(self, project):
        project.set_param("auto", "steps", [k for k, cb in self.checks.items() if cb.isChecked()])
        project.set_param("transcribe", "engine", combo_value(self.opts["asr"]))
        project.set_param("translate", "provider", combo_value(self.opts["provider"]))
        prov_model = self.main.settings.provider(combo_value(self.opts["provider"])).get("model", "")
        if not project.param("translate", "model") or project.param("translate", "provider") != combo_value(self.opts["provider"]):
            project.set_param("translate", "model", prov_model)
        project.set_param("tts", "engine", combo_value(self.opts["tts"]))

    def _params(self) -> dict:
        steps = [k for k, cb in self.checks.items() if cb.isChecked()]
        return {"steps": steps, "start_from": combo_value(self.start_from) or None, "shorten_loop": self.loop.isChecked()}

    def _run_current(self):
        if not self.project:
            QMessageBox.information(self, "Chưa có dự án", "Hãy tạo/mở dự án và chọn video trước.")
            return
        if not self.project.media.path:
            QMessageBox.information(self, "Chưa có video", "Hãy chọn video ở bước 1.")
            return
        self.steps_view.clear()
        self.run("auto", self._params(), "Chạy tự động", self._done)

    def _done(self, ok, res):
        it = QListWidgetItem(("🏁 " if ok else "✖ ") + (res.splitlines()[-1] if res else ""))
        self.steps_view.addItem(it)
        if ok:
            QMessageBox.information(self, "Tự động xong", res)
            self.main.go("EditorPage")
        else:
            QMessageBox.warning(self, "Tự động dừng", res)

    def _on_log(self, level, msg):
        if not self.main.jobs.busy():
            return
        if re.match(r"^(▶ Bước|✔|↻)", msg):
            self.steps_view.addItem(msg)
            self.steps_view.scrollToBottom()

    def _add_queue(self):
        d = QFileDialog.getExistingDirectory(self, "Chọn thư mục dự án")
        if d and Project.is_project(d):
            it = QListWidgetItem(Path(d).name)
            it.setData(Qt.UserRole, d)
            self.queue.addItem(it)
        elif d:
            QMessageBox.warning(self, "Không phải dự án", "Thư mục này không có project.json.")

    def _run_queue(self):
        if self.queue.count() == 0:
            return
        self.main.save_project(quiet=True)
        params = self._params()
        for i in range(self.queue.count()):
            d = self.queue.item(i).data(Qt.UserRole)
            self.main.jobs.submit("auto", d, params, f"Tự động: {Path(d).name}")
        self.steps_view.addItem(f"Đã xếp {self.queue.count()} dự án vào hàng đợi.")
