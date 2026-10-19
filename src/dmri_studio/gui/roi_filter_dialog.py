"""Reviewed full-source ROI filtering inside the existing tractography workspace."""

from __future__ import annotations

import json
import time
from pathlib import Path
from threading import Event
from uuid import uuid4

from PySide6.QtCore import Qt, QThread
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from dmri_studio.core.execution import execute_plan
from dmri_studio.core.roi_filtering import export_roi_result, prepare_roi_filter

from .execution import ProcessingWorker


class RoiFilterDialog(QDialog):
    """No preview-only intersection: every run uses the complete immutable source."""

    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.thread = None
        self.worker = None
        self.retired = []
        self.cancel_event = Event()
        self.plan = None
        self.result_record = None
        self.setWindowTitle("Filter full source tractogram")
        self.resize(850, 760)
        self.source = viewer.current_group()
        if self.source is None:
            raise ValueError("Load the source tractogram first")
        regions = [r for r in viewer.rois if r.get("role") in ("Include", "Exclude")]
        if not regions:
            raise ValueError("Assign Include/Exclude roles before opening native filtering")
        if len({r.get("atlas_id") for r in regions}) != 1 or not regions[0].get("atlas_source"):
            raise ValueError("Use regions from one original discrete atlas per native filter")
        if any(r.get("edited") or r.get("atlas_kind") != "labels" for r in regions):
            raise ValueError(
                "This workflow uses original discrete atlas labels. Export edited/manual masks through the existing scientific ROI workflow."
            )
        if any(getattr(r, "atlas", None) is not None and not r.atlas.direct for r in regions):
            raise ValueError(
                "Atlas was sampled for display. Native filtering requires a separately verified exact-grid atlas."
            )
        self.atlas = Path(regions[0]["atlas_source"])
        self.reference = viewer.reference.path
        self.reference_volume = viewer.reference
        ordered = sorted(regions, key=lambda r: r.get("role_rank", 100000))
        self.label_names = {r["label_id"]: r["name"] for r in regions if r.get("name_available")}
        self.includes = [r["label_id"] for r in ordered if r["role"] == "Include"]
        self.excludes = [r["label_id"] for r in ordered if r["role"] == "Exclude"]
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.summary = summary = QLabel(
            f"Source: {self.source.path}\nTotal: {self.source.total:,} streamlines; filtering evaluates the complete file.\nAtlas: {self.atlas}\nInclude IDs (entered order): {self.includes}\nExclude IDs: {self.excludes}\nDisplay selections, hidden fibers and mesh smoothing do not alter filtering."
        )
        summary.setWordWrap(True)
        self.update_summary()
        form.addRow(summary)
        self.subject = QLineEdit()
        self.space = QLineEdit()
        self.evidence = QLineEdit()
        self.review_record = QLineEdit()
        self.review_fields = {
            key: QLineEdit()
            for key in (
                "session_id",
                "acquisition_id",
                "registration_qc",
                "registration_diagnostic",
            )
        }
        for key, field in self.review_fields.items():
            form.addRow(
                key.replace("_", " ") + (" JSON" if key.startswith("registration") else ""), field
            )
            field.textChanged.connect(self.invalidate)
        form.addRow("Subject ID", self.subject)
        form.addRow("Physical space ID", self.space)
        form.addRow("Reviewed alignment evidence", self.evidence)
        form.addRow("Registration record JSON (optional)", self.review_record)
        self.reviewed = QCheckBox(
            "Historical spatial declaration (native review or explicit diagnostic record required)"
        )
        form.addRow(self.reviewed)
        self.mode = QComboBox()
        self.mode.addItems(["OR", "AND", "ORDERED"])
        self.mode.setCurrentText("AND" if viewer.roi_filter_mode.currentIndex() == 0 else "OR")
        self.mode.setToolTip(
            "OR: one union mask. AND: every ROI. ORDERED: entered IDs in saved vertex direction; reversing order can change results."
        )
        form.addRow("Inclusion rule", self.mode)
        self.ends = QCheckBox("Test endpoints only (distinct from passing through ROIs)")
        form.addRow(self.ends)
        self.parent_dir = QLineEdit()
        choose = QPushButton("Choose output parent…")
        choose.clicked.connect(self.choose_output)
        form.addRow(choose, self.parent_dir)
        self.tools = QLineEdit(
            "/usr/local/mrtrix3/bin" if Path("/usr/local/mrtrix3/bin").is_dir() else ""
        )
        form.addRow("MRtrix directory", self.tools)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        layout.addWidget(self.preview, 1)
        self.approve = QCheckBox("I reviewed this exact command and approve full-source filtering")
        layout.addWidget(self.approve)
        row = QHBoxLayout()
        layout.addLayout(row)
        self.preview_button = QPushButton("Preview command")
        self.run_button = QPushButton("Run filter")
        self.cancel_button = QPushButton("Cancel job")
        self.open_button = QPushButton("Open filtered tracks")
        self.full_button = QPushButton("Return to full tractogram")
        for b in (
            self.preview_button,
            self.run_button,
            self.cancel_button,
            self.open_button,
            self.full_button,
        ):
            row.addWidget(b)
        self.preview_button.clicked.connect(self.prepare)
        self.run_button.clicked.connect(self.execute)
        self.cancel_button.clicked.connect(lambda: self.cancel_event.set())
        self.open_button.clicked.connect(self.open_result)
        self.full_button.clicked.connect(self.return_full)
        extra = QHBoxLayout()
        layout.addLayout(extra)
        for text, callback in [
            ("View QC", self.show_qc),
            ("Save configuration…", self.save_configuration),
            ("Load configuration…", self.load_configuration),
        ]:
            b = QPushButton(text)
            b.clicked.connect(callback)
            extra.addWidget(b)
        self.export_button = QPushButton("Save result as…")
        self.export_button.clicked.connect(self.export_result)
        extra.addWidget(self.export_button)
        self.status = QLabel(
            "Preview creates separate exact mask snapshots; processing requires approval."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        for field in (
            self.subject,
            self.space,
            self.evidence,
            self.review_record,
            self.parent_dir,
            self.tools,
        ):
            field.textChanged.connect(self.invalidate)
        self.mode.currentTextChanged.connect(self.invalidate)
        self.ends.toggled.connect(self.invalidate)
        self.reviewed.toggled.connect(self.invalidate)
        self.approve.toggled.connect(self.actions)
        self.actions()

    def update_summary(self):
        def labels(ids):
            return (
                ", ".join(
                    str(i) + (" · " + self.label_names[i] if i in self.label_names else "")
                    for i in ids
                )
                or "None"
            )

        self.summary.setText(
            f"Source: {self.source.path}\nTotal: {self.source.total:,}; filtering evaluates the complete file.\n"
            f"Atlas: {self.atlas}\nInclude (entered order): {labels(self.includes)}\nExclude: {labels(self.excludes)}\n"
            "Display selection, visibility and mesh smoothing do not alter filtering."
        )

    def export_result(self):
        if not self.result_record or self.thread:
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "Save verified filtered tracks", "", "TCK (*.tck)"
        )
        if name:
            result = self.result_record
            self.start(
                "export",
                lambda: export_roi_result(result, Path(name), cancelled=self.cancel_event.is_set),
            )

    def configuration(self):
        review = {k: w.text().strip() for k, w in self.review_fields.items() if w.text().strip()}
        if "registration_diagnostic" in review:
            raw = review["registration_diagnostic"]
            review["registration_diagnostic"] = json.loads(
                raw if raw.startswith("{") else Path(raw).read_text()
            )
        return dict(
            **review,
            subject_id=self.subject.text().strip(),
            space_id=self.space.text().strip(),
            alignment_evidence=self.evidence.text().strip(),
            registration_reviewed=self.reviewed.isChecked(),
            **(
                {"registration_record": self.review_record.text().strip()}
                if self.review_record.text().strip()
                else {}
            ),
        )

    def invalidate(self, *_):
        self.plan = None
        self.approve.setChecked(False)
        self.actions()

    def actions(self, *_):
        idle = self.thread is None
        for field in self.review_fields.values():
            field.setEnabled(idle)
        self.preview_button.setEnabled(idle)
        self.run_button.setEnabled(idle and self.plan is not None and self.approve.isChecked())
        self.cancel_button.setEnabled(not idle)
        self.full_button.setEnabled(idle)
        self.open_button.setEnabled(idle and self.result_record is not None)
        self.export_button.setEnabled(idle and self.result_record is not None)
        for w in (
            self.subject,
            self.space,
            self.evidence,
            self.review_record,
            self.parent_dir,
            self.tools,
            self.mode,
            self.ends,
            self.reviewed,
        ):
            w.setEnabled(idle)

    def choose_output(self):
        name = QFileDialog.getExistingDirectory(self, "Choose output parent")
        if name:
            self.parent_dir.setText(name)

    def prepare(self):
        if not Path(self.parent_dir.text()).is_dir() or not self.parent_dir.text().strip():
            self.status.setText("Choose an existing local output parent first.")
            return
        folder = Path(self.parent_dir.text()) / (
            "roi-filter-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        )
        options = dict(
            include=self.includes,
            exclude=self.excludes,
            mode=self.mode.currentText(),
            ends_only=self.ends.isChecked(),
            declarations=self.configuration(),
            tool_directory=Path(self.tools.text()) if self.tools.text() else None,
        )
        self.start(
            "preview",
            lambda: prepare_roi_filter(
                self.source.path,
                self.atlas,
                self.reference,
                folder,
                cancelled=self.cancel_event.is_set,
                progress=self.worker.progress.emit,
                **options,
            ),
        )

    def execute(self):
        if not self.plan or not self.approve.isChecked():
            return
        plan = self.plan
        self.start(
            "execute",
            lambda: execute_plan(
                None,
                plan,
                approval=plan.digest,
                cancelled=self.cancel_event.is_set,
                progress=self.worker.progress.emit,
            ),
        )

    def start(self, kind, operation):
        if self.thread:
            return
        self.kind = kind
        if kind == "execute":
            self.result_record = None
        self.cancel_event = Event()
        self.thread = QThread(self)
        self.worker = ProcessingWorker(0, operation)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.preview.appendPlainText)
        self.worker.result.connect(self.worker_result)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread_finished)
        self.status.setText(
            "Validating inputs…"
            if kind == "preview"
            else "Saving verified result…"
            if kind == "export"
            else "Filtering complete source; scientific QC follows native execution…"
        )
        self.actions()
        self.thread.start()

    def worker_result(self, _, result, error):
        if error:
            self.status.setText(error)
            return
        if self.kind == "export":
            self.status.setText("Verified TCK and provenance saved: " + str(result))
            return
        if self.kind == "preview":
            self.plan = result
            self.approve.setChecked(False)
            self.preview.setPlainText(
                "\n\n".join(c["display"] for c in result.to_dict()["commands"])
            )
            self.status.setText(
                "Review exact commands. No source tractogram has been filtered yet."
            )
        else:
            self.plan = None
            self.approve.setChecked(False)
            if result["status"] == "validated_pending_qc":
                self.result_record = result
                self.status.setText(
                    "Filtering completed and saved as a new immutable result. Scientific QC requires review."
                )
                self.show_qc()
            else:
                self.status.setText(result.get("error") or result["status"])
                self.result_record = None

    def thread_finished(self):
        t, w = self.thread, self.worker
        self.thread = self.worker = None
        w.task = lambda: None
        self.retired.append((t, w))
        t.deleteLater()
        self.actions()

    def show_qc(self):
        if self.result_record:
            self.preview.setPlainText(
                json.dumps(self.result_record.get("validation", {}), indent=2)
            )

    def source_context_available(self):
        if (
            self.viewer.reference is not self.reference_volume
            or self.source not in self.viewer.groups
        ):
            self.status.setText(
                "The original reference/source workspace was closed. Reopen the verified result through Runs or restore that workspace before comparing tracks."
            )
            return False
        return True

    def open_result(self):
        if not self.result_record or not self.source_context_available():
            return
        product = next(
            p["artifact"]
            for p in self.result_record["validation"]["products"]
            if p["role"] == "tracts"
        )
        self.source.visible = False
        existing = next((g for g in self.viewer.groups if str(g.path) == product["path"]), None)
        if existing:
            existing.visible = True
            self.viewer.tract_list.setCurrentRow(self.viewer.groups.index(existing))
            self.viewer.refresh_tracts()
        else:
            self.viewer.load_tracts(product["path"], expected_hash=product["sources"][0]["sha256"])
        self.hide()

    def return_full(self):
        if not self.source_context_available():
            return
        for g in self.viewer.groups:
            g.visible = g is self.source
        self.viewer.tract_list.setCurrentRow(self.viewer.groups.index(self.source))
        self.viewer.refresh_tracts()
        self.hide()

    def save_configuration(self):
        name, _ = QFileDialog.getSaveFileName(
            self, "Save filtering configuration", "", "JSON (*.json)"
        )
        if name:
            try:
                with open(name, "x") as f:
                    json.dump(
                        dict(
                            schema="tractosphere.roi-rule/1",
                            source=str(self.source.path),
                            atlas=str(self.atlas),
                            reference=str(self.reference),
                            include=self.includes,
                            exclude=self.excludes,
                            mode=self.mode.currentText(),
                            ends_only=self.ends.isChecked(),
                            declarations=self.configuration(),
                        ),
                        f,
                        indent=2,
                    )
            except (OSError, ValueError) as e:
                self.status.setText(str(e))

    def load_configuration(self):
        if self.thread:
            return
        name, _ = QFileDialog.getOpenFileName(
            self, "Load exact-source filtering configuration", "", "JSON (*.json)"
        )
        if not name:
            return
        try:
            value = json.loads(Path(name).read_text())
            if value.get("schema") != "tractosphere.roi-rule/1" or any(
                value[k] != str(v)
                for k, v in [
                    ("source", self.source.path),
                    ("atlas", self.atlas),
                    ("reference", self.reference),
                ]
            ):
                raise ValueError("Configuration belongs to different source files")
            self.includes = value["include"]
            self.excludes = value["exclude"]
            self.mode.setCurrentText(value["mode"])
            self.ends.setChecked(value["ends_only"])
            d = value["declarations"]
            for key, field in self.review_fields.items():
                value = d.get(key, "")
                field.setText(json.dumps(value) if isinstance(value, dict) else value)
            self.subject.setText(d["subject_id"])
            self.space.setText(d["space_id"])
            self.evidence.setText(d["alignment_evidence"])
            self.review_record.setText(d.get("registration_record", ""))
            self.reviewed.setChecked(d["registration_reviewed"])
            self.invalidate()
            self.update_summary()
            self.status.setText(
                f"Loaded Include {self.includes}; Exclude {self.excludes}. Preview revalidates source identities."
            )
        except (OSError, ValueError, KeyError) as e:
            self.status.setText(str(e))

    def closeEvent(self, event):
        if self.thread:
            self.status.setText(
                "Cancel the active job and wait for process termination before closing."
            )
            event.ignore()
        else:
            super().closeEvent(event)
