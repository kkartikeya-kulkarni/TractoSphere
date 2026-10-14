"""Atlas controls and grouped ROI manager within the existing scientific viewer."""

import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
)

from dmri_studio.core.atlas import Atlas, filter_streamlines, find_table, xml_images
from dmri_studio.core.scene import TractGroup

from .components import label


class RegionItem(QTreeWidgetItem):
    def setText(self, *args):
        super().setText(0, args[0]) if len(args) == 1 else super().setText(*args)

    def text(self, column=0):
        return super().text(column)


class RoiTree(QTreeWidget):
    """Grouped regions with the existing row-index API for binary ROI tools."""

    currentRowChanged = Signal(int)
    visibilityRequested = Signal(int, bool)

    def __init__(self, rois):
        super().__init__()
        self.rois = rois
        self.entries = []
        self.setColumnCount(3)
        self.setHeaderLabels(["Region / label", "mm³", "Role"])
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setIndentation(12)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.setColumnWidth(0, 245)
        self.setStyleSheet("QTreeView::item { padding: 4px 2px; }")
        for col in (1, 2):
            self.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.currentItemChanged.connect(lambda *_: self.currentRowChanged.emit(self.currentRow()))
        self.itemChanged.connect(self.changed)

    def currentRow(self):
        item = self.currentItem()
        return (
            int(item.data(0, Qt.ItemDataRole.UserRole))
            if item and item.data(0, Qt.ItemDataRole.UserRole) is not None
            else -1
        )

    def setCurrentRow(self, i):
        if 0 <= i < len(self.entries):
            self.setCurrentItem(self.entries[i])
            self.scrollToItem(self.entries[i])

    def selected_rows(self):
        return [
            int(item.data(0, Qt.ItemDataRole.UserRole))
            for item in self.selectedItems()
            if item.data(0, Qt.ItemDataRole.UserRole) is not None
        ]

    def addItem(self, _name):
        self.rebuild()

    def takeItem(self, _index):
        self.rebuild()

    def clear(self):
        self.entries = []
        super().clear()

    def rebuild(self):
        current = self.currentRow()
        with QSignalBlocker(self):
            self.clear()
            parents = {}
            counts = {}
            for roi in self.rois:
                key = roi.get("atlas_id", "Manual ROIs")
                counts[key] = counts.get(key, 0) + 1
            for i, roi in enumerate(self.rois):
                key = roi.get("atlas_id", "Manual ROIs")
                if key not in parents:
                    name = roi.get("atlas_name", "Manual ROIs")
                    parent = RegionItem([f"{name} ({counts[key]} regions)"])
                    parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                    parent.setToolTip(0, name)
                    self.addTopLevelItem(parent)
                    parent.setFirstColumnSpanned(True)
                    parent.setExpanded(True)
                    parents[key] = parent
                name = f"{roi['label_id']} · {roi['name']}" if "label_id" in roi else roi["name"]
                volume = roi.get("volume_mm3")
                item = RegionItem(
                    [
                        name,
                        f"{volume:.0f}" if volume is not None else "—",
                        roi.get("role", "Display"),
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, i)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    0, Qt.CheckState.Checked if roi["visible"] else Qt.CheckState.Unchecked
                )
                swatch = QPixmap(12, 12)
                swatch.fill(QColor.fromRgbF(*roi["color"]))
                item.setIcon(0, QIcon(swatch))
                item.setToolTip(
                    0,
                    f"{name}\n{roi.get('voxel_count', '')} voxels\n{roi.get('atlas_source', roi.get('path', ''))}\nRole filters existing fibers, not new tractography",
                )
                parents[key].addChild(item)
                self.entries.append(item)
            if 0 <= current < len(self.entries):
                self.setCurrentItem(self.entries[current])

    def changed(self, item, column):
        index = item.data(0, Qt.ItemDataRole.UserRole)
        if column == 0 and index is not None:
            self.visibilityRequested.emit(int(index), item.checkState(0) == Qt.CheckState.Checked)

    def update_region(self, i):
        if 0 <= i < len(self.entries):
            roi, item = self.rois[i], self.entries[i]
            with QSignalBlocker(self):
                item.setCheckState(
                    0, Qt.CheckState.Checked if roi["visible"] else Qt.CheckState.Unchecked
                )
                item.setText(2, roi.get("role", "Display"))
                if roi.get("volume_mm3") is not None:
                    item.setText(1, f"{roi['volume_mm3']:.0f}")
                pix = QPixmap(12, 12)
                pix.fill(QColor.fromRgbF(*roi["color"]))
                item.setIcon(0, QIcon(pix))


