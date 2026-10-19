"""Scoped presentation lifecycle, not a task runner or scientific QC state.

Each start issues a token. Delayed progress/errors from earlier work cannot
replace the current status. Terminal states close progress and remain in local
history; retry clears only the active presentation, never processing records.
"""

from enum import StrEnum

from PySide6.QtCore import QObject, Signal


class State(StrEnum):
    IDLE = "idle"
    VALIDATING = "validating"
    LOADING = "loading"
    READY = "ready"
    PROCESSING = "processing"
    COMPLETED = "completed"
    WARNING = "warning"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE = frozenset({State.VALIDATING, State.LOADING, State.PROCESSING})


class OperationState(QObject):
    """One logical operation's tokenized, accessible text and terminal history."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.token = 0
        self.state = State.IDLE
        self.message = ""
        self.history = []

    @property
    def busy(self):
        return self.state in ACTIVE

    def begin(self, state, message):
        """Explicit new work supersedes any previous active state."""
        if self.busy:
            self.history.append(
                dict(
                    token=self.token,
                    state=State.CANCELLED.value,
                    message="Superseded by a newer request; background work may finish safely",
                )
            )
        self.token += 1
        self.state = State(state)
        self.message = message
        self.changed.emit()
        return self.token

    def update(self, token, state, message):
        """Ignore stale/duplicate terminal callbacks and preserve real errors."""
        if token != self.token or not self.busy:
            return False
        self.state = State(state)
        self.message = message
        if not self.busy:
            self.history.append(dict(token=token, state=self.state.value, message=message))
        self.changed.emit()
        return True

    def bind(self, label):
        """One neutral status label; colors never substitute for state text."""

        def present():
            label.setText(self.message)
            label.setAccessibleName(self.message)
            label.setAccessibleDescription(self.state.value)

        self.changed.connect(present)
