"""Registration inspection inside the existing linked image viewer, without tracts."""

from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
)

from dmri_studio.core.execution import hash_file
from dmri_studio.core.registration_qc import record_decision, review_status, verify_bundle
from dmri_studio.core.scene import ImageVolume
from dmri_studio.core.viewer_display import robust_range

from .components import button, label


class RegistrationView:
    def setup_registration_view(self):
        layout = self.layers_panel.layout()
        self.registration_bundle = None
        self.registration_review_state = "NEEDS_REVIEW"
        self.registration_layer = None
        self.registration_info = label("Registration QC · no review opened", "caption")
        layout.insertWidget(0, self.registration_info)
        self.registration_mode = QComboBox()
        self.registration_mode.addItems(
            ["Overlay", "Outlines", "Checkerboard", "Registered only", "Reference only"]
        )
        self.registration_mode.setAccessibleName("Registration display mode")
        self.registration_mode.currentIndexChanged.connect(self.refresh_slices)
        layout.insertWidget(1, self.registration_mode)
        self.registration_mode_info = label("", "caption")
        self.registration_mode.currentIndexChanged.connect(self.explain_registration_mode)
        layout.insertWidget(2, self.registration_mode_info)
        self.explain_registration_mode()
        self.registration_blink = QCheckBox("Blink registered result / reference")
        self.registration_blink.setAccessibleName("Blink registered result and reference")
        layout.insertWidget(3, self.registration_blink)
        self.registration_timer = QTimer(self)
        self.registration_timer.setInterval(650)
        self.registration_timer.timeout.connect(self.blink_registration)
        self.registration_blink.toggled.connect(self.set_registration_blink)
        self.registration_regions = QListWidget()
        self.registration_regions.setMaximumHeight(120)
        self.registration_regions.setAccessibleName(
            "Registered atlas regions — visibility by original ID"
        )
        self.registration_regions.itemChanged.connect(self.refresh_slices)
        layout.insertWidget(4, self.registration_regions)
        reviews = QHBoxLayout()
        self.registration_review_buttons = []
        for title, decision in (
            ("Approve…", "APPROVED"),
            ("Reject…", "REJECTED"),
            ("Needs review…", "NEEDS_REVIEW"),
        ):
            control = button(title, lambda checked=False, d=decision: self.review_registration(d))
            control.setEnabled(False)
            self.registration_review_buttons.append(control)
            reviews.addWidget(control)
        layout.insertLayout(5, reviews)
        exports = QHBoxLayout()
        exports.addWidget(button("Snapshot…", self.registration_snapshot))
        self.registration_report_button = button("QC report", self.registration_report)
        self.registration_report_button.setEnabled(False)
        exports.addWidget(self.registration_report_button)
        layout.insertLayout(6, exports)
        self.image_loaded.connect(self.registration_background_ready)
        self.pending_registration = None
        self.registration_restore = None
        self.theme.changed.connect(self.registration_indicator_style)
        self.registration_indicator_style()

    def explain_registration_mode(self, *_):
        descriptions = {
            "Overlay": "Blend the registered result with the fixed reference to inspect gross alignment.",
            "Outlines": "Show result boundaries over the fixed reference; boundaries alone do not prove anatomical correctness.",
            "Checkerboard": "Alternate patches of the two images to reveal mismatched boundaries.",
            "Registered only": "Show only the transformed result on the reference grid.",
            "Reference only": "Show only the unchanged fixed reference image.",
        }
        self.registration_mode_info.setText(
            descriptions.get(self.registration_mode.currentText(), "")
        )

    def registration_indicator_style(self):
        """Keep checked visibility readable when Cocoa switches appearance."""
        accent = self.theme.color("accent.primary").name()
        for widget, selector in (
            (self.registration_regions, "QListWidget"),
            (self.registration_blink, "QCheckBox"),
            (self.layer_visible, "QCheckBox"),
        ):
            widget.setStyleSheet(
                widget.styleSheet()
                + f"\n{selector}::indicator:checked {{background-color: {accent}; border: 1px solid {accent}; border-radius: 3px;}}"
            )

    def load_registration_product(self, product):
        self.registration_timer.stop()
        self.registration_blink.setChecked(False)

        def read():
            if hash_file(Path(product["path"])) != product["sha256"]:
                raise ValueError("Registration result changed")
            if product["kind"] == "registration_qc":
                bundle, _ = verify_bundle(product["path"])
                return dict(
                    product=product,
                    bundle=bundle,
                    reference=bundle["reference"]["path"],
                    reference_hash=hash_file(Path(bundle["reference"]["path"])),
                    overlay=bundle["atlas"]["path"],
                    overlay_hash=hash_file(Path(bundle["atlas"]["path"])),
                    categorical=True,
                    review_state=review_status(product["path"])
                    if (Path(product["path"]).parent / "reviews").exists()
                    else "NEEDS_REVIEW",
                )
            return dict(
                product=product,
                bundle=None,
                reference=product["reference"],
                reference_hash=product["reference_sha256"],
                overlay=product["path"],
                overlay_hash=product["sha256"],
                categorical=bool(product.get("categorical", False)),
            )

        def ready(result):
            self.pending_registration = result
            self.load_image(
                result["reference"], expected_hashes={result["reference"]: result["reference_hash"]}
            )

        self.background(read, ready)

    def registration_background_ready(self):
        pending = self.pending_registration
        self.pending_registration = None
        if pending is None or self.reference.path.resolve() != Path(pending["reference"]).resolve():
            return
        ref = self.reference

        def read():
            if hash_file(Path(pending["overlay"])) != pending["overlay_hash"]:
                raise ValueError("Registered overlay changed")
            im = ImageVolume(Path(pending["overlay"]))
            if im.shape != ref.shape or not np.allclose(im.affine, ref.affine, atol=1e-4):
                raise ValueError("QC overlay grid differs; no implicit registration")
            return im.volume()

        def ready(values):
            if self.reference is not ref:
                return
            lo, hi = robust_range(values)
            layer = dict(
                path=pending["overlay"],
                data=values,
                visible=True,
                opacity=0.35,
                low=lo,
                high=hi,
                cmap="gray",
                registration=True,
                categorical=pending["categorical"],
            )
            self.registration_layer = layer
            self.registration_bundle = pending
            self.registration_review_state = pending.get("review_state", "NEEDS_REVIEW")
            review_available = pending["bundle"] is not None
            for control in self.registration_review_buttons:
                control.setEnabled(review_available)
            self.registration_report_button.setEnabled(review_available)
            self.layers.append(layer)
            self.layer_list.addItem(Path(pending["overlay"]).name)
            self.layer_list.setCurrentRow(len(self.layers) - 1)
            self.registration_regions.blockSignals(True)
            self.registration_regions.clear()
            names = (
                (pending["bundle"] or {})
                .get("atlas", {})
                .get("metadata", {})
                .get("region_names", {})
            )
            if pending["categorical"]:
                for i in np.unique(values):
                    if i == 0:
                        continue
                    name = names.get(str(int(i)), "")
                    if name == f"Region {int(i)}":
                        name = "numeric ID; anatomical name unavailable"
                    item = QListWidgetItem(f"{int(i)} · {name or 'numeric ID'}")
                    item.setToolTip(item.text())
                    item.setData(Qt.ItemDataRole.UserRole, int(i))
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Checked)
                    self.registration_regions.addItem(item)
            self.registration_regions.blockSignals(False)
            if review_available:
                self.registration_info.setText(
                    "Registered atlas QC on verified preprocessed low-b reference · "
                    + self.registration_review_state.replace("_", " ").title()
                )
            else:
                self.registration_info.setText(
                    "Registered result on selected reference · inspect in all planes. "
                    "Formal anatomical review requires a supported atlas QC bundle."
                )
            self.registration_mode.setCurrentText("Overlay")
            restore = self.registration_restore
            self.registration_restore = None
            if restore:
                layer["opacity"] = restore["opacity"]
                layer["visible"] = restore["visible"]
                self.registration_mode.setCurrentText(restore["mode"])
                for i in range(self.registration_regions.count()):
                    item = self.registration_regions.item(i)
                    item.setCheckState(
                        Qt.CheckState.Checked
                        if item.data(Qt.ItemDataRole.UserRole) in restore["visible_ids"]
                        else Qt.CheckState.Unchecked
                    )
                self.range_changed(*restore["range"])
                self.navigate(restore["position"])
            self.refresh_slices()
            self.tabs.setCurrentIndex(4)  # existing Layers inspector, never the FOD panel

        self.background(read, ready)

    def registration_workspace(self):
        """Preserve exact evidence identity and display settings without copying MRI."""
        if not self.registration_bundle or self.registration_layer is None:
            return None
        return dict(
            product=self.registration_bundle["product"],
            opacity=self.registration_layer["opacity"],
            visible=self.registration_layer["visible"],
            mode=self.registration_mode.currentText(),
            visible_ids=self.registration_visible_ids(),
        )

    def registration_region_name(self, parcel):
        for i in range(self.registration_regions.count()):
            item = self.registration_regions.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == parcel:
                return item.text()
        return f"label {parcel} (background)" if parcel == 0 else f"label {parcel}"

    def registration_visible_ids(self):
        return [
            self.registration_regions.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.registration_regions.count())
            if self.registration_regions.item(i).checkState() == Qt.CheckState.Checked
        ]

    def layer_info(self, index):
        super().layer_info(index)
        categorical = 0 <= index < len(self.layers) and self.layers[index].get("categorical", False)
        for control in (self.layer_low, self.layer_high, self.layer_cmap):
            control.setEnabled(not categorical)
            control.setToolTip(
                "Categorical atlas uses stable label-ID colors. Adjust the background window in Image display."
                if categorical
                else "Scalar overlay display setting"
            )

    def set_registration_blink(self, enabled):
        if enabled and self.registration_layer is not None:
            self.registration_blink_original = self.registration_layer["visible"]
            self.registration_timer.start()
        else:
            self.registration_timer.stop()
            if self.registration_layer is not None:
                self.registration_layer["visible"] = getattr(
                    self, "registration_blink_original", True
                )
                self.layer_info(self.layer_list.currentRow())
                self.refresh_slices()

    def blink_registration(self):
        if self.registration_layer is not None and any(
            layer is self.registration_layer for layer in self.layers
        ):
            self.registration_layer["visible"] = not self.registration_layer["visible"]
            self.refresh_slices()

    def registration_snapshot(self):
        if self.registration_bundle is None:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save actual registration viewer screenshot", "", "PNG (*.png)"
        )
        if path:
            if Path(path).exists():
                self.registration_info.setText(
                    "Choose a new screenshot file; existing evidence is preserved"
                )
                return
            from .capture import capture_widget

            capture_widget(self).save(path)

    def registration_report(self):
        if self.registration_bundle and self.registration_bundle["bundle"]:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(
                QUrl.fromLocalFile(
                    str(Path(self.registration_bundle["product"]["path"]).parent / "qc-report.html")
                )
            )

    def review_registration(self, decision):
        if not self.registration_bundle or not self.registration_bundle["bundle"]:
            self.registration_info.setText(
                "Generate registered-atlas QC before recording a review."
            )
            return
        reviewer, ok = QInputDialog.getText(self, "Registration review", "Reviewer name")
        if not ok:
            return
        comments, ok = QInputDialog.getMultiLineText(
            self,
            "Registration review",
            "Anatomical observations, planes/range reviewed and remaining limitations",
        )
        if not ok:
            return
        path = self.registration_bundle["product"]["path"]

        def work():
            return record_decision(path, decision, reviewer, comments)

        def reviewed(_):
            self.registration_review_state = review_status(path)
            self.refresh_slices()

        self.background(work, reviewed)
