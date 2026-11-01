"""Read-only presentation of a saved chronological H1 research comparison."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, Slot
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.bci_reports import (
    CONDITIONS,
    prepare_h1_prediction_traces,
    read_h1_chronological_report,
)

from .components import Card, button, label
from .workers import GuiOwnedWorker

NAMES = {
    "constant_training_mean": "Constant training mean",
    "fixed_ridge": "Fixed ridge",
    "normalization_recalibrated_ridge": "Normalization recalibrated ridge",
}
COLORS = ("accent.primary", "info.fg", "warning.fg")


def _metric(value: float | None, reason: str | None = None) -> str:
    if value is None:
        return f"Undefined ({reason or 'reason unavailable'})"
    return f"{value:.5g}"


class _ReportWorker(GuiOwnedWorker):
    result = Signal(object, object)
    error = Signal(str)

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    @Slot()
    def run(self) -> None:
        try:
            view = read_h1_chronological_report(self.path)
            traces = {
                feature: prepare_h1_prediction_traces(view, feature)
                for feature in view["feature_labels"]
            }
            self.result.emit(view, traces)
        except OSError:
            self.error.emit(
                "Selected report is unavailable or unreadable. Check the file and try again."
            )
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finish()


class PredictionPlot(QWidget):
    """Bounded, discontinuous line display; metrics remain untouched."""

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self.trace = None
        self.setMinimumHeight(280)
        self.setAccessibleName("Saved prediction traces by original source bin index")
        theme.changed.connect(self.update)

    def set_trace(self, trace: dict | None) -> None:
        self.trace = trace
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        trace = self.trace
        if not trace or not trace["available"]:
            return
        groups = [segment for series in trace["series"].values() for segment in series]
        points = [point for segment in groups for point in segment]
        if not points:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(55, 12, -18, -38)
        x0, x1 = min(point[0] for point in points), max(point[0] for point in points)
        y0, y1 = min(point[1] for point in points), max(point[1] for point in points)
        if math.isclose(y0, y1):
            y0 -= 1
            y1 += 1

        def xy(point):
            return (
                rect.left() + (point[0] - x0) / max(1, x1 - x0) * rect.width(),
                rect.bottom() - (point[1] - y0) / (y1 - y0) * rect.height(),
            )

        painter.setPen(QPen(self.theme.color("border.control"), 1))
        painter.drawRect(rect)
        painter.setPen(self.theme.color("text.secondary"))
        painter.drawText(3, 22, f"{y1:.3g}")
        painter.drawText(3, rect.bottom(), f"{y0:.3g}")
        painter.drawText(rect.left(), self.height() - 10, str(x0))
        painter.drawText(rect.right() - 45, self.height() - 10, str(x1))
        for boundary in trace["trial_boundaries"]:
            if x0 < boundary < x1:
                x, _ = xy((boundary, y0))
                pen = QPen(self.theme.color("border.control"), 1, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.drawLine(int(x), rect.top(), int(x), rect.bottom())
        palette = [(name, token) for name, token in zip(CONDITIONS, COLORS, strict=True)]
        if "observed" in trace["series"]:
            palette.append(("observed", "text.primary"))
        for name, token in palette:
            painter.setPen(QPen(self.theme.color(token), 2.0 if name == "observed" else 1.5))
            for segment in trace["series"][name]:
                if len(segment) == 1:
                    x, y = xy(segment[0])
                    painter.drawEllipse(int(x) - 2, int(y) - 2, 4, 4)
                for left, right in zip(segment, segment[1:], strict=False):
                    x0p, y0p = xy(left)
                    x1p, y1p = xy(right)
                    painter.drawLine(int(x0p), int(y0p), int(x1p), int(y1p))


class BciResultsPage(QWidget):
    """Open only an explicitly chosen local JSON report; no training or source reads."""

    def __init__(self, theme) -> None:
        super().__init__()
        self.theme = theme
        self.thread = None
        self.worker = None
        self.view = None
        self.traces = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        root.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(label("BCI Results", "title"))
        layout.addWidget(
            label(
                "Open an existing FALCON H1 chronological research report. This view does not run a decoder, read source recordings, or approve scientific results.",
                "caption",
            )
        )
        action_row = QHBoxLayout()
        self.open_button = button("Open chronological report…", self.browse, primary=True)
        self.open_button.setAccessibleName("Open existing BCI chronological JSON report")
        action_row.addWidget(self.open_button)
        action_row.addStretch()
        layout.addLayout(action_row)
        self.status = label("No report open.", "caption")
        layout.addWidget(self.status)
        provenance = Card(theme, "Experiment and evidence")
        self.provenance = label("Choose a local report to inspect.")
        provenance.body.addWidget(self.provenance)
        layout.addWidget(provenance)
        selection = Card(theme, "Selections and exclusions")
        self.selections = label("Training, calibration and evaluation selections appear here.")
        selection.body.addWidget(self.selections)
        layout.addWidget(selection)
        comparison = Card(theme, "Condition comparison")
        self.condition_note = label(
            "Normalization recalibration keeps ridge coefficients unchanged. It uses earlier calibration neural data only; calibration target labels are not used.",
            "caption",
        )
        comparison.body.addWidget(self.condition_note)
        self.conditions = QTableWidget(3, 4)
        self.conditions.setHorizontalHeaderLabels(
            ("Condition", "Included bins", "Uniform-average R²", "Variance-weighted R²")
        )
        self.conditions.setAccessibleName("Pooled condition comparison")
        self.conditions.horizontalHeader().setStretchLastSection(True)
        self.conditions.setMinimumHeight(150)
        comparison.body.addWidget(self.conditions)
        layout.addWidget(comparison)
        traces = Card(theme, "Recorded predictions")
        feature_row = QHBoxLayout()
        feature_row.addWidget(label("Output feature"))
        self.feature = QComboBox()
        self.feature.setAccessibleName("Select output feature for saved predictions and metrics")
        self.feature.currentIndexChanged.connect(self.show_feature)
        feature_row.addWidget(self.feature)
        feature_row.addWidget(label("Prediction condition"))
        self.condition = QComboBox()
        self.condition.setAccessibleName(
            "Select prediction condition to compare with observed targets"
        )
        self.condition.addItem("All conditions", None)
        for name in CONDITIONS:
            self.condition.addItem(NAMES[name], name)
        self.condition.currentIndexChanged.connect(self.show_feature)
        feature_row.addWidget(self.condition)
        feature_row.addStretch()
        traces.body.addLayout(feature_row)
        self.feature_metrics = label("No output selected.", "caption")
        traces.body.addWidget(self.feature_metrics)
        self.legend = label(
            "Blue: constant training mean · light blue: fixed ridge · amber: normalization recalibrated ridge",
            "caption",
        )
        traces.body.addWidget(self.legend)
        self.plot = PredictionPlot(theme)
        traces.body.addWidget(self.plot)
        self.plot_note = label(
            "Observed trace unavailable. The v1 report stores predictions, but not observed target values.",
            "caption",
        )
        traces.body.addWidget(self.plot_note)
        traces.body.addWidget(
            label(
                "Each line segment follows consecutive included original bin indices. Breaks mark excluded bins or trial transitions; dashed lines mark trial starts. Display decimation retains bin endpoints and extrema. Saved metrics are unchanged.",
                "caption",
            )
        )
        layout.addWidget(traces)
        limitations = Card(theme, "Limitations")
        self.limitations = label("Report limitations appear here.")
        limitations.body.addWidget(self.limitations)
        layout.addWidget(limitations)
        layout.addStretch()

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open chronological BCI report", "", "JSON reports (*.json)"
        )
        if path:
            self.load(Path(path))

    def load(self, path: str | Path) -> None:
        if self.thread is not None:
            self.status.setText("A report is still opening. Wait for the current read to finish.")
            return
        self.view = self.traces = None
        self.feature.clear()
        self.provenance.setText("No valid report open.")
        self.selections.setText("")
        self.conditions.clearContents()
        self.feature_metrics.setText("")
        self.plot.set_trace(None)
        self.limitations.setText("")
        self.status.setText("Reading and validating the selected local report…")
        self.open_button.setEnabled(False)
        self.thread = QThread(self)
        self.worker = _ReportWorker(Path(path))
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.result.connect(self._show_report)
        self.worker.error.connect(self._show_error)
        self.worker.finished.connect(self.thread.quit)
        self.thread.finished.connect(self.worker.deleteLater)
        self.thread.finished.connect(self._finished)
        self.thread.start()

    @Slot(object, object)
    def _show_report(self, view: dict, traces: dict) -> None:
        self.view, self.traces = view, traces
        source = view["source"]
        has_targets = "observations" in view
        self.status.setText(
            "Saved report and embedded metrics agree; source files not independently verified."
            if has_targets
            else "Saved report structure validated; source files not independently verified."
        )
        self.provenance.setText(
            f"Custom chronological H1 research comparison · {view['schema']}\n"
            f"Report SHA-256: {view['report_sha256']}\n"
            f"Public asset ID: {source['asset_id']} · partition: {source['partition']}\n"
            f"Recording SHA-256: {source['sha256']}\n"
            f"Specification SHA-256: {view['specification_sha256']}\n"
            "Structure: valid supported schema · reported fields: internally checked where possible\n"
            "Source file verification: not performed · scientific acceptance: unassigned\n"
            + (
                "Metric recomputation: pooled and per-trial values agree with embedded targets."
                if has_targets
                else "Metric recomputation: unavailable because observed targets are not embedded."
            )
        )
        selected = view["selections"]
        evaluation = view["evaluation"]

        def role(name):
            item = selected[name]
            return (
                f"{name.replace('_', ' ').title()}: source bins [{item['start']}, {item['stop']}); "
                f"trials {', '.join(map(str, item['trial_numbers']))}; {item['samples']:,} bins; "
                f"{item['exposure_s']:.2f} s represented exposure; raw mask true/false "
                f"{item['raw_eval_mask_true']:,}/{item['raw_eval_mask_false']:,}"
            )

        self.selections.setText(
            "\n".join(
                role(name)
                for name in (
                    "training",
                    "calibration",
                    "unused_gap",
                    "evaluation",
                    "unused_trailing",
                )
            )
            + f"\nTraining selected: {view['training']['selected_samples']:,}; exclusion union: "
            f"{view['training']['excluded_union']:,} (raw mask false "
            f"{view['training']['raw_mask_false']:,}, nonfinite target "
            f"{view['training']['nonfinite_target']:,}, still-time "
            f"{view['training']['still_time']:,}; categories may overlap)."
            + f"\nCalibration selected: {view['calibration']['selected_samples']:,}; target labels used: no."
            + f"\nEvaluation included: {evaluation['included_samples']:,} of {evaluation['samples']:,}; "
            f"raw mask false {evaluation['raw_mask_false']:,}; additional nonfinite-target "
            f"exclusions {evaluation['additional_nonfinite_target_exclusions']:,}."
        )
        for row, condition in enumerate(CONDITIONS):
            pooled = evaluation["conditions"][condition]["pooled"]
            values = (
                NAMES[condition],
                str(pooled["included_samples"]),
                _metric(pooled["uniform_average_r2"], pooled["aggregate_reason"]),
                _metric(pooled["variance_weighted_r2"], pooled["aggregate_reason"]),
            )
            for column, value in enumerate(values):
                self.conditions.setItem(row, column, QTableWidgetItem(value))
        self.conditions.resizeColumnsToContents()
        self.feature.addItems(view["feature_labels"])
        self.limitations.setText(
            "\n".join(f"• {item}" for item in view["limitations"])
            + "\n• This report is not the official FALCON benchmark and does not establish a general adaptation effect."
        )
        self.show_feature()

    @Slot()
    def show_feature(self, *_args) -> None:
        if not self.view or not self.traces or self.feature.currentIndex() < 0:
            return
        feature = self.feature.currentText()
        rows = []
        for condition in CONDITIONS:
            value = self.view["evaluation"]["conditions"][condition]["pooled"]["per_output"][
                feature
            ]
            rows.append(
                f"{NAMES[condition]}: R² {_metric(value['r2'], value['r2_reason'])}; "
                f"MAE {_metric(value['mae'])}; RMSE {_metric(value['rmse'])}"
            )
        units = "MAE/RMSE units unavailable in the v1 report"
        if "observations" in self.view:
            item = self.view["observations"]["output_units"][
                self.view["feature_labels"].index(feature)
            ]
            units = (
                f"MAE/RMSE units: {item['unit']} ({item['provenance']})"
                if item["unit"]
                else "MAE/RMSE units unavailable (not established)"
            )
        self.feature_metrics.setText(f"{feature} · {units}\n" + "\n".join(rows))
        trace = self.traces[feature]
        selected = self.condition.currentData()
        legends = {
            "constant_training_mean": "blue: constant training mean",
            "fixed_ridge": "light blue: fixed ridge",
            "normalization_recalibrated_ridge": "amber: normalization recalibrated ridge",
        }
        names = (selected,) if selected in CONDITIONS else CONDITIONS
        self.legend.setText(
            ("White/black: observed target · " if "observations" in self.view else "")
            + " · ".join(legends[name] for name in names)
        )
        if trace["available"] and selected in CONDITIONS:
            trace = {
                **trace,
                "series": {
                    name: segments
                    for name, segments in trace["series"].items()
                    if name in {selected, "observed"}
                },
            }
        self.plot.set_trace(trace)
        if trace["available"]:
            observed = (
                "Observed targets shown from included v2 observations. "
                if "observations" in self.view
                else "Observed target trace unavailable: not embedded in the v1 report. "
            )
            self.plot_note.setText(
                f"{trace['x_label']}. {observed}"
                + (
                    "Display decimated with endpoints and extrema retained."
                    if trace["downsampled"]
                    else "All included predictions displayed."
                )
            )
        else:
            self.plot_note.setText(f"Prediction plot unavailable: {trace['reason']}")

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.status.setText(f"Could not open chronological report: {message}")

    @Slot()
    def _finished(self) -> None:
        self.thread = self.worker = None
        self.open_button.setEnabled(True)

    def shutdown(self) -> bool:
        """A current JSON read must finish before its QThread can be destroyed."""
        return self.thread is None
