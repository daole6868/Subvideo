"""Table of subtitle / dubbing lines (model + filterable view)."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QHeaderView, QStyledItemDelegate, QTableView

from ...core.project import Project, Segment
from ...core.textutil import count_vi_syllables
from ...core.timeutil import ms_to_clock, parse_time
from ..theme import C

FLAG_LABELS = {
    "ocr_asr_mismatch": "Chữ hình ≠ chữ nghe",
    "no_asr_match": "Không nghe thấy lời",
    "asr_only": "Chỉ có tiếng (OCR thiếu)",
    "need_shorten": "Lồng tiếng quá dài",
    "untranslated": "Chưa dịch",
    "qa": "Soát dịch",
    "tts_error": "Lỗi tạo giọng",
    "tts_check": "Giọng đọc sai",
}

COLS = [
    ("id", "#"),
    ("start", "Bắt đầu"),
    ("end", "Kết thúc"),
    ("who", "Nhân vật"),
    ("zh", "Tiếng Trung"),
    ("vi_sub", "Phụ đề Việt"),
    ("vi_dub", "Lời lồng tiếng"),
    ("syl", "Âm tiết"),
    ("pace", "Nhịp"),
    ("flags", "Cần xem"),
]


class SegmentModel(QAbstractTableModel):
    edited = Signal(int)  # segment id

    def __init__(self, parent=None):
        super().__init__(parent)
        self.project: Project | None = None
        self.rows: list[Segment] = []
        self.columns = [c for c, _ in COLS]

    def set_project(self, project: Project | None):
        self.beginResetModel()
        self.project = project
        self.rows = sorted(project.segments, key=lambda s: s.start) if project else []
        self.endResetModel()

    def refresh(self):
        self.set_project(self.project)

    def row_of(self, seg_id: int) -> int:
        for i, s in enumerate(self.rows):
            if s.id == seg_id:
                return i
        return -1

    def seg(self, row: int) -> Segment | None:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return dict(COLS)[self.columns[section]]
        return None

    def _who(self, s: Segment) -> str:
        if not self.project:
            return s.speaker
        ch = self.project.character_for_segment(s)
        return ch.display_name() if ch else (s.speaker or "")

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        s = self.rows[index.row()]
        col = self.columns[index.column()]
        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == "id":
                return s.id
            if col == "start":
                return ms_to_clock(s.start)
            if col == "end":
                return ms_to_clock(s.end)
            if col == "who":
                return self._who(s) if role == Qt.DisplayRole else (s.character or "")
            if col == "zh":
                return s.zh
            if col == "vi_sub":
                return s.vi_sub
            if col == "vi_dub":
                return s.vi_dub
            if col == "syl":
                n = count_vi_syllables(s.vi_dub)
                return f"{n}/{s.max_syllables}" if s.max_syllables else (str(n) if n else "")
            if col == "pace":
                if s.place_start is None or not s.has_dub():
                    return ""
                t = f"×{s.stretch:.2f}"
                if s.slow_region:
                    t += f" 🎞{s.slow_region[2]:.2f}"
                return t
            if col == "flags":
                parts = [FLAG_LABELS.get(f, f) for f in s.flags]
                if s.kind == "onscreen":
                    parts.insert(0, "Chữ trên hình")
                elif s.kind in ("nonverbal", "song"):
                    parts.insert(0, "Không lồng")
                return ", ".join(parts)
        if role == Qt.ToolTipRole:
            tips = []
            if s.zh_asr and s.zh_asr != s.zh:
                tips.append(f"Nghe được: {s.zh_asr}")
            if s.zh_ocr and s.zh_ocr != s.zh:
                tips.append(f"Chữ trên hình: {s.zh_ocr}")
            if s.qa_note:
                tips.append(s.qa_note)
            if s.note:
                tips.append(s.note)
            return "\n".join(tips) or None
        if role == Qt.BackgroundRole:
            if s.kind == "onscreen" or s.kind in ("nonverbal", "song"):
                c = QColor(C["panel2"])
                return c
            if any(f in s.flags for f in ("need_shorten", "tts_error", "untranslated")):
                c = QColor(C["err"])
                c.setAlpha(45)
                return c
            if s.flags:
                c = QColor(C["warn"])
                c.setAlpha(40)
                return c
            if s.reviewed:
                c = QColor(C["ok"])
                c.setAlpha(35)
                return c
        if role == Qt.ForegroundRole:
            if col in ("id", "start", "end", "syl", "pace") or s.kind != "speech":
                return QColor(C["muted"])
            if col == "syl" and s.max_syllables and count_vi_syllables(s.vi_dub) > s.max_syllables:
                return QColor(C["err"])
        if role == Qt.FontRole and s.kind == "onscreen":
            f = QFont()
            f.setItalic(True)
            return f
        return None

    def flags(self, index):
        f = Qt.ItemIsSelectable | Qt.ItemIsEnabled
        if self.columns[index.column()] in ("start", "end", "who", "zh", "vi_sub", "vi_dub"):
            f |= Qt.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        s = self.rows[index.row()]
        col = self.columns[index.column()]
        try:
            if col == "start":
                s.start = min(parse_time(str(value)), s.end - 40)
            elif col == "end":
                s.end = max(parse_time(str(value)), s.start + 40)
            elif col == "who":
                s.character = str(value or "")
            elif col in ("zh", "vi_sub", "vi_dub"):
                setattr(s, col, str(value).strip())
                if col == "vi_sub":
                    s.remove_flag("untranslated")
        except ValueError:
            return False
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), self.columnCount() - 1))
        self.edited.emit(s.id)
        return True

    def row_changed(self, seg_id: int):
        r = self.row_of(seg_id)
        if r >= 0:
            self.dataChanged.emit(self.index(r, 0), self.index(r, self.columnCount() - 1))


class CharacterDelegate(QStyledItemDelegate):
    def __init__(self, model: SegmentModel, parent=None):
        super().__init__(parent)
        self.model = model

    def createEditor(self, parent, option, index):
        cb = QComboBox(parent)
        cb.addItem("(theo nhận diện giọng)", "")
        if self.model.project:
            for c in self.model.project.characters:
                cb.addItem(c.display_name(), c.id)
        return cb

    def setEditorData(self, editor, index):
        val = index.data(Qt.EditRole)
        i = editor.findData(val)
        editor.setCurrentIndex(max(0, i))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData(), Qt.EditRole)


class SegmentFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mode = "all"
        self.text = ""

    def set_mode(self, mode: str):
        self.mode = mode
        self.invalidateFilter()

    def set_text(self, text: str):
        self.text = text.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        m: SegmentModel = self.sourceModel()
        s = m.seg(row)
        if s is None:
            return False
        if self.mode == "flagged" and not s.flags:
            return False
        if self.mode == "unreviewed" and s.reviewed:
            return False
        if self.mode == "dub" and not s.has_dub():
            return False
        if self.text:
            hay = f"{s.zh} {s.vi_sub} {s.vi_dub} {s.speaker}".lower()
            return self.text in hay
        return True


class SegmentTable(QTableView):
    segmentSelected = Signal(int)

    def __init__(self, model: SegmentModel | None = None, columns: list[str] | None = None, parent=None):
        super().__init__(parent)
        self.smodel = model or SegmentModel(self)
        if columns:
            self.smodel.columns = columns
        self.proxy = SegmentFilter(self)
        self.proxy.setSourceModel(self.smodel)
        self.setModel(self.proxy)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(26)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed | QAbstractItemView.AnyKeyPressed)
        hh = self.horizontalHeader()
        hh.setHighlightSections(False)
        self._apply_widths()
        if "who" in self.smodel.columns:
            self.setItemDelegateForColumn(self.smodel.columns.index("who"), CharacterDelegate(self.smodel, self))
        self.selectionModel().currentRowChanged.connect(self._row)

    def _apply_widths(self):
        hh = self.horizontalHeader()
        widths = {"id": 46, "start": 92, "end": 92, "who": 110, "syl": 64, "pace": 80, "flags": 150}
        for i, c in enumerate(self.smodel.columns):
            if c in ("zh", "vi_sub", "vi_dub"):
                hh.setSectionResizeMode(i, QHeaderView.Stretch)
            else:
                hh.setSectionResizeMode(i, QHeaderView.Interactive)
                self.setColumnWidth(i, widths.get(c, 90))

    def _row(self, cur, prev):
        if not cur.isValid():
            return
        src = self.proxy.mapToSource(cur)
        s = self.smodel.seg(src.row())
        if s:
            self.segmentSelected.emit(s.id)

    def select_id(self, seg_id: int):
        r = self.smodel.row_of(seg_id)
        if r < 0:
            return
        idx = self.proxy.mapFromSource(self.smodel.index(r, 0))
        if idx.isValid():
            self.blockSignals(True)
            self.selectionModel().blockSignals(True)
            self.setCurrentIndex(idx)
            self.selectionModel().blockSignals(False)
            self.blockSignals(False)
            self.scrollTo(idx, QAbstractItemView.PositionAtCenter)

    def current_id(self) -> int | None:
        idx = self.currentIndex()
        if not idx.isValid():
            return None
        s = self.smodel.seg(self.proxy.mapToSource(idx).row())
        return s.id if s else None
