"""Explicit anatomical history and separate registration prerequisites."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from .components import button, label


class AnatomyInputs(QWidget):
    """Gather evidence without assuming processing history from a filename."""

    changed = Signal()

    def __init__(self):
        """Expose only the implemented preparation modes and review-based alignment."""
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.help = label("", "caption")
        layout.addWidget(self.help)
        self.mode = QComboBox()
        self.mode.setAccessibleName("Established anatomical input history")
        self.mode.addItem("Select established anatomical history…", "")
        self.mode.addItem("Already extracted and bias-corrected T1", "prepared_t1")
        self.mode.addItem(
            "MP2RAGE UNI + associated INV2; extraction not yet performed", "mp2rage_inv2"
        )
        self.mode.addItem(
            "Unprocessed T1: extraction and offline bias correction confirmed not done", "raw_t1"
        )
        layout.addWidget(self.mode)
        self.fields, self.rows = {}, {}
        for key, title in [
            ("t1", "Associated T1 / UNI NIfTI"),
            ("inv2", "Same-acquisition INV2 NIfTI"),
            ("evidence", "History source and subject/session association"),
            ("anatomy_manifest", "Validated anatomy manifest JSON"),
            ("anatomy_review", "Accepted anatomy review JSON"),
            ("diffusion_manifest", "Validated final N4 manifest JSON"),
            ("fsl_directory", "Installed FSL bin directory"),
        ]:
            row = QWidget()
            body = QHBoxLayout(row)
            body.setContentsMargins(0, 0, 0, 0)
            caption = label(title, "caption")
            caption.setFixedWidth(200)
            body.addWidget(caption)
            field = QLineEdit()
            field.setAccessibleName(title)
            field.setPlaceholderText(title)
            field.textChanged.connect(self.changed)
            body.addWidget(field, 1)
            if key != "evidence":
                body.addWidget(button("Browse", lambda _, k=key: self.browse(k)))
            layout.addWidget(row)
            self.fields[key], self.rows[key] = field, row
        self.operation = "anatomy"
        self.mode.currentIndexChanged.connect(self.refresh)
        self.mode.currentIndexChanged.connect(self.changed)
        self.refresh()

    def refresh(self, *_):
        """Only expose fields that the selected operation uses."""
        preparing = self.operation == "anatomy"
        self.mode.setVisible(preparing)
        for key, row in self.rows.items():
            row.setVisible(
                key == "fsl_directory"
                or (preparing and key in {"t1", "evidence"})
                or (preparing and key == "inv2" and self.mode.currentData() == "mp2rage_inv2")
                or (
                    not preparing
                    and key in {"anatomy_manifest", "anatomy_review", "diffusion_manifest"}
                )
            )
        self.help.setText(
            "Declare anatomical history and its evidence. Prepared T1 and MP2RAGE use FAST -N; MP2RAGE extraction uses its INV2. Confirmed unprocessed T1 uses BET -R (0.5) and standard FAST joint bias/tissue estimation. Unknown history is not raw-data confirmation. Review extraction, tissue boundaries and any bias field before BBR."
            if preparing
            else "Load the verified final N4 DWI with its rotated gradients. Select its manifest, the associated anatomy manifest and your saved accepted anatomy review. Rigid/BBR produces separate 3D QC images and transforms; the 4D DWI remains native."
        )

    def set_operation(self, operation):
        """Update contextual inputs without discarding the user's paths."""
        self.operation = operation
        self.refresh()

    def options(self):
        """Snapshot plain values before a worker starts; no widget crosses threads."""
        keys = (
            {"t1", "evidence", "fsl_directory"}
            if self.operation == "anatomy"
            else {"anatomy_manifest", "anatomy_review", "diffusion_manifest", "fsl_directory"}
        )
        if self.operation == "anatomy" and self.mode.currentData() == "mp2rage_inv2":
            keys.add("inv2")
        values = {k: self.fields[k].text().strip() for k in keys}
        if self.operation == "anatomy":
            values["mode"] = self.mode.currentData()
        return values

    def browse(self, key):
        """Choose explicit sources or immutable producer/review evidence."""
        if key == "fsl_directory":
            path = QFileDialog.getExistingDirectory(self, "Installed FSL bin directory")
        else:
            path, _ = QFileDialog.getOpenFileName(self, self.fields[key].placeholderText())
        if path:
            self.fields[key].setText(path)
