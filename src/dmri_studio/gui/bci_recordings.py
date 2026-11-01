"""Read-only, asynchronous H1 recording and manifest inspection workspaces."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.bci import inspect_falcon_h1
from dmri_studio.core.bci_inventory import inspect_h1_inventory

from .components import button, label
from .workers import GuiOwnedWorker


def _text(value: object) -> str:
    """Do not expose incidental local paths from declarations or errors."""
    text = str(value)
    if re.search(r"file://|(?<![\w:/])(?:/[^\s]+|[A-Za-z]:\\[^\s]+)", text):
        return "[text omitted: local path]"
    return text


def _finding(errors: list[str]) -> str:
    """Keep a useful recovery clue without displaying an arbitrary source path."""
    if not errors:
        return "None"
    first = errors[0]
    if "No such file or directory" in first:
        message = "Listed recording unavailable; check its manifest path."
    elif "Permission denied" in first:
        message = "Listed recording unreadable; check file permissions."
    else:
        message = _text(first)
    if len(errors) > 1:
        message += f" (+{len(errors) - 1} more; expand details)"
    return message


class _InspectionWorker(GuiOwnedWorker):
    result = Signal(object)
    error = Signal(str)

    def __init__(self, path: Path, inventory: bool) -> None:
        super().__init__()
        self.path, self.inventory = path, inventory

    @Slot()
    def run(self) -> None:
        try:
            inspect = inspect_h1_inventory if self.inventory else inspect_falcon_h1
            self.result.emit(inspect(self.path))
        except OSError:
            self.error.emit("Selected input or a listed recording is unavailable or unreadable.")
        except (ValueError, RuntimeError, TypeError, KeyError) as exc:
            self.error.emit(_text(exc))
        except Exception:
            self.error.emit("Unexpected inspection failure. No result is available.")
        finally:
            self.finish()


class BciRecordingsPage(QWidget):
    """Reuse the CLI validators; never fit a model, repair or write a recording."""

    def __init__(self, theme, *, inventory: bool = False) -> None:
        super().__init__()
        self.inventory = inventory
        self.thread = self.worker = None
        self.report = None
        self._pending = {}
        self._next_node = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(label("Compare Recordings" if inventory else "Inspect Recording", "title"))
        layout.addWidget(
            label(
                "Open a versioned H1 inventory manifest. The manifest explicitly authorizes reading its listed recordings and checks their declared SHA-256 hashes."
                if inventory
                else "Open one modern FALCON H1 NWB recording. Inspection reads the source without changing it; arbitrary NWB, EEG and other FALCON tasks are unsupported.",
                "caption",
            )
        )
        self.open_button = button(
            "Open H1 inventory manifest…" if inventory else "Open H1 NWB recording…",
            self.browse,
            primary=True,
        )
        layout.addWidget(self.open_button)
        self.status = label("No manifest open." if inventory else "No recording open.", "caption")
        layout.addWidget(self.status)
        self.summary = label("Choose an input to inspect.")
        layout.addWidget(self.summary)
        self.recordings = QTableWidget(0, 8)
        self.recordings.setHorizontalHeaderLabels(
            [
                "Entry",
                "Status",
                "Samples",
                "Exposure (s)",
                "Units",
                "In-window spikes",
                "Raw mask true / samples",
                "Finding",
            ]
        )
        self.recordings.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.recordings.setAccessibleName("Recording comparison with explicit denominators")
        self.recordings.setVisible(inventory)
        self.recordings.setMaximumHeight(200)
        if inventory:
            layout.addWidget(self.recordings)
        self.details = QTreeWidget()
        self.details.setColumnCount(2)
        self.details.setHeaderLabels(["Measurement / recorded declaration", "Value"])
        self.details.setAccessibleName("Read-only H1 measurements and provenance")
        self.details.setColumnWidth(0, 340)
        self.details.itemExpanded.connect(self._expand)
        layout.addWidget(self.details, 1)
        layout.addWidget(
            label(
                "Expand sections to inspect timing, raw masks, missing values, units, trials and provenance. "
                "Counts and rates include all raw-mask statuses. False masks and zero spikes do not diagnose acquisition failure. "
                "Constant-step clocks cannot reveal unrepresented missing acquisitions. "
                "Descriptive compatibility does not prove independent observations, decoder suitability or cross-session neuron identity. "
                "Scientific acceptance remains unassigned. Local source paths are omitted from this display.",
                "caption",
            )
        )

    def browse(self) -> None:
        title, pattern = (
            ("Open H1 inventory manifest", "JSON manifests (*.json)")
            if self.inventory
            else ("Open FALCON H1 recording", "NWB recordings (*.nwb)")
        )
        path, _ = QFileDialog.getOpenFileName(self, title, "", pattern)
        if path:
            self.load(path)

    def load(self, path: str | Path) -> None:
        if self.thread is not None:
            return
        self.report = None
        self._pending.clear()
        self.details.clear()
        self.recordings.setRowCount(0)
        self.summary.setText("No completed inspection.")
        self.status.setText("Reading and validating… Large recordings may take time.")
        self.open_button.setEnabled(False)
        self.thread = QThread(self)
        self.worker = _InspectionWorker(Path(path), self.inventory)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.result.connect(self._show)
        self.worker.error.connect(self._error)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._finished)
        self.thread.start()

    @Slot(object)
    def _show(self, report: dict) -> None:
        self.report = report
        if self.inventory:
            records = report["recordings"]
            success = sum(row["status"] == "ok" for row in records)
            compatible = report["comparability"]["descriptive_eligible"]
            self.recordings.setRowCount(len(records))
            for index, row in enumerate(records):
                inspection = row.get("inspection")
                errors = row.get("errors", [])
                finding = _finding(errors)
                values = [row["entry_id"], row["status"], *(["Unavailable"] * 5), finding]
                if inspection is not None:
                    n = inspection["time"]["sample_count"]
                    values = [
                        row["entry_id"],
                        row["status"],
                        n,
                        inspection["time"]["analyzed_exposure_s"],
                        inspection["units"]["count"],
                        sum(u["observed_spikes"] for u in inspection["units"]["per_unit"]),
                        f"{inspection['evaluation_mask']['true_count']} / {n}",
                        finding,
                    ]
                for column, value in enumerate(values):
                    self.recordings.setItem(index, column, QTableWidgetItem(_text(value)))
            self.recordings.resizeColumnsToContents()
            self.status.setText(
                "Inventory complete."
                if report["complete"]
                else "Inventory incomplete — inspect every failed entry below."
            )
            self.summary.setText(
                f"{success} of {len(records)} entries succeeded. "
                f"Descriptive compatibility: {'eligible' if compatible else 'not established'}.\n"
                f"Manifest SHA-256: {report['manifest']['sha256']}\n"
                "Per-entry source checks and declared identity evidence are recorded below; unknown identities remain unknown."
            )
        else:
            time, units, mask = report["time"], report["units"], report["evaluation_mask"]
            self.status.setText("H1 inspection complete — descriptive measurements only.")
            self.summary.setText(
                f"{time['sample_count']:,} samples · {time['analyzed_exposure_s']:.6g} s analyzed exposure · "
                f"{units['count']:,} units · {report['trials']['count']:,} trial segments\n"
                f"Raw mask true: {mask['true_count']:,}; false: {mask['false_count']:,}; "
                f"denominator: {time['sample_count']:,} samples.\n"
                f"Source SHA-256: {report['source']['sha256']}\n"
                "Source read and hashed during this inspection; scientific acceptance: unassigned."
            )
        self._children(self.details.invisibleRootItem(), report)

    def _children(self, parent, value) -> None:
        if isinstance(value, list) and len(value) > 100:
            entries = (
                (
                    f"Items {start}–{min(start + 100, len(value)) - 1}",
                    {str(i): value[i] for i in range(start, min(start + 100, len(value)))},
                )
                for start in range(0, len(value), 100)
            )
        else:
            entries = value.items() if isinstance(value, dict) else enumerate(value)
        for key, child in entries:
            if key == "path":
                continue
            nested = isinstance(child, (dict, list))
            item = QTreeWidgetItem(
                parent,
                [
                    _text(key),
                    f"{len(child)} entries"
                    if nested
                    else "Unknown / unavailable"
                    if child is None
                    else _text(child),
                ],
            )
            if nested and child:
                # Populate only on demand, avoiding thousands of widgets at first open.
                self._next_node += 1
                item.setData(0, Qt.ItemDataRole.UserRole, self._next_node)
                self._pending[self._next_node] = child
                QTreeWidgetItem(item, ["Expand to inspect", ""])

    @Slot(QTreeWidgetItem)
    def _expand(self, item) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if key in self._pending:
            value = self._pending.pop(key)
            item.takeChildren()
            self._children(item, value)

    @Slot(str)
    def _error(self, message: str) -> None:
        self.status.setText(f"Could not inspect input: {message}")

    @Slot()
    def _finished(self) -> None:
        self.thread = self.worker = None
        self.open_button.setEnabled(True)

    def shutdown(self) -> bool:
        """Wait for the active read before the shell destroys this page."""
        return self.thread is None
