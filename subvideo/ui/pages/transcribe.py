"""Step 3 — listen: VAD, Chinese ASR with per-character timestamps, speakers."""

from __future__ import annotations

import json

from PySide6.QtWidgets import QCheckBox, QFileDialog, QFormLayout, QHeaderView, QTableWidget, QTableWidgetItem

from ...core.timeutil import ms_to_clock
from ...pipeline.transcribe import ASR_ENGINES, DIARIZE_ENGINES, VAD_ENGINES
from ..widgets.common import button, card, combo, combo_value, hint, hrow, primary, set_combo, spin
from .base import BasePage


class TranscribePage(BasePage):
    step_id = "transcribe"
    title = "Nghe & nhận dạng lời"
    icon = "👂"
    subtitle = ("Phát hiện lúc nhân vật nói/ngừng, nhận dạng tiếng Trung kèm mốc thời gian từng chữ, phân biệt người nói. "
                "Kết quả dùng để căn thời gian chính xác và ghép với phụ đề OCR.")

    def build(self):
        g, lay = card("Thiết lập")
        form = QFormLayout()
        self.engine = combo(ASR_ENGINES, "funasr")
        self.diarize = combo(DIARIZE_ENGINES, "funasr")
        self.vad = combo(VAD_ENGINES, "silero")
        self.nspk = spin(0, 30, 0)
        self.nspk.setSpecialValueText("Tự động")
        self.emotion = QCheckBox("Nhận diện cảm xúc từng câu (SenseVoice) — để chọn giọng lồng hợp cảm xúc")
        form.addRow("Nhận dạng giọng nói:", self.engine)
        form.addRow("Phân biệt người nói:", self.diarize)
        form.addRow("Phát hiện tiếng nói:", self.vad)
        form.addRow("Số người nói:", self.nspk)
        lay.addLayout(form)
        lay.addWidget(self.emotion)
        lay.addWidget(hint("• FunASR Paraformer: rất nhanh, mốc thời gian từng chữ, tích hợp phân biệt người nói.\n"
                           "• Qwen3-ASR: chính xác hơn với phương ngữ/tiếng ồn; cần môi trường Python riêng (xem Cài đặt).\n"
                           "• Nên chạy sau bước Tách giọng để nghe trên giọng sạch."))
        b = self.action(primary("▶ Chạy nhận dạng"))
        b.clicked.connect(lambda: self.run("transcribe", {}, "Nghe & nhận dạng lời"))
        b2 = self.action(button("Dùng file phụ đề làm lời nghe…", "Khi đã có phụ đề chuẩn (SRT/ASS/VTT) của video"))
        b2.clicked.connect(self._import)
        lay.addLayout(hrow(b, b2, None))
        self.body.addWidget(g)
        g2, l2 = card("Kết quả nghe được")
        self.summary = hint("")
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Bắt đầu", "Kết thúc", "Người nói", "Lời nghe được"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setMinimumHeight(320)
        self.table.setAlternatingRowColors(True)
        l2.addWidget(self.summary)
        l2.addWidget(self.table)
        self.body.addWidget(g2, 1)

    def load_project(self, project):
        self.table.setRowCount(0)
        self.summary.setText("")
        if not project:
            return
        s = self.main.settings
        set_combo(self.engine, project.param("transcribe", "engine", s.default("asr")))
        set_combo(self.diarize, project.param("transcribe", "diarize", s.default("diarize")))
        set_combo(self.vad, project.param("transcribe", "vad", s.default("vad")))
        self.nspk.setValue(int(project.param("transcribe", "num_speakers", 0) or 0))
        self.emotion.setChecked(bool(project.param("transcribe", "emotion", False)))
        if project.asr_json.exists():
            try:
                d = json.loads(project.asr_json.read_text("utf-8"))
            except Exception:
                return
            sents = d.get("sentences", [])
            spk = sorted({x.get("spk") for x in sents if x.get("spk")})
            self.summary.setText(f"{len(sents)} câu • {sum(len(x.get('chars', [])) for x in sents)} chữ • "
                                 f"{len(spk)} người nói • {len(d.get('vad', []))} đoạn có tiếng • engine: {d.get('engine')}")
            self.table.setRowCount(len(sents))
            for i, x in enumerate(sents):
                emo = f" [{x['emotion']}]" if x.get("emotion") else ""
                for j, v in enumerate((ms_to_clock(x["start"]), ms_to_clock(x["end"]), x.get("spk", ""), x["text"] + emo)):
                    it = QTableWidgetItem(str(v))
                    self.table.setItem(i, j, it)
            self.table.resizeColumnsToContents()
            self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

    def save_project(self, project):
        project.set_param("transcribe", "engine", combo_value(self.engine))
        project.set_param("transcribe", "diarize", combo_value(self.diarize))
        project.set_param("transcribe", "vad", combo_value(self.vad))
        project.set_param("transcribe", "num_speakers", self.nspk.value())
        project.set_param("transcribe", "emotion", self.emotion.isChecked())

    def _import(self):
        f, _ = QFileDialog.getOpenFileName(self, "Chọn phụ đề tiếng Trung", "", "Phụ đề (*.srt *.ass *.ssa *.vtt)")
        if f:
            self.run("import_subs_asr", {"path": f}, "Nhập phụ đề làm lời nghe")
