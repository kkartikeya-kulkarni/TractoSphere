"""Read-only presentation of the already calculated input validation report."""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .components import ValidationPanel
from .workstation import EmptyState, PageHeader


class DataValidationPage(QWidget):
    """No new checks: use the same immutable loader report as Data & Imports."""

    def __init__(self, theme, open_inputs):
        super().__init__()
        self.theme = theme
        self.report = None
        self.panel = None
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 24, 24, 24)
        self.root.addWidget(
            PageHeader(
                "Data validation",
                "Input integrity evidence is separate from processing QC and scientific acceptance.",
            )
        )
        self.empty = EmptyState(
            "No input report available",
            "Import diffusion data to inspect its existing validation report.",
            [("Import diffusion MRI", open_inputs)],
        )
        self.root.addWidget(self.empty, 1)

    def set_report(self, report):
        if report is self.report:
            return
        self.report = report
        if self.panel:
            self.root.removeWidget(self.panel)
            self.panel.deleteLater()
            self.panel = None
        self.empty.setVisible(report is None)
        if report is not None:
            self.panel = ValidationPanel(self.theme, report, "Input verification")
            self.root.addWidget(self.panel)
