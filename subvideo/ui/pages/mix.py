"""Step 10 — mix: dub voice / original voice / background volumes."""

from __future__ import annotations

from PySide6.QtWidgets import QButtonGroup, QCheckBox, QFormLayout, QRadioButton

from ..widgets.common import AudioPlayer, DbSlider, button, card, combo, combo_value, dspin, hint, hrow, primary, set_combo
from .base import BasePage

PRESETS = {
    "sub": dict(dub_db=-60.0, orig_db=0.0, bgm_db=0.0, ducking=False, duck_db=6.0, duck_orig=False, keep_nonverbal=True),
    "narration": dict(dub_db=0.0, orig_db=-14.0, bgm_db=-4.0, ducking=True, duck_db=8.0, duck_orig=True, keep_nonverbal=True),
    "dub": dict(dub_db=0.0, orig_db=-60.0, bgm_db=0.0, ducking=True, duck_db=4.0, duck_orig=False, keep_nonverbal=True),
}
MODES = [("sub", "📝 Chỉ phụ đề", "Giữ nguyên âm thanh gốc, chỉ thêm phụ đề Việt."),
         ("narration", "🗣 Thuyết minh", "Giọng Việt đọc đè lên, giọng gốc vẫn nghe nhỏ phía sau (kiểu phim truyền hình VN)."),
         ("dub", "🎭 Lồng tiếng", "Thay hẳn giọng gốc bằng giọng Việt, giữ nhạc nền & tiếng động; giữ tiếng gốc cho tiếng cười/hét/bài hát.")]


class MixPage(BasePage):
    step_id = "mix"
    title = "Trộn âm"
    icon = "🎚"
    subtitle = ("Chỉnh to/nhỏ giọng lồng, giọng gốc và nhạc nền. Bản trộn này dùng chung cho xem trước và xuất video, "
                "nên nghe thế nào thì xuất ra y như vậy.")

    def build(self):
        g, lay = card("Kiểu âm thanh")
        self.group = QButtonGroup(self)
        self.radios = {}
        for key, label, tip in MODES:
            r = QRadioButton(label)
            r.setToolTip(tip)
            self.group.addButton(r)
            self.radios[key] = r
            lay.addWidget(r)
            lay.addWidget(hint("      " + tip))
            r.toggled.connect(lambda on, k=key: on and self._apply_preset(k))
        self.body.addWidget(g)
        g2, l2 = card("Âm lượng")
        self.s_dub = DbSlider("Giọng lồng tiếng Việt", 0.0)
        self.s_orig = DbSlider("Giọng gốc (tiếng Trung)", -60.0)
        self.s_bgm = DbSlider("Nhạc nền & tiếng động", 0.0)
        self.s_keep = DbSlider("Tiếng gốc ở đoạn không lồng", 0.0, -30, 6)
        self.keep_cb = QCheckBox("Giữ tiếng gốc cho tiếng cười / hét / thở / bài hát (đoạn không lồng)")
        self.duck_cb = QCheckBox("Tự hạ nhạc nền khi có lời lồng tiếng (ducking)")
        self.duck_db = dspin(0, 24, 6, 0.5, 1, " dB")
        self.duck_db.setMaximumWidth(110)
        self.duck_orig = QCheckBox("Hạ cả giọng gốc khi có lời lồng (thuyết minh)")
        self.loud = combo([(-14.0, "-14 LUFS (YouTube, TikTok, Facebook)"), (-16.0, "-16 LUFS (podcast/web)"),
                           (-23.0, "-23 LUFS (truyền hình EBU R128)"), (0.0, "Không chuẩn hoá")], -14.0)
        for w in (self.s_dub, self.s_orig, self.s_bgm, self.s_keep):
            l2.addWidget(w)
        l2.addWidget(self.keep_cb)
        f = QFormLayout()
        f.addRow(self.duck_cb, self.duck_db)
        f.addRow("", self.duck_orig)
        f.addRow("Độ to chuẩn:", self.loud)
        l2.addLayout(f)
        b = self.action(primary("▶ Trộn âm"))
        b.clicked.connect(lambda: self.run("mix", {}, "Trộn âm"))
        b_ed = button("✏️ Xem cùng video ở tab Chỉnh sửa")
        b_ed.clicked.connect(lambda: self.main.go("EditorPage"))
        l2.addLayout(hrow(b, b_ed, None))
        l2.addWidget(hint("Nếu chưa tách giọng, “giọng gốc” không tách riêng được: hãy chạy bước Tách giọng trước khi lồng tiếng."))
        self.body.addWidget(g2)
        g3, l3 = card("Nghe bản trộn")
        self.player = AudioPlayer("Bản trộn")
        l3.addWidget(self.player)
        self.body.addWidget(g3)
        self.body.addStretch(1)

    def _apply_preset(self, key: str):
        if getattr(self, "_loading", False):
            return
        p = PRESETS[key]
        self.s_dub.setValue(p["dub_db"])
        self.s_orig.setValue(p["orig_db"])
        self.s_bgm.setValue(p["bgm_db"])
        self.duck_cb.setChecked(p["ducking"])
        self.duck_db.setValue(p["duck_db"])
        self.duck_orig.setChecked(p["duck_orig"])
        self.keep_cb.setChecked(p["keep_nonverbal"])

    def load_project(self, project):
        if not project:
            return
        self._loading = True
        m = project.mix
        r = self.radios.get(m.mode)
        if r:
            r.setChecked(True)
        self.s_dub.setValue(m.dub_db)
        self.s_orig.setValue(m.orig_db)
        self.s_bgm.setValue(m.bgm_db)
        self.s_keep.setValue(m.keep_db)
        self.keep_cb.setChecked(m.keep_nonverbal)
        self.duck_cb.setChecked(m.ducking)
        self.duck_db.setValue(m.duck_db)
        self.duck_orig.setChecked(m.duck_orig)
        set_combo(self.loud, float(m.loudness))
        self._loading = False
        self.player.set_file(project.mix_wav if project.mix_wav.exists() else None)

    def save_project(self, project):
        m = project.mix
        for k, r in self.radios.items():
            if r.isChecked():
                m.mode = k
        m.dub_db, m.orig_db, m.bgm_db, m.keep_db = self.s_dub.value(), self.s_orig.value(), self.s_bgm.value(), self.s_keep.value()
        m.keep_nonverbal = self.keep_cb.isChecked()
        m.ducking = self.duck_cb.isChecked()
        m.duck_db = self.duck_db.value()
        m.duck_orig = self.duck_orig.isChecked()
        m.loudness = float(combo_value(self.loud))

    def shutdown(self):
        self.player.stop()
