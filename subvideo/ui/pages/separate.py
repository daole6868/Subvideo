"""Step 2 — separate voice and background (music + effects)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFormLayout

from ...pipeline.separate import DEREVERB_MODELS, VOCAL_MODELS
from ..widgets.common import AudioPlayer, card, combo, combo_value, hint, primary, set_combo
from .base import BasePage

ENGINES = [("audio_separator", "UVR / RoFormer (audio-separator) — khuyên dùng"),
           ("demucs", "Demucs htdemucs_ft"),
           ("none", "Không tách (dùng âm thanh gốc)")]


class SeparatePage(BasePage):
    step_id = "separate"
    title = "Tách giọng & nhạc nền"
    icon = "🎵"
    subtitle = ("Tách lời thoại khỏi nhạc nền + hiệu ứng. Giọng đã tách giúp nghe chính xác hơn và làm mẫu nhái giọng; "
                "nhạc nền (giữ nguyên tiếng động) dùng để trộn với giọng lồng Việt.")

    def build(self):
        g, lay = card("Thiết lập")
        form = QFormLayout()
        self.engine = combo(ENGINES, "audio_separator")
        self.model = combo(VOCAL_MODELS)
        self.dereverb = combo(DEREVERB_MODELS)
        form.addRow("Công cụ:", self.engine)
        form.addRow("Mô hình tách giọng:", self.model)
        form.addRow("Khử vang giọng:", self.dereverb)
        lay.addLayout(form)
        lay.addWidget(hint("• BS-RoFormer/Mel-RoFormer cho giọng sạch nhất hiện nay; lần đầu sẽ tự tải mô hình (~300–600 MB).\n"
                           "• Khử vang chỉ dùng cho nhận dạng lời và mẫu giọng — bản trộn vẫn giữ giọng tự nhiên.\n"
                           "• Tiếng cười/hét/thở và bài hát có lời có thể nằm trong phần giọng: SubVideo giữ tiếng gốc cho các đoạn không lồng."))
        b = self.action(primary("▶ Chạy tách giọng"))
        b.clicked.connect(lambda: self.run("separate", {}, "Tách giọng & nhạc nền"))
        lay.addWidget(b, 0, Qt.AlignLeft)
        self.body.addWidget(g)
        g2, l2 = card("Nghe kết quả")
        self.p_orig = AudioPlayer("Âm thanh gốc")
        self.p_voc = AudioPlayer("Giọng nói")
        self.p_dry = AudioPlayer("Giọng (khử vang)")
        self.p_ins = AudioPlayer("Nhạc nền + hiệu ứng")
        for w in (self.p_orig, self.p_voc, self.p_dry, self.p_ins):
            l2.addWidget(w)
        self.body.addWidget(g2)
        self.body.addStretch(1)

    def load_project(self, project):
        if not project:
            return
        set_combo(self.engine, project.param("separate", "engine", self.main.settings.default("separator")))
        set_combo(self.model, project.param("separate", "model", self.main.settings.default("separator_model") or VOCAL_MODELS[0][0]))
        set_combo(self.dereverb, project.param("separate", "dereverb", self.main.settings.default("dereverb_model") or ""))
        self.p_orig.set_file(project.original_wav)
        self.p_voc.set_file(project.vocals_wav)
        self.p_dry.set_file(project.vocals_dry_wav)
        self.p_ins.set_file(project.instrumental_wav)

    def save_project(self, project):
        project.set_param("separate", "engine", combo_value(self.engine))
        project.set_param("separate", "model", combo_value(self.model))
        project.set_param("separate", "dereverb", combo_value(self.dereverb))

    def shutdown(self):
        for w in (self.p_orig, self.p_voc, self.p_dry, self.p_ins):
            w.stop()
