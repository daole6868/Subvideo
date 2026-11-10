"""Step 7 — translation (API or local LLM)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGridLayout, QMessageBox

from ...pipeline.llm import PROVIDERS
from ..widgets.common import button, card, combo, combo_value, hint, hrow, primary, set_combo, spin
from ..widgets.segment_table import SegmentTable
from .base import BasePage


class TranslatePage(BasePage):
    step_id = "translate"
    title = "Dịch"
    icon = "🌐"
    subtitle = ("Dịch theo từng cảnh với hồ sơ nhân vật + bảng xưng hô + thuật ngữ. Mỗi câu có 2 bản: phụ đề (đủ ý, dễ đọc) "
                "và lời lồng tiếng (văn nói, không vượt số âm tiết cho phép để nhân vật nói đều nhịp).")

    def build(self):
        grid = QGridLayout()
        g, lay = card("Mô hình dịch")
        f = QFormLayout()
        self.provider = combo(PROVIDERS)
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumWidth(240)
        b_models = button("↻", "Lấy danh sách model từ nhà cung cấp")
        b_models.setFixedWidth(34)
        b_models.clicked.connect(self._list_models)
        self.batch = spin(5, 120, 40, " câu/lần")
        self.ctx = spin(0, 30, 8, " câu")
        f.addRow("Nhà cung cấp:", self.provider)
        f.addRow("Model:", hrow(self.model, b_models))
        f.addRow("Số câu mỗi lần gửi:", self.batch)
        f.addRow("Ngữ cảnh câu trước:", self.ctx)
        lay.addLayout(f)
        lay.addWidget(hint("Khoá API nhập ở tab Cài đặt. Model local: chọn “Ollama” và tên model (vd. qwen3:14b)."))
        self.provider.currentIndexChanged.connect(self._provider_changed)
        grid.addWidget(g, 0, 0)

        g2, l2 = card("Thực hiện")
        b_all = self.action(primary("▶ Dịch toàn bộ"))
        b_all.clicked.connect(lambda: self._translate(True))
        b_new = self.action(button("Dịch các câu chưa dịch"))
        b_new.clicked.connect(lambda: self._translate(False))
        b_short = self.action(button("✂ Rút gọn câu lồng tiếng quá dài"))
        b_short.clicked.connect(self._shorten)
        self.qa_apply = QCheckBox("Tự áp dụng gợi ý sửa của AI")
        b_qa = self.action(button("🔎 Soát bản dịch (xưng hô, thuật ngữ, sai nghĩa)"))
        b_qa.clicked.connect(lambda: self.run("qa", {"auto_apply": self.qa_apply.isChecked()}, "Soát bản dịch"))
        b_chars = button("👥 Mở hồ sơ nhân vật")
        b_chars.clicked.connect(lambda: self.main.go("CharactersPage"))
        l2.addWidget(b_all)
        l2.addWidget(b_new)
        l2.addWidget(b_short)
        l2.addWidget(b_qa)
        l2.addWidget(self.qa_apply)
        l2.addWidget(b_chars)
        l2.addWidget(hint("Nên chạy “Phân tích bằng AI” ở bước Nhân vật trước để có bảng xưng hô — đó là chìa khoá dịch đúng vai."))
        grid.addWidget(g2, 0, 1)
        self.body.addLayout(grid)
        self.filter = combo([("all", "Tất cả"), ("flagged", "Cần xem lại"), ("dub", "Có lồng tiếng")])
        self.filter.currentIndexChanged.connect(lambda: self.table.proxy.set_mode(combo_value(self.filter)))
        self.body.addLayout(hrow(hint("Hiển thị:", False), self.filter, None))
        self.table = SegmentTable(columns=["id", "who", "zh", "vi_sub", "vi_dub", "syl", "flags"])
        self.table.setMinimumHeight(420)
        self.body.addWidget(self.table, 1)

    def _provider_changed(self):
        prov = combo_value(self.provider)
        cfg = self.main.settings.provider(prov)
        self.model.setCurrentText(cfg.get("model", ""))

    def load_project(self, project):
        self.table.smodel.set_project(project)
        s = self.main.settings
        prov = (project.param("translate", "provider") if project else None) or s["translate_provider"]
        set_combo(self.provider, prov)
        model = (project.param("translate", "model") if project else None) or s.provider(prov).get("model", "")
        self.model.setCurrentText(model)
        if project:
            self.batch.setValue(int(project.param("translate", "batch_size", 40)))
            self.ctx.setValue(int(project.param("translate", "context_lines", 8)))

    def save_project(self, project):
        project.set_param("translate", "provider", combo_value(self.provider))
        project.set_param("translate", "model", self.model.currentText().strip())
        project.set_param("translate", "batch_size", self.batch.value())
        project.set_param("translate", "context_lines", self.ctx.value())

    def _translate(self, overwrite: bool):
        p = self.project
        if not p or not p.segments:
            QMessageBox.information(self, "Chưa có câu", "Hãy chạy bước Hợp nhất phụ đề trước.")
            return
        if overwrite and any(s.vi_sub for s in p.segments):
            if QMessageBox.question(self, "Dịch lại toàn bộ?", "Các bản dịch hiện tại sẽ bị thay thế. Tiếp tục?") != QMessageBox.Yes:
                return
        self.run("translate", {"overwrite": overwrite}, "Dịch")

    def _shorten(self):
        p = self.project
        if not p:
            return
        from ...core.textutil import count_vi_syllables

        ids = [s.id for s in p.segments if s.vi_dub and ("need_shorten" in s.flags or
               (s.max_syllables and count_vi_syllables(s.vi_dub) > s.max_syllables))]
        if not ids:
            QMessageBox.information(self, "Không có câu dài", "Không có câu lồng tiếng nào vượt số âm tiết cho phép.")
            return
        self.run("shorten", {"ids": ids}, f"Rút gọn {len(ids)} câu")

    def _list_models(self):
        from PySide6.QtWidgets import QApplication

        from ...pipeline.llm import LLMClient

        prov = combo_value(self.provider)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            models = LLMClient(prov, self.model.currentText() or "x").list_models()
            cur = self.model.currentText()
            self.model.clear()
            self.model.addItems(models)
            self.model.setCurrentText(cur)
        except Exception as e:
            QMessageBox.warning(self, "Không lấy được danh sách", str(e))
        finally:
            QApplication.restoreOverrideCursor()
