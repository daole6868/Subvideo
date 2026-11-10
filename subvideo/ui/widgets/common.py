"""Small reusable widgets and helpers."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from PySide6.QtCore import QUrl, Qt, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QFrame, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QSizePolicy, QSlider, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from ...core.timeutil import ms_to_clock


def hint(text: str, wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("Hint")
    lb.setWordWrap(wrap)
    lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return lb


def primary(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setObjectName("Primary")
    b.setCursor(Qt.PointingHandCursor)
    return b


def button(text: str, tip: str = "", obj: str = "") -> QPushButton:
    b = QPushButton(text)
    if tip:
        b.setToolTip(tip)
    if obj:
        b.setObjectName(obj)
    b.setCursor(Qt.PointingHandCursor)
    return b


def tool(text: str, tip: str = "", checkable: bool = False) -> QToolButton:
    b = QToolButton()
    b.setText(text)
    b.setToolTip(tip)
    b.setCheckable(checkable)
    b.setCursor(Qt.PointingHandCursor)
    return b


def card(title: str = "") -> tuple[QGroupBox, QVBoxLayout]:
    g = QGroupBox(title)
    lay = QVBoxLayout(g)
    lay.setSpacing(8)
    return g, lay


def hrow(*widgets, stretch_last: bool = False, spacing: int = 8) -> QHBoxLayout:
    h = QHBoxLayout()
    h.setSpacing(spacing)
    for w in widgets:
        if w is None:
            h.addStretch(1)
        elif isinstance(w, int):
            h.addSpacing(w)
        elif hasattr(w, "setParent") and isinstance(w, QWidget):
            h.addWidget(w)
        else:
            h.addLayout(w)
    if stretch_last:
        h.addStretch(1)
    return h


def combo(items: Iterable[tuple[Any, str]], current: Any = None) -> QComboBox:
    cb = QComboBox()
    for val, label in items:
        cb.addItem(label, val)
    if current is not None:
        set_combo(cb, current)
    return cb


def set_combo(cb: QComboBox, value: Any) -> None:
    for i in range(cb.count()):
        if cb.itemData(i) == value:
            cb.setCurrentIndex(i)
            return


def combo_value(cb: QComboBox) -> Any:
    return cb.currentData()


def spin(lo: int, hi: int, val: int, suffix: str = "", step: int = 1) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(val))
    s.setSingleStep(step)
    if suffix:
        s.setSuffix(suffix)
    return s


def dspin(lo: float, hi: float, val: float, step: float = 0.05, decimals: int = 2, suffix: str = "") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    s.setValue(float(val))
    if suffix:
        s.setSuffix(suffix)
    return s


class DbSlider(QWidget):
    """Volume slider in dB (-60 = muted)."""

    changed = Signal(float)

    def __init__(self, label: str, value: float = 0.0, lo: float = -60.0, hi: float = 12.0, parent=None):
        super().__init__(parent)
        self.lo, self.hi = lo, hi
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(label)
        self.name.setMinimumWidth(150)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(int(lo * 2), int(hi * 2))
        self.slider.setValue(int(value * 2))
        self.val = QLabel()
        self.val.setMinimumWidth(70)
        self.val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.mute = tool("🔇", "Tắt / bật", checkable=True)
        lay.addWidget(self.name)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.val)
        lay.addWidget(self.mute)
        self._last = value if value > lo else 0.0
        self.slider.valueChanged.connect(self._on)
        self.mute.toggled.connect(self._on_mute)
        self._on(self.slider.value())

    def value(self) -> float:
        return self.slider.value() / 2.0

    def setValue(self, v: float) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(round(max(self.lo, min(self.hi, v)) * 2)))
        self.slider.blockSignals(False)
        self._update_label()

    def _update_label(self) -> None:
        v = self.value()
        self.mute.blockSignals(True)
        self.mute.setChecked(v <= self.lo + 0.01)
        self.mute.blockSignals(False)
        self.val.setText("Tắt" if v <= self.lo + 0.01 else f"{v:+.1f} dB")

    def _on(self, raw: int) -> None:
        self._update_label()
        if self.value() > self.lo:
            self._last = self.value()
        self.changed.emit(self.value())

    def _on_mute(self, on: bool) -> None:
        self.slider.setValue(int(self.lo * 2) if on else int(self._last * 2))


class PathEdit(QWidget):
    changed = Signal(str)

    def __init__(self, value: str = "", mode: str = "file", filter: str = "", placeholder: str = "", parent=None):
        super().__init__(parent)
        self.mode, self.filter = mode, filter
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText(placeholder)
        b = button("…", "Chọn")
        b.setFixedWidth(34)
        lay.addWidget(self.edit, 1)
        lay.addWidget(b)
        b.clicked.connect(self._browse)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.edit.text()))

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, t: str) -> None:
        self.edit.setText(t or "")

    def _browse(self) -> None:
        cur = self.text()
        if self.mode == "dir":
            p = QFileDialog.getExistingDirectory(self, "Chọn thư mục", cur)
        elif self.mode == "save":
            p, _ = QFileDialog.getSaveFileName(self, "Lưu thành", cur, self.filter)
        else:
            p, _ = QFileDialog.getOpenFileName(self, "Chọn tệp", cur, self.filter)
        if p:
            self.edit.setText(p)
            self.changed.emit(p)


def open_path(path: str | Path) -> None:
    p = str(path)
    if sys.platform.startswith("win"):
        os.startfile(p)  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", p])
    else:
        QDesktopServices.openUrl(QUrl.fromLocalFile(p))


def stat_box(title: str) -> tuple[QFrame, QLabel]:
    f = QFrame()
    f.setObjectName("Card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 8, 12, 8)
    t = QLabel(title)
    t.setObjectName("Hint")
    v = QLabel("—")
    v.setObjectName("Stat")
    lay.addWidget(t)
    lay.addWidget(v)
    f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    return f, v


class AudioPlayer(QWidget):
    """Minimal audio player (play/pause + seek) for a file."""

    def __init__(self, label: str = "", parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        self.player = QMediaPlayer(self)
        self.out = QAudioOutput(self)
        self.player.setAudioOutput(self.out)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.name = QLabel(label)
        self.name.setMinimumWidth(110)
        self.btn = tool("▶", "Phát / dừng")
        self.slider = QSlider(Qt.Horizontal)
        self.time = QLabel("00:00 / 00:00")
        self.time.setObjectName("Hint")
        if label:
            lay.addWidget(self.name)
        lay.addWidget(self.btn)
        lay.addWidget(self.slider, 1)
        lay.addWidget(self.time)
        self.btn.clicked.connect(self.toggle)
        self.player.positionChanged.connect(self._pos)
        self.player.durationChanged.connect(lambda d: self.slider.setRange(0, int(d)))
        self.player.playbackStateChanged.connect(self._state)
        self.slider.sliderMoved.connect(self.player.setPosition)
        self.path: str = ""
        self.setEnabled(False)

    def set_file(self, path: str | Path | None) -> None:
        self.player.stop()
        if path and Path(path).exists():
            self.path = str(path)
            self.player.setSource(QUrl.fromLocalFile(str(path)))
            self.setEnabled(True)
        else:
            self.path = ""
            self.player.setSource(QUrl())
            self.setEnabled(False)

    def play_range(self, start_ms: int, end_ms: int | None = None) -> None:
        if not self.path:
            return
        self.player.setPosition(int(start_ms))
        self.player.play()
        self._stop_at = end_ms

    def toggle(self) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self._stop_at = None
            self.player.play()

    def _pos(self, p: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(int(p))
        self.time.setText(f"{ms_to_clock(p, False)} / {ms_to_clock(self.player.duration(), False)}")
        stop = getattr(self, "_stop_at", None)
        if stop is not None and p >= stop:
            self.player.pause()
            self._stop_at = None

    def _state(self, st) -> None:
        from PySide6.QtMultimedia import QMediaPlayer

        self.btn.setText("⏸" if st == QMediaPlayer.PlayingState else "▶")

    def stop(self) -> None:
        self.player.stop()


_CLIP_PLAYER: AudioPlayer | None = None


def play_clip(path: str | Path, start_ms: int = 0, end_ms: int | None = None) -> None:
    """Play a short audio file/range in a shared hidden player."""
    global _CLIP_PLAYER
    if _CLIP_PLAYER is None:
        _CLIP_PLAYER = AudioPlayer()
    _CLIP_PLAYER.set_file(path)
    _CLIP_PLAYER.play_range(start_ms, end_ms)


def stop_clip() -> None:
    if _CLIP_PLAYER is not None:
        _CLIP_PLAYER.stop()
