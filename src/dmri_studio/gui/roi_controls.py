"""Compact selection and display controls within the existing atlas workspace."""

from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, QSignalBlocker, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from dmri_studio.core.roi_selection import apply_selection, parse_ids, select_names

from .roi_surfaces import roi_mesh


class RoiControls:
    """Selection does not change visibility, scientific masks or filter roles."""

    def setup_bulk_rois(self):
        self.roi_selection_order = []
        self.roi_surface_queue = []
        self.roi_surface_busy = False
        panel = QWidget()
        self.roi_bulk_panel = panel
        grid = QGridLayout(panel)
        grid.setContentsMargins(0, 0, 0, 0)
        actions = [
            ("Select All", lambda: self.bulk_selection("all")),
            ("Deselect All", lambda: self.bulk_selection("none")),
            ("Invert Selection", lambda: self.bulk_selection("invert")),
            ("Select by ID…", lambda: self.select_dialog(False)),
            ("Select by Name…", lambda: self.select_dialog(True)),
            ("Show Selected Only", lambda: self.show_regions(True)),
            ("Show All Regions", lambda: self.show_regions(False)),
        ]
        self.bulk_buttons = {}
        for i, (name, callback) in enumerate(actions):
            button = self.action(name, lambda checked=False, cb=callback: self.safe(cb))
            grid.addWidget(button, i // 2, i % 2)
            self.bulk_buttons[name] = button
        self.roi_global_opacity = self.spin(0, 1, 0.3, 0.05)
        self.roi_global_opacity.setToolTip(
            "Apply opacity to every region; does not alter visibility or masks."
        )
        self.roi_global_opacity.valueChanged.connect(self.global_roi_opacity)
        grid.addWidget(QLabel("All ROI opacity"), 4, 0)
        grid.addWidget(self.roi_global_opacity, 4, 1)
        self.roi_quality = QComboBox()
        self.roi_quality.addItems(["Voxel boundary", "Smooth", "Detailed"])
        self.roi_quality.setCurrentText("Smooth")
        self.roi_display_mode = QComboBox()
        self.roi_display_mode.addItems(["Surface", "Boundary only", "Wireframe"])
        grid.addWidget(self.roi_quality, 5, 0)
        grid.addWidget(self.roi_display_mode, 5, 1)
        grid.addWidget(
            self.action("Apply display", lambda: self.safe(self.apply_roi_display)),
            6,
            0,
        )
        grid.addWidget(
            self.action("Reset display", lambda: self.safe(self.reset_roi_display)), 6, 1
        )
        self.rois_layout.insertWidget(2, panel)

    def selected_atlas_rows(self):
        current = self.roi_list.currentRow()
        if current < 0 or not self.rois[current].get("atlas_id"):
            raise ValueError(
                "Select a row in the intended atlas first; IDs are local to that atlas."
            )
        atlas = self.rois[current]["atlas_id"]
        return [i for i, r in enumerate(self.rois) if r.get("atlas_id") == atlas]

    def select_roi_rows(self, rows, mode="replace"):
        selected = apply_selection(self.roi_selection_order, rows, mode)
        with QSignalBlocker(self.roi_list):
            for i, item in enumerate(self.roi_list.entries):
                item.setSelected(i in selected)
            if selected:
                self.roi_list.setCurrentItem(
                    self.roi_list.entries[selected[0]],
                    0,
                    QItemSelectionModel.SelectionFlag.NoUpdate,
                )
        if selected:
            self.roi_list.currentRowChanged.emit(selected[0])
        self.roi_selection_order = selected
        self.atlas_selection()
        self.status.setText(
            f"{len(selected)} regions selected. Visibility and filtering roles unchanged."
        )

    def bulk_selection(self, mode):
        current = set(self.roi_list.selected_rows())
        rows = (
            list(range(len(self.rois)))
            if mode == "all"
            else []
            if mode == "none"
            else [i for i in range(len(self.rois)) if i not in current]
        )
        self.select_roi_rows(rows)

    def selection_preview(self, text, by_name=False):
        rows = self.selected_atlas_rows()
        ids = {self.rois[i]["label_id"]: i for i in rows}
        if by_name:
            names = {
                self.rois[i]["label_id"]: self.rois[i]["name"]
                for i in rows
                if self.rois[i].get("name_available", False)
            }
            if not names:
                raise ValueError(
                    "Anatomical names are unavailable. Use the original numeric label IDs."
                )
            selected = select_names(text, names)
            if not selected:
                raise ValueError("No anatomical names match this query.")
        else:
            selected = parse_ids(text, ids)
        return [ids[key] for key in selected]

    def select_dialog(self, by_name=False):
        self.selected_atlas_rows()
        dialog = QDialog(self)
        dialog.setWindowTitle("Select atlas regions by " + ("name" if by_name else "ID"))
        form = QFormLayout(dialog)
        entry = QLineEdit()
        entry.setPlaceholderText(
            "Name fragments, separated by commas" if by_name else "1, 3, 7-12, 25, 41-45"
        )
        mode = QComboBox()
        mode.addItems(["replace", "add", "remove"])
        preview = QLabel("Preview before applying.")
        preview.setWordWrap(True)
        form.addRow("Labels", entry)
        form.addRow("Selection operation", mode)
        form.addRow(preview)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        candidates = []

        def update():
            candidates.clear()
            try:
                candidates.extend(self.selection_preview(entry.text(), by_name))
                preview.setText(
                    f"{len(candidates)} regions: "
                    + ", ".join(str(self.rois[i]["label_id"]) for i in candidates)
                )
            except ValueError as error:
                preview.setText(str(error))
            buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(candidates))

        entry.textChanged.connect(update)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.select_roi_rows(candidates, mode.currentText())

    def show_regions(self, selected_only):
        selected = set(self.roi_list.selected_rows())
        for i, roi in enumerate(self.rois):
            roi["visible"] = not selected_only or i in selected
            if ("roi", i) in self.scene.actors:
                self.scene.actors[("roi", i)].SetVisibility(roi["visible"])
            self.roi_list.update_region(i)
            if roi["visible"] and ("roi", i) not in self.scene.actors:
                self.queue_roi_surface(i)
        # Retain only a small hidden mesh cache; showing hundreds of parcels
        # must not permanently retain all meshes after they are hidden.
        hidden = [k for k in self.scene.actors if k[0] == "roi" and not self.rois[k[1]]["visible"]]
        for key in hidden[:-8]:
            self.scene.remove(key)
        self.refresh_slices()
        self.scene.draw()

    def global_roi_opacity(self, value):
        for i, roi in enumerate(self.rois):
            roi["opacity"] = value
            if ("roi", i) in self.scene.actors:
                self.scene.actors[("roi", i)].GetProperty().SetOpacity(value)
        self.roi_info(self.roi_list.currentRow())
        self.refresh_slices()
        self.scene.draw()

    def apply_roi_display(self):
        for i in self.roi_list.selected_rows():
            self.rois[i].update(
                surface_quality=self.roi_quality.currentText(),
                surface_mode=self.roi_display_mode.currentText(),
            )
            if self.rois[i]["visible"]:
                self.queue_roi_surface(i)

    def reset_roi_display(self):
        for i, roi in enumerate(self.rois):
            roi.update(opacity=0.3, surface_quality="Smooth", surface_mode="Surface")
            if "original_color" in roi:
                roi["color"] = roi["original_color"]
            if roi["visible"]:
                self.queue_roi_surface(i)
        self.roi_global_opacity.setValue(0.3)
        self.roi_quality.setCurrentText("Smooth")
        self.roi_display_mode.setCurrentText("Surface")
        self.refresh_slices()

    def queue_roi_surface(self, index):
        if index not in self.roi_surface_queue:
            self.roi_surface_queue.append(index)
        if not self.roi_surface_busy:
            QTimer.singleShot(0, self.next_roi_surface)

    def next_roi_surface(self):
        if self.roi_surface_busy or not self.roi_surface_queue:
            return
        index = self.roi_surface_queue.pop(0)
        if index >= len(self.rois):
            QTimer.singleShot(0, self.next_roi_surface)
            return
        roi = self.rois[index]
        ref = self.reference
        if not roi["visible"]:
            QTimer.singleShot(0, self.next_roi_surface)
            return
        quality = roi.get("surface_quality", "Smooth")
        mode = roi.get("surface_mode", "Surface")
        self.roi_surface_busy = True

        def finish():
            self.roi_surface_busy = False
            QTimer.singleShot(0, self.next_roi_surface)

        def ready(poly):
            if self.reference is ref and index < len(self.rois) and self.rois[index] is roi:
                if quality != roi.get("surface_quality", "Smooth") or mode != roi.get(
                    "surface_mode", "Surface"
                ):
                    self.queue_roi_surface(index)
                elif roi["visible"]:
                    self.scene.roi_polydata(
                        ("roi", index), poly, roi["color"], roi["opacity"], mode
                    )
                    roi["surface_built"] = True
            finish()

        def failed(error):
            if self.reference is ref:
                self.status.setText("ROI surface failed: " + str(error))
            finish()

        self.background(
            lambda: roi_mesh(roi["data"], ref.affine, quality),
            ready,
            current=lambda: True,
            on_error=failed,
        )
