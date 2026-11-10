"""Log panel shown at the bottom of the main window."""

from __future__ import annotations

import html
import time

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from ..theme import C


class LogPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 6)
        top = QHBoxLayout()
        self.debug = QCheckBox("Hiện chi tiết kỹ thuật")
        clear = QPushButton("Xoá")
        clear.setObjectName("Flat")
        top.addWidget(self.debug)
        top.addStretch(1)
        top.addWidget(clear)
        lay.addLayout(top)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        lay.addWidget(self.text)
        clear.clicked.connect(self.text.clear)

    def add(self, level: str, message: str) -> None:
        if level == "debug" and not self.debug.isChecked():
            return
        color = {"error": C["err"], "warning": C["warn"], "debug": C["muted"]}.get(level, C["text"])
        ts = time.strftime("%H:%M:%S")
        for line in message.splitlines() or [""]:
            self.text.appendHtml(f"<span style='color:{C['muted']}'>{ts}</span> "
                                 f"<span style='color:{color}'>{html.escape(line)}</span>")
