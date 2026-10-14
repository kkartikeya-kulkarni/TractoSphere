"""Evidence inputs for optional matched AP/PA derivatives."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from .components import button, label


class CombinationInputs(QWidget):
    """Collect immutable pairing evidence; never infer pairs from b-values alone."""

    changed = Signal()

    def __init__(self):
        """Expose real adapter inputs and explain that corrected parents remain."""
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(
            label(
                "Retaining all corrected measurements is the default. Select this optional operation to retain both the parent and a separate combined derivative. Pairing must establish matched full diffusion measurements; reverse-PE b0 calibration alone does not qualify. No EDDY, N4 or interpolation is repeated. Review effective gradients and replacement fidelity.",
                "caption",
            )
        )
        self.fields = {}
        for key, title in [
            ("pairing_spec", "Verified pairing specification JSON"),
            ("parent_manifest", "Validated final N4 manifest JSON"),
            ("hcp_helper", "Pinned HCP average_bvecs.py"),
            ("fsl_directory", "Installed FSL bin directory"),
        ]:
            row = QHBoxLayout()
            caption = label(title, "caption")
            caption.setFixedWidth(200)
            row.addWidget(caption)
            field = QLineEdit()
            field.setAccessibleName(title)
            field.setPlaceholderText(title)
            field.textChanged.connect(self.changed)
            row.addWidget(field, 1)
            row.addWidget(button("Browse", lambda _, k=key: self.browse(k)))
            layout.addLayout(row)
            self.fields[key] = field

    def options(self):
        """Capture data before starting a background worker."""
        return {k: v.text().strip() for k, v in self.fields.items()}

    def browse(self, key):
        """Select existing evidence, never create or modify source records."""
        if key == "fsl_directory":
            path = QFileDialog.getExistingDirectory(self, "Installed FSL bin directory")
        else:
            path, _ = QFileDialog.getOpenFileName(self, self.fields[key].placeholderText())
        if path:
            self.fields[key].setText(path)
