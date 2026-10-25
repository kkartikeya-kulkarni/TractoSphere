"""Explicit custom-folder role mapping over the existing batch discovery contract."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.batch.discovery import ROLES

from .components import button, label

COMMON_ROLES = (
    ("dwi", "Diffusion image"),
    ("bval", "b-values"),
    ("bvec", "b-vectors"),
    ("json", "Acquisition JSON"),
    ("mask", "Brain mask"),
    ("t1", "Anatomical T1"),
)


class CustomMappingDialog(QDialog):
    """Build a mapping with named roles; never infer matches or scientific identity."""

    def __init__(self, dataset_root: Path, parent: QWidget | None = None):
        super().__init__(parent)
        self.dataset_root = Path(dataset_root).expanduser()
        self.saved_path: Path | None = None
        self.setWindowTitle("Create custom folder mapping")
        self.resize(700, 600)
        root = QVBoxLayout(self)
        root.addWidget(label("Describe your dataset folders", "section"))
        root.addWidget(
            label(
                "Enter folder patterns and explicitly assign filename patterns to scientific roles. "
                "A chosen example file fills only a relative filename; review the pattern for every acquisition. "
                "Discovery reports zero or multiple matches and does not certify scientific eligibility.",
                "caption",
            )
        )
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        folder_form = QFormLayout()
        layout.addLayout(folder_form)
        self.subject_pattern = QLineEdit()
        self.subject_pattern.setPlaceholderText("e.g. participant-*")
        self.session_pattern = QLineEdit()
        self.session_pattern.setPlaceholderText("Optional; blank if no session folders")
        self.acquisition_pattern = QLineEdit()
        self.acquisition_pattern.setPlaceholderText("Optional; e.g. scan-*")
        for title, field in (
            ("Subject folder pattern", self.subject_pattern),
            ("Session folder pattern", self.session_pattern),
            ("Acquisition folder pattern", self.acquisition_pattern),
        ):
            field.setAccessibleName(title)
            folder_form.addRow(title, field)
        self.example_folder = QLineEdit()
        self.example_folder.setReadOnly(True)
        self.example_folder.setAccessibleName("Example acquisition folder")
        example_row = QWidget()
        example_layout = QHBoxLayout(example_row)
        example_layout.setContentsMargins(0, 0, 0, 0)
        example_layout.addWidget(self.example_folder, 1)
        self.example_browse = button("Browse folder…", self.browse_example_folder)
        self.example_browse.setAccessibleName("Browse example acquisition folder")
        example_layout.addWidget(self.example_browse)
        folder_form.addRow("Example acquisition folder", example_row)
        layout.addWidget(
            label(
                "For each role below, enter a filename or a bounded relative pattern inside every acquisition folder. "
                "Blank roles are omitted. Choose an example folder above to browse representative files.",
                "caption",
            )
        )
        self.role_form = QFormLayout()
        layout.addLayout(self.role_form)
        self.role_patterns: dict[str, QLineEdit] = {}
        self.role_buttons = {}
        for role, title in COMMON_ROLES:
            self.add_role(role, title)
        more = QHBoxLayout()
        self.additional_role = QComboBox()
        self.additional_role.setAccessibleName("Additional scientific file role")
        self.additional_role.addItem("Choose another supported role…", None)
        for role in ROLES:
            if role not in self.role_patterns:
                self.additional_role.addItem(role.replace("_", " ").title(), role)
        more.addWidget(self.additional_role, 1)
        more.addWidget(button("Add role", self.add_selected_role))
        layout.addLayout(more)
        self.error = label("", "caption")
        root.addWidget(self.error)
        actions = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        actions.button(QDialogButtonBox.StandardButton.Save).setText("Save Mapping")
        actions.accepted.connect(self.save_mapping)
        actions.rejected.connect(self.reject)
        root.addWidget(actions)
        # Scroll-area children do not always inherit construction-order tab
        # navigation on Xvfb. Keep the three folder patterns adjacent in the
        # keyboard sequence on every platform.
        QWidget.setTabOrder(self.subject_pattern, self.session_pattern)
        QWidget.setTabOrder(self.session_pattern, self.acquisition_pattern)

    def add_role(self, role: str, title: str | None = None):
        if role in self.role_patterns:
            return
        field = QLineEdit()
        field.setAccessibleName(f"{role} relative file pattern")
        field.setPlaceholderText("Relative filename or pattern, e.g. *_dwi.nii.gz")
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(field, 1)
        browse = button(
            "Browse file…", lambda _=False, selected=role: self.browse_role_file(selected)
        )
        browse.setAccessibleName(f"Browse example {role} file")
        line.addWidget(browse)
        self.role_form.addRow(f"{title or role.replace('_', ' ').title()} ({role})", row)
        self.role_patterns[role] = field
        self.role_buttons[role] = browse

    def add_selected_role(self):
        role = self.additional_role.currentData()
        if not role:
            self.error.setText("Choose a supported file role before adding it.")
            return
        self.add_role(role)
        self.additional_role.removeItem(self.additional_role.currentIndex())
        self.additional_role.setCurrentIndex(0)
        self.error.clear()

    def browse_example_folder(self):
        if not self.dataset_root.is_dir():
            self.error.setText("Choose an existing dataset root in Batch Processing first.")
            return
        selected = QFileDialog.getExistingDirectory(
            self, "Choose one representative acquisition folder", str(self.dataset_root)
        )
        if not selected:
            return
        chosen = Path(selected).resolve()
        if not chosen.is_relative_to(self.dataset_root.resolve()):
            self.error.setText(
                "The example acquisition folder must be inside the selected dataset root."
            )
            return
        self.example_folder.setText(str(chosen))
        self.error.clear()

    def browse_role_file(self, role: str):
        if not self.example_folder.text():
            self.error.setText("Choose an example acquisition folder before browsing role files.")
            return
        folder = Path(self.example_folder.text()).resolve()
        selected, _ = QFileDialog.getOpenFileName(
            self, f"Choose example {role} file", str(folder), "All files (*)"
        )
        if not selected:
            return
        chosen = Path(selected).resolve()
        if not chosen.is_relative_to(folder):
            self.error.setText(
                "The chosen role file must be inside the example acquisition folder."
            )
            return
        self.role_patterns[role].setText(chosen.relative_to(folder).as_posix())
        self.error.setText(
            "The example filename is filled in. Edit it to an explicit relative pattern if names vary between acquisitions."
        )

    def mapping(self) -> dict:
        if not self.dataset_root.is_dir():
            raise ValueError("Choose an existing dataset root before creating a mapping.")
        subject = self.subject_pattern.text().strip()
        if not subject:
            raise ValueError("Enter the subject folder pattern explicitly.")
        files = {
            role: field.text().strip()
            for role, field in self.role_patterns.items()
            if field.text().strip()
        }
        if not files:
            raise ValueError("Assign at least one scientific file role to a relative pattern.")
        mapping = {"subject_pattern": subject, "files": files}
        for name, field in (
            ("session_pattern", self.session_pattern),
            ("acquisition_pattern", self.acquisition_pattern),
        ):
            if field.text().strip():
                mapping[name] = field.text().strip()
        for pattern in (
            subject,
            *files.values(),
            *[mapping[k] for k in ("session_pattern", "acquisition_pattern") if k in mapping],
        ):
            path = Path(pattern)
            if path.is_absolute() or ".." in path.parts or "**" in pattern:
                raise ValueError("Patterns must be bounded, relative and cannot contain ** or ..")
        return mapping

    def save_mapping(self):
        try:
            mapping = self.mapping()
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        selected, _ = QFileDialog.getSaveFileName(
            self, "Save custom dataset mapping", "custom-mapping.json", "JSON (*.json)"
        )
        if not selected:
            return
        path = Path(selected)
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(mapping, stream, indent=2)
                stream.write("\n")
        except FileExistsError:
            self.error.setText(
                "That mapping already exists. Choose a new filename; no file was overwritten."
            )
            return
        except OSError as exc:
            self.error.setText(f"Could not save the mapping: {exc}")
            return
        self.saved_path = path
        self.accept()
