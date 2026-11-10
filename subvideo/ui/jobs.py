"""Job manager: runs pipeline stages in background worker processes.

One worker process per Python interpreter (an engine with conflicting
dependencies, e.g. Qwen3-ASR, can use its own virtual environment).  Jobs run
one at a time so two jobs never edit the same project simultaneously.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from ..config import APP_DIR, Settings


def _windowless(python: str) -> str:
    """On Windows use pythonw.exe for workers so no console window pops up."""
    if sys.platform.startswith("win") and python.lower().endswith("python.exe"):
        cand = python[:-10] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return python


@dataclass
class Job:
    id: str
    stage: str
    project: str
    params: dict
    title: str
    python: str
    on_done: Callable[[bool, str], None] | None = None
    extra: dict = field(default_factory=dict)


class WorkerProcess(QObject):
    message = Signal(dict)
    died = Signal(str)
    stderr = Signal(str)

    def __init__(self, python: str, parent=None):
        super().__init__(parent)
        self.python = python
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        pp = env.value("PYTHONPATH", "")
        env.insert("PYTHONPATH", str(APP_DIR) + (os.pathsep + pp if pp else ""))
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        env.insert("PYTHONUTF8", "1")
        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(str(APP_DIR))
        self.proc.readyReadStandardOutput.connect(self._out)
        self.proc.readyReadStandardError.connect(self._err)
        self.proc.finished.connect(self._finished)
        self._buf = b""
        self._ebuf = b""
        self.ready = False
        self.proc.start(python, ["-u", "-m", "subvideo.worker"])

    def send(self, obj: dict) -> None:
        self.proc.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    def alive(self) -> bool:
        return self.proc.state() != QProcess.NotRunning

    def kill(self) -> None:
        if self.alive():
            try:
                self.send({"cmd": "quit"})
            except Exception:
                pass
            self.proc.kill()
            self.proc.waitForFinished(2000)

    def _out(self) -> None:
        self._buf += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                self.stderr.emit(line.decode("utf-8", "replace"))
                continue
            if msg.get("type") == "ready":
                self.ready = True
            self.message.emit(msg)

    def _err(self) -> None:
        self._ebuf += bytes(self.proc.readAllStandardError())
        while b"\n" in self._ebuf or b"\r" in self._ebuf:
            idx = min(i for i in (self._ebuf.find(b"\n"), self._ebuf.find(b"\r")) if i >= 0)
            line, self._ebuf = self._ebuf[:idx], self._ebuf[idx + 1:]
            t = line.decode("utf-8", "replace").strip()
            if t:
                self.stderr.emit(t)

    def _finished(self, code, status) -> None:
        self.died.emit(f"Tiến trình xử lý đã dừng (mã {code}).")


class JobManager(QObject):
    progress = Signal(str, float, str)
    log = Signal(str, str)
    started = Signal(str, str)
    finished = Signal(str, bool, str)
    busyChanged = Signal(bool)
    queueChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.workers: dict[str, WorkerProcess] = {}
        self.queue: deque[Job] = deque()
        self.current: Job | None = None
        self._cancel_timer = QTimer(self)
        self._cancel_timer.setSingleShot(True)
        self._cancel_timer.timeout.connect(self._hard_cancel)

    # -------------------------------------------------------------- api
    def python_for(self, stage: str, params: dict) -> str:
        s = Settings.get()
        py = sys.executable
        if stage in ("transcribe", "auto"):
            eng = params.get("engine") or (params.get("overrides", {}).get("transcribe", {}).get("engine"))
            if eng == "qwen3" and s["python_qwen"]:
                py = s["python_qwen"]
        if stage in ("tts", "timing", "voice_preview") and s["python_tts"]:
            py = s["python_tts"]
        return _windowless(py)

    def submit(self, stage: str, project_dir: str, params: dict | None = None, title: str = "",
               on_done: Callable[[bool, str], None] | None = None) -> str:
        params = params or {}
        job = Job(uuid.uuid4().hex[:10], stage, project_dir, params, title or stage, self.python_for(stage, params), on_done)
        self.queue.append(job)
        self.queueChanged.emit(len(self.queue))
        self._next()
        return job.id

    def busy(self) -> bool:
        return self.current is not None

    def cancel(self) -> None:
        if self.current is None:
            return
        w = self.workers.get(self.current.python)
        if w:
            w.send({"cmd": "cancel", "id": self.current.id})
        self.log.emit("warning", "Đang dừng công việc…")
        self._cancel_timer.start(8000)

    def clear_queue(self) -> None:
        self.queue.clear()
        self.queueChanged.emit(0)

    def restart_workers(self) -> None:
        """Kill all worker processes (frees GPU memory)."""
        for w in list(self.workers.values()):
            w.kill()
        self.workers.clear()
        if self.current is not None:
            self._finish(False, "Đã khởi động lại tiến trình xử lý")

    def shutdown(self) -> None:
        self.queue.clear()
        for w in list(self.workers.values()):
            w.kill()
        self.workers.clear()

    # --------------------------------------------------------- internals
    def _worker(self, python: str) -> WorkerProcess:
        w = self.workers.get(python)
        if w is None or not w.alive():
            w = WorkerProcess(python, self)
            w.message.connect(self._on_message)
            w.stderr.connect(lambda t: self.log.emit("debug", t))
            w.died.connect(lambda msg, py=python: self._on_died(py, msg))
            self.workers[python] = w
        return w

    def _next(self) -> None:
        if self.current is not None or not self.queue:
            return
        job = self.queue.popleft()
        self.queueChanged.emit(len(self.queue))
        self.current = job
        self.busyChanged.emit(True)
        self.started.emit(job.id, job.title)
        self.log.emit("info", f"▶ {job.title}")
        w = self._worker(job.python)
        w.send({"cmd": "run", "id": job.id, "stage": job.stage, "project": job.project, "params": job.params})

    def _on_message(self, msg: dict) -> None:
        t = msg.get("type")
        if t == "ready":
            return
        if self.current is None or msg.get("id") != self.current.id:
            return
        if t == "progress":
            self.progress.emit(self.current.id, float(msg.get("value", 0)), msg.get("message", ""))
        elif t == "log":
            self.log.emit(msg.get("level", "info"), msg.get("message", ""))
        elif t == "done":
            self._finish(True, str(msg.get("result", "")))
        elif t == "cancelled":
            self._finish(False, "Đã dừng theo yêu cầu")
        elif t == "error":
            self.log.emit("debug", msg.get("trace", ""))
            self._finish(False, msg.get("error", "Lỗi"))

    def _on_died(self, python: str, message: str) -> None:
        self.workers.pop(python, None)
        if self.current is not None and self.current.python == python:
            self.log.emit("error", message)
            self._finish(False, message + " Hãy xem nhật ký (có thể thiếu thư viện hoặc hết bộ nhớ GPU).")

    def _hard_cancel(self) -> None:
        if self.current is None:
            return
        w = self.workers.pop(self.current.python, None)
        if w:
            w.kill()
        self._finish(False, "Đã buộc dừng")

    def _finish(self, ok: bool, result: str) -> None:
        self._cancel_timer.stop()
        job = self.current
        self.current = None
        if job is None:
            return
        self.log.emit("info" if ok else "error", ("✔ " if ok else "✖ ") + f"{job.title}: {result.splitlines()[0] if result else ''}")
        self.finished.emit(job.id, ok, result)
        if job.on_done:
            try:
                job.on_done(ok, result)
            except Exception as e:  # never break the queue
                self.log.emit("error", f"Lỗi xử lý kết quả: {e}")
        self.busyChanged.emit(self.current is not None or bool(self.queue))
        QTimer.singleShot(0, self._next)
