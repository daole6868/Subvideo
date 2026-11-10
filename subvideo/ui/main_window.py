"""Main window: top bar, step sidebar, pages, job progress and log."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (QDockWidget, QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QStackedWidget, QVBoxLayout,
                               QWidget)

from .. import APP_NAME, __version__
from ..config import Settings
from ..core.project import Project
from .jobs import JobManager
from .widgets.common import button
from .widgets.log_panel import LogPanel

STATUS_ICON = {"done": "✔", "stale": "⟳", "error": "⚠", "running": "●", "todo": "○"}


class MainWindow(QMainWindow):
    projectChanged = Signal(object)
    busyChanged = Signal(bool)

    def __init__(self):
        super().__init__()
        self.settings = Settings.get()
        self.project: Project | None = None
        self.jobs = JobManager(self)
        self.setWindowTitle(f"{APP_NAME} {__version__} — Dịch & lồng tiếng video Trung → Việt")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)

        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._top_bar())
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(250)
        self.sidebar.setIconSize(QSize(18, 18))
        self.stack = QStackedWidget()
        mid.addWidget(self.sidebar)
        mid.addWidget(self.stack, 1)
        root.addLayout(mid, 1)
        self.setCentralWidget(central)

        self.log = LogPanel()
        dock = QDockWidget("Nhật ký xử lý", self)
        dock.setObjectName("LogDock")
        dock.setWidget(self.log)
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.log_dock = dock
        self.resizeDocks([dock], [105], Qt.Vertical)

        self._build_pages()
        self.sidebar.currentRowChanged.connect(self._on_nav)
        self.jobs.progress.connect(self._on_progress)
        self.jobs.log.connect(self.log.add)
        self.jobs.started.connect(self._on_started)
        self.jobs.busyChanged.connect(self._on_busy)
        self.jobs.queueChanged.connect(lambda n: self.queue_label.setText(f"+{n} việc chờ" if n else ""))

        save = QAction("Lưu", self)
        save.setShortcut(QKeySequence.Save)
        save.triggered.connect(self.save_project)
        self.addAction(save)
        self._autosave = QTimer(self)
        self._autosave.timeout.connect(lambda: self.save_project(quiet=True))
        self._autosave.start(60_000)
        self.set_project(None)
        rec = self.settings["recent_projects"]
        if rec and Project.is_project(rec[0]):
            QTimer.singleShot(200, lambda: self.open_project(rec[0]))

    # ------------------------------------------------------------- layout
    def _top_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("TopBar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(14, 8, 14, 8)
        logo = QLabel(f"<b style='font-size:13pt'>🎬 {APP_NAME}</b>")
        self.proj_label = QLabel("Chưa mở dự án")
        self.proj_label.setObjectName("Badge")
        b_new = button("＋ Dự án mới")
        b_open = button("📂 Mở dự án")
        b_save = button("💾 Lưu", "Ctrl+S")
        b_new.clicked.connect(self.new_project_dialog)
        b_open.clicked.connect(self.open_project_dialog)
        b_save.clicked.connect(self.save_project)
        self.job_label = QLabel("")
        self.job_label.setObjectName("Hint")
        self.job_label.setMinimumWidth(260)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setFixedWidth(260)
        self.progress.setVisible(False)
        self.queue_label = QLabel("")
        self.queue_label.setObjectName("Hint")
        self.cancel_btn = button("■ Dừng", "Dừng công việc đang chạy", "Danger")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.jobs.cancel)
        self.free_btn = button("♻", "Giải phóng bộ nhớ GPU (tắt tiến trình xử lý, mô hình sẽ tải lại khi cần)")
        self.free_btn.clicked.connect(self._free_gpu)
        h.addWidget(logo)
        h.addSpacing(12)
        h.addWidget(self.proj_label)
        h.addSpacing(8)
        h.addWidget(b_new)
        h.addWidget(b_open)
        h.addWidget(b_save)
        h.addStretch(1)
        h.addWidget(self.job_label)
        h.addWidget(self.progress)
        h.addWidget(self.queue_label)
        h.addWidget(self.cancel_btn)
        h.addWidget(self.free_btn)
        return bar

    def _build_pages(self):
        from .pages.auto import AutoPage
        from .pages.characters import CharactersPage
        from .pages.editor import EditorPage
        from .pages.export_page import ExportPage
        from .pages.fusion import FusionPage
        from .pages.mix import MixPage
        from .pages.ocr_page import OCRPage
        from .pages.separate import SeparatePage
        from .pages.settings_page import SettingsPage
        from .pages.source import SourcePage
        from .pages.timing_page import TimingPage
        from .pages.transcribe import TranscribePage
        from .pages.translate_page import TranslatePage
        from .pages.tts_page import TTSPage

        self.pages = []
        self._step_items: dict[str, QListWidgetItem] = {}
        order = [SourcePage, SeparatePage, TranscribePage, OCRPage, FusionPage, CharactersPage, TranslatePage,
                 TTSPage, TimingPage, MixPage, ExportPage]
        self._section("QUY TRÌNH TỪNG BƯỚC")
        for i, cls in enumerate(order, 1):
            page = cls(self)
            self._add_page(page, f"{i:>2}. {page.title}", page.step_id)
        self._section("")
        self._add_page(AutoPage(self), "⚡ Tự động (dừng trước khi xuất)")
        self._add_page(EditorPage(self), "✏️ Chỉnh sửa & căn chỉnh")
        self._add_page(SettingsPage(self), "⚙️ Cài đặt")
        self.sidebar.setCurrentRow(1)

    def _section(self, text: str):
        it = QListWidgetItem(text)
        it.setFlags(Qt.NoItemFlags)
        it.setSizeHint(QSize(10, 26 if text else 10))
        self.sidebar.addItem(it)
        self.stack.addWidget(QWidget())

    def _add_page(self, page, label: str, step: str | None = None):
        it = QListWidgetItem(label)
        it.setData(Qt.UserRole, label)
        self.sidebar.addItem(it)
        self.stack.addWidget(page)
        self.pages.append(page)
        if step:
            self._step_items[step] = it

    def page(self, cls_name: str):
        return next((p for p in self.pages if type(p).__name__ == cls_name), None)

    def go(self, cls_name: str):
        for i in range(self.stack.count()):
            if type(self.stack.widget(i)).__name__ == cls_name:
                self.sidebar.setCurrentRow(i)
                return

    def _on_nav(self, row: int):
        if row < 0:
            return
        w = self.stack.widget(row)
        if not hasattr(w, "on_show"):
            return
        prev = self.stack.currentWidget()
        if prev is not w and hasattr(prev, "save_project") and self.project:
            prev.save_project(self.project)
        self.stack.setCurrentIndex(row)
        w.on_show()

    # ------------------------------------------------------------ project
    def set_project(self, project: Project | None):
        self.project = project
        if project:
            self.proj_label.setText(f"📁 {project.name}")
            self.proj_label.setToolTip(str(project.root))
        else:
            self.proj_label.setText("Chưa mở dự án")
        for p in self.pages:
            try:
                p.load_project(project)
                p.refresh_status()
                p.set_busy(self.jobs.busy())
            except Exception as e:  # keep the UI alive
                self.log.add("error", f"Lỗi hiển thị {type(p).__name__}: {e}")
        self._refresh_sidebar()
        self.projectChanged.emit(project)

    def _refresh_sidebar(self):
        for step, it in self._step_items.items():
            base = it.data(Qt.UserRole)
            st = self.project.steps.get(step).status if self.project else "todo"
            it.setText(f"{base}   {STATUS_ICON.get(st, '')}" if self.project else base)

    def reload_project(self):
        if not self.project:
            return
        try:
            self.project.reload()
        except Exception as e:
            self.log.add("error", f"Không đọc lại được dự án: {e}")
            return
        self.set_project(self.project)

    def save_project(self, quiet: bool = False):
        if not self.project or self.jobs.busy():
            return
        for p in self.pages:
            try:
                p.save_project(self.project)
            except Exception as e:
                self.log.add("error", f"Lỗi lưu {type(p).__name__}: {e}")
        self.project.save()
        if not quiet:
            self.statusBar().showMessage("Đã lưu dự án", 2500)

    def new_project_dialog(self):
        base = self.settings["projects_dir"] or str(Path.home() / "SubVideo")
        name, ok = QInputDialog.getText(self, "Dự án mới", "Tên dự án (ví dụ: TenPhim_Tap01):")
        if not ok or not name.strip():
            return
        folder = QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa dự án", base)
        if not folder:
            return
        root = Path(folder) / name.strip()
        try:
            proj = Project.create(root, name.strip())
        except FileExistsError:
            proj = Project.load(root)
        self.settings["projects_dir"] = folder
        self.settings.add_recent(str(root))
        self.set_project(proj)
        self.go("SourcePage")

    def open_project_dialog(self):
        base = self.settings["projects_dir"] or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Mở thư mục dự án", base)
        if folder:
            self.open_project(folder)

    def open_project(self, folder: str):
        if not Project.is_project(folder):
            QMessageBox.warning(self, "Không phải dự án", "Thư mục này không có project.json.")
            return
        if self.project and not self.jobs.busy():
            self.save_project(quiet=True)
        try:
            proj = Project.load(folder)
        except Exception as e:
            QMessageBox.critical(self, "Lỗi", f"Không mở được dự án:\n{e}")
            return
        self.settings.add_recent(str(Path(folder)))
        self.set_project(proj)
        self.log.add("info", f"Đã mở dự án {proj.name}")

    # --------------------------------------------------------------- jobs
    def run_job(self, stage: str, params: dict, title: str, on_done=None, project_required: bool = True):
        if project_required and not self.project:
            QMessageBox.information(self, "Chưa có dự án", "Hãy tạo hoặc mở một dự án trước.")
            return
        if self.project and not self.jobs.busy():
            self.save_project(quiet=True)
        if self.project:
            st = self.project.steps.get(stage)
            if st:
                st.status = "running"

        def done(ok: bool, result: str):
            self.reload_project()
            if on_done:
                on_done(ok, result)
            elif not ok:
                QMessageBox.warning(self, "Không thành công", f"{title}:\n\n{result}")
            else:
                self.statusBar().showMessage(f"✔ {title}: {result.splitlines()[0] if result else ''}", 6000)

        self.jobs.submit(stage, str(self.project.root) if self.project else "", params, title, done)

    def _on_started(self, jid: str, title: str):
        self.job_label.setText(f"● {title}")
        self.progress.setValue(0)

    def _on_progress(self, jid: str, value: float, msg: str):
        self.progress.setValue(int(value * 1000))
        if msg:
            self.job_label.setText(msg[:60])

    def _on_busy(self, busy: bool):
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        if not busy:
            self.job_label.setText("")
        for p in self.pages:
            p.set_busy(busy)
        self.busyChanged.emit(busy)

    def _free_gpu(self):
        if self.jobs.busy():
            if QMessageBox.question(self, "Đang xử lý", "Đang có việc chạy. Dừng và giải phóng bộ nhớ?") != QMessageBox.Yes:
                return
        self.jobs.restart_workers()
        self.log.add("info", "Đã tắt tiến trình xử lý — bộ nhớ GPU được giải phóng.")

    def closeEvent(self, e: QCloseEvent):
        if self.jobs.busy():
            r = QMessageBox.question(self, "Đang xử lý", "Có công việc đang chạy. Thoát và dừng công việc?")
            if r != QMessageBox.Yes:
                e.ignore()
                return
        self.save_project(quiet=True)
        self.jobs.shutdown()
        for p in self.pages:
            if hasattr(p, "shutdown"):
                p.shutdown()
        e.accept()
