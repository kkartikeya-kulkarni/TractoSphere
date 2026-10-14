"""Asynchronous, shared first-run/setup UI for optional scientific executables."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.dependencies import INSTALL_LINKS, inspect_dependencies
from dmri_studio.core.software_setup import family_state, verify_archive

from .components import button, label
from .settings import Settings
from .workers import ViewerTaskWorker


class SetupWorker(ViewerTaskWorker):
    """Use the established owner-thread cleanup pattern for bounded version probes."""

    progress = Signal(str)


class DependencyController(QObject):
    """One scan per window, shared between first run and preferences; no downloads."""

    updated = Signal(object)
    busy_changed = Signal(bool)
    progress = Signal(str)
    configuration_applied = Signal(object)
    archive_checked = Signal(object, str)

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.thread = self.worker = None
        self.cancel = Event()
        self.report = None
        self.error = ""
        self.inputs = self.saved_directories()
        self.common = bool(settings.get("dependencies/search_common", True))
        self.sequence = self.last_report_request = 0
        self.task_kind = "inventory"

    def saved_directories(self) -> dict:
        """Read only app-owned preferences, never system profiles or tool configs."""
        return {name: str(self.settings.get(f"dependencies/{name}", "")) for name in INSTALL_LINKS}

    def scan(self, directories: dict, common=True):
        """Launch one background inventory; all widget values are copied first."""
        if self.thread is not None:
            return
        self.inputs = dict(directories)
        self.common = common
        frozen = dict(directories)
        return self.launch_task(
            lambda: inspect_dependencies(
                directories=frozen,
                search_common=common,
                cancelled=self.cancel.is_set,
                progress=self.progress.emit,
            ),
            "inventory",
        )

    def launch_task(self, function, kind):
        """Reuse the existing single owned worker for inventory or local checksum I/O."""
        if self.thread is not None:
            return None
        self.sequence += 1
        self.task_kind = kind
        self.cancel = Event()
        self.error = ""
        self.thread = QThread(self)
        self.worker = SetupWorker(self.sequence, function)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.result.connect(self.finish_result)
        self.worker.finished.connect(self.thread.quit, Qt.ConnectionType.DirectConnection)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.retire)
        self.busy_changed.emit(True)
        self.progress.emit(
            "Inspecting installed software; no MRI commands or installation…"
            if kind == "inventory"
            else "Reading archive SHA-256; no extraction or installation…"
        )
        self.thread.start()
        return self.sequence

    def check_archive(self, path, guide):
        """Hash a user-selected download through the same bounded-lifecycle worker."""
        return self.launch_task(
            lambda: verify_archive(path, guide, cancelled=self.cancel.is_set), "archive"
        )

    def finish_result(self, request, report, error):
        """Publish a genuine result/error, keeping cancellation explicitly incomplete."""
        if self.task_kind == "archive":
            self.progress.emit(
                "Archive verification failed/interrupted; see setup details."
                if error
                else "Archive integrity check complete; installation/version verification remains separate."
            )
            self.archive_checked.emit(report, error)
        else:
            self.last_report_request = request
            self.report, self.error = report, error
            self.updated.emit(report)

    def retire(self):
        """Release owned QObjects only after the bounded worker is idle."""
        self.thread.deleteLater()
        self.thread = self.worker = None
        self.busy_changed.emit(False)

    def apply(self):
        """Explicit user action: save bin paths as app defaults, never alter tools."""
        if not self.report or self.report["cancelled"] or self.thread is not None:
            raise ValueError("Finish a complete scan before saving tool locations.")
        locations = {}
        for family in INSTALL_LINKS:
            paths = {
                str(Path(r["path"]).parent)
                for r in self.report["tools"]
                if r["family"] == family and r["path"]
            }
            if len(paths) > 1:
                raise ValueError(
                    f"{family} uses multiple executable folders. Select one installation and recheck."
                )
            locations[family] = next(iter(paths)) if paths else self.inputs.get(family, "")
        # The window consumes this signal before settings persist; it protects
        # active plans/runs and explicitly invalidates previews when paths change.
        self.configuration_applied.emit(locations)
        for family, path in locations.items():
            self.settings.set(f"dependencies/{family}", path)
        self.settings.set("dependencies/search_common", self.common)
        self.settings.store.sync()
        return locations

    def apply_verified_family(self, family):
        """Persist one fully verified family; leave unrelated manual paths/settings alone."""
        if (
            self.thread is not None
            or self.cancel.is_set()
            or family_state(self.report, family)["state"] != "ready"
        ):
            raise ValueError(
                "This installation is incomplete, incompatible or unsupported; no settings saved."
            )
        rows = [r for r in self.report["tools"] if r["family"] == family]
        path = str(Path(rows[0]["path"]).parent)
        old = self.settings.get(f"dependencies/{family}", "")
        self.settings.set(f"dependencies/{family}", path)
        self.settings.store.sync()
        if self.settings.store.status() != QSettings.Status.NoError:
            self.settings.set(f"dependencies/{family}", old)
            self.settings.store.sync()
            raise OSError(
                "Could not save application settings; previous configuration retained. Check preferences permissions."
            )
        self.inputs[family] = path
        self.configuration_applied.emit({family: path})
        return path

    def shutdown(self):
        """Cooperative stop between read-only queries; never kill an MRI process."""
        self.cancel.set()
        return self.thread is None


class DependencyWidget(QWidget):
    """Exact executable inventory, adapter requirements and explicit path choices."""

    def __init__(self, controller: DependencyController, can_apply=lambda: True):
        super().__init__()
        self.controller, self.can_apply = controller, can_apply
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(label("Software Dependencies", "section"))
        root.addWidget(
            label(
                "Viewer, atlas, ROI/tract editing and saved QC work without MRtrix3, FSL or ANTs. "
                "Preprocessing needs the tools and version gates below. Setup never installs software or changes scientific settings.",
                "caption",
            )
        )
        self.fields = {}
        self.guides = {}
        for family, url in INSTALL_LINKS.items():
            row = QHBoxLayout()
            row.addWidget(label(family, "control"))
            field = QLineEdit(controller.inputs.get(family, ""))
            field.setPlaceholderText("Auto-detect, or select existing root/bin folder")
            field.setAccessibleName(f"{family} setup location")
            row.addWidget(field, 1)
            row.addWidget(
                button("Locate Existing Installation", lambda _, f=family: self.browse(f))
            )
            root.addLayout(row)
            actions = QHBoxLayout()
            actions.addWidget(button("Install / Setup", lambda _, f=family: self.open_guide(f)))
            actions.addWidget(
                button("Test Installation", lambda _, f=family: self.open_guide(f, test=True))
            )
            actions.addWidget(button("Scan Again", self.scan))
            state = label("Not checked", "caption")
            state.setAccessibleName(f"{family} installation state")
            actions.addWidget(state, 1)
            if not hasattr(self, "family_labels"):
                self.family_labels = {}
            self.family_labels[family] = state
            root.addLayout(actions)
            link = label(
                f'<a href="{url}">Official {family} installation instructions</a>', "caption"
            )
            link.setTextFormat(Qt.TextFormat.RichText)
            link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            link.setOpenExternalLinks(True)
            link.setToolTip("Opens your browser only when clicked; does not install anything")
            root.addWidget(link)
            self.fields[family] = field
            field.textChanged.connect(self.refresh_actions)
        self.common = QCheckBox("Also inspect common installation folders")
        self.common.setChecked(controller.common)
        root.addWidget(self.common)
        self.common.toggled.connect(self.refresh_actions)
        actions = QHBoxLayout()
        self.check = button("Check installed tools", self.scan, primary=True)
        self.save = button("Use checked tool locations", self.apply)
        self.export = button("Export setup report", self.export_report)
        actions.addWidget(self.check)
        actions.addWidget(self.save)
        actions.addWidget(self.export)
        root.addLayout(actions)
        self.status = label("Setup has not been checked. Viewer-only use is available.", "caption")
        root.addWidget(self.status)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Tool / state", "Exact version / build", "Executable path"])
        self.tree.setMinimumHeight(210)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(0, 260)
        self.tree.setColumnWidth(1, 210)
        self.tree.setColumnWidth(2, 420)
        self.tree.setToolTip(
            "Select/hover any row to inspect exact version evidence and limitations."
        )
        self.tree.currentItemChanged.connect(self.detail)
        root.addWidget(self.tree)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(115)
        self.details.setPlaceholderText("Tool version evidence and adapter guidance appear here.")
        root.addWidget(self.details)
        self.features = label("", "caption")
        root.addWidget(self.features)
        controller.progress.connect(self.status.setText)
        controller.updated.connect(self.populate)
        controller.busy_changed.connect(self.busy)
        controller.configuration_applied.connect(self.locations_applied)
        self.populate(controller.report)
        self.busy(controller.thread is not None)

    def open_guide(self, family, *, test=False):
        """Extend this manager with one guided detail dialog per package."""
        from .software_installation import InstallationGuideDialog

        if family not in self.guides:
            self.guides[family] = InstallationGuideDialog(self, family)
        self.guides[family].refresh()
        self.guides[family].open()
        if test:
            self.guides[family].test_installation()

    def locations_applied(self, locations):
        """Keep first-run and Preferences in sync after explicit verified configuration."""
        for family, path in locations.items():
            self.fields[family].setText(path)
        self.refresh_actions()

    def browse(self, family):
        """User selects an existing installation; no folder is created."""
        path = QFileDialog.getExistingDirectory(self, f"Installed {family} root/bin folder")
        if path:
            self.fields[family].setText(path)

    def scan(self):
        """Submit immutable UI choices to the shared worker."""
        self.controller.scan(
            {f: w.text().strip() for f, w in self.fields.items()}, self.common.isChecked()
        )

    def refresh_actions(self, *_):
        """Edited path choices cannot save a compatibility result for older choices."""
        if not hasattr(self, "check"):
            return
        self.busy(self.controller.thread is not None)

    def choices_current(self):
        """Bind the inventory to the folder/search choices actually inspected."""
        return {
            f: w.text().strip() for f, w in self.fields.items()
        } == self.controller.inputs and self.common.isChecked() == self.controller.common

    def busy(self, value):
        """Keep viewer-only continuation usable even while queries are running."""
        self.check.setEnabled(not value)
        self.save.setEnabled(
            not value
            and self.controller.report is not None
            and self.choices_current()
            and not self.controller.report["cancelled"]
        )
        self.export.setEnabled(not value and self.controller.report is not None)

    def populate(self, report):
        """Missing/unvalidated tools never masquerade as scientifically accepted."""
        self.tree.clear()
        from .software_installation import STATE_LABELS

        for family, widget in self.family_labels.items():
            widget.setText(STATE_LABELS[family_state(report, family)["state"]])
        if report is None:
            if self.controller.error:
                self.status.setText("Setup check failed: " + self.controller.error)
            return
        families = {}
        for record in report["tools"]:
            family = record["family"]
            if family not in families:
                parent = QTreeWidgetItem([family])
                if family == "FSL":
                    versions = set(report["fsl_suite_versions"].values()) - {None}
                    parent.setText(1, "Suite: " + (", ".join(sorted(versions)) or "unknown"))
                self.tree.addTopLevelItem(parent)
                parent.setExpanded(True)
                families[family] = parent
            names = {
                "missing": "Missing",
                "unverified": "Not verified",
                "unsupported": "Unvalidated version",
                "version_match": "Version gate matched",
            }
            if family == "FSL" and record["name"] != "eddy_cpu":
                names["version_match"] = "Component/build recorded"
            version = record["version"] or "Unknown"
            if record.get("build"):
                version += " / " + record["build"]
            item = QTreeWidgetItem(
                [
                    record["name"] + " · " + names[record["state"]],
                    version,
                    record["path"] or "Not installed / not found",
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, record)
            for i in range(3):
                item.setToolTip(i, record["detail"] + "\n" + (record["path"] or ""))
            families[family].addChild(item)
        lines = []
        for feature in report["features"].values():
            lines.append(
                feature["title"]
                + ": "
                + {
                    "available": "Available now",
                    "needs_setup": "Needs setup",
                    "version_requirements_met": "Version requirements met; command preview still required",
                }[feature["state"]]
            )
        self.features.setText("\n".join(lines))
        self.refresh_actions()
        self.status.setText(
            "Scan cancelled; results incomplete. Viewer-only use remains available."
            if report["cancelled"]
            else "Read-only check complete. Matching versions do not approve scientific processing."
        )
        if "MRtrix3" in families:
            self.tree.setCurrentItem(families["MRtrix3"].child(0))

    def detail(self, item, _previous):
        """Show actual source of a version rather than treating suite labels as builds."""
        record = item.data(0, Qt.ItemDataRole.UserRole) if item else None
        self.details.setPlainText(
            json.dumps(record, indent=2)
            if record
            else "Select a tool to inspect its exact evidence."
        )

    def apply(self):
        """User-selected location changes require idle processing and a fresh preview."""
        if not self.choices_current():
            self.status.setText("Tool locations changed since the scan. Recheck before applying.")
            return
        if not self.can_apply():
            self.status.setText(
                "Finish the active command preview/run before changing tool locations."
            )
            return
        try:
            locations = self.controller.apply()
            self.controller.inputs = dict(locations)
            for family, path in locations.items():
                self.fields[family].setText(path)
            self.refresh_actions()
            self.status.setText(
                "Saved application tool locations. Scientific version and provenance checks still apply."
            )
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def export_report(self):
        """Export only on user request; no MRI arrays, account secrets or auto uploads."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save dependency inventory", "", "JSON (*.json)"
        )
        if not path:
            return
        try:
            with Path(path).open("x") as f:
                json.dump(self.controller.report, f, indent=2)
            self.status.setText(
                "Saved setup report. Review local paths before sharing it publicly."
            )
        except OSError as exc:
            self.status.setText(str(exc))


class DependencyDialog(QDialog):
    """First-run setup is optional for viewing and never blocks on installation."""

    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("TractoSphere · first-run software setup")
        self.resize(1000, 750)
        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.content = DependencyWidget(
            window.dependencies, lambda: window.workflow.execution.thread is None
        )
        scroll.setWidget(self.content)
        layout.addWidget(scroll)
        row = QHBoxLayout()
        row.addWidget(button("Continue with viewer only", self.accept, primary=True))
        row.addWidget(button("Continue to studio", self.accept))
        layout.addLayout(row)
        self.finished.connect(lambda _: window.settings.set("dependencies/acknowledged", True))
        if window.dependencies.report is None:
            self.content.scan()
