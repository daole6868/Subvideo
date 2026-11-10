"""Step 6 — characters, voices, forms of address (xưng hô), glossary, setting."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QHeaderView, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPlainTextEdit, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from ...core.project import Character, Project, Relation, Term
from ...pipeline.tts import EDGE_VOICES, ENGINES, VIENEU_PRESETS
from ..widgets.common import (DbSlider, PathEdit, button, card, combo, combo_value, dspin, hint, hrow, play_clip,
                              primary, set_combo)
from .base import BasePage

GENDERS = [("", "—"), ("nam", "Nam"), ("nữ", "Nữ"), ("khác", "Khác")]
AGES = [("", "—"), ("trẻ em", "Trẻ em"), ("thiếu niên", "Thiếu niên"), ("thanh niên", "Thanh niên"),
        ("trung niên", "Trung niên"), ("già", "Người già")]


class CharactersPage(BasePage):
    step_id = "characters"
    title = "Nhân vật & xưng hô"
    icon = "👥"
    subtitle = ("Hồ sơ phim giúp dịch đúng vai: ai là ai, giới tính, vai vế, ai gọi ai thế nào (huynh/muội, ta/ngươi, anh/em…), "
                "thuật ngữ, và giọng lồng của từng nhân vật. Dùng lại được cho các tập sau.")

    def build(self):
        self._loading = False
        acts = hrow()
        b_ai = self.action(primary("🤖 Phân tích bằng AI"))
        b_ai.setToolTip("AI đọc toàn bộ lời thoại và đề xuất nhân vật, xưng hô, thuật ngữ (cần bước Hợp nhất trước)")
        b_ai.clicked.connect(lambda: self.run("bible", {"merge": True}, "Phân tích nhân vật & xưng hô"))
        b_ref = self.action(button("🎤 Lấy mẫu giọng tự động", "Chọn đoạn thoại sạch 3–9 giây của từng nhân vật làm mẫu nhái giọng"))
        b_ref.clicked.connect(lambda: self.run("voice_refs", {"overwrite": False}, "Lấy mẫu giọng nhân vật"))
        b_imp = button("📥 Nhập hồ sơ từ dự án/tập khác…")
        b_imp.clicked.connect(self._import)
        b_exp = button("📤 Xuất hồ sơ…")
        b_exp.clicked.connect(self._export)
        for b in (b_ai, b_ref, b_imp, b_exp):
            acts.addWidget(b)
        acts.addStretch(1)
        self.body.addLayout(acts)

        split = QSplitter(Qt.Horizontal)
        # speakers
        g1, l1 = card("Giọng nói phát hiện được")
        self.spk = QTableWidget(0, 4)
        self.spk.setHorizontalHeaderLabels(["Nhãn", "Số câu", "Nghe", "Là nhân vật"])
        self.spk.verticalHeader().setVisible(False)
        self.spk.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.spk.setMinimumWidth(330)
        l1.addWidget(self.spk)
        l1.addWidget(hint("Gán mỗi nhãn giọng cho một nhân vật. Một nhân vật có thể có nhiều nhãn."))
        split.addWidget(g1)
        # characters list
        g2, l2 = card("Nhân vật")
        self.clist = QListWidget()
        self.clist.currentRowChanged.connect(self._select_char)
        b_add = button("＋ Thêm")
        b_del = button("Xoá", obj="Danger")
        b_add.clicked.connect(self._add_char)
        b_del.clicked.connect(self._del_char)
        l2.addWidget(self.clist)
        l2.addLayout(hrow(b_add, b_del, None))
        split.addWidget(g2)
        # form
        g3, l3 = card("Chi tiết nhân vật")
        f = QFormLayout()
        self.f_zh = QLineEdit()
        self.f_vi = QLineEdit()
        self.f_gender = combo(GENDERS)
        self.f_age = combo(AGES)
        self.f_role = QLineEdit()
        self.f_pers = QLineEdit()
        self.f_engine = combo([("", "(theo mặc định dự án)")] + ENGINES)
        self.f_preset = QComboBox()
        self.f_preset.setEditable(True)
        self.f_preset.addItems([""] + VIENEU_PRESETS + EDGE_VOICES)
        self.f_ref = PathEdit(mode="file", filter="Âm thanh (*.wav *.mp3 *.flac)")
        self.f_reftext = QLineEdit()
        self.f_instr = QLineEdit()
        self.f_instr.setPlaceholderText("vd: female, young adult, soft voice (OmniVoice)")
        self.f_speed = dspin(0.7, 1.4, 1.0, 0.05)
        self.f_rate = dspin(3.0, 8.0, 5.0, 0.1, 1, " âm tiết/giây")
        self.f_gain = DbSlider("", 0.0, -20, 12)
        f.addRow("Tên tiếng Trung:", self.f_zh)
        f.addRow("Tên tiếng Việt:", self.f_vi)
        f.addRow("Giới tính:", self.f_gender)
        f.addRow("Độ tuổi:", self.f_age)
        f.addRow("Vai trò:", self.f_role)
        f.addRow("Tính cách / giọng điệu:", self.f_pers)
        f.addRow("Engine lồng tiếng:", self.f_engine)
        f.addRow("Giọng mẫu có sẵn:", self.f_preset)
        f.addRow("File mẫu giọng (nhái):", self.f_ref)
        f.addRow("Lời của file mẫu:", self.f_reftext)
        f.addRow("Mô tả giọng:", self.f_instr)
        f.addRow("Tốc độ đọc TTS:", self.f_speed)
        f.addRow("Nhịp nói mục tiêu:", self.f_rate)
        f.addRow("Âm lượng giọng:", self.f_gain)
        l3.addLayout(f)
        b_ref_play = button("▶ Nghe mẫu")
        b_ref_play.clicked.connect(self._play_ref)
        self.b_try = self.action(button("🔊 Nghe thử giọng lồng"))
        self.b_try.clicked.connect(self._try_voice)
        l3.addLayout(hrow(b_ref_play, self.b_try, None))
        l3.addWidget(hint("Nhịp nói mục tiêu quyết định độ dài lời dịch lồng tiếng (ngân sách âm tiết). "
                          "Tiếng Việt tự nhiên ~4.5–5.5 âm tiết/giây; nhân vật nói nhanh có thể đặt 6."))
        split.addWidget(g3)
        split.setSizes([360, 220, 520])
        self.body.addWidget(split)
        for w in (self.f_zh, self.f_vi, self.f_role, self.f_pers, self.f_reftext, self.f_instr):
            w.editingFinished.connect(self._write_char)
        for w in (self.f_gender, self.f_age, self.f_engine):
            w.currentIndexChanged.connect(self._write_char)
        self.f_preset.currentTextChanged.connect(self._write_char)
        self.f_ref.changed.connect(lambda _: self._write_char())
        self.f_speed.valueChanged.connect(self._write_char)
        self.f_rate.valueChanged.connect(self._write_char)
        self.f_gain.changed.connect(lambda _: self._write_char())

        tabs = QTabWidget()
        # relations
        rel = QWidget()
        rl = QVBoxLayout(rel)
        self.rel = QTableWidget(0, 5)
        self.rel.setHorizontalHeaderLabels(["Người nói", "Nói với", "Gọi người nghe là", "Tự xưng là", "Ghi chú"])
        self.rel.verticalHeader().setVisible(False)
        self.rel.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rel.setMinimumHeight(220)
        b_ra = button("＋ Thêm cặp xưng hô")
        b_rd = button("Xoá dòng", obj="Danger")
        b_ra.clicked.connect(lambda: self._add_rel_row(Relation()))
        b_rd.clicked.connect(lambda: self.rel.removeRow(self.rel.currentRow()))
        rl.addWidget(self.rel)
        rl.addLayout(hrow(b_ra, b_rd, None))
        rl.addWidget(hint("Ví dụ: Tiêu Viêm → Huân Nhi: gọi “Huân Nhi”, tự xưng “ta”. Huân Nhi → Tiêu Viêm: gọi “Tiêu Viêm ca ca”, "
                          "tự xưng “Huân Nhi”. Bảng này được đưa vào mọi lần dịch."))
        tabs.addTab(rel, "Xưng hô")
        # terms
        tw = QWidget()
        tl = QVBoxLayout(tw)
        self.terms = QTableWidget(0, 3)
        self.terms.setHorizontalHeaderLabels(["Tiếng Trung", "Tiếng Việt", "Ghi chú"])
        self.terms.verticalHeader().setVisible(False)
        self.terms.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.terms.setMinimumHeight(220)
        b_ta = button("＋ Thêm thuật ngữ")
        b_td = button("Xoá dòng", obj="Danger")
        b_ta.clicked.connect(lambda: self._add_term_row(Term()))
        b_td.clicked.connect(lambda: self.terms.removeRow(self.terms.currentRow()))
        tl.addWidget(self.terms)
        tl.addLayout(hrow(b_ta, b_td, None))
        tabs.addTab(tw, "Thuật ngữ && tên riêng")
        # setting
        sw = QWidget()
        sl = QFormLayout(sw)
        self.genre = QLineEdit()
        self.era = combo([("", "—"), ("cổ trang", "Cổ trang"), ("tiên hiệp", "Tiên hiệp / huyền huyễn"),
                          ("hiện đại", "Hiện đại"), ("dân quốc", "Dân quốc"), ("khác", "Khác")])
        self.style = QPlainTextEdit()
        self.style.setMaximumHeight(90)
        self.synopsis = QPlainTextEdit()
        self.synopsis.setMaximumHeight(110)
        sl.addRow("Thể loại:", self.genre)
        sl.addRow("Bối cảnh:", self.era)
        sl.addRow("Văn phong dịch:", self.style)
        sl.addRow("Tóm tắt:", self.synopsis)
        tabs.addTab(sw, "Bối cảnh && văn phong")
        self.body.addWidget(tabs)

    # ---------------------------------------------------------------- load
    def load_project(self, project):
        self._loading = True
        try:
            self.clist.clear()
            self.spk.setRowCount(0)
            self.rel.setRowCount(0)
            self.terms.setRowCount(0)
            if not project:
                return
            for c in project.characters:
                it = QListWidgetItem(self._char_label(c))
                it.setData(Qt.UserRole, c.id)
                self.clist.addItem(it)
            self._fill_speakers(project)
            for r in project.relations:
                self._add_rel_row(r)
            for t in project.terms:
                self._add_term_row(t)
            b = project.bible
            self.genre.setText(b.genre)
            set_combo(self.era, b.era)
            self.style.setPlainText(b.style_notes)
            self.synopsis.setPlainText(b.synopsis)
        finally:
            self._loading = False
        if self.clist.count():
            self.clist.setCurrentRow(0)
        else:
            self._select_char(-1)

    def _char_label(self, c: Character) -> str:
        g = {"nam": "♂", "nữ": "♀"}.get(c.gender, "")
        v = " 🎤" if c.voice_ref else ""
        return f"{c.display_name()} {g}{v}"

    def _fill_speakers(self, project: Project):
        labels = project.speaker_labels()
        self.spk.setRowCount(len(labels))
        for i, lab in enumerate(labels):
            n = sum(1 for s in project.segments if s.speaker == lab)
            self.spk.setItem(i, 0, QTableWidgetItem(lab))
            self.spk.setItem(i, 1, QTableWidgetItem(str(n)))
            b = button("▶")
            b.setFixedWidth(36)
            b.clicked.connect(lambda _=False, l=lab: self._play_speaker(l))
            self.spk.setCellWidget(i, 2, b)
            cb = QComboBox()
            cb.addItem("(chưa gán)", "")
            cb.addItem("＋ Tạo nhân vật mới", "__new__")
            for c in project.characters:
                cb.addItem(c.display_name(), c.id)
            owner = next((c.id for c in project.characters if lab in c.speaker_labels), "")
            idx = cb.findData(owner)
            cb.setCurrentIndex(max(0, idx))
            cb.currentIndexChanged.connect(lambda _=0, l=lab, w=cb: self._assign(l, w.currentData()))
            self.spk.setCellWidget(i, 3, cb)
        self.spk.resizeColumnsToContents()
        self.spk.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)

    def _assign(self, label: str, cid: str):
        p = self.project
        if not p or self._loading:
            return
        for c in p.characters:
            if label in c.speaker_labels:
                c.speaker_labels.remove(label)
        if cid == "__new__":
            c = Character(id=p.new_character_id(), name_vi=f"Nhân vật {len(p.characters) + 1}", speaker_labels=[label])
            p.characters.append(c)
            self.load_project(p)
            return
        c = p.character(cid)
        if c:
            c.speaker_labels.append(label)
        self.main.save_project(quiet=True)

    def _play_speaker(self, label: str):
        p = self.project
        if not p:
            return
        segs = sorted([s for s in p.segments if s.speaker == label], key=lambda s: -s.duration)
        if not segs:
            return
        s = segs[0]
        src = p.vocals_wav if p.vocals_wav.exists() else p.original_wav
        play_clip(src, max(0, s.start - 100), s.end + 100)

    # --------------------------------------------------------- characters
    def _current_char(self) -> Character | None:
        it = self.clist.currentItem()
        if not it or not self.project:
            return None
        return self.project.character(it.data(Qt.UserRole))

    def _select_char(self, row: int):
        c = self._current_char()
        self._loading = True
        try:
            enabled = c is not None
            for w in (self.f_zh, self.f_vi, self.f_gender, self.f_age, self.f_role, self.f_pers, self.f_engine,
                      self.f_preset, self.f_ref, self.f_reftext, self.f_instr, self.f_speed, self.f_rate, self.f_gain):
                w.setEnabled(enabled)
            if not c:
                return
            self.f_zh.setText(c.name_zh)
            self.f_vi.setText(c.name_vi)
            set_combo(self.f_gender, c.gender)
            set_combo(self.f_age, c.age)
            self.f_role.setText(c.role)
            self.f_pers.setText(c.personality)
            set_combo(self.f_engine, c.tts_engine)
            self.f_preset.setCurrentText(c.voice_preset)
            self.f_ref.setText(c.voice_ref)
            self.f_reftext.setText(c.voice_ref_text)
            self.f_instr.setText(c.voice_instruct)
            self.f_speed.setValue(c.speed or 1.0)
            self.f_rate.setValue(c.rate or 5.0)
            self.f_gain.setValue(c.gain_db)
        finally:
            self._loading = False

    def _write_char(self, *_):
        if self._loading:
            return
        c = self._current_char()
        if not c:
            return
        c.name_zh = self.f_zh.text().strip()
        c.name_vi = self.f_vi.text().strip()
        c.gender = combo_value(self.f_gender)
        c.age = combo_value(self.f_age)
        c.role = self.f_role.text().strip()
        c.personality = self.f_pers.text().strip()
        c.tts_engine = combo_value(self.f_engine)
        c.voice_preset = self.f_preset.currentText().strip()
        c.voice_ref = self.f_ref.text()
        c.voice_ref_text = self.f_reftext.text().strip()
        c.voice_instruct = self.f_instr.text().strip()
        c.speed = self.f_speed.value()
        c.rate = self.f_rate.value()
        c.gain_db = self.f_gain.value()
        it = self.clist.currentItem()
        if it:
            it.setText(self._char_label(c))

    def _add_char(self):
        p = self.project
        if not p:
            return
        c = Character(id=p.new_character_id(), name_vi=f"Nhân vật {len(p.characters) + 1}")
        p.characters.append(c)
        it = QListWidgetItem(self._char_label(c))
        it.setData(Qt.UserRole, c.id)
        self.clist.addItem(it)
        self.clist.setCurrentItem(it)
        self._fill_speakers(p)

    def _del_char(self):
        p = self.project
        c = self._current_char()
        if not p or not c:
            return
        if QMessageBox.question(self, "Xoá nhân vật", f"Xoá “{c.display_name()}”?") != QMessageBox.Yes:
            return
        p.characters = [x for x in p.characters if x.id != c.id]
        p.relations = [r for r in p.relations if c.id not in (r.src, r.dst)]
        for s in p.segments:
            if s.character == c.id:
                s.character = ""
        self.load_project(p)

    def _play_ref(self):
        c = self._current_char()
        if c and c.voice_ref and self.project:
            play_clip(self.project.abs(c.voice_ref))

    def _try_voice(self):
        c = self._current_char()
        if not c or not self.project:
            return
        self.main.save_project(quiet=True)
        text = "Ta đã đợi ngươi rất lâu rồi. Hôm nay, chúng ta sẽ phân thắng bại!" if self.project.bible.era in (
            "cổ trang", "tiên hiệp") else "Xin chào, đây là giọng lồng tiếng thử cho nhân vật này."

        def done(ok, res):
            if ok:
                play_clip(res.strip())
            else:
                QMessageBox.warning(self, "Không tạo được giọng", res)

        self.run("voice_preview", {"character": c.id, "text": text}, "Nghe thử giọng", done)

    # --------------------------------------------------------- relations
    def _char_combo(self, value: str) -> QComboBox:
        cb = QComboBox()
        cb.addItem("—", "")
        for c in (self.project.characters if self.project else []):
            cb.addItem(c.display_name(), c.id)
        i = cb.findData(value)
        cb.setCurrentIndex(max(0, i))
        return cb

    def _add_rel_row(self, r: Relation):
        i = self.rel.rowCount()
        self.rel.insertRow(i)
        self.rel.setCellWidget(i, 0, self._char_combo(r.src))
        self.rel.setCellWidget(i, 1, self._char_combo(r.dst))
        for j, v in ((2, r.call), (3, r.self_call), (4, r.note)):
            self.rel.setItem(i, j, QTableWidgetItem(v))

    def _add_term_row(self, t: Term):
        i = self.terms.rowCount()
        self.terms.insertRow(i)
        for j, v in enumerate((t.zh, t.vi, t.note)):
            self.terms.setItem(i, j, QTableWidgetItem(v))

    # ---------------------------------------------------------------- save
    def save_project(self, project):
        self._write_char()
        rels = []
        for i in range(self.rel.rowCount()):
            a = self.rel.cellWidget(i, 0)
            b = self.rel.cellWidget(i, 1)
            if not a or not b or not a.currentData() or not b.currentData():
                continue
            txt = [(self.rel.item(i, j).text() if self.rel.item(i, j) else "") for j in (2, 3, 4)]
            rels.append(Relation(a.currentData(), b.currentData(), *txt))
        if self.rel.rowCount() or not project.relations:
            project.relations = rels
        terms = []
        for i in range(self.terms.rowCount()):
            vals = [(self.terms.item(i, j).text().strip() if self.terms.item(i, j) else "") for j in range(3)]
            if vals[0]:
                terms.append(Term(*vals))
        if self.terms.rowCount() or not project.terms:
            project.terms = terms
        b = project.bible
        b.genre = self.genre.text().strip()
        b.era = combo_value(self.era)
        b.style_notes = self.style.toPlainText().strip()
        b.synopsis = self.synopsis.toPlainText().strip()

    def _import(self):
        p = self.project
        if not p:
            return
        f, _ = QFileDialog.getOpenFileName(self, "Chọn project.json của tập khác hoặc file hồ sơ", "",
                                           "Hồ sơ (*.json)")
        if not f:
            return
        try:
            d = json.loads(Path(f).read_text("utf-8"))
        except Exception as e:
            QMessageBox.warning(self, "Lỗi", str(e))
            return
        self.save_project(p)
        p.import_bible(d, merge=True)
        self.load_project(p)
        self.main.save_project(quiet=True)
        QMessageBox.information(self, "Đã nhập", f"Hiện có {len(p.characters)} nhân vật, {len(p.relations)} cặp xưng hô, "
                                f"{len(p.terms)} thuật ngữ. Hãy gán lại nhãn giọng cho tập này.")

    def _export(self):
        p = self.project
        if not p:
            return
        self.save_project(p)
        f, _ = QFileDialog.getSaveFileName(self, "Lưu hồ sơ phim", str(p.root / "ho_so_phim.json"), "Hồ sơ (*.json)")
        if f:
            Path(f).write_text(json.dumps(p.bible_dict(), ensure_ascii=False, indent=1), "utf-8")
