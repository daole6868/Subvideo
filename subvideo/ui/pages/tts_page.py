"""Step 8 — dubbing voices (TTS)."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QGridLayout, QHeaderView, QTableWidget, QTableWidgetItem

from ...pipeline.tts import EDGE_VOICES, ENGINES, VIENEU_PRESETS, engine_for
from ..widgets.common import button, card, combo, combo_value, hint, play_clip, primary, set_combo
from ..widgets.segment_table import SegmentTable
from .base import BasePage


class TTSPage(BasePage):
    step_id = "tts"
    title = "Lồng tiếng"
    icon = "🎙"
    subtitle = ("Tạo giọng Việt cho từng câu theo giọng của nhân vật (nhái giọng từ mẫu hoặc giọng có sẵn). "
                "Câu nào đổi lời/giọng mới được tạo lại.")

    def build(self):
        grid = QGridLayout()
        g, lay = card("Thiết lập chung")
        f = QFormLayout()
        self.engine = combo(ENGINES, "omnivoice")
        self.preset = QComboBox()
        self.preset.setEditable(True)
        self.preset.addItems([""] + VIENEU_PRESETS + EDGE_VOICES)
        self.verify = QCheckBox("Nghe lại bằng AI để phát hiện câu đọc sai (chậm hơn)")
        f.addRow("Engine mặc định:", self.engine)
        f.addRow("Giọng mặc định (nhân vật chưa gán):", self.preset)
        lay.addLayout(f)
        lay.addWidget(self.verify)
        lay.addWidget(hint("• OmniVoice: nhái giọng xuyên ngôn ngữ + đặt đúng thời lượng → khớp nhịp tốt nhất. "
                           "Mẫu tiếng Trung có thể làm giọng Việt hơi lơ lớ — khi đó dùng VieNeu hoặc mẫu giọng Việt.\n"
                           "• VieNeu-TTS: giọng Việt tự nhiên nhất, 25 giọng có sẵn; tốc độ được chỉnh ở bước Căn nhịp.\n"
                           "• Giọng riêng từng nhân vật chỉnh ở bước Nhân vật."))
        grid.addWidget(g, 0, 0)
        g2, l2 = card("Thực hiện")
        b1 = self.action(primary("▶ Lồng tiếng (câu mới/đã sửa)"))
        b1.clicked.connect(lambda: self.run("tts", {"verify": self.verify.isChecked()}, "Lồng tiếng"))
        b2 = self.action(button("Tạo lại toàn bộ"))
        b2.clicked.connect(lambda: self.run("tts", {"force": True, "verify": self.verify.isChecked()}, "Lồng tiếng lại toàn bộ"))
        b3 = self.action(button("Tạo lại câu đang chọn"))
        b3.clicked.connect(self._redo_selected)
        b4 = button("▶ Nghe câu đang chọn")
        b4.clicked.connect(self._play_selected)
        for b in (b1, b2, b3, b4):
            l2.addWidget(b)
        grid.addWidget(g2, 0, 1)
        self.body.addLayout(grid)
        g3, l3 = card("Giọng của từng nhân vật")
        self.voices = QTableWidget(0, 4)
        self.voices.setHorizontalHeaderLabels(["Nhân vật", "Engine", "Giọng", "Mẫu nhái giọng"])
        self.voices.verticalHeader().setVisible(False)
        self.voices.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.voices.setEditTriggers(QTableWidget.NoEditTriggers)
        self.voices.setMaximumHeight(170)
        l3.addWidget(self.voices)
        self.body.addWidget(g3)
        self.table = SegmentTable(columns=["id", "start", "who", "vi_dub", "syl", "flags"])
        self.table.setMinimumHeight(330)
        self.table.doubleClicked.connect(lambda *_: self._play_selected())
        self.body.addWidget(self.table, 1)

    def load_project(self, project):
        self.table.smodel.set_project(project)
        self.voices.setRowCount(0)
        if not project:
            return
        set_combo(self.engine, project.param("tts", "engine", self.main.settings.default("tts")))
        self.preset.setCurrentText(project.param("tts", "default_preset", ""))
        self.verify.setChecked(bool(project.param("tts", "verify", False)))
        names = dict(ENGINES)
        self.voices.setRowCount(len(project.characters))
        for i, c in enumerate(project.characters):
            eng = engine_for(project, c)
            vals = (c.display_name(), names.get(eng, eng).split(" (")[0], c.voice_preset or (c.voice_instruct or "—"),
                    "✔ " + c.voice_ref if c.voice_ref else "—")
            for j, v in enumerate(vals):
                self.voices.setItem(i, j, QTableWidgetItem(v))
        done = sum(1 for s in project.dub_segments() if s.tts_file)
        self.set_status(f"{done}/{len(project.dub_segments())} câu đã có giọng")

    def save_project(self, project):
        project.set_param("tts", "engine", combo_value(self.engine))
        project.set_param("tts", "default_preset", self.preset.currentText().strip())
        project.set_param("tts", "verify", self.verify.isChecked())

    def _redo_selected(self):
        sid = self.table.current_id()
        if sid is not None:
            self.run("tts", {"only_ids": [sid], "force": True}, f"Tạo lại giọng câu {sid}")

    def _play_selected(self):
        p = self.project
        sid = self.table.current_id()
        s = p.segment(sid) if (p and sid is not None) else None
        if s and s.tts_file:
            play_clip(p.abs(s.tts_file))
