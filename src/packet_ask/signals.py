"""task 동안 종료 signal을 Python cleanup 경로로 전달한다."""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType


def _task_signals() -> tuple[signal.Signals, ...]:
    """현재 플랫폼에서 packet cleanup에 연결할 종료 signal 목록."""
    names = ("SIGTERM", "SIGHUP")
    return tuple(getattr(signal, name) for name in names if hasattr(signal, name))


@contextmanager
def task_signal_handlers() -> Iterator[tuple[signal.Signals, ...]]:
    """main thread의 종료 signal을 128+signum SystemExit로 바꾼다."""
    if threading.current_thread() is not threading.main_thread():
        yield ()
        return

    managed = _task_signals()
    previous: dict[signal.Signals, signal.Handlers] = {}

    def exit_for_signal(signum: int, _frame: FrameType | None) -> None:
        raise SystemExit(128 + signum)

    try:
        for item in managed:
            previous[item] = signal.getsignal(item)
            signal.signal(item, exit_for_signal)
        yield managed
    finally:
        with blocked_signals(managed):
            for item, handler in previous.items():
                signal.signal(item, handler)


@contextmanager
def blocked_signals(managed: tuple[signal.Signals, ...]) -> Iterator[None]:
    """packet 생성·삭제의 assignment/cleanup 구간에서 종료 signal을 미룬다."""
    if not managed or not hasattr(signal, "pthread_sigmask"):
        yield
        return
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, managed)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@contextmanager
def deferred_task_signals() -> Iterator[None]:
    """spawn/assignment 동안 signal을 기록해 child 등록 직후 기존 handler로 전달한다."""
    managed = _task_signals()
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    previous: dict[signal.Signals, signal.Handlers] = {}
    pending: list[int] = []

    def defer(signum: int, _frame: FrameType | None) -> None:
        if not pending:
            pending.append(signum)

    with blocked_signals(managed):
        for item in managed:
            previous[item] = signal.getsignal(item)
            signal.signal(item, defer)
    try:
        yield
    finally:
        with blocked_signals(managed):
            for item, handler in previous.items():
                signal.signal(item, handler)
        if pending:
            _replay_signal(pending[0], previous[signal.Signals(pending[0])])


def _replay_signal(signum: int, handler: signal.Handlers) -> None:
    """defer 전에 설치돼 있던 handler 의미를 등록 완료 뒤 복원한다."""
    if handler == signal.SIG_IGN:
        return
    if callable(handler):
        handler(signum, None)
        return
    signal.raise_signal(signum)
