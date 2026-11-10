"""Video player used by the editor.

* mpv backend (libmpv-2.dll next to the app or in Settings): renders the ASS
  subtitles with libass — exactly what FFmpeg burns into the export — and plays
  the rendered mix as the audio track.
* Qt backend (fallback): QMediaPlayer for video (muted) + a second player for
  the mix, with a simple subtitle overlay.
"""

from __future__ import annotations

import bisect
import locale
import os
from pathlib import Path

from PySide6.QtCore import QRectF, QSizeF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QGraphicsScene, QGraphicsTextItem, QGraphicsView, QLabel, QVBoxLayout, QWidget

from ...core.subtitles import Cue


def _try_import_mpv():
    try:
        from ...config import Settings

        d = Settings.get()["mpv_dir"]
        from ...config import APP_DIR

        for cand in (d, str(APP_DIR / "bin")):
            if cand and Path(cand).is_dir() and cand not in os.environ.get("PATH", ""):
                os.environ["PATH"] = cand + os.pathsep + os.environ.get("PATH", "")
        import mpv  # noqa: F401

        return mpv
    except Exception:
        return None


class BasePlayer(QWidget):
    positionChanged = Signal(int)
    playingChanged = Signal(bool)
    durationChanged = Signal(int)

    backend = "none"

    def load(self, video: str, audio: str | None = None, subs: str | None = None, cues: list[Cue] | None = None):
        ...

    def set_audio(self, audio: str | None) -> None:
        ...

    def set_subs(self, subs: str | None, cues: list[Cue] | None = None) -> None:
        ...

    def play(self):
        ...

    def pause(self):
        ...

    def toggle(self):
        if self.is_playing():
            self.pause()
        else:
            self.play()

    def is_playing(self) -> bool:
        return False

    def seek(self, ms: int, exact: bool = True):
        ...

    def position(self) -> int:
        return 0

    def frame_step(self, n: int = 1):
        ...

    def set_rate(self, r: float):
        ...

    def close_media(self):
        ...


class MpvPlayer(BasePlayer):
    backend = "mpv"

    def __init__(self, mpv_module, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DontCreateNativeAncestors)
        self.setAttribute(Qt.WA_NativeWindow)
        self.setMinimumSize(320, 180)
        self.setStyleSheet("background: black;")
        locale.setlocale(locale.LC_NUMERIC, "C")
        self.mpv = mpv_module.MPV(wid=str(int(self.winId())), keep_open="yes", hr_seek="yes", idle="yes",
                                  osc="no", input_default_bindings="no", input_vo_keyboard="no",
                                  sub_auto="no", audio_file_auto="no")
        self._pos = 0
        self._playing = False
        self._audio_id = None
        self._sub_id = None
        self.mpv.observe_property("time-pos", self._on_time)
        self.mpv.observe_property("pause", self._on_pause)
        self.mpv.observe_property("duration", self._on_dur)
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.positionChanged.emit(self._pos))
        self._timer.start(40)
        self._video = ""
        self._pending: tuple | None = None

    def _on_time(self, name, value):
        if value is not None:
            self._pos = int(round(value * 1000))

    def _on_pause(self, name, value):
        self._playing = not bool(value)

    def _on_dur(self, name, value):
        if value:
            self._dur = int(value * 1000)

    def load(self, video, audio=None, subs=None, cues=None):
        self._video = video
        self.mpv.pause = True
        self.mpv.loadfile(video)
        self._pending = (audio, subs)
        QTimer.singleShot(600, self._apply_pending)

    def _apply_pending(self):
        if not self._pending:
            return
        audio, subs = self._pending
        self._pending = None
        self.set_audio(audio)
        self.set_subs(subs)

    def set_audio(self, audio):
        try:
            if audio and Path(audio).exists():
                self.mpv.command("audio-add", audio, "select", "mix")
            else:
                self.mpv.aid = "auto"
        except Exception:
            pass

    def set_subs(self, subs, cues=None):
        try:
            self.mpv.command("sub-remove")
        except Exception:
            pass
        try:
            if subs and Path(subs).exists():
                self.mpv.command("sub-add", subs, "select")
        except Exception:
            pass

    def play(self):
        self.mpv.pause = False
        self.playingChanged.emit(True)

    def pause(self):
        self.mpv.pause = True
        self.playingChanged.emit(False)

    def is_playing(self):
        return self._playing

    def seek(self, ms, exact=True):
        try:
            self.mpv.seek(ms / 1000.0, reference="absolute", precision="exact" if exact else "keyframes")
            self._pos = int(ms)
        except Exception:
            pass

    def position(self):
        return self._pos

    def frame_step(self, n=1):
        try:
            if n > 0:
                self.mpv.frame_step()
            else:
                self.mpv.frame_back_step()
        except Exception:
            pass

    def set_rate(self, r):
        self.mpv.speed = r

    def close_media(self):
        try:
            self.mpv.command("stop")
        except Exception:
            pass

    def closeEvent(self, e):
        try:
            self.mpv.terminate()
        except Exception:
            pass
        super().closeEvent(e)


