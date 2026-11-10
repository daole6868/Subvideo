"""Application entry point (GUI)."""

from __future__ import annotations

import logging
import multiprocessing
import sys


def main() -> int:
    multiprocessing.freeze_support()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from .config import Settings

    settings = Settings.get()
    settings.apply_env()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("SubVideo")
    app.setOrganizationName("SubVideo")
    from .ui.theme import apply_theme

    apply_theme(app, settings["theme"])
    from .ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
