"""Goal-first controls over the existing typed registration planner and worker."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dmri_studio.core.scientific import Task

from .components import Disclosure, button, label

GOALS = (
    (
        "reference",
        "Create a Diffusion Reference",
        "Select low-b measurements from one identified, preprocessed DWI. This does not align it with anatomy.",
    ),
    (
        "epi_t1",
        "Align Diffusion and T1",
        "Estimate EPI/b0 → T1 alignment using the required brain-extracted T1 and white-matter segmentation.",
    ),
    (
        "align_two",
        "Align Two Images",
        "Estimate a rigid or affine moving-image → reference-image transform.",
    ),
    (
        "move",
        "Move an Atlas or ROI",
        "Apply an imported transform to a compatible atlas, ROI, scalar image or probability map. Its exact identity and direction are checked during plan validation.",
    ),
    (
        "inspect",
        "Inspect a Registration",
        "Open verified registration evidence in the existing Viewer and record a separate anatomical review.",
    ),
)


class GuidedRegistration(QWidget):
    """Compose real registry tasks; preparation and approval stay in RegistrationPanel."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.goal = "align_two"
        self.active = True
        self._refreshing = False
        root = QVBoxLayout(self)
        root.addWidget(label("What would you like to do?", "title"))
        root.addWidget(
            label(
                "Choose a result, select its exact inputs, then validate and approve the scientific plan. Registration completion still needs anatomical review.",
                "caption",
            )
        )
        choices = QGridLayout()
        self.goal_buttons = {}
        for i, (key, title, explanation) in enumerate(GOALS):
            control = button(title, lambda _=False, value=key: self.select_goal(value))
            control.setCheckable(True)
            control.setAccessibleName(title)
            control.setToolTip(explanation)
            choices.addWidget(control, i // 2, i % 2)
            self.goal_buttons[key] = control
        root.addLayout(choices)
        self.explanation = label("", "caption")
        root.addWidget(self.explanation)
        self.inventory_notice = label("", "caption")
        root.addWidget(self.inventory_notice)
        self.advanced_notice = label("", "caption")
        root.addWidget(self.advanced_notice)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._reference_page())
        self.pages.addWidget(self._epi_page())
        self.pages.addWidget(self._align_page())
        self.pages.addWidget(self._move_page())
        self.pages.addWidget(self._inspect_page())
        root.addWidget(self.pages)

        self.direction = label(
            "Select the moving image and reference image. Their roles are never swapped automatically.",
            "caption",
        )
        root.addWidget(self.direction)
        self.output_section = QWidget()
        output = QFormLayout(self.output_section)
        self.output = QLineEdit()
        self.output.setAccessibleName("New registration derivative directory")
        self.output.setPlaceholderText("New immutable derivative directory")
        output_row = QHBoxLayout()
        output_row.addWidget(self.output, 1)
        output_row.addWidget(button("Choose parent…", owner.choose_parent))
        output.addRow("Output directory", output_row)
        root.addWidget(self.output_section)

        root.addWidget(label("Review the plan", "section"))
        self.summary = label(
            "No plan has been validated. Nothing will run until you approve it.", "caption"
        )
        root.addWidget(self.summary)
        self.preview = button("Validate and Preview Plan", owner.prepare, primary=True)
        root.addWidget(self.preview)
        self.commands = QPlainTextEdit()
        self.commands.setReadOnly(True)
        self.commands.setMinimumHeight(120)
        self.commands.setAccessibleName("Exact registration commands and scientific limitations")
        self.command_details = Disclosure(
            "Exact commands and scientific limitations", self.commands
        )
        root.addWidget(self.command_details)
        self.approval = QCheckBox(
            "I reviewed these exact inputs, direction, interpolation and commands"
        )
        root.addWidget(self.approval)
        self.run_button = button("Run Approved Plan", owner.run, primary=True)
        root.addWidget(self.run_button)
        self.result = label("Execution success does not approve anatomical alignment.", "caption")
        root.addWidget(self.result)
        actions = QHBoxLayout()
        self.open_button = button("Inspect Result in Viewer", owner.open_result)
        self.qc_button = button("Technical QC", owner.open_qc)
        self.saved_button = button("Open Saved Atlas QC…", owner.open_saved)
        for action in (self.open_button, self.qc_button, self.saved_button):
            actions.addWidget(action)
        root.addLayout(actions)
        root.addStretch()

        self.output.textChanged.connect(owner.output.setText)
        owner.output.textChanged.connect(self._output_from_advanced)
        self.approval.toggled.connect(owner.approval.setChecked)
        owner.approval.toggled.connect(self._approval_from_advanced)
        self.refresh_inventory()
        self.select_goal(self.goal, initial=True)
        self._output_from_advanced(owner.output.text())
        self._approval_from_advanced(owner.approval.isChecked())
        self.update_actions()

    def _page(self, heading, help_text):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(label(heading, "section"))
        layout.addWidget(label(help_text, "caption"))
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        layout.addLayout(form)
        return page, layout, form

    def _input(self, name):
        combo = QComboBox()
        combo.setAccessibleName(name)
        combo.currentIndexChanged.connect(self._input_changed)
        return combo

    def _reference_page(self):
        page, _, form = self._page(
            "Create a Diffusion Reference",
            "Only an identified preprocessed DWI with verified gradients and recorded correction history can supply low-b measurements. This prepares a reference; it does not register images.",
        )
        self.dwi = self._input("Preprocessed DWI and gradients")
        self.low_b_selection = QComboBox()
        self.low_b_selection.addItem("Choose how to combine low-b measurements…", None)
        self.low_b_selection.addItem("Mean of all selected low-b measurements", "mean_low_b")
        self.low_b_selection.addItem("First measurement that meets the threshold", "first_low_b")
        self.low_b_selection.currentIndexChanged.connect(self._input_changed)
        self.threshold = QSpinBox()
        self.threshold.setRange(0, 200)
        self.threshold.setSuffix(" s/mm²")
        self.threshold.setAccessibleName("Maximum low-b threshold")
        self.threshold.valueChanged.connect(self._input_changed)
        form.addRow("Preprocessed DWI", self.dwi)
        form.addRow("Low-b selection", self.low_b_selection)
        form.addRow("Maximum included b-value", self.threshold)
        return page

    def _epi_page(self):
        page, _, form = self._page(
            "Align Diffusion and T1",
            "The EPI/b0 reference is moving; the T1 is fixed. Brain-extracted T1 and white-matter segmentation must be associated with that exact T1 space. An inverse is a separate reviewed operation.",
        )
        self.epi_moving = self._input("Moving EPI or b0 image")
        self.epi_reference = self._input("Fixed T1 reference image")
        self.t1_brain = self._input("Brain-extracted T1")
        self.wmseg = self._input("White-matter segmentation")
        form.addRow("Moving EPI / diffusion reference", self.epi_moving)
        form.addRow("Reference T1 image", self.epi_reference)
        form.addRow("Brain-extracted T1", self.t1_brain)
        form.addRow("White-matter segmentation", self.wmseg)
        return page

    def _align_page(self):
        page, _, form = self._page(
            "Align Two Images",
            "The moving image is transformed toward the fixed reference. Rigid preserves shape; affine additionally permits scaling and shearing. Neither result is anatomically approved until reviewed.",
        )
        self.moving = self._input("Moving image")
        self.reference = self._input("Fixed reference image")
        self.alignment_method = QComboBox()
        self.alignment_method.addItem("Choose a registration method…", None)
        self.alignment_method.addItem("Rigid · rotation and translation", "rigid")
        self.alignment_method.addItem("Affine · may scale and shear", "affine")
        self.alignment_method.currentIndexChanged.connect(self._input_changed)
        self.cost = QComboBox()
        self.cost.addItem("Choose an image similarity measure…", None)
        for key, title in (
            ("mutualinfo", "Mutual information"),
            ("normmi", "Normalized mutual information"),
            ("corratio", "Correlation ratio"),
            ("normcorr", "Normalized correlation"),
            ("leastsq", "Least squares"),
        ):
            self.cost.addItem(title, key)
        self.cost.currentIndexChanged.connect(self._input_changed)
        form.addRow("Moving image", self.moving)
        form.addRow("Reference image", self.reference)
        form.addRow("Method", self.alignment_method)
        form.addRow("Similarity measure", self.cost)
        return page

    def _move_page(self):
        page, _, form = self._page(
            "Move an Atlas or ROI",
            "Resample an atlas, ROI, scalar image or probability map using a supported imported transform. Validation must prove its source and target spaces; no chain or inverse is guessed.",
        )
        self.derivative = self._input("Atlas, ROI or image to move")
        self.target = self._input("Target reference image")
        self.transform = self._input("Imported source-to-target transform")
        self.transform.setToolTip(
            "The exact source, target, convention and direction are verified during plan preview."
        )
        form.addRow("Atlas, ROI or image", self.derivative)
        form.addRow("Reference image in target space", self.target)
        form.addRow("Source → target transform", self.transform)
        return page

    def _inspect_page(self):
        page, layout, _ = self._page(
            "Inspect an Existing Registration",
            "Open a verified output or a saved registered-atlas QC bundle. Overlay, outlines, checkerboard, reference-only and blink are in the existing Viewer. Processing success is separate from anatomical review.",
        )
        layout.addWidget(button("Inspect Current Verified Result", self.owner.open_result))
        layout.addWidget(button("Open Saved Atlas QC…", self.owner.open_saved))
        return page

    def select_goal(self, key, *, initial=False):
        if key not in self.goal_buttons:
            raise ValueError("Unsupported guided registration goal")
        changed = key != self.goal or not self.active
        self.goal, self.active = key, True
        for value, control in self.goal_buttons.items():
            control.setChecked(value == key)
        self.pages.setCurrentIndex([item[0] for item in GOALS].index(key))
        self.explanation.setText(next(row[2] for row in GOALS if row[0] == key))
        self._explain_inventory()
        self.advanced_notice.clear()
        self._update_direction()
        self._set_execution_visible(key != "inspect")
        if changed and not initial:
            self.owner.task = None
            self.owner.selection.clear()
            self.owner.invalidate()

    def advanced_selected(self):
        self.active = False
        self.advanced_notice.setText(
            "Advanced typed task selected. Its exact bindings and parameters, not the guided draft above, will be prepared."
        )
        self.owner.invalidate()

    def _set_execution_visible(self, visible):
        for widget in (
            self.output_section,
            self.preview,
            self.approval,
            self.run_button,
            self.command_details,
        ):
            widget.setVisible(visible)
        self.direction.setVisible(visible)

    def _input_changed(self, *_):
        if self._refreshing:
            return
        self._update_direction()
        if self.active:
            self.owner.invalidate()

    def _update_direction(self):
        names = {
            "epi_t1": (self.epi_moving, self.epi_reference),
            "align_two": (self.moving, self.reference),
            "move": (self.derivative, self.target),
        }
        if self.goal == "reference":
            self.direction.setText(
                "One DWI → a new low-b reference. No anatomical registration is estimated."
            )
        elif self.goal in names:
            moving, fixed = names[self.goal]
            self.direction.setText(
                f"{moving.currentText()} → {fixed.currentText()} · moving → reference. A transform records this direction; resampling is a separate task unless this plan explicitly includes it."
            )
        else:
            self.direction.setText("Inspect existing evidence; do not run a new registration.")

    def refresh_inventory(self):
        self._refreshing = True
        specs = (
            (self.dwi, {"dwi"}),
            (self.epi_moving, {"anatomy"}),
            (self.epi_reference, {"anatomy"}),
            (self.t1_brain, {"anatomy"}),
            (self.wmseg, {"mask"}),
            (self.moving, {"anatomy"}),
            (self.reference, {"anatomy"}),
            (self.derivative, {"anatomy", "atlas_labels", "roi", "atlas_probability"}),
            (self.target, {"anatomy"}),
            (self.transform, {"spatial_transform"}),
        )
        for combo, kinds in specs:
            old = combo.currentData()
            combo.clear()
            combo.addItem("Choose an imported candidate…", None)
            for artifact in self.owner.owner.artifacts:
                if artifact.kind in kinds and self._candidate(combo, artifact):
                    name = Path(artifact.payload["path"]).name
                    combo.addItem(f"{name} · {artifact.id[:8]}", artifact.id)
            combo.setCurrentIndex(max(0, combo.findData(old)))
        self._refreshing = False
        self._update_direction()
        self._explain_inventory()

    def _explain_inventory(self):
        if self.goal == "reference":
            message = (
                "Import a preprocessed DWI with gradients and recorded correction history to prepare a low-b reference."
                if self.dwi.count() <= 1
                else "Select the recorded DWI acquisition; its gradients determine which volumes meet the threshold."
            )
        elif self.goal == "epi_t1":
            missing = [
                name
                for name, control in (
                    ("a diffusion b0/EPI reference", self.epi_moving),
                    ("a T1 image", self.epi_reference),
                    ("a brain-extracted T1", self.t1_brain),
                    ("white-matter segmentation", self.wmseg),
                )
                if control.count() <= 1
            ]
            message = (
                "Import " + ", ".join(missing) + " before planning."
                if missing
                else "Select the exact EPI, T1 and structural evidence associated with this subject."
            )
        elif self.goal == "align_two":
            message = (
                "Import two identified, compatible images before planning an alignment."
                if self.moving.count() <= 2
                else "Choose moving and fixed images explicitly; the planner checks their identity and geometry."
            )
        elif self.goal == "move":
            missing = [
                name
                for name, control in (
                    ("an atlas, ROI or image", self.derivative),
                    ("a target reference", self.target),
                    ("a verified source-to-target transform", self.transform),
                )
                if control.count() <= 1
            ]
            message = (
                "Import " + ", ".join(missing) + " before planning."
                if missing
                else "Select the exact source, target and transform; matching image dimensions are insufficient."
            )
        else:
            message = "Open a verified current result or a saved atlas QC bundle; no new command is required."
        self.inventory_notice.setText(message)

    def _candidate(self, combo, artifact):
        """Hide clearly ineligible imports; the core still performs exact validation."""
        declarations = artifact.payload.get("declarations", {})
        kind = declarations.get("space_kind")
        contrast = declarations.get("anatomical_modality")
        if combo is self.dwi:
            return (
                declarations.get("processing_state") == "preprocessed"
                and bool(declarations.get("reference_history"))
                and bool(artifact.payload.get("gradients"))
            )
        if combo is self.epi_moving:
            return (
                kind == "DWI_NATIVE"
                and contrast in {"b0", "mean_b0"}
                and bool(declarations.get("reference_history"))
            )
        if combo in (self.epi_reference, self.t1_brain):
            return kind == "T1_NATIVE" and contrast == "T1w"
        if combo is self.wmseg:
            return kind == "T1_NATIVE"
        if combo in (self.moving, self.reference):
            return contrast in {"T1w", "T2w", "b0", "mean_b0", "FA", "MD"} and (
                kind != "DWI_NATIVE" or bool(declarations.get("reference_history"))
            )
        return True

    @staticmethod
    def _required(combo, title):
        value = combo.currentData()
        if value is None:
            raise ValueError(f"Choose {title} before validating the plan")
        return value

    def build_task(self):
        if self.goal == "reference":
            return Task(
                "registration-guided",
                "registration.reference",
                {"dwi": self._required(self.dwi, "a preprocessed DWI")},
                {
                    "selection": self._required(self.low_b_selection, "a low-b selection method"),
                    "b0_threshold": self.threshold.value(),
                },
            )
        if self.goal == "epi_t1":
            return Task(
                "registration-guided",
                "registration.epi_reg",
                {
                    "moving": self._required(self.epi_moving, "the moving EPI/b0 image"),
                    "reference": self._required(self.epi_reference, "the fixed T1 image"),
                    "t1_brain": self._required(self.t1_brain, "a brain-extracted T1"),
                    "wmseg": self._required(self.wmseg, "a white-matter segmentation"),
                },
                {},
            )
        if self.goal == "align_two":
            return Task(
                "registration-guided",
                "registration.flirt",
                {
                    "moving": self._required(self.moving, "the moving image"),
                    "reference": self._required(self.reference, "the reference image"),
                },
                {
                    "model": self._required(self.alignment_method, "rigid or affine registration"),
                    "cost": self._required(self.cost, "a similarity measure"),
                },
            )
        if self.goal == "move":
            source = self._required(self.derivative, "the atlas, ROI or image")
            artifact = next(a for a in self.owner.owner.artifacts if a.id == source)
            suffix = {
                "anatomy": "scalar",
                "atlas_labels": "labels",
                "roi": "roi",
                "atlas_probability": "probability",
            }[artifact.kind]
            return Task(
                "registration-guided",
                "registration.apply." + suffix,
                {
                    "moving": source,
                    "reference": self._required(self.target, "the target reference"),
                    "transform": self._required(self.transform, "a verified transform"),
                },
                {},
            )
        raise ValueError("Choose a processing goal; inspection does not create a command plan")

    def clear_plan(self):
        self.summary.setText("No approved plan. Validate the current inputs before running.")
        self.commands.clear()
        self.update_actions()

    def plan_ready(self, plan):
        payload = plan.payload
        source = payload.get("source", payload.get("dwi", {}))
        target = payload.get("reference", {})
        source_name = Path(source.get("image", source.get("path", ""))).name
        target_name = Path(target.get("image", target.get("path", ""))).name
        task = payload["tasks"][0]
        self.summary.setText(
            f"{task['algorithm']} · {source_name or 'selected DWI'} → {target_name or 'new low-b reference'}\nOutput: {payload['output_directory']}\nApproval digest: {plan.digest}\nTechnical validity is not anatomical approval."
        )
        commands = "\n".join(c["display"] for c in payload["commands"])
        self.commands.setPlainText(commands + "\n\n" + "\n".join(payload["scientific_limits"]))
        self.update_actions()

    def result_ready(self, result, *, verified):
        self.result.setText(
            "Processing completed; technical validation recorded. Anatomical review remains required."
            if verified
            else result.get("status", "Registration did not complete")
        )
        self.update_actions()

    def update_actions(self):
        idle = self.owner.owner.thread is None
        for control in self.goal_buttons.values():
            control.setEnabled(idle)
        self.pages.setEnabled(idle)
        self.output.setEnabled(idle)
        self.preview.setEnabled(idle and self.goal != "inspect")
        self.approval.setEnabled(idle and self.owner.plan is not None)
        self.run_button.setEnabled(
            idle and self.owner.plan is not None and self.owner.approval.isChecked()
        )
        verified_result = (
            self.owner.manifest is not None
            and self.owner.manifest.get("status") == "validated_pending_qc"
            and self.owner.manifest.get("inputs_unchanged") is True
        )
        self.open_button.setEnabled(idle and verified_result)
        self.qc_button.setEnabled(idle and verified_result)
        self.saved_button.setEnabled(idle)

    def _output_from_advanced(self, value):
        if self.output.text() != value:
            self.output.setText(value)

    def _approval_from_advanced(self, value):
        if self.approval.isChecked() != value:
            self.approval.setChecked(value)
        self.update_actions()
