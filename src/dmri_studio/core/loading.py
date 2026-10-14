"""One Qt-free load operation shared by Python, CLI, and the future GUI.

Use ``DatasetLoader().load(DatasetPaths(dwi=..., bval=..., bvec=...))``.
Inputs may live anywhere, including an external HCP folder. The loader records
references and small metadata only: no source is copied, repaired, or written.
Validation stages are independent where possible, so one attempt can describe
both a malformed image and a malformed JSON/gradient file. Payload checks use
only two centered blocks; they are not an exhaustive integrity scan.
"""

from __future__ import annotations

import logging
import platform
import stat
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from uuid import uuid4

import nibabel as nib
import numpy as np

from ._nifti import NiftiInspection, inspect_nifti
from ._sidecar import inspect_sidecar
from .dataset import DatasetPaths, DwiDataset, SourceFingerprint
from .gradients import GradientParseError, GradientTable
from .validation import (
    CHECK_CODES,
    CheckResult,
    ValidationPolicy,
    ValidationReport,
    describe_coverage,
    utc_now,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LoadProgress:
    """Describe a real stage, without an invented duration or ETA."""

    stage: str
    completed: int
    total: int | None
    message: str


@dataclass(frozen=True, slots=True)
class LoadResult:
    """Return either a usable dataset or an evidence-rich unsuccessful report."""

    dataset: DwiDataset | None
    report: ValidationReport


class _LoadCancelled(Exception):
    """Unwind a candidate load at a cooperative cancellation checkpoint."""


def _versions() -> dict[str, str]:
    """Record the actual runtime rather than assuming environment pins were used."""
    try:
        package = version("dmri-studio")
    except PackageNotFoundError:
        package = "uninstalled-source"
    return {
        "dmri_studio": package,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "nibabel": nib.__version__,
    }


def _sibling_deviation(dwi: Path | None) -> CheckResult:
    """Detect exact sibling names through stat only; never open either file."""
    if dwi is None:
        return CheckResult(
            "F05",
            "info",
            "Gradient deviation file presence could not be determined: DWI path unavailable.",
        )
    found: list[str] = []
    try:
        for name in ("grad_dev.nii", "grad_dev.nii.gz"):
            candidate = dwi.parent / name
            try:
                mode = candidate.stat().st_mode
            except FileNotFoundError:
                continue
            if stat.S_ISREG(mode):
                found.append(str(candidate))
    except OSError as exc:
        return CheckResult(
            "F05",
            "info",
            f"Gradient deviation file presence could not be determined: {exc}",
            source=str(dwi.parent),
        )
    return CheckResult(
        "F05",
        "info",
        "Gradient nonlinearity deviation file present; not used by this loader."
        if found
        else "No sibling gradient nonlinearity deviation file found.",
        source=str(dwi.parent),
        observed=found,
    )


class DatasetLoader:
    """Validate and load one diffusion dataset without importing GUI code."""

    def load(
        self,
        paths: DatasetPaths,
        *,
        policy: ValidationPolicy | None = None,
        progress: Callable[[LoadProgress], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> LoadResult:
        """Run the documented checklist and return expected errors as findings.

        Cancellation is cooperative between stages and reads. A blocked OS read
        cannot be interrupted safely; its result is discarded at the following
        checkpoint. A candidate dataset is created only after validation finishes.
        """
        active_policy = policy if policy is not None else ValidationPolicy()
        checks: list[CheckResult] = []
        fingerprints: dict[str, SourceFingerprint] = {}
        originals: dict[str, Path] = {}
        resolved: dict[str, Path] = {}
        usable: set[str] = set()
        metadata: dict[str, Any] = {}
        sampled_regions: list[dict[str, Any]] = []
        nifti = NiftiInspection()
        gradients: GradientTable | None = None
        sidecar: dict[str, Any] = {}
        started_at = utc_now()
        run_id = str(uuid4())
        complete, was_cancelled = True, False

        def checkpoint() -> None:
            """Observe a thread-safe caller cancellation predicate."""
            if cancelled is not None and cancelled():
                raise _LoadCancelled

        def stage(name: str, completed: int, message: str) -> None:
            """Publish stage progress and observe cancellation on both sides."""
            checkpoint()
            LOGGER.info(message)
            if progress is not None:
                progress(LoadProgress(name, completed, 6, message))
            checkpoint()

        try:
            stage("files", 0, "Checking selected input files.")
            identities: dict[tuple[int, int], str] = {}
            for role in ("dwi", "bval", "bvec", "json"):
                checkpoint()
                selected = getattr(paths, role, None)
                if selected is None or not str(selected).strip():
                    checks.append(
                        CheckResult(
                            "F01",
                            "info" if role == "json" else "fail",
                            "Optional JSON sidecar not selected."
                            if role == "json"
                            else f"Required {role} path was not supplied.",
                            remediation=None if role == "json" else f"Select the {role} file.",
                        )
                    )
                    continue
                checks.append(
                    CheckResult("F01", "pass", f"{role} path supplied.", source=str(selected))
                )
                try:
                    original = Path(selected).expanduser().absolute()
                    originals[role] = original
                    initial_fingerprint = SourceFingerprint.capture(original)
                    canonical = Path(initial_fingerprint.resolved_path)
                    resolved[role] = canonical
                    details = canonical.stat()
                    if not stat.S_ISREG(details.st_mode):
                        raise ValueError("selected path is not a regular file")
                    if details.st_size == 0:
                        raise ValueError("selected file is empty")
                    # The operation itself establishes readability, unlike an
                    # access-bit test that can disagree with ACLs or mounts.
                    with canonical.open("rb") as stream:
                        if not stream.read(1):
                            raise ValueError("selected file became empty")
                    fingerprints[role] = initial_fingerprint
                    usable.add(role)
                    checks.append(
                        CheckResult(
                            "F02",
                            "pass",
                            f"{role} is a readable nonempty regular file.",
                            source=str(original),
                        )
                    )
                    identity = (details.st_dev, details.st_ino)
                    if identity in identities:
                        checks.append(
                            CheckResult(
                                "F03",
                                "fail",
                                f"{role} and {identities[identity]} refer to the same file.",
                                source=str(original),
                                remediation="Select distinct files for each role.",
                            )
                        )
                    else:
                        identities[identity] = role
                except (OSError, ValueError, RuntimeError) as exc:
                    checks.append(
                        CheckResult(
                            "F02",
                            "fail",
                            f"Cannot use {role} input: {exc}",
                            source=str(selected),
                            remediation="Select an existing readable, nonempty regular file.",
                        )
                    )
            if not any(c.code == "F03" for c in checks):
                checks.append(
                    CheckResult(
                        "F03",
                        "pass" if len(usable) >= 2 else "skipped",
                        "Available inputs have distinct file identities."
                        if len(usable) >= 2
                        else "Insufficient accessible files to compare identities.",
                    )
                )
            checks.append(_sibling_deviation(resolved.get("dwi", originals.get("dwi"))))

            stage(
                "image_metadata",
                1,
                "Checking raw NIfTI header, geometry, scaling and payload bounds.",
            )
            if "dwi" in usable:
                nifti = inspect_nifti(resolved["dwi"], checkpoint=checkpoint)
                checks.extend(nifti.checks)
                metadata.update(nifti.metadata)

            stage("gradients", 2, "Parsing and validating the complete gradient table.")
            if "bval" in usable and "bvec" in usable:
                try:
                    gradients = GradientTable.from_files(
                        resolved["bval"], resolved["bvec"], layout=active_policy.bvec_layout
                    )
                    checks.extend(
                        gradients.validate(
                            expected_volumes=nifti.shape[3] if nifti.shape is not None else None,
                            policy=active_policy,
                        )
                    )
                    if not any(c.status == "fail" and c.code.startswith("G") for c in checks):
                        metadata.update(
                            n_volumes=gradients.n_volumes,
                            b0_count=int(gradients.b0_mask(b0_max=active_policy.b0_max).sum()),
                            weighted_count=int(
                                (~gradients.b0_mask(b0_max=active_policy.b0_max)).sum()
                            ),
                            gradient_convention=gradients.convention,
                            bvec_layout=gradients.source_layout,
                            shells=[
                                shell.to_dict()
                                for shell in gradients.shells(
                                    b0_max=active_policy.b0_max, max_span=active_policy.shell_span
                                )
                            ],
                        )
                except GradientParseError as exc:
                    checks.extend(exc.checks)
                except (OSError, UnicodeError) as exc:
                    checks.append(
                        CheckResult(
                            "G01",
                            "fail",
                            f"Gradient file could not be read: {exc}",
                            remediation="Restore readable gradient files and reload.",
                        )
                    )

            stage("json", 3, "Checking optional JSON metadata.")
            if getattr(paths, "json", None) is None or not str(getattr(paths, "json", "")).strip():
                sidecar, findings = inspect_sidecar(None)
                checks.extend(findings)
            elif "json" in usable:
                sidecar, findings = inspect_sidecar(resolved["json"])
                checks.extend(findings)

            stage("samples", 4, "Reading centered blocks in the first and last volumes.")
            if nifti.image is not None and nifti.shape is not None:
                self._samples(
                    nifti.image,
                    nifti.shape,
                    active_policy,
                    checks,
                    sampled_regions,
                    metadata,
                    checkpoint,
                )

            stage("summary", 5, "Rechecking source identities and assembling the report.")
            for role, fingerprint in fingerprints.items():
                checkpoint()
                try:
                    unchanged = fingerprint.unchanged(originals[role])
                except (OSError, ValueError, RuntimeError):
                    unchanged = False
                checks.append(
                    CheckResult(
                        "C01",
                        "pass" if unchanged else "fail",
                        f"{role} source identity, size and modification time are unchanged."
                        if unchanged
                        else f"{role} changed or disappeared during loading; reload required.",
                        source=str(originals[role]),
                        remediation=None
                        if unchanged
                        else "Reload the dataset after the source files stop changing.",
                    )
                )
            checkpoint()
        except _LoadCancelled:
            complete, was_cancelled = False, True
            checks.append(
                CheckResult(
                    "C02",
                    "info",
                    "Loading cancelled; candidate discarded and report is incomplete.",
                )
            )
        except Exception as exc:
            # Expected malformed files are handled near their source. A bug or
            # callback failure must never produce a misleading successful report.
            complete = False
            LOGGER.exception("Unexpected dataset loading failure")
            checks.append(
                CheckResult(
                    "C02",
                    "fail",
                    f"Unexpected technical failure ({type(exc).__name__}): {exc}",
                    remediation="Inspect the technical log and report this issue; no dataset was activated.",
                )
            )

        # Every code has an explicit result, even if a prerequisite prevented it
        # from running. Only cancellation/technical interruption is incomplete;
        # an evaluated bad-input attempt is a complete failed report.
        present = {check.code for check in checks}
        for code in CHECK_CODES:
            if code not in present and code not in ("C02", "C03"):
                checks.append(
                    CheckResult(
                        code,
                        "skipped",
                        "Not run because loading was cancelled/interrupted."
                        if not complete
                        else "Not run because a required input or earlier dependent check was unavailable.",
                    )
                )
        if "C02" not in present:
            failed = any(check.status == "fail" for check in checks)
            candidate_ready = (
                nifti.image is not None
                and gradients is not None
                and nifti.affine is not None
                and nifti.voxel_sizes_native is not None
            )
            checks.append(
                CheckResult(
                    "C02",
                    "fail" if failed or not candidate_ready else "pass",
                    "Candidate cannot be loaded; see failed checks."
                    if failed
                    else "Required load components are unavailable."
                    if not candidate_ready
                    else "Required load stages completed; metadata and warnings retained.",
                )
            )
        checks.append(
            CheckResult(
                "C03",
                "info",
                describe_coverage(checks, complete=complete, cancelled=was_cancelled),
                observed={
                    "unperformed": [
                        "exhaustive voxel finiteness",
                        "complete gzip integrity/CRC",
                        "anatomical gradient correctness",
                        "motion/artifact QC",
                        "processing readiness",
                    ]
                },
            )
        )
        metadata["sampled_voxel_count"] = sum(
            region.get("voxel_count", 0) for region in sampled_regions
        )
        report = ValidationReport(
            tuple(checks),
            policy=active_policy,
            complete=complete,
            cancelled=was_cancelled,
            run_id=run_id,
            started_at=started_at,
            finished_at=utc_now(),
            sources={role: value.to_dict() for role, value in fingerprints.items()},
            metadata=metadata,
            sampled_regions=tuple(sampled_regions),
            software_versions=_versions(),
        )
        dataset = None
        if report.can_load():
            dataset = DwiDataset(
                id=run_id,
                paths=paths,
                image=nifti.image,
                gradients=gradients,
                validation=report,
                voxel_sizes_native=nifti.voxel_sizes_native,
                spatial_units=nifti.spatial_units,
                affine=nifti.affine,
                affine_source=nifti.affine_source,
                axis_codes=nifti.axis_codes,
                fingerprints=fingerprints,
                metadata=metadata,
                sidecar=sidecar,
            )
        # keep_file_open=False avoids retained handles on rejected candidates;
        # no array cache or persistent memmap was created by metadata loading.
        return LoadResult(dataset, report)

    @staticmethod
    def _samples(
        image: Any,
        shape: tuple[int, ...],
        policy: ValidationPolicy,
        checks: list[CheckResult],
        sampled_regions: list[dict[str, Any]],
        metadata: dict[str, Any],
        checkpoint: Callable[[], None],
    ) -> None:
        """Read only the documented first/last centered blocks and summarize."""
        slices: list[slice] = []
        ranges: list[list[int]] = []
        for length in shape[:3]:
            edge = min(policy.sample_edge, length)
            start = (length - edge) // 2
            slices.append(slice(start, start + edge))
            ranges.append([start, start + edge])
        volumes = (0,) if shape[3] == 1 else (0, shape[3] - 1)
        any_negative, all_zero, read_count = False, True, 0
        sample_min, sample_max = np.inf, -np.inf
        for volume in volumes:
            checkpoint()
            region = {
                "volume_index": volume,
                "ranges": ranges,
                "dtype": "float64",
                "voxel_count": int(np.prod([s.stop - s.start for s in slices])),
            }
            try:
                # Index first, cast second. np.asarray(image.dataobj) would read
                # the entire 4D payload, defeating the bounded-load contract.
                with np.errstate(over="ignore", invalid="ignore"):
                    values = np.array(
                        image.dataobj[tuple(slices) + (volume,)], dtype=np.float64, copy=True
                    )
                checkpoint()
                sampled_regions.append(region)
                read_count += 1
                checks.append(
                    CheckResult(
                        "V01",
                        "pass",
                        f"Read centered block from zero-based volume index {volume}.",
                        volume_indices=(volume,),
                        observed=region,
                    )
                )
                finite = bool(np.isfinite(values).all())
                checks.append(
                    CheckResult(
                        "V02",
                        "pass" if finite else "fail",
                        "All sampled scaled values are finite."
                        if finite
                        else "Sampled scaled values contain NaN or infinity.",
                        volume_indices=(volume,),
                    )
                )
                finite_values = values[np.isfinite(values)]
                if finite_values.size:
                    sample_min = min(sample_min, float(finite_values.min()))
                    sample_max = max(sample_max, float(finite_values.max()))
                any_negative |= bool(np.any(values < 0))
                all_zero &= bool(np.all(values == 0))
            except (
                OSError,
                EOFError,
                ValueError,
                OverflowError,
                MemoryError,
                nib.filebasedimages.ImageFileError,
            ) as exc:
                checks.append(
                    CheckResult(
                        "V01",
                        "fail",
                        f"Cannot read sample from zero-based volume index {volume}: {exc}",
                        volume_indices=(volume,),
                        remediation="Check file integrity and source availability, then reload.",
                    )
                )
        if np.isfinite(sample_min):
            metadata["sample_minimum"], metadata["sample_maximum"] = sample_min, sample_max
        if read_count:
            checks.append(
                CheckResult(
                    "V03",
                    "info" if any_negative else "pass",
                    "Negative sampled intensities observed; preprocessing/interpolation can produce these. No clipping applied."
                    if any_negative
                    else "No negative values observed in sampled blocks.",
                )
            )
        if read_count == len(volumes):
            checks.append(
                CheckResult(
                    "V03",
                    "warn" if all_zero else "pass",
                    "All sampled blocks contain only zero; inspect the image. Unexamined voxels are unknown."
                    if all_zero
                    else "Sampled blocks are not all zero.",
                )
            )
