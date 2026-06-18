"""Small timeout helper for blocking model calls."""

from __future__ import annotations

import signal
import threading
from collections.abc import Callable


class OperationTimeoutError(TimeoutError):
    pass


def call_with_timeout[T](fn: Callable[[], T], seconds: int | None, label: str) -> T:
    if not seconds or seconds <= 0 or threading.current_thread() is not threading.main_thread():
        return fn()

    def _handle_timeout(_signum, _frame):
        raise OperationTimeoutError(f"{label} timed out after {seconds}s")

    previous = signal.signal(signal.SIGALRM, _handle_timeout)
    signal.alarm(seconds)
    try:
        return fn()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