class QtPlayer(BasePlayer):
    backend = "qt"

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QGraphicsVideoItem

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.scene = QGraphicsScene(self)
        self.view = QGraphicsView(self.scene)
        self.view.setStyleSheet("background: black; border: none;")
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lay.addWidget(self.view)
        self.item = QGraphicsVideoItem()
        self.scene.addItem(self.item)
        self.sub_item = QGraphicsTextItem()
        self.sub_item.setDefaultTextColor(QColor("white"))
        self.scene.addItem(self.sub_item)
        self.video = QMediaPlayer(self)
        self.video.setVideoOutput(self.item)
        self.vout = QAudioOutput(self)
        self.video.setAudioOutput(self.vout)
        self.audio = QMediaPlayer(self)
        self.aout = QAudioOutput(self)
        self.audio.setAudioOutput(self.aout)
        self.has_mix = False
        self.cues: list[Cue] = []
        self._starts: list[int] = []
        self.video.positionChanged.connect(self._tick)
        self.video.durationChanged.connect(lambda d: self.durationChanged.emit(int(d)))
        self.item.nativeSizeChanged.connect(lambda *_: self._layout())
        self._sync = QTimer(self)
        self._sync.timeout.connect(self._resync)
        self._sync.start(250)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()

    def _layout(self):
        w, h = max(1, self.view.width()), max(1, self.view.height())
        self.scene.setSceneRect(QRectF(0, 0, w, h))
        self.item.setSize(QSizeF(w, h))
        self._place_sub()

    def _place_sub(self):
        w, h = max(1, self.view.width()), max(1, self.view.height())
        f = QFont("Arial", max(10, int(h * 0.045)))
        f.setBold(True)
        self.sub_item.setFont(f)
        self.sub_item.setTextWidth(w * 0.9)
        br = self.sub_item.boundingRect()
        self.sub_item.setPos(w * 0.05, h - br.height() - h * 0.04)

    def load(self, video, audio=None, subs=None, cues=None):
        self.video.setSource(QUrl.fromLocalFile(video))
        self.set_audio(audio)
        self.set_subs(subs, cues)
        self.video.pause()

    def set_audio(self, audio):
        pos = self.position()
        if audio and Path(audio).exists():
            self.audio.setSource(QUrl.fromLocalFile(audio))
            self.has_mix = True
            self.vout.setMuted(True)
            self.audio.setPosition(pos)
        else:
            self.audio.setSource(QUrl())
            self.has_mix = False
            self.vout.setMuted(False)

    def set_subs(self, subs, cues=None):
        self.cues = sorted(cues or [], key=lambda c: c.start)
        self._starts = [c.start for c in self.cues]
        self._tick(self.position())

    def _tick(self, p):
        p = int(p)
        self.positionChanged.emit(p)
        text = ""
        i = bisect.bisect_right(self._starts, p) - 1
        if 0 <= i < len(self.cues) and self.cues[i].start <= p < self.cues[i].end:
            c = self.cues[i]
            text = (c.secondary + "\n" if c.secondary else "") + c.text
        html = "<div style='text-align:center'>" + "<br>".join(
            f"<span style='background-color: rgba(0,0,0,150)'>&nbsp;{t}&nbsp;</span>" for t in text.split("\n") if t) + "</div>"
        if getattr(self, "_last_html", None) != html:
            self._last_html = html
            self.sub_item.setHtml(html if text else "")
            self._place_sub()

    def _resync(self):
        from PySide6.QtMultimedia import QMediaPlayer

        if self.has_mix and self.video.playbackState() == QMediaPlayer.PlayingState:
            if abs(self.audio.position() - self.video.position()) > 90:
                self.audio.setPosition(self.video.position())

    def play(self):
        self.video.play()
        if self.has_mix:
            self.audio.setPosition(self.video.position())
            self.audio.play()
        self.playingChanged.emit(True)

    def pause(self):
        self.video.pause()
        self.audio.pause()
        self.playingChanged.emit(False)

    def is_playing(self):
        from PySide6.QtMultimedia import QMediaPlayer

        return self.video.playbackState() == QMediaPlayer.PlayingState

    def seek(self, ms, exact=True):
        self.video.setPosition(int(ms))
        if self.has_mix:
            self.audio.setPosition(int(ms))
        self._tick(ms)

    def position(self):
        return int(self.video.position())

    def frame_step(self, n=1):
        self.seek(self.position() + int(40 * n))

    def set_rate(self, r):
        self.video.setPlaybackRate(r)
        self.audio.setPlaybackRate(r)

    def close_media(self):
        self.video.stop()
        self.audio.stop()


def make_player(parent=None) -> BasePlayer:
    mpv = _try_import_mpv()
    if mpv is not None:
        try:
            return MpvPlayer(mpv, parent)
        except Exception:
            pass
    try:
        return QtPlayer(parent)
    except Exception:
        w = BasePlayer(parent)
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Không khởi tạo được trình phát video (thiếu Qt Multimedia / libmpv)."))
        return w
