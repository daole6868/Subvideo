"""Editor: video + waveform timeline + line table + per-line detail.

Only for reviewing and adjusting — it never exports.
"""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from ...core.audio import load_or_make_peaks
from ...core.project import Segment
from ...core.subtitles import build_cues
from ...core.textutil import count_vi_syllables, cps
from ...core.timeutil import ms_to_clock, parse_time
from ...pipeline.export import write_project_subs
from ...pipeline.mixer import mix_is_current
from ..widgets.common import DbSlider, button, card, combo, combo_value, dspin, hint, hrow, play_clip, primary, set_combo, tool
from ..widgets.segment_table import FLAG_LABELS, SegmentTable
from ..widgets.timeline import Timeline
from ..widgets.video_player import make_player
from .base import BasePage

KINDS = [("speech", "Lời thoại"), ("onscreen", "Chữ trên hình"), ("nonverbal", "Tiếng không lời"), ("song", "Bài hát")]


class SmallText(QPlainTextEdit):
    def __init__(self, lines: int = 2):
        super().__init__()
        fm = self.fontMetrics()
        self.setFixedHeight(int(fm.lineSpacing() * lines + 14))
        self.setTabChangesFocus(True)


class EditorPage(BasePage):
    title = "Chỉnh sửa & căn chỉnh"
    icon = "✏️"
    subtitle = ("Xem video với bản trộn và phụ đề, kéo mép từng câu trên dòng thời gian (tới từng mili giây), sửa lời, "
                "nghe lại giọng gốc / giọng lồng. Tab này chỉ để căn chỉnh — không xuất video.")
    scroll = False

    def build(self):
        self.cur: Segment | None = None
        self._loading = False
        self._subs_timer = QTimer(self)
        self._subs_timer.setSingleShot(True)
        self._subs_timer.timeout.connect(self._refresh_subs)

        # ---------------- toolbar
        tb = QHBoxLayout()
        self.b_prev = tool("⏮", "Câu trước (Ctrl+↑)")
        self.b_back = tool("◀|", "Lùi 1 khung (Alt+←)")
        self.b_play = tool("▶", "Phát / dừng (Ctrl+Space)")
        self.b_fwd = tool("|▶", "Tới 1 khung (Alt+→)")
        self.b_next = tool("⏭", "Câu sau (Ctrl+↓)")
        self.pos_lbl = QLabel("00:00.000")
        self.pos_lbl.setMinimumWidth(110)
        self.follow = QCheckBox("Theo dõi")
        self.follow.setChecked(True)
        z_in = tool("🔍+", "Phóng to (Ctrl+lăn chuột)")
        z_out = tool("🔍−", "Thu nhỏ")
        z_ms = tool("1ms", "Phóng tối đa: 1 mili giây / điểm ảnh")
        self.mix_state = QLabel("")
        self.mix_state.setObjectName("Hint")
        self.b_update = self.action(primary("🔄 Cập nhật bản trộn"))
        self.b_update.setToolTip("Lồng lại các câu đã sửa → căn nhịp → trộn âm, rồi nạp lại vào trình phát")
        self.b_exact = self.action(button("👁 Bản xuất 20s", "Dựng đúng như bản xuất (giãn video, che phụ đề gốc…) 20 giây quanh vị trí phát"))
        for w in (self.b_prev, self.b_back, self.b_play, self.b_fwd, self.b_next, self.pos_lbl, self.follow, z_out, z_in, z_ms):
            tb.addWidget(w)
        tb.addStretch(1)
        tb.addWidget(self.b_exact)
        tb.addWidget(self.b_update)
        self.body.addLayout(tb)
        self.body.addWidget(self.mix_state)

        vsplit = QSplitter(Qt.Vertical)
        top = QSplitter(Qt.Horizontal)
        self.player = make_player()
        top.addWidget(self.player)
        top.addWidget(self._build_detail())
        top.setSizes([760, 560])
        vsplit.addWidget(top)
        self.timeline = Timeline()
        vsplit.addWidget(self.timeline)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.filter = combo([("all", "Tất cả câu"), ("flagged", "Cần xem lại"), ("unreviewed", "Chưa duyệt"), ("dub", "Có lồng tiếng")])
        self.search = QLineEdit()
        self.search.setPlaceholderText("Tìm chữ Trung / Việt…")
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("Hint")
        bl.addLayout(hrow(QLabel("Hiển thị:"), self.filter, self.search, self.count_lbl, None))
        self.table = SegmentTable()
        bl.addWidget(self.table)
        vsplit.addWidget(bottom)
        vsplit.setSizes([430, 210, 260])
        self.body.addWidget(vsplit, 1)

        # ---------------- wiring
        self.b_play.clicked.connect(self.player.toggle)
        self.b_back.clicked.connect(lambda: self.player.frame_step(-1))
        self.b_fwd.clicked.connect(lambda: self.player.frame_step(1))
        self.b_prev.clicked.connect(lambda: self._jump(-1))
        self.b_next.clicked.connect(lambda: self._jump(1))
        z_in.clicked.connect(lambda: self.timeline.set_zoom(self.timeline.mpp * 0.6))
        z_out.clicked.connect(lambda: self.timeline.set_zoom(self.timeline.mpp / 0.6))
        z_ms.clicked.connect(lambda: self.timeline.set_zoom(1.0))
        self.follow.toggled.connect(lambda on: setattr(self.timeline, "follow", on))
        self.player.positionChanged.connect(self._on_pos)
        self.player.playingChanged.connect(lambda on: self.b_play.setText("⏸" if on else "▶"))
        self.timeline.seekRequested.connect(self._seek)
        self.timeline.segmentSelected.connect(lambda sid: self.select(sid, seek=False))
        self.timeline.segmentActivated.connect(self._play_line)
        self.timeline.segmentEdited.connect(self._on_timeline_edit)
        self.timeline.placementMoved.connect(self._on_timeline_edit)
        self.timeline.viewChanged.connect(lambda: self.follow.setChecked(self.timeline.follow))
        self.table.segmentSelected.connect(lambda sid: self.select(sid, seek=True))
        self.table.smodel.edited.connect(self._on_table_edit)
        self.filter.currentIndexChanged.connect(lambda: self.table.proxy.set_mode(combo_value(self.filter)))
        self.search.textChanged.connect(self.table.proxy.set_text)
        self.b_update.clicked.connect(self._update_mix)
        self.b_exact.clicked.connect(self._exact_preview)
        for seq, fn in (("Ctrl+Space", self.player.toggle), ("Ctrl+Return", lambda: self.cur and self._play_line(self.cur.id)),
                        ("Alt+Left", lambda: self.player.frame_step(-1)), ("Alt+Right", lambda: self.player.frame_step(1)),
                        ("Ctrl+Up", lambda: self._jump(-1)), ("Ctrl+Down", lambda: self._jump(1)),
                        ("Ctrl+[", lambda: self._set_edge("start")), ("Ctrl+]", lambda: self._set_edge("end")),
                        ("Ctrl+R", self._toggle_reviewed)):
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)

    # ------------------------------------------------------------ detail
    def _build_detail(self) -> QWidget:
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 0, 4, 0)
        self.d_title = QLabel("Chọn một câu")
        self.d_title.setStyleSheet("font-size: 12pt; font-weight: 600;")
        self.d_kind = combo(KINDS)
        self.d_reviewed = QCheckBox("Đã duyệt")
        lay.addLayout(hrow(self.d_title, None, self.d_kind, self.d_reviewed))
        grid = QGridLayout()
        self.d_start = QLineEdit()
        self.d_end = QLineEdit()
        self.d_dstart = QLineEdit()
        self.d_dend = QLineEdit()
        for e in (self.d_start, self.d_end, self.d_dstart, self.d_dend):
            e.setMaximumWidth(96)
        grid.addWidget(QLabel("Nói:"), 0, 0)
        grid.addLayout(self._nudge(self.d_start, "start"), 0, 1)
        grid.addWidget(QLabel("→"), 0, 2)
        grid.addLayout(self._nudge(self.d_end, "end"), 0, 3)
        grid.addWidget(QLabel("Hiện chữ:"), 1, 0)
        grid.addLayout(self._nudge(self.d_dstart, "disp_start"), 1, 1)
        grid.addWidget(QLabel("→"), 1, 2)
        grid.addLayout(self._nudge(self.d_dend, "disp_end"), 1, 3)
        lay.addLayout(grid)
        b_auto = button("Tự tính thời gian hiện chữ")
        b_auto.clicked.connect(self._auto_display)
        b_s = button("[ Đầu = vị trí phát")
        b_e = button("Cuối = vị trí phát ]")
        b_s.clicked.connect(lambda: self._set_edge("start"))
        b_e.clicked.connect(lambda: self._set_edge("end"))
        lay.addLayout(hrow(b_s, b_e, b_auto, None))
        f = QFormLayout()
        self.d_char = QComboBox()
        self.d_addr = QComboBox()
        self.d_emotion = QLineEdit()
        f.addRow("Nhân vật:", self.d_char)
        f.addRow("Nói với:", self.d_addr)
        f.addRow("Cảm xúc:", self.d_emotion)
        lay.addLayout(f)
        lay.addWidget(QLabel("Tiếng Trung"))
        self.d_zh = SmallText(2)
        self.d_asr = QLabel("")
        self.d_asr.setObjectName("Hint")
        self.d_asr.setWordWrap(True)
        self.b_use_asr = button("Dùng bản nghe")
        self.b_use_asr.clicked.connect(self._use_asr)
        lay.addWidget(self.d_zh)
        lay.addLayout(hrow(self.d_asr, self.b_use_asr))
        self.d_sub_lbl = QLabel("Phụ đề Việt")
        self.d_sub = SmallText(2)
        self.d_dub_lbl = QLabel("Lời lồng tiếng")
        self.d_dub = SmallText(2)
        lay.addWidget(self.d_sub_lbl)
        lay.addWidget(self.d_sub)
        lay.addWidget(self.d_dub_lbl)
        lay.addWidget(self.d_dub)
        self.d_dubcb = QCheckBox("Lồng tiếng câu này")
        self.d_keep = QCheckBox("Giữ tiếng gốc")
        self.d_gain = dspin(-20, 12, 0, 0.5, 1, " dB")
        lay.addLayout(hrow(self.d_dubcb, self.d_keep, QLabel("Âm lượng câu:"), self.d_gain, None))
        self.d_flags = QLabel("")
        self.d_flags.setWordWrap(True)
        self.d_flags.setStyleSheet("color: #f5b94a;")
        lay.addWidget(self.d_flags)
        acts = QGridLayout()
        btns = [
            ("▶ Phát câu", self._play_current, "Ctrl+Enter"),
            ("🔈 Giọng gốc", self._play_orig, "Nghe giọng nhân vật gốc (đã tách)"),
            ("🔊 Giọng lồng", self._play_dub, "Nghe câu lồng tiếng"),
            ("🌐 Dịch lại", self._retranslate, "Dịch lại câu này với ngữ cảnh"),
            ("✂ Rút gọn", self._shorten, "Rút gọn lời lồng tiếng"),
            ("🎙 Tạo lại giọng", self._regen, "Tạo lại giọng lồng câu này"),
            ("＋ Thêm câu", self._add_here, "Thêm câu mới tại vị trí phát"),
            ("⟂ Tách", self._split_here, "Tách câu tại vị trí phát"),
            ("⤵ Gộp câu sau", self._merge_next, "Gộp với câu kế tiếp"),
            ("🔓 Bỏ khoá vị trí", self._unlock, "Cho bộ căn nhịp tự đặt lại vị trí câu"),
            ("🗑 Xoá câu", self._delete, "Xoá câu này"),
        ]
        self._seg_actions = []
        for i, (t, fn, tip) in enumerate(btns):
            b = button(t, tip)
            b.clicked.connect(fn)
            acts.addWidget(b, i // 3, i % 3)
            self._seg_actions.append(b)
            if t not in ("▶ Phát câu", "🔈 Giọng gốc", "🔊 Giọng lồng"):
                self.action(b)
        lay.addLayout(acts)
        g, gl = card("Âm lượng nhanh (giống tab Trộn âm)")
        self.q_dub = DbSlider("Giọng lồng", 0)
        self.q_orig = DbSlider("Giọng gốc", -60)
        self.q_bgm = DbSlider("Nhạc nền", 0)
        for s in (self.q_dub, self.q_orig, self.q_bgm):
            s.name.setMinimumWidth(80)
            gl.addWidget(s)
            s.changed.connect(self._quick_mix)
        gl.addWidget(hint("Bấm “Cập nhật bản trộn” để nghe thay đổi."))
        lay.addWidget(g)
        lay.addStretch(1)
        sa.setWidget(w)
        # field bindings
        for e, attr in ((self.d_start, "start"), (self.d_end, "end"), (self.d_dstart, "disp_start"), (self.d_dend, "disp_end")):
            e.editingFinished.connect(lambda e=e, a=attr: self._time_edited(e, a))
        self.d_zh.textChanged.connect(lambda: self._text_edited("zh", self.d_zh))
        self.d_sub.textChanged.connect(lambda: self._text_edited("vi_sub", self.d_sub))
        self.d_dub.textChanged.connect(lambda: self._text_edited("vi_dub", self.d_dub))
        self.d_emotion.editingFinished.connect(lambda: self._set("emotion", self.d_emotion.text().strip()))
        self.d_kind.currentIndexChanged.connect(lambda: self._set("kind", combo_value(self.d_kind)))
        self.d_reviewed.toggled.connect(lambda v: self._set("reviewed", v))
        self.d_dubcb.toggled.connect(lambda v: self._set("dub", v))
        self.d_keep.toggled.connect(lambda v: self._set("keep_original", v))
        self.d_gain.valueChanged.connect(lambda v: self._set("gain_db", float(v)))
        self.d_char.currentIndexChanged.connect(lambda: self._set("character", self.d_char.currentData() or ""))
        self.d_addr.currentIndexChanged.connect(lambda: self._set("addressee", self.d_addr.currentData() or ""))
        self._detail_widgets = [self.d_kind, self.d_reviewed, self.d_start, self.d_end, self.d_dstart, self.d_dend,
                                self.d_char, self.d_addr, self.d_emotion, self.d_zh, self.d_sub, self.d_dub, self.d_dubcb,
                                self.d_keep, self.d_gain, b_auto, b_s, b_e, self.b_use_asr]
        return sa

    def _nudge(self, edit: QLineEdit, attr: str) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(2)
        minus = tool("−", "Lùi 10 ms (Shift: 1 khung)")
        plus = tool("+", "Tới 10 ms (Shift: 1 khung)")
        minus.clicked.connect(lambda: self._nudge_time(attr, -1))
        plus.clicked.connect(lambda: self._nudge_time(attr, +1))
        h.addWidget(minus)
        h.addWidget(edit)
        h.addWidget(plus)
        return h

    # ------------------------------------------------------------ project
    def load_project(self, project):
        keep = self.cur.id if self.cur else None
        self.cur = None
        self.table.smodel.set_project(project)
        self.count_lbl.setText(f"{len(project.segments)} câu" if project else "")
        if not project:
            self.timeline.set_segments([])
            self._show(None)
            return
        self.timeline.set_segments(project.segments, project.media.fps)
        self.timeline.set_shots(project.shots)
        self._fill_char_combos()
        m = project.mix
        for s, v in ((self.q_dub, m.dub_db), (self.q_orig, m.orig_db), (self.q_bgm, m.bgm_db)):
            s.blockSignals(True)
            s.setValue(v)
            s.blockSignals(False)
        self._media_loaded = False
        if self.isVisible():
            self._load_media()
        if keep is not None and project.segment(keep):
            self.select(keep, seek=False)
        else:
            self._show(None)

    def on_show(self):
        if self.project and not getattr(self, "_media_loaded", False):
            self._load_media()

    def _audio_and_subs(self):
        p = self.project
        slow = bool(p.slow_regions())
        audio = None
        if slow and p.mix_src_wav.exists():
            audio = p.mix_src_wav
        elif p.mix_wav.exists():
            audio = p.mix_wav
        try:
            subs = write_project_subs(p, "source", out_ass=p.subs_src_ass)[0]
        except Exception:
            subs = None
        return audio, subs, slow

    def _load_media(self):
        p = self.project
        if not p or not p.media.path or not p.video_path.exists():
            return
        self._media_loaded = True
        audio, subs, slow = self._audio_and_subs()
        cues = build_cues(p.segments, "vi_sub", p.display, p.style.bilingual)
        self.player.load(str(p.video_path), str(audio) if audio else None, str(subs) if subs else None, cues)
        current = mix_is_current(p, "source" if slow else "output") if audio else False
        self.mix_state.setText(("🔊 bản trộn" if audio else "🔈 âm thanh gốc (chưa trộn)") +
                               ("" if current or not audio else " — cũ, hãy cập nhật") +
                               (" • có đoạn giãn video: xem bản xuất để thấy chính xác" if slow else ""))
        src = p.asr_wav if p.asr_wav.exists() else (p.vocals_wav if p.vocals_wav.exists() else p.original_wav)
        if src.exists():
            try:
                mins, maxs = load_or_make_peaks(src, p.path("cache", f"peaks_{src.stem}.npz"))
                self.timeline.set_audio_peaks(mins, maxs, 200, p.media.duration_ms)
            except Exception as e:
                self.main.log.add("warning", f"Không vẽ được sóng âm: {e}")
        try:
            from ...pipeline.ocr import load_events

            self.timeline.set_events([(e.start, e.end) for e in load_events(p)])
        except Exception:
            pass

    def _fill_char_combos(self):
        p = self.project
        for cb, first in ((self.d_char, "(theo nhận diện giọng)"), (self.d_addr, "—")):
            cb.blockSignals(True)
            cb.clear()
            cb.addItem(first, "")
            for c in (p.characters if p else []):
                cb.addItem(c.display_name(), c.id)
            cb.blockSignals(False)

    def save_project(self, project):
        m = project.mix
        m.dub_db, m.orig_db, m.bgm_db = self.q_dub.value(), self.q_orig.value(), self.q_bgm.value()

    def set_busy(self, busy):
        super().set_busy(busy)
        for w in getattr(self, "_detail_widgets", []):
            w.setEnabled(not busy and self.cur is not None)
        from PySide6.QtWidgets import QAbstractItemView as V

        self.table.setEditTriggers(V.NoEditTriggers if busy else (V.DoubleClicked | V.EditKeyPressed | V.AnyKeyPressed))

    def shutdown(self):
        try:
            self.player.close_media()
        except Exception:
            pass

    # ----------------------------------------------------------- selection
    def select(self, seg_id: int, seek: bool = True):
        p = self.project
        s = p.segment(seg_id) if p else None
        self._show(s)
        if s is None:
            return
        self.table.select_id(seg_id)
        self.timeline.select(seg_id, center=True)
        if seek and not self.player.is_playing():
            self._seek(max(0, s.display_start - 200))

    def _show(self, s: Segment | None):
        self.cur = s
        self._loading = True
        try:
            busy = self.main.jobs.busy()
            for w in self._detail_widgets:
                w.setEnabled(s is not None and not busy)
            for b in self._seg_actions:
                b.setEnabled(s is not None and (not busy or b.text() in ("▶ Phát câu", "🔈 Giọng gốc", "🔊 Giọng lồng")))
            if s is None:
                self.d_title.setText("Chọn một câu")
                for e in (self.d_start, self.d_end, self.d_dstart, self.d_dend, self.d_emotion):
                    e.setText("")
                for t in (self.d_zh, self.d_sub, self.d_dub):
                    t.setPlainText("")
                self.d_flags.setText("")
                self.d_asr.setText("")
                return
            self.d_title.setText(f"Câu #{s.id}")
            set_combo(self.d_kind, s.kind)
            self.d_reviewed.setChecked(s.reviewed)
            self.d_start.setText(ms_to_clock(s.start))
            self.d_end.setText(ms_to_clock(s.end))
            self.d_dstart.setText(ms_to_clock(s.display_start))
            self.d_dend.setText(ms_to_clock(s.display_end))
            set_combo(self.d_char, s.character)
            set_combo(self.d_addr, s.addressee)
            self.d_emotion.setText(s.emotion)
            self.d_zh.setPlainText(s.zh)
            self.d_sub.setPlainText(s.vi_sub)
            self.d_dub.setPlainText(s.vi_dub)
            self.d_dubcb.setChecked(s.dub)
            self.d_keep.setChecked(s.keep_original)
            self.d_gain.setValue(s.gain_db)
            alt = s.zh_asr if s.zh_asr and s.zh_asr != s.zh else ""
            self.d_asr.setText(f"Nghe được: {alt}" if alt else "")
            self.b_use_asr.setVisible(bool(alt))
            self._update_counters()
            flags = [FLAG_LABELS.get(f, f) for f in s.flags]
            extra = [x for x in (s.qa_note, s.note) if x]
            self.d_flags.setText(("⚠ " + ", ".join(flags) if flags else "") + ("\n" + "\n".join(extra) if extra else ""))
        finally:
            self._loading = False

    def _update_counters(self):
        s = self.cur
        if not s:
            return
        n = count_vi_syllables(s.vi_dub)
        over = s.max_syllables and n > s.max_syllables
        self.d_dub_lbl.setText(f"Lời lồng tiếng — {n}/{s.max_syllables or '?'} âm tiết" + (" ⚠ dài" if over else ""))
        dur = max(1, s.display_end - s.display_start)
        c = cps(s.vi_sub, dur)
        self.d_sub_lbl.setText(f"Phụ đề Việt — {c:.0f} ký tự/giây" + (" ⚠ khó đọc kịp" if c > 20 else ""))

    # --------------------------------------------------------------- edits
    def _changed(self, structure: bool = False):
        s = self.cur
        if s is None:
            return
        if structure:
            self.table.smodel.refresh()
            self.timeline.set_segments(self.project.segments, self.project.media.fps)
            self.count_lbl.setText(f"{len(self.project.segments)} câu")
        else:
            self.table.smodel.row_changed(s.id)
            self.timeline.update()
        self._subs_timer.start(700)
        self.mix_state.setText("✎ đã sửa — bấm “Cập nhật bản trộn” để nghe")

    def _set(self, attr: str, value):
        if self._loading or not self.cur:
            return
        setattr(self.cur, attr, value)
        if attr in ("character",):
            self.table.smodel.row_changed(self.cur.id)
        self._changed()

    def _text_edited(self, attr: str, w: QPlainTextEdit):
        if self._loading or not self.cur:
            return
        setattr(self.cur, attr, w.toPlainText().strip())
        if attr == "vi_sub":
            self.cur.remove_flag("untranslated")
        self._update_counters()
        self._changed()

    def _time_edited(self, edit: QLineEdit, attr: str):
        if self._loading or not self.cur:
            return
        try:
            v = parse_time(edit.text())
        except ValueError:
            self._show(self.cur)
            return
        self._apply_time(attr, v)

    def _apply_time(self, attr: str, v: int):
        s = self.cur
        if attr == "start":
            s.start = min(v, s.end - 40)
        elif attr == "end":
            s.end = max(v, s.start + 40)
        elif attr == "disp_start":
            s.disp_start = min(v, s.display_end - 40)
            s.disp_end = s.display_end
        elif attr == "disp_end":
            s.disp_end = max(v, s.display_start + 40)
            s.disp_start = s.display_start
        self._show(s)
        self._changed(structure=attr == "start")

    def _nudge_time(self, attr: str, sign: int):
        from PySide6.QtWidgets import QApplication

        s = self.cur
        if not s:
            return
        step = (1000.0 / (self.project.media.fps or 25)) if QApplication.keyboardModifiers() & Qt.ShiftModifier else 10
        cur = {"start": s.start, "end": s.end, "disp_start": s.display_start, "disp_end": s.display_end}[attr]
        self._apply_time(attr, int(round(cur + sign * step)))

    def _set_edge(self, which: str):
        if self.cur:
            self._apply_time(which, self.player.position())

    def _auto_display(self):
        from ...core.subtitles import compute_display_times

        s = self.cur
        if not s:
            return
        s.disp_start = s.disp_end = None
        compute_display_times(self.project.segments, self.project.media.fps or 25, self.project.display,
                              self.project.shots, only_missing=True)
        self._show(s)
        self._changed()

    def _use_asr(self):
        if self.cur and self.cur.zh_asr:
            self.d_zh.setPlainText(self.cur.zh_asr)
            self.cur.remove_flag("ocr_asr_mismatch")

    def _toggle_reviewed(self):
        if self.cur:
            self.d_reviewed.setChecked(not self.cur.reviewed)

    def _on_table_edit(self, sid: int):
        if self.cur and self.cur.id == sid:
            self._show(self.cur)
        self.timeline.update()
        self._subs_timer.start(700)

    def _on_timeline_edit(self, sid: int):
        if self.cur and self.cur.id == sid:
            self._show(self.cur)
        self.table.smodel.row_changed(sid)
        self._subs_timer.start(700)

    def _refresh_subs(self):
        p = self.project
        if not p:
            return
        try:
            subs = write_project_subs(p, "source", out_ass=p.subs_src_ass)[0]
            self.player.set_subs(str(subs), build_cues(p.segments, "vi_sub", p.display, p.style.bilingual))
        except Exception as e:
            self.main.log.add("warning", f"Không cập nhật được phụ đề xem trước: {e}")

    # ------------------------------------------------------------ playback
    def _seek(self, ms: int):
        self.player.seek(ms)
        self.timeline.set_playhead(ms)
        self.pos_lbl.setText(ms_to_clock(ms))

    def _on_pos(self, ms: int):
        self.timeline.set_playhead(ms)
        self.pos_lbl.setText(ms_to_clock(ms))
        stop = getattr(self, "_stop_at", None)
        if stop is not None and ms >= stop and self.player.is_playing():
            self.player.pause()
            self._stop_at = None

    def _play_line(self, sid: int):
        s = self.project.segment(sid) if self.project else None
        if not s:
            return
        a = min(s.start, s.display_start) - 300
        b = max(s.end, s.display_end, (s.place_start or 0) + s.place_dur) + 300
        self.player.seek(max(0, a))
        self._stop_at = b
        self.player.play()

    def _play_current(self):
        if self.cur:
            self._play_line(self.cur.id)

    def _play_orig(self):
        p, s = self.project, self.cur
        if p and s:
            src = p.vocals_wav if p.vocals_wav.exists() else p.original_wav
            play_clip(src, max(0, s.start - 150), s.end + 150)

    def _play_dub(self):
        p, s = self.project, self.cur
        if p and s:
            f = s.final_file or s.tts_file
            if f:
                play_clip(p.abs(f))
            else:
                self.main.statusBar().showMessage("Câu này chưa có giọng lồng", 3000)

    def _jump(self, d: int):
        p = self.project
        if not p or not p.segments:
            return
        segs = sorted(p.segments, key=lambda s: s.start)
        if self.cur is None:
            t = self.player.position()
            nxt = next((s for s in segs if s.start > t), segs[-1]) if d > 0 else next((s for s in reversed(segs) if s.start < t), segs[0])
        else:
            i = next(i for i, s in enumerate(segs) if s.id == self.cur.id)
            nxt = segs[max(0, min(len(segs) - 1, i + d))]
        self.select(nxt.id, seek=True)

    # ----------------------------------------------------------- structure
    def _add_here(self):
        p = self.project
        if not p:
            return
        t = self.player.position()
        s = Segment(id=p.next_segment_id(), start=t, end=t + 1500, zh_source="manual")
        p.segments.append(s)
        p.sort_segments()
        self.cur = s
        self._changed(structure=True)
        self.select(s.id, seek=False)

    def _split_here(self):
        p, s = self.project, self.cur
        if not p or not s:
            return
        t = self.player.position()
        if not (s.start + 80 < t < s.end - 80):
            QMessageBox.information(self, "Tách câu", "Hãy đặt vị trí phát nằm bên trong câu cần tách.")
            return
        b = copy.deepcopy(s)
        b.id = p.next_segment_id()
        b.start, s.end = t, t
        b.disp_start = b.disp_end = s.disp_start = s.disp_end = None
        if s.words:
            s_words = [w for w in s.words if w[2] <= t + 20]
            b_words = [w for w in s.words if w[2] > t + 20]
            s.words, b.words = s_words, b_words
            s.zh = "".join(w[0] for w in s_words)
            b.zh = "".join(w[0] for w in b_words)
        b.vi_sub = b.vi_dub = ""
        b.tts_file = b.final_file = b.tts_key = b.final_key = ""
        b.tts_ms = 0
        b.place_start = None
        b.add_flag("untranslated")
        p.segments.append(b)
        p.sort_segments()
        self._auto_display_for([s, b])
        self._changed(structure=True)
        self.select(s.id, seek=False)

    def _auto_display_for(self, segs):
        from ...core.subtitles import compute_display_times

        compute_display_times(self.project.segments, self.project.media.fps or 25, self.project.display,
                              self.project.shots, only_missing=True)

    def _merge_next(self):
        p, s = self.project, self.cur
        if not p or not s:
            return
        segs = sorted(p.segments, key=lambda x: x.start)
        i = next(i for i, x in enumerate(segs) if x.id == s.id)
        if i + 1 >= len(segs):
            return
        n = segs[i + 1]
        s.end = max(s.end, n.end)
        s.disp_start = s.disp_end = None
        s.zh = (s.zh + n.zh).strip()
        s.zh_asr = (s.zh_asr + n.zh_asr).strip()
        s.vi_sub = (s.vi_sub + " " + n.vi_sub).strip()
        s.vi_dub = (s.vi_dub + " " + n.vi_dub).strip()
        s.words = (s.words or []) + (n.words or [])
        s.flags = sorted(set(s.flags) | set(n.flags))
        s.place_start = None
        p.segments = [x for x in p.segments if x.id != n.id]
        self._auto_display_for([s])
        self._changed(structure=True)
        self.select(s.id, seek=False)

    def _delete(self):
        p, s = self.project, self.cur
        if not p or not s:
            return
        if QMessageBox.question(self, "Xoá câu", f"Xoá câu #{s.id}?") != QMessageBox.Yes:
            return
        p.segments = [x for x in p.segments if x.id != s.id]
        self.cur = None
        self.table.smodel.refresh()
        self.timeline.set_segments(p.segments, p.media.fps)
        self._show(None)
        self._subs_timer.start(300)

    def _unlock(self):
        if self.cur:
            self.cur.locked = False
            self._changed()

    # ------------------------------------------------------------- AI jobs
    def _retranslate(self):
        if self.cur:
            self.run("translate", {"only_ids": [self.cur.id], "overwrite": True}, f"Dịch lại câu {self.cur.id}")

    def _shorten(self):
        if self.cur:
            self.run("shorten", {"ids": [self.cur.id]}, f"Rút gọn câu {self.cur.id}")

    def _regen(self):
        if self.cur:
            self.run("tts", {"only_ids": [self.cur.id], "force": True}, f"Tạo lại giọng câu {self.cur.id}")

    def _quick_mix(self, _v=None):
        p = self.project
        if not p:
            return
        p.mix.dub_db, p.mix.orig_db, p.mix.bgm_db = self.q_dub.value(), self.q_orig.value(), self.q_bgm.value()
        self.mix_state.setText("✎ âm lượng đã đổi — bấm “Cập nhật bản trộn”")

    def _update_mix(self):
        p = self.project
        if not p:
            return
        pos = self.player.position()

        def after(ok, res):
            if not ok:
                QMessageBox.warning(self, "Không cập nhật được", res)
                return
            self._media_loaded = False
            self._load_media()
            QTimer.singleShot(700, lambda: self._seek(pos))

        def step3(ok, res):
            if ok:
                self.run("mix", {"timelines": ["source", "output"] if p.slow_regions() else ["output"]}, "Trộn âm", after)
            else:
                after(ok, res)

        def step2(ok, res):
            if ok:
                self.run("timing", {}, "Căn nhịp", step3)
            else:
                after(ok, res)

        if any(s.has_dub() for s in p.segments):
            self.run("tts", {}, "Lồng tiếng câu đã sửa", step2)
        else:
            self.run("mix", {}, "Trộn âm", after)

    def _exact_preview(self):
        p = self.project
        if not p:
            return
        from ...pipeline.mixer import time_map_for
        from ..widgets.common import open_path

        t_out = int(time_map_for(p, "output").out(self.player.position()))

        def done(ok, res):
            if ok:
                open_path(res.strip())
            else:
                QMessageBox.warning(self, "Xem trước lỗi", res)

        self.run("preview_clip", {"start_ms": max(0, t_out - 2000), "end_ms": t_out + 18000}, "Xem bản xuất chính xác", done)
