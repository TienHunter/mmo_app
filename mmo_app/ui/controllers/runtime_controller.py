from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ...application import Application


class _Operation(QRunnable):
    def __init__(self, operation: Callable[[], object], succeeded: Callable[[], None], failed: Callable[[str], None]):
        super().__init__()
        self._operation = operation
        self._succeeded = succeeded
        self._failed = failed

    def run(self) -> None:
        try:
            self._operation()
        except Exception as exc:
            self._failed(str(exc))
        else:
            self._succeeded()


class RuntimeController(QObject):
    """Asynchronous UI bridge around the existing Application lifecycle."""

    state_changed = Signal(bool)
    operation_failed = Signal(str)
    operation_busy = Signal(bool)

    def __init__(self, application: Application):
        super().__init__()
        self.application = application
        self._pool = QThreadPool.globalInstance()
        self._busy = False

    @Slot()
    def start_all(self) -> None:
        self._run(self.application.start, True)

    @Slot()
    def pause_all(self) -> None:
        self._run(self.application.pause, False)

    def _run(self, operation: Callable[[], object], target_state: bool) -> None:
        if self._busy:
            return
        self._busy = True
        self.operation_busy.emit(True)

        def complete() -> None:
            self._busy = False
            self.operation_busy.emit(False)
            self.state_changed.emit(target_state)

        def fail(message: str) -> None:
            self._busy = False
            self.operation_busy.emit(False)
            self.operation_failed.emit(message)

        self._pool.start(_Operation(operation, complete, fail))
