"""Job context: progress reporting + cooperative cancellation.

Pipeline functions receive a ``JobContext`` and never import Qt, so they can run
in the worker process, in tests, or from the command line.
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Callable

log = logging.getLogger("subvideo")


class Cancelled(Exception):
    """Raised when the user cancels a running job."""


class JobContext:
    def __init__(
        self,
        on_progress: Callable[[float, str], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ):
        self._on_progress = on_progress
        self._on_log = on_log
        self._is_cancelled = is_cancelled or (lambda: False)
        self._range = (0.0, 1.0)
        self._last_emit = 0.0
        self.message = ""

    # -- progress ---------------------------------------------------------
    def progress(self, frac: float, message: str | None = None, force: bool = False) -> None:
        frac = min(1.0, max(0.0, float(frac)))
        lo, hi = self._range
        val = lo + (hi - lo) * frac
        if message is not None:
            self.message = message
        now = time.monotonic()
        if force or message is not None or now - self._last_emit > 0.15:
            self._last_emit = now
            if self._on_progress:
                self._on_progress(val, self.message)
        self.check()

    def sub(self, lo: float, hi: float) -> "_SubRange":
        """Map progress 0..1 of a sub-task into [lo, hi] of the current range."""
        return _SubRange(self, lo, hi)

    # -- logging ----------------------------------------------------------
    def log(self, message: str, level: str = "info") -> None:
        getattr(log, level if level in ("debug", "info", "warning", "error") else "info")(message)
        if self._on_log:
            self._on_log(level, message)

    def warn(self, message: str) -> None:
        self.log(message, "warning")

    # -- cancellation -----------------------------------------------------
    def cancelled(self) -> bool:
        return bool(self._is_cancelled())

    def check(self) -> None:
        if self._is_cancelled():
            raise Cancelled()


class _SubRange:
    def __init__(self, ctx: JobContext, lo: float, hi: float):
        self.ctx, self.lo, self.hi = ctx, lo, hi

    def __enter__(self) -> JobContext:
        self.saved = self.ctx._range
        a, b = self.saved
        self.ctx._range = (a + (b - a) * self.lo, a + (b - a) * self.hi)
        return self.ctx

    def __exit__(self, *exc) -> None:
        self.ctx._range = self.saved


class PrintContext(JobContext):
    """Console context for CLI / tests."""

    def __init__(self, quiet: bool = False):
        def prog(v, msg):
            if not quiet:
                print(f"\r[{v * 100:5.1f}%] {msg[:90]:<90}", end="", flush=True)

        def lg(level, msg):
            if not quiet:
                print(f"\n[{level}] {msg}", flush=True)

        super().__init__(prog, lg)


def format_exc() -> str:
    return traceback.format_exc()
