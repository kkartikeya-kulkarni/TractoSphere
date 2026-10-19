"""Exact saved-task selection shared by dedicated scientific execution panels."""

from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QListWidget, QSpinBox


class SelectedTask(QComboBox):
    """Choose form-built task or an unchanged typed task, including reopened settings.

    Saved mode disables parameter widgets: editing cannot silently replace the
    selected task's values. Output location, threads and execution approval are
    separate execution controls and remain available.
    """

    def __init__(self, panel, accepts):
        super().__init__(panel)
        self.panel, self.accepts = panel, accepts
        self.setAccessibleName("Scientific task source")
        self.setToolTip(
            "Saved typed tasks execute exact parameters. Choose form settings to edit dedicated controls."
        )
        self.addItem("Form settings", None)
        self.currentIndexChanged.connect(self.changed)

    def refresh_tasks(self):
        previous = self.currentData()
        self.blockSignals(True)
        self.clear()
        self.addItem("Form settings", None)
        for task in self.panel.owner.tasks:
            if self.accepts(task.algorithm):
                self.addItem(f"Saved task: {task.id} · {task.algorithm}", task.id)
        self.setCurrentIndex(max(0, self.findData(previous)))
        self.blockSignals(False)

    def task(self):
        identity = self.currentData()
        if identity is None:
            return None
        return next(t for t in self.panel.owner.tasks if t.id == identity)

    def changed(self):
        self.apply_enabled(True, reset=True)
        self.panel.invalidate()

    def apply_enabled(self, idle, reset=False):
        if self.currentData() is None and not reset:
            return
        form_enabled = idle and self.currentData() is None
        exceptions = {self, self.panel.output, self.panel.threads, self.panel.approval}
        for widget in self.panel.findChildren(QComboBox):
            if widget not in exceptions:
                widget.setEnabled(form_enabled)
        for cls in (QLineEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QListWidget):
            for widget in self.panel.findChildren(cls):
                if widget not in exceptions and not isinstance(
                    widget.parent(), (QSpinBox, QDoubleSpinBox)
                ):
                    widget.setEnabled(form_enabled)
        self.setEnabled(idle)
