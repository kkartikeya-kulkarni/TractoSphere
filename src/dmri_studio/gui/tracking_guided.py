"""Task-first presentation over one approved tractography configuration.

TrackingExecutionPanel owns every input, typed Task, plan and worker. These
controls edit that same state; no second command builder or executor exists.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .components import Disclosure, button, label

TASKS = (
    (
        "generate",
        "Generate New Streamlines",
        "Create a new tractogram from a verified FOD or suitable DWI tensor input.",
    ),
    (
        "filter",
        "Select or Filter Existing Streamlines",
        "Keep tracks that satisfy reviewed spatial rules; the original tractogram stays unchanged.",
    ),
    (
        "density",
        "Create a Tract-Density Map",
        "Map streamline-derived density onto a compatible reference grid. This is not an axon count.",
    ),
)

METHOD_HELP = {
    "tckgen.ifod2": "Probabilistic iFOD2 samples directions from an FOD; different runs can differ.",
    "tckgen.ifod1": "Probabilistic iFOD1 samples directions from an FOD; different runs can differ.",
    "tckgen.sdstream": "Deterministic SD_Stream follows FOD directions under the selected tracking rules.",
    "tckgen.tensor_det": "Deterministic tensor tracking requires a compatible diffusion series and explicit shell selection.",
    "tckgen.tensor_prob": "Probabilistic tensor tracking requires a compatible diffusion series and explicit shell selection.",
}


class GuidedTracking(QWidget):
    """A small, contextual view of the legacy panel's authoritative controls."""

    highlight_requested = Signal(str)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._syncing = False
        self.roi_metadata = {}
        self._roi_signature = None
        root = QVBoxLayout(self)
        root.addWidget(label("What would you like to do?", "title"))
        root.addWidget(
            label(
                "Choose one task, select verified inputs, then review the exact scientific plan before running.",
                "caption",
            )
        )
        choices = QHBoxLayout()
        self.task_buttons = {}
        for key, title, explanation in TASKS:
            choice = button(title, lambda _=False, task=key: self.select_task(task))
            choice.setCheckable(True)
            choice.setAccessibleName(title)
            choice.setToolTip(explanation)
            choices.addWidget(choice)
            self.task_buttons[key] = choice
        root.addLayout(choices)
        self.task_help = label("", "caption")
        root.addWidget(self.task_help)
        self.saved_task_notice = label("", "caption")
        root.addWidget(self.saved_task_notice)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._generate_page())
        self.pages.addWidget(self._filter_page())
        self.pages.addWidget(self._density_page())
        root.addWidget(self.pages)
        self.roi_section = Disclosure("Choose pathway regions", self._roi_page(), expanded=True)
        root.addWidget(self.roi_section)

        output = QFormLayout()
        self.output = QLineEdit()
        self.output.setPlaceholderText("Choose a new derivative directory")
        self.output.setAccessibleName("New tractography output directory")
        output_row = QHBoxLayout()
        output_row.addWidget(self.output, 1)
        self.choose_output_button = button("Choose parent…", self.choose_parent)
        output_row.addWidget(self.choose_output_button)
        output.addRow("New output directory", output_row)
        root.addLayout(output)

        root.addWidget(label("Review the plan", "section"))
        self.summary = label(
            "No plan has been validated. Nothing will run until you approve it.", "caption"
        )
        root.addWidget(self.summary)
        self.preview = button("Validate and Preview Plan", owner.prepare, primary=True)
        root.addWidget(self.preview)
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        self.commands.setAccessibleName("Exact tractography commands and scientific limitations")
        self.command_details = Disclosure("Exact commands and limitations", self.commands)
        root.addWidget(self.command_details)
        self.approval = QCheckBox(
            "I reviewed this exact plan, source association and scientific limitations"
        )
        root.addWidget(self.approval)
        self.run_button = button("Run Approved Plan", owner.run, primary=True)
        root.addWidget(self.run_button)
        self.result = label("", "caption")
        root.addWidget(self.result)
        result_actions = QHBoxLayout()
        self.open_button = button("Open Result in Viewer", owner.open_result)
        self.original_button = button("View Original Tracks", owner.open_original)
        self.qc_button = button("Review QC", owner.open_qc)
        self.details_button = button("View Run Details", owner.open_run)
        for action in (self.open_button, self.original_button, self.qc_button, self.details_button):
            result_actions.addWidget(action)
        root.addLayout(result_actions)
        root.addStretch()

        self.output.textChanged.connect(owner.output.setText)
        owner.output.textChanged.connect(self._output_from_advanced)
        self.approval.toggled.connect(owner.approval.setChecked)
        owner.approval.toggled.connect(self._approval_from_advanced)
        owner.operation.currentIndexChanged.connect(self._operation_from_advanced)
        owner.task_source.currentIndexChanged.connect(self._saved_task_changed)
        owner.method.currentIndexChanged.connect(self._method_from_advanced)
        owner.count.valueChanged.connect(self._count_from_advanced)
        for source in (
            owner.source,
            owner.seed_method,
            owner.seed,
            owner.mask,
            owner.tracts,
            owner.reference,
            owner.five_tt,
            owner.mode,
        ):
            source.currentIndexChanged.connect(self.refresh_inventory)
        self.refresh_inventory()
        self._operation_from_advanced()
        self._output_from_advanced(owner.output.text())
        self._approval_from_advanced(owner.approval.isChecked())
        self._saved_task_changed()
        self.update_actions()

    def _form_page(self, title, explanation):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label(title, "section"))
        layout.addWidget(label(explanation, "caption"))
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        return page, layout, form

    def _linked_combo(self, legacy, name):
        combo = QComboBox()
        combo.setAccessibleName(name)
        combo.currentIndexChanged.connect(
            lambda _index, source=combo, target=legacy: self._set_advanced_combo(source, target)
        )
        return combo

    def _input_row(self, form, title, combo, kind):
        row = QWidget()
        line = QHBoxLayout(row)
        line.setContentsMargins(0, 0, 0, 0)
        line.addWidget(combo, 1)
        action = button(
            "Import…",
            lambda _=False, requested=kind: self.owner.owner.browse_import(
                requested() if callable(requested) else requested
            ),
        )
        action.setAccessibleName(f"Import {title}")
        line.addWidget(action)
        form.addRow(title, row)
        self.import_buttons[title] = action
        return row

    def _generate_page(self):
        page, layout, form = self._form_page(
            "Generate New Streamlines",
            "A computational seed starts tracking; it is not necessarily an anatomical endpoint.",
        )
        self.source = self._linked_combo(self.owner.source, "Tracking input")
        self.method = self._linked_combo(self.owner.method, "Tracking method")
        self.mode = self._linked_combo(self.owner.mode, "Tracking mode")
        self.seed_method = self._linked_combo(self.owner.seed_method, "Seed method")
        self.seed = self._linked_combo(self.owner.seed, "Seed region or mask")
        self.mask = self._linked_combo(self.owner.mask, "Tracking mask")
        self.five_tt = self._linked_combo(self.owner.five_tt, "Five-tissue anatomy")
        self.reference_generate = self._linked_combo(self.owner.reference, "Tracking reference")
        self.count = QSpinBox()
        self.count.setRange(1, 10000000)
        self.count.setAccessibleName("Requested output streamline count")
        self.count.valueChanged.connect(self.owner.count.setValue)
        self.tensor_shells = QLineEdit()
        self.tensor_shells.setPlaceholderText("Explicit b0 and one weighted shell, s/mm²")
        self.tensor_shells.setAccessibleName("Tensor tracking shells")
        self.tensor_shells.textChanged.connect(self.owner.shells.setText)
        self.owner.shells.textChanged.connect(
            lambda value: self._copy_text(self.tensor_shells, value)
        )
        self.sphere_coords = QLineEdit()
        self.sphere_coords.setPlaceholderText("World RAS x, y, z, radius in mm")
        self.sphere_coords.setAccessibleName("Seed sphere coordinates")
        self.sphere_coords.textChanged.connect(self.owner.sphere.setText)
        self.owner.sphere.textChanged.connect(
            lambda value: self._copy_text(self.sphere_coords, value)
        )
        self.backtrack = QCheckBox("Retry poor structural terminations (ACT iFOD2 only)")
        self.crop = QCheckBox("Crop endpoints at the gray/white matter interface")
        self.backtrack.toggled.connect(self.owner.backtrack.setChecked)
        self.crop.toggled.connect(self.owner.crop.setChecked)
        self.owner.backtrack.toggled.connect(lambda value: self._copy_check(self.backtrack, value))
        self.owner.crop.toggled.connect(lambda value: self._copy_check(self.crop, value))
        self.import_buttons = {}
        self.generate_input_rows = {}
        for title, widget in (
            ("Tracking input", self.source),
            ("Method", self.method),
            ("Tracking mode", self.mode),
            ("Where should streamlines be seeded?", self.seed_method),
            ("Seed region or mask", self.seed),
            ("Where may tracks travel? Tracking mask", self.mask),
            ("Five-tissue anatomy", self.five_tt),
            ("Reference image", self.reference_generate),
            ("Requested output streamlines", self.count),
            ("Tensor shells (s/mm²)", self.tensor_shells),
            ("Seed sphere (world RAS mm)", self.sphere_coords),
            ("ACT backtracking", self.backtrack),
            ("ACT endpoint cropping", self.crop),
        ):
            kinds = {
                "Tracking input": lambda: (
                    "dwi"
                    if self.owner.method.currentData()
                    in {"tckgen.tensor_det", "tckgen.tensor_prob"}
                    else "fod"
                ),
                "Seed region or mask": lambda: (
                    "roi"
                    if self.owner.seed_method.currentData() == "roi"
                    else "gmwmi"
                    if self.owner.seed_method.currentData() == "gmwmi"
                    else "mask"
                ),
                "Where may tracks travel? Tracking mask": "mask",
                "Five-tissue anatomy": "5tt",
                "Reference image": "anatomy",
            }
            if title in kinds:
                self.generate_input_rows[title] = self._input_row(form, title, widget, kinds[title])
            else:
                form.addRow(title, widget)
        self.generate_form = form
        self.method_help = label("", "caption")
        layout.addWidget(self.method_help)
        layout.addWidget(
            label(
                "A tracking mask limits propagation; a required ROI is a pathway condition. Configure pathway ROIs below or in Advanced Settings.",
                "caption",
            )
        )
        layout.addStretch()
        return page

    def _filter_page(self):
        page, layout, form = self._form_page(
            "Select or Filter Existing Streamlines",
            "Choose an existing tractogram and reference. Include rules test regions a track must cross; exclude rules reject intersections.",
        )
        self.filter_tracts = self._linked_combo(self.owner.tracts, "Source tractogram")
        self.filter_reference = self._linked_combo(
            self.owner.reference, "Tractogram reference image"
        )
        self.ends_only = QCheckBox("Test endpoints only, instead of the full pathway")
        self.ends_only.toggled.connect(self.owner.ends.setChecked)
        self.owner.ends.toggled.connect(self._ends_from_advanced)
        self.filter_limit = QCheckBox("Limit the number of retained streamlines")
        self.filter_limit.toggled.connect(self.owner.filter_limit.setChecked)
        self.owner.filter_limit.toggled.connect(
            lambda value: self._copy_check(self.filter_limit, value)
        )
        self.filter_count = QSpinBox()
        self.filter_count.setRange(1, 10000000)
        self.filter_count.setAccessibleName("Maximum retained streamline count")
        self.filter_count.valueChanged.connect(self.owner.filter_count.setValue)
        self.owner.filter_count.valueChanged.connect(self._filter_count_from_advanced)
        self._input_row(form, "Filter existing tractogram", self.filter_tracts, "tractogram")
        self._input_row(form, "Filter reference image", self.filter_reference, "anatomy")
        self.convert_trk_button = button(
            "Convert reviewed full-source TRK to TCK…", self.owner.convert_trk
        )
        layout.addWidget(self.convert_trk_button)
        self.viewer_source_notice = label(
            "Open a tractogram in the Viewer, select its group, then use Filter These Tracks to carry its full source here.",
            "caption",
        )
        layout.addWidget(self.viewer_source_notice)
        viewer_actions = QHBoxLayout()
        self.review_viewer_source = button(
            "Review Viewer track source…", self.owner.viewer_source_requested.emit
        )
        self.review_viewer_reference = button(
            "Review Viewer reference…", self.owner.viewer_reference_requested.emit
        )
        viewer_actions.addWidget(self.review_viewer_source)
        viewer_actions.addWidget(self.review_viewer_reference)
        layout.addLayout(viewer_actions)
        self.review_viewer_source.hide()
        self.review_viewer_reference.hide()
        form.addRow(self.ends_only)
        form.addRow(self.filter_limit)
        form.addRow("Maximum retained streamlines", self.filter_count)
        layout.addWidget(
            label(
                "Ordered waypoints use the saved streamline vertex direction and cannot be combined with endpoint-only filtering. ROI constraints require explicit, compatible source and space identity.",
                "caption",
            )
        )
        self.atlas_filter_button = button(
            "Filter using original Viewer atlas (Include ANY / ALL)…",
            self.owner.owner.roi_filter_requested.emit,
        )
        layout.addWidget(self.atlas_filter_button)
        layout.addStretch()
        return page

    def _density_page(self):
        page, layout, form = self._form_page(
            "Create a Tract-Density Map",
            "Use a verified tractogram and compatible reference image. The source tracks remain unchanged.",
        )
        self.density_tracts = self._linked_combo(self.owner.tracts, "Density source tractogram")
        self.density_reference = self._linked_combo(self.owner.reference, "Density reference grid")
        self._input_row(form, "Density existing tractogram", self.density_tracts, "tractogram")
        self._input_row(form, "Density reference image / grid", self.density_reference, "anatomy")
        layout.addWidget(
            label(
                "Density is streamline-derived spatial information, not a direct measure of axon counts. Directional channels are in Advanced Settings.",
                "caption",
            )
        )
        layout.addStretch()
        return page

    def _roi_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(
            label(
                "Select an imported region, then assign its role. Required regions use Include ALL: a track must intersect every selected mask. Exclude ANY rejects a track that intersects any excluded mask.",
                "caption",
            )
        )
        self.roi_search = QLineEdit()
        self.roi_search.setPlaceholderText("Search imported regions")
        self.roi_search.setAccessibleName("Search tracking regions")
        self.roi_search.textChanged.connect(self._filter_roi_inventory)
        layout.addWidget(self.roi_search)
        self.roi_inventory = QListWidget()
        self.roi_inventory.setAccessibleName("Available verified ROI masks")
        self.roi_inventory.setMaximumHeight(150)
        layout.addWidget(self.roi_inventory)
        actions = QHBoxLayout()
        actions.addWidget(button("Must pass through", lambda: self.assign_roi("include")))
        actions.addWidget(button("Must avoid", lambda: self.assign_roi("exclude")))
        actions.addWidget(button("Add waypoint", lambda: self.assign_roi("ordered")))
        layout.addLayout(actions)
        self.highlight_button = button("Inspect selected region in Viewer", self.highlight_roi)
        layout.addWidget(self.highlight_button)
        self.roi_notice = label(
            "No region selected. Select an imported ROI, or use the existing Viewer to create one.",
            "caption",
        )
        layout.addWidget(self.roi_notice)
        self.import_rois = button(
            "Use selected Viewer ROIs…", self.owner.owner.roi_import_requested.emit
        )
        layout.addWidget(
            button("Choose regions in Viewer…", self.owner.viewer_regions_requested.emit)
        )
        layout.addWidget(self.import_rois)
        layout.addWidget(button("Import ROI mask…", lambda: self.owner.owner.browse_import("roi")))
        groups = QHBoxLayout()
        self.roi_groups = {}
        for role, title, explanation in (
            ("include", "Must pass through (all)", "Every required mask must be crossed."),
            ("exclude", "Must avoid (any)", "Any excluded-mask hit rejects the track."),
            (
                "ordered",
                "Pass through in this order",
                "Waypoint order follows the saved streamline vertex direction for filtering.",
            ),
        ):
            column = QVBoxLayout()
            column.addWidget(label(title, "section"))
            column.addWidget(label(explanation, "caption"))
            items = QListWidget()
            items.setAccessibleName(title)
            items.setMaximumHeight(120)
            column.addWidget(items)
            column.addWidget(button("Remove selected", lambda _=False, r=role: self.remove_roi(r)))
            if role == "ordered":
                moves = QHBoxLayout()
                moves.addWidget(button("Move up", lambda: self.move_waypoint(-1)))
                moves.addWidget(button("Move down", lambda: self.move_waypoint(1)))
                column.addLayout(moves)
            groups.addLayout(column)
            self.roi_groups[role] = items
        layout.addLayout(groups)
        layout.addWidget(
            label(
                "Endpoint-only filtering tests the actual streamline endpoints against the selected masks; it is not the same as passing through them. Ordered waypoints cannot be combined with endpoint-only filtering.",
                "caption",
            )
        )
        return page

    def set_imported_roi_metadata(self, metadata):
        """Keep display names/colors separate from immutable scientific artifacts."""
        self.roi_metadata.update(metadata)
        self._roi_signature = None
        self.refresh_roi_inventory()

    def _roi_name(self, artifact):
        metadata = self.roi_metadata.get(artifact.id, {})
        if metadata.get("name"):
            return str(metadata["name"])
        statement = artifact.payload.get("provenance", {}).get("statement", "")
        prefix = "Separate selected viewer ROI snapshot: "
        if statement.startswith(prefix):
            return statement[len(prefix) :].split(". Source/space association", 1)[0]
        return Path(artifact.payload["path"]).name

    def refresh_roi_inventory(self):
        artifacts = [a for a in self.owner.owner.artifacts if a.kind == "roi"]
        signature = tuple((a.id, self._roi_name(a)) for a in artifacts)
        if signature != self._roi_signature:
            current = self.roi_inventory.currentItem()
            selected = current.data(Qt.ItemDataRole.UserRole) if current else None
            self.roi_inventory.clear()
            for artifact in artifacts:
                item = QListWidgetItem(self._roi_name(artifact))
                item.setData(Qt.ItemDataRole.UserRole, artifact.id)
                item.setToolTip(str(artifact.payload["path"]))
                color = self.roi_metadata.get(artifact.id, {}).get("color")
                if color:
                    swatch = QPixmap(14, 14)
                    swatch.fill(QColor.fromRgbF(*color[:3]))
                    item.setIcon(QIcon(swatch))
                self.roi_inventory.addItem(item)
                if artifact.id == selected:
                    self.roi_inventory.setCurrentItem(item)
            self._roi_signature = signature
        self._filter_roi_inventory()
        self.refresh_constraints()

    def _filter_roi_inventory(self, *_):
        query = self.roi_search.text().casefold().strip()
        for index in range(self.roi_inventory.count()):
            item = self.roi_inventory.item(index)
            item.setHidden(bool(query and query not in item.text().casefold()))

    def refresh_constraints(self):
        for role, source in self.owner.constraints.items():
            target = self.roi_groups[role]
            selected = target.currentItem()
            selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
            values = [
                source.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(source.count())
                if source.item(i).checkState() == Qt.CheckState.Checked
            ]
            existing = [
                target.item(i).data(Qt.ItemDataRole.UserRole) for i in range(target.count())
            ]
            if values == existing:
                continue
            target.clear()
            for key in values:
                artifact = next((a for a in self.owner.owner.artifacts if a.id == key), None)
                if artifact is None:
                    continue
                item = QListWidgetItem(self._roi_name(artifact))
                item.setData(Qt.ItemDataRole.UserRole, key)
                target.addItem(item)
                if key == selected_id:
                    target.setCurrentItem(item)
        self.roi_notice.setText(
            f"{self.roi_inventory.count()} imported regions available. "
            "Original masks and tractograms remain unchanged."
            if self.roi_inventory.count()
            else "No imported regions. Select ROIs in the Viewer, then use selected Viewer ROIs."
        )

    def _selected_roi(self):
        item = self.roi_inventory.currentItem()
        if item is None or item.isHidden():
            self.roi_notice.setText("Select a visible imported region first.")
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def assign_roi(self, role):
        key = self._selected_roi()
        if not key:
            return
        self._use_form_settings()
        source = self.owner.constraints[role]
        for index in range(source.count()):
            item = source.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                if item.checkState() != Qt.CheckState.Checked:
                    item.setCheckState(Qt.CheckState.Checked)
                break
        self.refresh_constraints()

    def remove_roi(self, role):
        selected = self.roi_groups[role].currentItem()
        if selected is None:
            return
        self._use_form_settings()
        key = selected.data(Qt.ItemDataRole.UserRole)
        source = self.owner.constraints[role]
        for index in range(source.count()):
            item = source.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                item.setCheckState(Qt.CheckState.Unchecked)
                break
        self.refresh_constraints()

    def move_waypoint(self, delta):
        target = self.roi_groups["ordered"]
        selected = target.currentItem()
        if selected is None:
            return
        self._use_form_settings()
        ids = [target.item(i).data(Qt.ItemDataRole.UserRole) for i in range(target.count())]
        position = ids.index(selected.data(Qt.ItemDataRole.UserRole))
        new_position = position + delta
        if not 0 <= new_position < len(ids):
            return
        ids[position], ids[new_position] = ids[new_position], ids[position]
        source = self.owner.constraints["ordered"]
        all_items = []
        while source.count():
            all_items.append(source.takeItem(0))
        items = {item.data(Qt.ItemDataRole.UserRole): item for item in all_items}
        # Preserve unchecked inventory after the selected waypoints.
        for key in ids:
            source.addItem(items.pop(key))
        for item in items.values():
            source.addItem(item)
        self.owner.invalidate()
        self.refresh_constraints()
        for i in range(target.count()):
            if target.item(i).data(Qt.ItemDataRole.UserRole) == ids[new_position]:
                target.setCurrentRow(i)
                break

    def highlight_roi(self):
        key = self._selected_roi()
        if key:
            self.highlight_requested.emit(key)

    def select_task(self, task):
        self._use_form_settings()
        index = self.owner.operation.findData(task)
        if index >= 0 and index != self.owner.operation.currentIndex():
            self.owner.operation.setCurrentIndex(index)
        elif task in self.task_buttons:
            self.task_buttons[task].setChecked(True)

    def _use_form_settings(self):
        if self.owner.task_source.currentData() is not None:
            self.owner.task_source.setCurrentIndex(0)

    def _saved_task_changed(self, *_):
        task = self.owner.task_source.task()
        if task is None:
            self.saved_task_notice.setText("")
            return
        operation = (
            "generate"
            if task.algorithm.startswith("tckgen.")
            else "filter"
            if task.algorithm == "tckedit"
            else "density"
        )
        index = self.owner.operation.findData(operation)
        if index >= 0 and index != self.owner.operation.currentIndex():
            self.owner.operation.setCurrentIndex(index)
        self.saved_task_notice.setText(
            "An exact saved scientific task is selected in Advanced Settings. Its parameters take precedence; choose a task above to edit form settings instead."
        )

    def _operation_from_advanced(self, *_):
        current = self.owner.operation.currentData()
        for index, (key, _title, explanation) in enumerate(TASKS):
            self.task_buttons[key].setChecked(key == current)
            if key == current:
                self.pages.setCurrentIndex(index)
                self.task_help.setText(explanation)
        self.roi_section.setVisible(current != "density")
        self.clear_plan()
        self.update_actions()

    def _set_advanced_combo(self, source, target):
        if self._syncing:
            return
        self._use_form_settings()
        index = target.findData(source.currentData())
        if index >= 0:
            target.setCurrentIndex(index)

    def _copy_combo(self, target, source):
        target.blockSignals(True)
        target.clear()
        for index in range(source.count()):
            target.addItem(source.itemText(index), source.itemData(index))
            target.setItemData(
                index,
                source.itemData(index, Qt.ItemDataRole.ToolTipRole),
                Qt.ItemDataRole.ToolTipRole,
            )
            model_item = source.model().item(index)
            if model_item is not None and target.model().item(index) is not None:
                target.model().item(index).setEnabled(model_item.isEnabled())
        target.setCurrentIndex(source.currentIndex())
        target.blockSignals(False)

    def refresh_inventory(self):
        self._syncing = True
        for target, source in (
            (self.source, self.owner.source),
            (self.method, self.owner.method),
            (self.mode, self.owner.mode),
            (self.seed_method, self.owner.seed_method),
            (self.seed, self.owner.seed),
            (self.mask, self.owner.mask),
            (self.five_tt, self.owner.five_tt),
            (self.reference_generate, self.owner.reference),
            (self.filter_reference, self.owner.reference),
            (self.density_reference, self.owner.reference),
            (self.filter_tracts, self.owner.tracts),
            (self.density_tracts, self.owner.tracts),
        ):
            self._copy_combo(target, source)
        self._syncing = False
        self._count_from_advanced(self.owner.count.value())
        self._ends_from_advanced(self.owner.ends.isChecked())
        self._copy_check(self.filter_limit, self.owner.filter_limit.isChecked())
        self._filter_count_from_advanced(self.owner.filter_count.value())
        self._copy_text(self.tensor_shells, self.owner.shells.text())
        self._copy_text(self.sphere_coords, self.owner.sphere.text())
        self._copy_check(self.backtrack, self.owner.backtrack.isChecked())
        self._copy_check(self.crop, self.owner.crop.isChecked())
        self._method_from_advanced()
        self.refresh_roi_inventory()
        self.update_actions()

    def show_viewer_source(self, context):
        """Identify the original source separately from Viewer display edits."""
        source = context["source"]
        reference = context["reference"]
        if context["edited"]:
            changes = (
                "Viewer selections or edits are display state. Filtering uses every streamline "
                "in the unchanged source file. Export a reviewed derivative first if you intend "
                "to filter the edited selection."
            )
        elif context["displayed"] < context["total"]:
            changes = (
                "Only a display subset is rendered. Filtering uses every streamline "
                "in the complete source file."
            )
        else:
            changes = "Filtering uses the complete source file."
        missing = []
        if self.owner.tracts.currentData() is None:
            missing.append("Review/import the track source with subject and spatial identity.")
        if self.owner.reference.currentData() is None:
            missing.append("Review/import the matching reference image and its spatial identity.")
        selected = [
            next(
                (a for a in self.owner.owner.artifacts if a.id == combo.currentData()),
                None,
            )
            for combo in (self.owner.tracts, self.owner.reference)
        ]
        for title, item in zip(("Tractogram", "Reference image"), selected, strict=True):
            if item is None:
                continue
            declarations = item.payload.get("declarations", {})
            absent = [name for name in ("subject_id", "space_id") if not declarations.get(name)]
            if absent:
                missing.append(
                    f"{title} is imported but needs {', '.join(absent)}. "
                    "Use its Review Viewer action to supply documented evidence."
                )
        if all(selected):
            left, right = (item.payload.get("declarations", {}) for item in selected)
            for key, title in (("subject_id", "subject"), ("space_id", "space")):
                if left.get(key) and right.get(key) and left[key] != right[key]:
                    missing.append(
                        f"Tractogram and reference declare different {title} identities; "
                        "choose a matching input or review documented evidence."
                    )
        selected_source = selected[0]
        selected_path = (
            Path(selected_source.payload["path"]) if selected_source is not None else None
        )
        if source.suffix.lower() == ".trk" and (
            selected_path is None or selected_path.suffix.lower() == ".trk"
        ):
            missing.append(
                "TRK is viewable, but the validated tckedit filter accepts TCK only. "
                "Use the explicit full-source conversion below after reviewing its reference; "
                "conversion is never automatic."
            )
        elif source.suffix.lower() == ".trk":
            missing.append(
                "The selected TCK derivative differs from the Viewer TRK source. "
                "Review its conversion record and exact plan before approval."
            )
        self.viewer_source_notice.setText(
            f"Viewer source: {source.name} · {context['total']:,} source streamlines. "
            f"Viewer reference: {reference.name}. {changes} " + " ".join(missing)
        )
        self.viewer_source_notice.setToolTip(f"Source: {source}\nReference: {reference}")
        self.review_viewer_source.show()
        self.review_viewer_reference.show()

    def _method_from_advanced(self, *_):
        self.method_help.setText(
            METHOD_HELP.get(self.owner.method.currentData(), "Choose a supported method.")
        )
        self.generate_form.setRowVisible(
            self.generate_input_rows["Five-tissue anatomy"], self.owner.mode.currentData() == "act"
        )
        self.generate_form.setRowVisible(
            self.generate_input_rows["Reference image"], self.owner.mode.currentData() == "act"
        )
        self.generate_form.setRowVisible(
            self.generate_input_rows["Seed region or mask"],
            self.owner.seed_method.currentData()
            in {"image", "roi", "random_per_voxel", "grid_per_voxel", "gmwmi"},
        )
        tensor = self.owner.method.currentData() in {
            "tckgen.tensor_det",
            "tckgen.tensor_prob",
        }
        act = self.owner.mode.currentData() == "act"
        self.generate_form.setRowVisible(self.tensor_shells, tensor)
        self.generate_form.setRowVisible(
            self.sphere_coords, self.owner.seed_method.currentData() == "sphere"
        )
        self.generate_form.setRowVisible(
            self.backtrack, act and self.owner.method.currentData() == "tckgen.ifod2"
        )
        self.generate_form.setRowVisible(self.crop, act)
        self.backtrack.setEnabled(self.owner.backtrack.isEnabled())

    @staticmethod
    def _copy_text(target, value):
        if target.text() != value:
            target.blockSignals(True)
            target.setText(value)
            target.blockSignals(False)

    @staticmethod
    def _copy_check(target, value):
        if target.isChecked() != value:
            target.blockSignals(True)
            target.setChecked(value)
            target.blockSignals(False)

    def _count_from_advanced(self, value):
        self.count.blockSignals(True)
        self.count.setValue(value)
        self.count.blockSignals(False)

    def _ends_from_advanced(self, checked):
        self.ends_only.blockSignals(True)
        self.ends_only.setChecked(checked)
        self.ends_only.blockSignals(False)

    def _filter_count_from_advanced(self, value):
        self.filter_count.blockSignals(True)
        self.filter_count.setValue(value)
        self.filter_count.blockSignals(False)

    def _output_from_advanced(self, value):
        if self.output.text() != value:
            self.output.blockSignals(True)
            self.output.setText(value)
            self.output.blockSignals(False)

    def _approval_from_advanced(self, checked):
        self.approval.blockSignals(True)
        self.approval.setChecked(checked)
        self.approval.blockSignals(False)
        self.update_actions()

    def choose_parent(self):
        path = QFileDialog.getExistingDirectory(self, "Tractography output parent")
        if path:
            self.output.setText(str(Path(path) / "tracking-attempt001"))

    def clear_plan(self):
        self.summary.setText("Configuration changed. Validate the exact plan before approval.")
        self.commands.clear()
        self.update_actions()

    def plan_ready(self, plan):
        data = plan.to_dict()
        row = data["bindings"][0] if data.get("bindings") else {}
        bound = row.get("input_bindings", {})
        parameters = row.get("parameters", {})
        algorithm = row.get("algorithm", "Unknown")
        source = bound.get("tracts") or bound.get("fod") or bound.get("dwi") or {}
        parts = [
            f"Task: {algorithm}",
            f"Input: {Path(source['path']).name if source.get('path') else 'Unavailable'}",
        ]
        if algorithm.startswith("tckgen."):
            parts.append(f"Seed method: {parameters.get('seed_method', 'unspecified')}")
            parts.append(f"Requested output streamlines: {parameters.get('select', 'unspecified')}")
            if bound.get("mask"):
                parts.append("Tracking mask: " + Path(bound["mask"]["path"]).name)
            if bound.get("five_tt"):
                parts.append("Five-tissue anatomy: " + Path(bound["five_tt"]["path"]).name)
        elif algorithm == "tckedit":
            parts.append(
                "Filtering scope: "
                + (
                    "streamline endpoints"
                    if parameters.get("ends_only") == "on"
                    else "full pathway"
                )
            )
            if parameters.get("number"):
                parts.append(f"Maximum output streamlines: {parameters['number']}")
        elif algorithm == "tckmap":
            parts.append("Reference grid: " + Path(bound["reference"]["path"]).name)
            parts.append("Directional channels: " + parameters.get("directional", "off"))
        for role, title in (
            ("include", "Must pass through every"),
            ("exclude", "Must avoid any"),
            ("ordered", "Ordered waypoints"),
        ):
            entries = row.get("constraints", {}).get(role, [])
            if entries:
                parts.append(title + ": " + " → ".join(self._bound_roi_name(e) for e in entries))
        parts += [
            "Output: " + data["output_directory"],
            f"Exact commands: {len(data['commands'])}; approval digest: {plan.digest}",
            "Technical validation and scientific QC/review are separate.",
        ]
        self.summary.setText("\n".join(parts))
        self.commands.setPlainText(self.owner.log.toPlainText())
        self.update_actions()

    def _bound_roi_name(self, record):
        artifact = next((a for a in self.owner.owner.artifacts if a.id == record.get("id")), None)
        return self._roi_name(artifact) if artifact else Path(record["path"]).name

    def result_ready(self, record):
        status = record.get("status", "unknown").replace("_", " ")
        self.result.setText(
            f"{status.capitalize()} · scientific QC and review remain separate. "
            + str(record.get("plan", {}).get("output_directory", ""))
        )
        self.update_actions()

    def update_actions(self):
        idle = self.owner.owner.thread is None
        self.pages.setEnabled(idle)
        self.output.setEnabled(idle)
        self.choose_output_button.setEnabled(idle)
        for choice in self.task_buttons.values():
            choice.setEnabled(idle)
        self.preview.setEnabled(idle)
        self.approval.setEnabled(idle and self.owner.plan is not None)
        self.run_button.setEnabled(
            idle and self.owner.plan is not None and self.owner.approval.isChecked()
        )
        self.open_button.setEnabled(idle and self.owner.maps.count() > 0)
        source = next(
            (a for a in self.owner.owner.artifacts if a.id == self.owner.tracts.currentData()),
            None,
        )
        self.convert_trk_button.setEnabled(
            idle
            and source is not None
            and Path(source.payload["path"]).suffix.lower() == ".trk"
            and self.owner.reference.currentData() is not None
        )
        self.original_button.setEnabled(
            idle
            and self.owner.manifest is not None
            and self.owner.manifest.get("status") == "validated_pending_qc"
            and self.owner.manifest.get("inputs_unchanged")
            and any(
                row.get("algorithm") == "tckedit"
                for row in self.owner.manifest.get("plan", {}).get("bindings", ())
            )
        )
        self.qc_button.setEnabled(idle and self.owner.qc_button.isEnabled())
        self.details_button.setEnabled(idle and self.owner.manifest is not None)
        self.roi_section.setEnabled(idle)
        self.filter_count.setEnabled(
            idle
            and self.owner.operation.currentData() == "filter"
            and self.owner.filter_limit.isChecked()
        )
