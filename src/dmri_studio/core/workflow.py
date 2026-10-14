"""Evidence-aware planning for the first MRtrix workflow, without execution.

Use ``build_plan(dataset, history, operations)`` after the existing read-only
loader. History is explicit input, never inferred from filenames or a successful
load. This module does not run tools, interpolate images or change gradients.
The method constraints are linked to upstream documentation in each decision.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import nibabel as nib

from .acquisition import gibbs_plane
from .dataset import DwiDataset
from .validation import freeze_json, json_compatible

STAGES = {
    "susceptibility": "Susceptibility correction",
    "eddy": "Eddy-current correction",
    "motion": "Motion correction",
    "gradient_nonlinearity": "Gradient nonlinearity correction",
    "denoise": "MP-PCA denoising",
    "gibbs": "Gibbs ringing removal",
    "interpolation": "Interpolation or smoothing",
    "bias": "Bias-field correction (N4 or other)",
}
OPERATIONS = {
    "convert": "Convert to MRtrix and embed gradients",
    "denoise": "MP-PCA denoising",
    "gibbs": "Gibbs ringing removal",
    "mask": "Generate brain mask",
    "correction": "TOPUP and EDDY correction with diagnostic gate",
    "bias": "N4 bias correction after verified EDDY",
    "anatomy": "Prepare anatomy and white-matter candidate for review",
    "alignment": "Align mean low-b to reviewed T1 (separate 3D QC)",
    "combine": "Optional verified AP/PA combined derivative",
}
DOCS = "https://userdocs.mrtrix.org/en/latest/reference/commands/"
SOURCES = {
    "convert": DOCS + "mrconvert.html",
    "denoise": DOCS + "dwidenoise.html",
    "gibbs": DOCS + "mrdegibbs.html",
    "mask": DOCS + "dwi2mask.html",
    "bias": DOCS + "dwibiascorrect.html",
    "correction": "https://fsl.fmrib.ox.ac.uk/fsl/docs/diffusion/eddy/users_guide/index.html",
    "anatomy": "https://fsl.fmrib.ox.ac.uk/fsl/docs/structural/fast.html",
    "alignment": "https://fsl.fmrib.ox.ac.uk/fsl/docs/registration/flirt/bbr.html",
    "combine": "https://github.com/Washington-University/HCPpipelines/blob/faec8f3ec23697018a6cdc0824569d4082e6201e/global/scripts/average_bvecs.py",
}


@dataclass(frozen=True, slots=True)
class HistoryClaim:
    """One dated claim about a completed operation, with its evidence retained.

    ``completed=False`` means explicitly reported not done, not simply absent from
    a log. ``verified`` is reserved for evidence verified by the caller; parsing
    does not authenticate a log. The GUI only creates user-declared claims.
    """

    stage: str
    completed: bool | None = None
    evidence: str = "unknown"
    source: str = ""
    confidence: str = "unknown"
    recorded_on: str = ""

    def __post_init__(self) -> None:
        """Reject contradictory labels and untraceable conclusive claims."""
        if self.stage not in STAGES:
            raise ValueError(f"Unknown history stage: {self.stage}")
        if self.completed is not None and type(self.completed) is not bool:
            raise ValueError("History completed must be true, false or null.")
        if self.evidence not in {"verified", "user_declared", "inferred", "unknown", "conflict"}:
            raise ValueError(f"Unknown evidence state: {self.evidence}")
        if self.confidence not in {"unknown", "low", "medium", "high"}:
            raise ValueError(f"Unknown confidence: {self.confidence}")
        if not isinstance(self.source, str) or not isinstance(self.recorded_on, str):
            raise ValueError("History source and date must be text.")
        if self.recorded_on:
            date.fromisoformat(self.recorded_on)
        if self.evidence in {"unknown", "conflict"}:
            if self.completed is not None:
                raise ValueError("Unknown or conflicting history cannot assert completion.")
        elif self.completed is None or not self.source.strip() or not self.recorded_on:
            raise ValueError("Conclusive history requires a value, source and recording date.")
        if self.evidence == "conflict" and not self.source.strip():
            raise ValueError("Conflicting history requires an explanation or evidence source.")


@dataclass(frozen=True, slots=True)
class ProcessingHistory:
    """Immutable history reviewed for this import; confirmation is not verification."""

    claims: tuple[HistoryClaim, ...] = ()
    confirmed: bool = False

    def __post_init__(self) -> None:
        """Freeze caller containers and reject non-claim input."""
        object.__setattr__(self, "claims", tuple(self.claims))
        if type(self.confirmed) is not bool or any(
            not isinstance(claim, HistoryClaim) for claim in self.claims
        ):
            raise ValueError("History requires claims and a boolean confirmation.")

    def resolve(self, stage: str) -> str:
        """Retain conflicts rather than choose whichever source is most convenient."""
        relevant = [claim for claim in self.claims if claim.stage == stage]
        values = {claim.completed for claim in relevant if claim.completed is not None}
        if any(claim.evidence == "conflict" for claim in relevant) or len(values) > 1:
            return "conflict"
        if not values:
            return "unknown"
        return "done" if True in values else "not_done"

    def to_dict(self) -> dict[str, Any]:
        """Serialize the original evidence, including contradictory claims."""
        return {
            "schema_version": 1,
            "confirmed": self.confirmed,
            "claims": [asdict(c) for c in self.claims],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ProcessingHistory:
        """Read a strict history document; unsupported fields are not silently ignored."""
        if not isinstance(payload, dict) or set(payload) != {
            "schema_version",
            "confirmed",
            "claims",
        }:
            raise ValueError("History must contain schema_version, confirmed and claims only.")
        if type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
            raise ValueError("Unsupported history schema version.")
        if not isinstance(payload["claims"], list):
            raise ValueError("History claims must be a list.")
        try:
            history = cls(
                tuple(HistoryClaim(**claim) for claim in payload["claims"]), payload["confirmed"]
            )
        except TypeError as exc:
            raise ValueError(f"Invalid history claim: {exc}") from exc
        if any(claim.evidence == "verified" for claim in history.claims):
            raise ValueError(
                "A history JSON label cannot verify upstream evidence. This phase accepts declared/inferred evidence; verified claims require a trusted Python caller that has checked their source."
            )
        return history


@dataclass(frozen=True, slots=True)
class OperationDecision:
    """Scientific eligibility and its exact supporting history, before tool execution."""

    operation: str
    status: str
    reasons: tuple[str, ...]
    history_stages: tuple[str, ...]
    required_inputs: tuple[str, ...]
    optional_inputs: tuple[str, ...]
    expected_outputs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        """Make a detached, readable method contract for GUI/CLI parity."""
        return {
            **asdict(self),
            "title": OPERATIONS[self.operation],
            "source": SOURCES[self.operation],
        }


def assess_operations(
    dataset: DwiDataset, history: ProcessingHistory
) -> tuple[OperationDecision, ...]:
    """Assess the bounded reference capabilities without making a pipeline preset.

    Unknown history prevents assuming a pre-interpolation noise model. An eligible
    mask does not depend on denoising/Gibbs. Tool installation and full integrity/QC
    are execution-phase checks, deliberately not certified by this planner.
    """
    dataset.assert_current()
    decisions = []
    requirements = ("Validated 4D NIfTI DWI", "Matching FSL b-values", "Matching FSL b-vectors")
    outputs = {
        "convert": ("dwi.mif",),
        "denoise": ("dwi_denoised.mif", "noise.mif"),
        "gibbs": ("dwi_degibbs.mif",),
        "mask": ("mask.mif",),
        "bias": ("bias-corrected.mif", "bias-field.mif"),
        "anatomy": ("t1w-brain.nii.gz", "wm-mask.nii.gz", "qc-catalog.json"),
        "combine": ("combined.nii.gz", "combined.bval", "combined.bvec", "volume-mapping.json"),
        "alignment": ("dwi-to-t1-rigid.mat", "dwi-to-t1-bbr.mat", "mean-b0-t1-bbr.nii.gz"),
        "correction": (
            "correction/eddy.nii.gz",
            "correction/corrected.bval",
            "correction/corrected.bvec",
            "correction-gate.json",
        ),
    }
    for operation in OPERATIONS:
        blocked, uncertain, information = [], [], []
        stages: tuple[str, ...] = ()
        if operation == "denoise":
            information.append(
                "MP-PCA estimates noise and reduces redundant noise components before Gibbs correction or interpolation; inspect residuals for anatomical signal leakage."
            )
            stages = (
                "interpolation",
                "denoise",
                "gibbs",
                "motion",
                "eddy",
                "susceptibility",
                "gradient_nonlinearity",
            )
            for stage in stages:
                state = history.resolve(stage)
                if state == "done" and stage in {"interpolation", "denoise", "gibbs"}:
                    blocked.append(
                        f"{STAGES[stage]} was reported completed. MP-PCA must precede interpolation, "
                        "smoothing and operations that alter its noise model; do not repeat it."
                    )
                elif state == "done":
                    uncertain.append(
                        f"{STAGES[stage]} was reported completed; review whether it involved interpolation, smoothing or altered the noise model."
                    )
                elif state in {"unknown", "conflict"}:
                    uncertain.append(
                        f"{STAGES[stage]} history is {state}; establish its order relative to MP-PCA."
                    )
        elif operation == "gibbs":
            stages = (
                "interpolation",
                "motion",
                "eddy",
                "susceptibility",
                "gradient_nonlinearity",
                "gibbs",
                "denoise",
            )
            for stage in stages:
                state = history.resolve(stage)
                if stage == "denoise":
                    if state == "done":
                        information.append(
                            "Denoising was reported completed; do not add it again before Gibbs removal."
                        )
                    continue
                if state == "done" and stage in {"interpolation", "motion", "eddy", "gibbs"}:
                    blocked.append(
                        f"{STAGES[stage]} was reported completed. Gibbs removal must precede interpolation and motion correction and must not be repeated."
                    )
                elif state == "done":
                    uncertain.append(
                        f"{STAGES[stage]} was reported completed; establish whether it interpolated the image before choosing Gibbs removal."
                    )
                elif state in {"unknown", "conflict"}:
                    uncertain.append(
                        f"{STAGES[stage]} history is {state}; confirm that Gibbs removal remains eligible."
                    )
            # Slice axes still need evidence before execution. Fourier coverage
            # and reconstruction filtering qualify expected performance; they
            # are not mandatory inputs or contraindications to mrdegibbs.
            try:
                plane = gibbs_plane(
                    nib.load(dataset.paths.dwi), json_compatible(dataset.sidecar) or {}
                )
                dataset.assert_current()
                information.append(
                    f"Verified slice axis {plane['slice_axis']} from {plane['slice_axis_source']}; "
                    f"native in-plane Gibbs axes {plane['axes']}. Partial Fourier: "
                    f"{plane['partial_fourier_original'] if plane['partial_fourier_original'] is not None else 'undocumented'}."
                )
            except ValueError as exc:
                uncertain.append(str(exc))
            information.append(
                "Partial Fourier or unknown scanner filtering does not by itself block MRtrix mrdegibbs. Record these limitations and inspect residual ringing and unwanted signal changes."
            )
        elif operation == "correction":
            stages = ("susceptibility", "eddy", "motion", "bias")
            for stage in stages:
                state = history.resolve(stage)
                if state == "done":
                    blocked.append(
                        f"{STAGES[stage]} is already completed; do not repeat correction on this input."
                    )
                elif state != "not_done":
                    uncertain.append(
                        f"Establish {STAGES[stage]} history before preparing correction."
                    )
            if not dataset.gradients.b0_mask(b0_max=dataset.validation.policy.b0_max).any():
                blocked.append("TOPUP requires documented low-b measurements.")
            information.append(
                "Requires associated magnitude reverse-PE b0 images, inherited acquisition metadata and confirmed native-axis geometry. Uses local FSL CPU TOPUP/EDDY, rotated gradients and a diagnostic gate before N4/alignment. Run any selected MP-PCA/Gibbs first."
            )
        elif operation == "bias":
            stages = ("bias", "eddy", "motion")
            if history.resolve("bias") == "done":
                blocked.append(
                    "Bias correction was already completed; select the original pre-N4 EDDY input instead."
                )
            elif history.resolve("bias") != "not_done":
                uncertain.append("Establish that this specific input has not been bias-corrected.")
            for stage in ("eddy", "motion"):
                if history.resolve(stage) != "done":
                    uncertain.append(
                        f"{STAGES[stage]} must be completed and its output validated before this N4 adapter."
                    )
            if not dataset.gradients.b0_mask(b0_max=dataset.validation.policy.b0_max).any():
                blocked.append("N4 requires documented low-b volumes for its mean fitting image.")
            information.append(
                "Requires an explicit binary fitting mask, validated EDDY producer record and passing correction gate bound to this pre-N4 image. The saved field divides every volume once; inspect field and tissue contrast."
            )
        elif operation == "combine":
            stages = ("eddy", "motion", "bias")
            for stage in stages:
                if history.resolve(stage) != "done":
                    uncertain.append(
                        f"Requires completed {STAGES[stage]} and verified native final-image producer."
                    )
            information.append(
                "Optional: retaining all corrected measurements is the default. A separate combined derivative requires documented original matched AP/PA measurements, exact volume mapping and the pinned HCP recipe. PA b0 calibration is not a reverse diffusion series. The parent is always preserved; combined encoding and replacement fidelity require review."
            )
        elif operation in {"anatomy", "alignment"}:
            if operation == "alignment":
                stages = ("eddy", "motion", "bias")
                for stage in stages:
                    if history.resolve(stage) != "done":
                        uncertain.append(
                            f"Establish completed {STAGES[stage]} and validated final-image producer before alignment."
                        )
                information.append(
                    "Requires the validated final N4 producer and accepted anatomy QC record. Six-degree rigid/BBR registers only the 3D low-b mean; native 4D DWI and gradients remain unchanged."
                )
            else:
                information.append(
                    "Requires explicit anatomical history and subject association. Prepared T1 or MP2RAGE UNI/INV2 use FAST -N. Confirmed unprocessed T1 uses BET and standard FAST joint tissue/bias estimation. Unknown history is not evidence of absent correction. Review anatomy before registration."
                )
        elif operation == "mask":
            b0_count = int(dataset.gradients.b0_mask(b0_max=dataset.validation.policy.b0_max).sum())
            if not b0_count or b0_count == dataset.n_volumes:
                blocked.append(
                    "MRtrix dwi2mask requires both b0-like and diffusion-weighted measurements; supply a suitable DWI or choose another masking method."
                )
            information.append(
                "Masking can run on the accepted DWI directly; denoising and Gibbs removal are optional independent selections."
            )
        else:
            information.append(
                "Preserve physical geometry and signal; embed gradients using MRtrix convention handling. Conversion is not resampling."
            )
        status = "blocked" if blocked else "uncertain" if uncertain else "eligible"
        decisions.append(
            OperationDecision(
                operation,
                status,
                tuple(blocked + uncertain + information),
                stages,
                requirements
                + (
                    (
                        "Explicit binary fitting mask",
                        "Validated EDDY producer JSON",
                        "Passing correction gate JSON bound to the loaded image",
                    )
                    if operation == "bias"
                    else (
                        "Associated magnitude reverse-phase b0 calibration images",
                        "BIDS acquisition metadata with PE direction/readout time and exact IntendedFor",
                        "Native producer record when loading a denoised/Gibbs derivative",
                    )
                    if operation == "correction"
                    else (
                        "Associated anatomy and explicit preparation history",
                        "Validated producer/review records for alignment",
                    )
                    if operation in {"anatomy", "alignment"}
                    else ()
                ),
                ("Acquisition JSON (retained when supplied)",),
                outputs[operation],
            )
        )
    return tuple(decisions)


@dataclass(frozen=True, slots=True)
class WorkflowPlan:
    """A reviewable proposal, explicitly distinct from an approved execution run."""

    payload: dict[str, Any]

    def __post_init__(self) -> None:
        """Freeze nested evidence as well as the outer dataclass."""
        object.__setattr__(self, "payload", freeze_json(self.payload))

    def to_dict(self) -> dict[str, Any]:
        """Return a detached snapshot so report clients cannot mutate the plan."""
        return json.loads(self.to_json())

    def to_json(self, *, indent: int = 2) -> str:
        """Emit strict JSON with no image array or GUI objects."""
        return json.dumps(
            json_compatible(self.payload), indent=indent, ensure_ascii=False, allow_nan=False
        )


def build_plan(
    dataset: DwiDataset, history: ProcessingHistory, operations: tuple[str, ...]
) -> WorkflowPlan:
    """Select supported operations and insert conversion as their shared dependency."""
    operations = tuple(operations)
    if len(operations) != len(set(operations)) or any(op not in OPERATIONS for op in operations):
        raise ValueError("Select each supported operation at most once.")
    decisions = assess_operations(dataset, history)
    # Anatomical operations never convert or resample the loaded 4D DWI.
    selected = (
        set(operations)
        if {"anatomy", "alignment", "combine"} & set(operations)
        else {"convert", *operations}
        if operations
        else set()
    )
    ordered = [d for d in decisions if d.operation in selected]
    reasons = []
    if not operations:
        reasons.append("Select at least one operation.")
    if not history.confirmed:
        reasons.append("Review and confirm processing history for this imported dataset.")
    statuses = {d.status for d in ordered}
    for separate in ("anatomy", "alignment", "combine"):
        if separate in selected and selected - {separate}:
            statuses.add("blocked")
            reasons.append(
                "Anatomical preparation and reviewed registration run as separate operations; preserve preceding outputs and review their evidence first."
            )
    if "correction" in selected and selected - {"convert", "correction"}:
        statuses.add("blocked")
        reasons.append(
            "Finish selected native operations first, then load their output and select correction alone with its native producer record. Correction generates its own evaluation mask."
        )
    if "bias" in selected and selected - {"convert", "bias"}:
        statuses.add("blocked")
        reasons.append(
            "N4 uses an explicit saved fitting mask in a separate post-EDDY run. Finish the preceding operations, then select N4 alone."
        )
    status = (
        "blocked"
        if "blocked" in statuses
        else "uncertain"
        if reasons or "uncertain" in statuses
        else "eligible"
    )
    predecessor = None
    nodes = []
    for decision in ordered:
        nodes.append({**decision.to_dict(), "depends_on": [predecessor] if predecessor else []})
        predecessor = decision.operation
    return WorkflowPlan(
        {
            "schema_version": 1,
            "kind": "workflow_proposal",
            "created_at": datetime.now(UTC).isoformat(),
            "status": status,
            "reasons": reasons,
            "execution_available": False,
            "approved_for_execution": False,
            "execution_limit": "Proposal only. Prepare an execution plan to check tools, versions, source hashes, output destination and parameters before running supported operations.",
            "inspection_coverage": dataset.validation.coverage_message(),
            "inputs": {role: str(path) for role, path in dataset.paths.supplied().items()},
            "input_fingerprints": {role: fp.to_dict() for role, fp in dataset.fingerprints.items()},
            "fingerprint_limit": "File identity, size and modification time; not content hashes.",
            "geometry": {
                "shape": list(dataset.shape),
                "affine": dataset.affine.tolist(),
                "units": dataset.spatial_units,
                "space": "Not established from form codes or filenames",
            },
            "history": history.to_dict(),
            "requested_operations": list(operations),
            "nodes": nodes,
            "integration_route": "MRtrix3 CLI adapters; no algorithm replication",
        }
    )


def export_plan(
    plan: WorkflowPlan, destination: Path, *, extra_sources: tuple[Path, ...] = ()
) -> Path:
    """Publish a proposal atomically to a new file, refusing source aliases.

    The source directory may be chosen explicitly for a new report, but no existing
    source/report is replaced. No imaging derivative or run directory is created.
    """
    destination = destination.expanduser().absolute()
    sources = [Path(p) for p in plan.payload["inputs"].values()] + list(extra_sources)
    for source in sources:
        if destination.resolve() == source.expanduser().resolve():
            raise ValueError("Plan destination must not overwrite an input file.")
        if destination.exists() and source.exists() and destination.samefile(source):
            raise ValueError("Plan destination must not overwrite an input alias.")
    if destination.exists() or destination.is_symlink():
        raise ValueError("Plan destination already exists; choose a new file.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(plan.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination
