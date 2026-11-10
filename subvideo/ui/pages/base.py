"""Base class for all step pages."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ...core.project import Project

if TYPE_CHECKING:
    from ..main_window import MainWindow


class BasePage(QWidget):
    step_id: str | None = None
    title = ""
    subtitle = ""
    icon = ""
    scroll = True

    def __init__(self, main: "MainWindow"):
        super().__init__()
        self.setObjectName("Page")
        self.main = main
        self._actions: list[QWidget] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        head = QFrame()
        hl = QVBoxLayout(head)
        hl.setContentsMargins(22, 16, 22, 6)
        t = QLabel(f"{self.icon}  {self.title}" if self.icon else self.title)
        t.setObjectName("PageTitle")
        hl.addWidget(t)
        if self.subtitle:
            st = QLabel(self.subtitle)
            st.setObjectName("PageSubtitle")
            st.setWordWrap(True)
            hl.addWidget(st)
        self.status_row = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setObjectName("Badge")
        self.status_label.setVisible(False)
        self.status_row.addWidget(self.status_label)
        self.status_row.addStretch(1)
        hl.addLayout(self.status_row)
        outer.addWidget(head)
        body = QWidget()
        body.setObjectName("Page")
        self.body = QVBoxLayout(body)
        self.body.setContentsMargins(22, 6, 22, 18)
        self.body.setSpacing(12)
        if self.scroll:
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setFrameShape(QFrame.NoFrame)
            sa.setWidget(body)
            outer.addWidget(sa, 1)
        else:
            outer.addWidget(body, 1)
        self.build()

    # -------------------------------------------------------------- hooks
    def build(self) -> None:
        """Create widgets (override)."""

    def load_project(self, project: Project | None) -> None:
        """Refresh widgets from the project (override)."""

    def save_project(self, project: Project) -> None:
        """Write widget values into the project before a job runs (override)."""

    def on_show(self) -> None:
        """Called when the page becomes visible."""

    # ------------------------------------------------------------ helpers
    @property
    def project(self) -> Project | None:
        return self.main.project

    def action(self, w: QWidget) -> QWidget:
        """Register a widget that must be disabled while a job runs."""
        self._actions.append(w)
        return w

    def set_busy(self, busy: bool) -> None:
        for w in self._actions:
            w.setEnabled(not busy and self.project is not None)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def run(self, stage: str, params: dict | None = None, title: str = "", on_done=None) -> None:
        self.main.run_job(stage, params or {}, title or self.title, on_done)

    def refresh_status(self) -> None:
        p = self.project
        if not p or not self.step_id:
            self.set_status("")
            return
        st = p.steps.get(self.step_id)
        if not st:
            return
        txt = {"done": "✔ Đã xong", "stale": "⟳ Cần chạy lại (bước trước đã thay đổi)", "error": "⚠ Lỗi",
               "running": "● Đang chạy", "todo": "○ Chưa chạy"}.get(st.status, st.status)
        if st.message:
            txt += f" — {st.message}"
        self.set_status(txt)


def placeholder(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setAlignment(Qt.AlignCenter)
    lb.setObjectName("Hint")
    return lb