class AtlasTools:
    def setup_atlas_tools(self):
        self.atlas_status = label(
            "Atlas regions appear here after import. Select a region to show it in 3D.", "caption"
        )
        self.atlas_status.setWordWrap(True)
        self.rois_layout.insertWidget(0, self.action("Import anatomical atlas", self.choose_atlas))
        self.rois_layout.insertWidget(1, self.atlas_status)
        self.roi_list.visibilityRequested.connect(
            lambda i, v: self.safe(lambda: self.atlas_visibility(i, v))
        )
        self.roi_list.itemSelectionChanged.connect(lambda: self.safe(self.atlas_selection))
        self.roi_role = QComboBox()
        self.roi_role.addItems(["Display", "Include", "Exclude"])
        self.roi_role.setToolTip(
            "Assign to selected regions. Include/Exclude applies only to filtering existing tracts."
        )
        self.rois_layout.insertWidget(3, self.roi_role)
        self.rois_layout.insertWidget(
            4, self.action("Assign role to selected ROIs", self.assign_roi_role)
        )
        self.roi_filter_mode = QComboBox()
        self.roi_filter_mode.addItems(
            ["Pass through ALL inclusion ROIs", "Pass through ANY inclusion ROI"]
        )
        self.rois_layout.insertWidget(5, self.roi_filter_mode)
        self.rois_layout.insertWidget(
            6, self.action("Filter tracts with ROI roles", self.filter_roi_roles)
        )
        self.rois_layout.insertWidget(
            7, self.action("Save selected tract group", self.export_selected_group)
        )
        self.rois_layout.insertWidget(
            8, self.action("Reimport atlas with new threshold", self.rethreshold_atlas)
        )

        self.tract_space_confirmed = QCheckBox("Tracts verified in this reference anatomical space")
        self.tract_space_confirmed.setToolTip(
            "TCK/TRK coordinates and image bounds alone cannot establish subject or template identity. "
            "Confirm the source provenance and alignment before filtering."
        )
        self.rois_layout.insertWidget(6, self.tract_space_confirmed)
        self.tract_list.currentRowChanged.connect(
            lambda _: self.tract_space_confirmed.setChecked(False)
        )

    def choose_atlas(self, path=None):
        if self.reference is None:
            raise ValueError("Open the anatomical reference first")
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "Import anatomical atlas", "", "Atlas (*.nii *.nii.gz *.xml)"
            )
        if not path:
            return
        table = None
        if str(path).endswith(".xml"):
            table = path
            choices = xml_images(path)
            if not choices:
                raise ValueError("FSL XML references no available NIfTI atlas images")
            name, ok = QInputDialog.getItem(
                self,
                "Atlas image",
                "Choose exact image / resolution",
                [str(p) for p in choices],
                0,
                False,
            )
            if not ok:
                return
            path = name
        table = table or find_table(path)
        dialog = QDialog(self)
        dialog.setWindowTitle("Atlas import · geometry and probabilities")
        form = QFormLayout(dialog)
        detail = QLabel("Atlas: " + str(path) + "\nReference: " + str(self.reference.path))
        detail.setWordWrap(True)
        form.addRow(detail)
        mode = QComboBox()
        mode.addItems(["auto", "labels", "probability"])
        form.addRow("Interpretation", mode)
        threshold = QDoubleSpinBox()
        threshold.setRange(0.001, 1)
        threshold.setDecimals(3)
        threshold.setSingleStep(0.05)
        threshold.setValue(0.25)
        form.addRow("Probability threshold (0–1)", threshold)
        scale = QComboBox()
        scale.addItems(["1", "100"])
        form.addRow("Stored probability maximum", scale)
        table_edit = QLineEdit(str(table) if table else "")
        form.addRow("Label table (optional)", table_edit)
        browse = QPushButton("Choose label table")
        browse.clicked.connect(
            lambda: table_edit.setText(
                QFileDialog.getOpenFileName(
                    dialog, "Label metadata", "", "Tables (*.xml *.tsv *.csv *.txt *.json)"
                )[0]
                or table_edit.text()
            )
        )
        form.addRow(browse)
        transform = QLineEdit()
        form.addRow("Verified affine JSON (optional)", transform)
        transform_button = QPushButton("Choose verified transform")
        transform_button.clicked.connect(
            lambda: transform.setText(
                QFileDialog.getOpenFileName(
                    dialog, "Verified atlas-to-reference transform", "", "JSON (*.json)"
                )[0]
                or transform.text()
            )
        )
        form.addRow(transform_button)
        same = QCheckBox("I verified this atlas already shares the reference anatomical space")
        form.addRow(same)
        hint = QLabel(
            "Matching voxel dimensions do not establish alignment. MNI/native mismatches require a verified transform. FSL XML sets its documented 0–100 probability scale. Empty regions are omitted."
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.load_atlas(
                path,
                table=table_edit.text() or None,
                kind=mode.currentText(),
                threshold=threshold.value(),
                probability_scale=float(scale.currentText()),
                same_space=same.isChecked(),
                transform=transform.text() or None,
            )

    def load_atlas(self, path, **options):
        ref = self.reference
        if ref is None:
            raise ValueError("Open the matching anatomical reference first")
        self.status.setText("Inspecting atlas labels, metadata and spatial association…")

        def ready(atlas):
            if self.reference is not ref:
                return
            existing = {(r.get("atlas_id"), r.get("label_id")) for r in self.rois}
            incoming = [
                r for r in atlas.regions() if (r["atlas_id"], r["label_id"]) not in existing
            ]
            if not incoming:
                self.status.setText("This atlas and threshold are already imported")
                return
            first = len(self.rois)
            self.rois.extend(incoming)
            self.roi_list.rebuild()
            self.tabs.setCurrentIndex(2)
            self.atlas_status.setText(
                f"{atlas.name} · {len(incoming)} regions · "
                + ("probabilistic" if atlas.probability else "discrete labels")
                + "\n"
                + "; ".join(atlas.warnings)
            )
            self.roi_list.setCurrentRow(first)
            self.status.setText("Imported atlas: " + atlas.name + ". " + "; ".join(atlas.warnings))

        self.background(lambda: Atlas(path, ref, **options), ready)

    def atlas_selection(self):
        for i in self.roi_list.selected_rows():
            if self.rois[i].get("atlas_id"):
                self.atlas_visibility(i, True)
        selected = set(self.roi_list.selected_rows())
        for key, actor in self.scene.actors.items():
            if key[0] == "roi":
                # Highlight without obscuring the anatomy with every triangle edge.
                actor.GetProperty().SetEdgeVisibility(False)
                actor.GetProperty().SetAmbient(0.4 if key[1] in selected else 0.15)
        self.scene.draw()

    def atlas_visibility(self, i, visible):
        if not 0 <= i < len(self.rois):
            return
        roi = self.rois[i]
        roi["visible"] = visible
        if visible and ("roi", i) not in self.scene.actors:
            # A zero border closes masks at the image edge without changing
            # source voxels. Translate the padded grid back by one voxel.
            padded = np.pad(roi["data"], 1)
            shift = np.eye(4)
            shift[:3, 3] = -1
            self.scene.surface(
                ("roi", i), padded, self.reference.affine @ shift, 0.5, roi["color"], roi["opacity"]
            )
            if self.scene.actors[("roi", i)].GetMapper().GetInput().GetNumberOfPoints() == 0:
                self.scene.remove(("roi", i))
                raise ValueError("Surface generation produced no geometry; check selected mask")
            roi["surface_built"] = True
        if ("roi", i) in self.scene.actors:
            self.scene.actors[("roi", i)].SetVisibility(visible)
        # Bound retained hidden surface cache; visible selections are user-controlled.
        hidden = [
            k for k, a in self.scene.actors.items() if k[0] == "roi" and not a.GetVisibility()
        ]
        for key in hidden[:-8]:
            self.scene.remove(key)
            self.rois[key[1]]["surface_built"] = False
        self.roi_list.update_region(i)
        if i == self.roi_list.currentRow():
            self.roi_info(i)
        self.refresh_slices()
        self.scene.draw()

    def assign_roi_role(self):
        ids = self.roi_list.selected_rows()
        if not ids:
            raise ValueError("Select one or more regions first")
        for i in ids:
            self.rois[i]["role"] = self.roi_role.currentText()
            self.roi_list.update_region(i)

    def filter_roi_roles(self):
        group = self.current_group()
        if group is None:
            raise ValueError("Load and select the matching-space tract group first")
        if not self.tract_space_confirmed.isChecked():
            raise ValueError(
                "Verify the selected tract group's subject and anatomical space, then confirm the tract/reference space checkbox before filtering"
            )
        rois = [
            dict(data=r["data"], role=r.get("role"))
            for r in self.rois
            if r.get("role") in ("Include", "Exclude")
        ]
        ref = self.reference
        snapshot = TractGroup(group.path, group.streamlines, group.source_ids, group.total)
        snapshot.cuts, snapshot.deleted = dict(group.cuts), set(group.deleted)
        require_all = self.roi_filter_mode.currentIndex() == 0

        def ready(selected):
            if self.reference is ref and any(g is group for g in self.groups):
                group.selected = selected - group.deleted
                self.refresh_tracts()
                self.status.setText(
                    f"{len(group.selected):,} fibers selected by volumetric ROI intersections; no new tractography generated"
                )

        self.status.setText("Filtering indexed fibers against volumetric ROI roles…")
        self.background(lambda: filter_streamlines(snapshot, rois, ref, require_all), ready)

    def export_selected_group(self):
        group = self.current_group()
        if group is None or not group.selected - group.deleted:
            raise ValueError("Select streamlines before saving a selected tract group")
        path, fmt = QFileDialog.getSaveFileName(
            self, "Save selected tract group", "", "MRtrix (*.tck);;TrackVis (*.trk)"
        )
        if not path:
            return
        if Path(path).suffix.lower() not in (".tck", ".trk"):
            path += ".trk" if "TrackVis" in fmt else ".tck"
        if Path(path + ".json").exists():
            raise FileExistsError(path + ".json")
        snapshot = TractGroup(group.path, group.streamlines, group.source_ids, group.total)
        ids = sorted(group.selected - group.deleted)
        snapshot.deleted = set(range(len(group.streamlines))) - set(ids)
        snapshot.cuts = dict(group.cuts)
        ref = self.reference
        roles = [
            {k: v for k, v in r.items() if k != "data"}
            for r in self.rois
            if r.get("role") in ("Include", "Exclude")
        ]

        def write():
            snapshot.export(path, ref)
            with open(path + ".json", "x") as f:
                json.dump(
                    dict(
                        source=str(group.path),
                        source_ids=[int(group.source_ids[i]) for i in ids],
                        roi_roles=roles,
                        operation="filter existing streamlines; no reconstruction",
                        tract_reference_space_user_confirmed=True,
                    ),
                    f,
                    indent=2,
                )
            return path

        self.background(write, lambda p: self.status.setText("Saved selected tract group: " + p))

    def rethreshold_atlas(self):
        i = self.roi_list.currentRow()
        if i < 0 or self.rois[i].get("atlas_kind") != "probability":
            raise ValueError(
                "Select a probabilistic atlas region first; reimport preserves the previous threshold group"
            )
        self.choose_atlas(self.rois[i]["atlas_source"])
