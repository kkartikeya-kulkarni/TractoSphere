"""MRtrix 3.0.5 DTI adapter over the shared scientific execution engine.

Python: prepare_dti(artifacts, tasks, NEW_directory), review plan.to_json(), then
execute_plan(None, plan, approval=plan.digest). No preprocessing is performed.
Selected volumes retain original order. FSL image-axis directions are converted
by MRtrix's documented import convention; no empirical gradient flip is allowed.
"""

from __future__ import annotations

import io
import shlex
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

import nibabel as nib
import numpy as np

from .._mif_checks import produced_mif, rearrange, voxel_mapping
from ..execution import (
    ExecutionPlan,
    MRtrixStep,
    _environment_contract,
    _publish_json,
    detect_mrtrix,
    execute_artifact_task,
    hash_file,
)
from ..gradients import mrtrix_world_scheme
from ..process import check_cancel
from ..storage import require_local_processing
from .contracts import Artifact, ImportRequest
from .imports import _scan, import_artifact
from .planning import OutputRef, Task, _order, build_workflow
from .tensor_qc import eigenvalue_findings

METRICS = {
    "FA": "-fa",
    "MD": "-adc",
    "AD": "-ad",
    "RD": "-rd",
    "V1": "-vector",
    "CL": "-cl",
    "CP": "-cp",
    "CS": "-cs",
    "EIGENVALUES": "-value",
}
TENSOR_DECLARATIONS = dict(
    tensor_layout="mrtrix_D11_D22_D33_D12_D13_D23", tensor_frame="world_ras", tensor_units="mm2/s"
)
LIMITS = [
    "Execution, numerical verification, automated findings and scientific acceptance are separate. Scientific review is required.",
    "External preprocessing and gradient-to-image correspondence are declarations, not independently authenticated; no preprocessing is repeated.",
    "DTI assumes mono-exponential Gaussian diffusion. High-b and multi-shell fits require scientific justification; no shell is automatically selected.",
    "MRtrix 3.0.5 internally floors signal below 1e-6 of each voxel maximum before log fitting. Affected values are counted, not silently repaired by TractoSphere.",
    "V1 and -num eigenvalues use descending absolute eigenvalue magnitude in MRtrix 3.0.5; AD/RD use descending algebraic order. Negative eigenvalues remain unchanged.",
    "MIF and NIfTI products describe identical physical voxels. Native-grid NIfTI export uses lossless index permutation/flips only; tensor/vector components remain world RAS.",
    "Residuals describe fit to selected observations, not independent validation. No universal residual or diffusivity acceptance threshold is imposed.",
]


@contextmanager
def image_data(path: Path):
    """Read supported source images without changing orientation or values."""
    if str(path).lower().endswith(".mif"):
        with produced_mif(path, allow_integer=True) as (_, affine, data):
            yield affine, data
    else:
        image = nib.load(path, mmap="r")
        factor = {"mm": 1, "meter": 1000, "micron": 0.001}[image.header.get_xyzt_units()[0]]
        affine = image.affine.copy()
        affine[:3] *= factor
        yield affine, image.dataobj


def _fresh(artifacts, cancelled, progress):
    """Reimport typed requests; never trust serialized image/gradient measurements."""
    result = []
    for saved in artifacts:
        actual = import_artifact(
            ImportRequest(**saved.to_dict()["request"]), cancelled=cancelled, progress=progress
        )
        if actual.id != saved.id:
            raise ValueError("Imported source changed; explicitly reimport it")
        # An import timestamp cannot change approval for otherwise identical bytes.
        data = actual.to_dict()
        data.pop("imported_at", None)
        for key in ("started_at", "finished_at", "run_id"):
            data.get("verification", {}).pop(key, None)
        result.append(Artifact(data))
    return tuple(result)


def _text_table(array):
    """Serialize exact binary64 gradient values, without b-value rescaling."""
    stream = io.StringIO()
    np.savetxt(stream, array, fmt="%.17g")
    return stream.getvalue()


def _world_scheme(artifact):
    """Central gradient contract plus MRtrix's documented FSL-to-world mapping."""
    g = artifact["gradients"]
    affine = np.asarray(artifact["geometry"]["affine_mm"])
    rotation = affine[:3, :3] / np.linalg.norm(affine[:3, :3], axis=0)
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-5):
        raise ValueError(
            "Sheared geometry requires separately verified gradient handling; no correction is implicit"
        )
    return mrtrix_world_scheme(g["bvals"], g["bvecs"], convention=g["convention"], affine=affine)


def _finite_dwi(base, mask, indices, cancelled, progress):
    """Scan every value in file-contiguous, bounded volume/slab order.

    Compressed NIfTI must not be repeatedly read as hundreds of spatial tiles
    spanning all timepoints. Preserve all source values; reject an unfittable
    selected-signal domain rather than edit a mask or fabricate log signals.
    """
    geo = base["geometry"]
    domain = (
        _aligned(Path(mask["path"]), geo).astype(bool)
        if mask
        else np.ones(geo["shape"][:3], dtype=bool)
    )
    if not domain.any():
        raise ValueError("Fitting mask contains no voxels")
    maxima = np.full(geo["shape"][:3], -np.inf, dtype=np.float32)
    low, high, checked, selected = float("inf"), float("-inf"), 0, set(indices)
    with image_data(Path(base["path"])) as (_, values):
        depth = max(1, 1_000_000 // (values.shape[0] * values.shape[1]))
        for volume in range(values.shape[3]):
            check_cancel(cancelled)
            for z in range(0, values.shape[2], depth):
                check_cancel(cancelled)
                block = np.asarray(values[:, :, z : z + depth, volume], dtype=np.float32)
                if not np.isfinite(block).all():
                    raise ValueError("Nonfinite input DWI values; source was not repaired")
                checked += block.size
                low, high = min(low, float(block.min())), max(high, float(block.max()))
                if volume in selected:
                    maxima[:, :, z : z + depth] = np.maximum(maxima[:, :, z : z + depth], block)
            progress(f"Finite DWI inspection: volume {volume + 1}/{values.shape[3]}")
    invalid = int(np.count_nonzero(domain & (maxima <= 0)))
    if invalid:
        raise ValueError(
            f"{invalid} fitting-domain voxels have no positive selected signal. Log-tensor estimation is undefined there. Review source/mask correspondence and provide an independently justified compatible domain; no mask edits or signal repairs were applied."
        )
    return dict(
        coverage="Every source value scanned in bounded file-contiguous slabs",
        values_checked=checked,
        minimum=low,
        maximum=high,
        fitting_voxels=int(domain.sum()),
        no_positive_selected_signal_voxels=invalid,
    )


def _revision():
    """Record checkout identity where available; never assume installed wheel history."""
    root = Path(__file__).parents[4]
    if not (root / ".git").exists():
        return {
            "git_commit": None,
            "source_state": "Installed distribution; implementation hashes recorded",
        }
    try:
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout
        return {"git_commit": commit, "uncommitted_changes": bool(dirty)}
    except (OSError, subprocess.SubprocessError):
        return {
            "git_commit": None,
            "source_state": "Git identity unavailable; implementation hashes recorded",
        }


def prepare_dti(
    artifacts: tuple[Artifact, ...],
    tasks: tuple[Task, ...],
    output_directory: Path,
    *,
    nthreads: int = 4,
    tool_directory: Path | None = None,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[str], None] = lambda _: None,
) -> ExecutionPlan:
    """Compile a complete reviewable DTI DAG; no files or MRI processes created.

    OLS = -ols -iter 0; WLS = -iter 0; IWLS = WLS followed by 1..10
    predicted-signal reweightings. Legacy Stage1 WLS_IWLS remains explicit.
    """
    if isinstance(nthreads, bool) or not isinstance(nthreads, int) or not 1 <= nthreads <= 64:
        raise ValueError("Thread count must be an integer in 1..64")
    if not tasks or any(t.algorithm not in {"dwi2tensor", "tensor2metric"} for t in tasks):
        raise ValueError(
            "Only implemented DTI tasks may execute; CSD/tracking/connectomics remain planned"
        )
    artifacts = _fresh(tuple(artifacts), cancelled, progress)
    proposal = build_workflow(
        artifacts, tuple(tasks), cancelled=cancelled, progress=progress, verify_sources=False
    ).to_dict()
    errors = [i["message"] for i in proposal["issues"] if i["severity"] == "error"]
    if errors:
        raise ValueError("; ".join(errors))
    folder = Path(output_directory).expanduser().absolute()
    if folder.exists() or folder.is_symlink() or not folder.parent.is_dir():
        raise ValueError("Choose a NEW attempt directory with an existing parent")
    folder = folder.resolve()
    sources = {s["path"]: s["sha256"] for a in artifacts for s in a.to_dict()["sources"]}
    if any(folder.is_relative_to(Path(p).resolve().parent) for p in sources):
        raise ValueError("Choose an output directory outside original input directories")
    require_local_processing([folder, *sources])
    tools = detect_mrtrix(
        names=tuple(sorted({"mrconvert", *(t.algorithm for t in tasks)})),
        tool_directory=tool_directory,
        cancelled=cancelled,
    )
    # Verify option availability against actual installed help, not latest docs.
    required = {
        "mrconvert": ("-coord", "-fslgrad", "-bvalue_scaling", "-datatype"),
        "dwi2tensor": ("-ols", "-iter", "-predicted_signal", "-b0", "-mask"),
        "tensor2metric": ("-fa", "-adc", "-ad", "-rd", "-vector", "-modulate", "-num", "-value"),
    }
    for name, flags in required.items():
        if name not in tools:
            continue
        result = subprocess.run(
            [tools[name]["path"], "-help"], capture_output=True, text=True, timeout=10, check=True
        )
        clean = result.stdout + result.stderr
        while "\b" in clean:
            import re

            clean = re.sub(".\b", "", clean)
        if any(flag not in clean for flag in flags):
            raise ValueError(f"{name} required options unavailable in installed executable")
    inventory = {a.id: a.to_dict() for a in artifacts}
    rows = {r["id"]: r for r in proposal["tasks"]}
    products, commands, generated, outputs, bindings = {}, [], {}, [], []
    extra = ["-nthreads", str(nthreads), "-config", "BValueScaling", "false"]

    def command(name, exe, args, inputs, names):
        """Build only validated argv; all outputs are exclusive attempt-local files."""
        argv = [tools[exe]["path"], *map(str, args), *extra]
        commands.append(
            dict(
                step=name,
                argv=argv,
                display=shlex.join(argv),
                inputs=list(map(str, inputs)),
                outputs=names,
            )
        )

    for task in _order(tuple(tasks)):
        check_cancel(cancelled)
        bound = {
            role: products[(ref.task_id, ref.output)]
            if isinstance(ref, OutputRef)
            else inventory[ref]
            for role, ref in task.inputs.items()
        }
        base = bound.get("dwi", bound.get("tensor"))
        mask = bound.get("mask")
        params = dict(task.parameters)
        root = task.id
        mask_args = []
        if mask:
            mask_args = ["-mask", mask["path"]]
        if task.algorithm == "dwi2tensor":
            sampling = rows[root]["sampling"]
            indices = sampling["selected_original_volume_indices"]
            if not any(g["is_b0"] for g in sampling["selected_shells"]):
                raise ValueError("DTI requires explicitly selected b0 measurements")
            scan = _finite_dwi(base, mask, indices, cancelled, progress)
            fit = params["fit"]
            iterations = 0 if fit in {"OLS", "WLS"} else params.get("iterations")
            if fit == "WLS" and params.get("iterations") not in (None, 0):
                raise ValueError("WLS uses zero iterative reweightings; choose IWLS for iterations")
            if iterations is None or isinstance(iterations, bool) or not 0 <= iterations <= 10:
                raise ValueError(
                    "IWLS requires explicit 1..10 iterations (legacy WLS_IWLS permits 0)"
                )
            if fit == "IWLS" and iterations < 1:
                raise ValueError("IWLS requires at least one reweighting; choose WLS for zero")
            scheme = _world_scheme(base)
            table = root + "-selected.grad"
            generated[table] = _text_table(scheme[indices])
            full = root + "-source.mif"
            args = [base["path"], folder / full, "-datatype", "float32", "-bvalue_scaling", "no"]
            if base["gradients"]["convention"] == "fsl_image_axes":
                args += ["-fslgrad", base["request"]["bvec"], base["request"]["bval"]]
            command(root + "-import", "mrconvert", args, [base["path"]], [full])
            selected = root + "-selected.mif"
            command(
                root + "-select",
                "mrconvert",
                [
                    folder / full,
                    folder / selected,
                    "-coord",
                    "3",
                    ",".join(map(str, indices)),
                    "-bvalue_scaling",
                    "no",
                    "-datatype",
                    "float32",
                ],
                [folder / full, folder / table],
                [selected],
            )
            tensor, predicted, b0 = root + "-tensor.mif", root + "-predicted.mif", root + "-b0.mif"
            args = [
                folder / selected,
                folder / tensor,
                "-iter",
                str(iterations),
                "-predicted_signal",
                folder / predicted,
                "-b0",
                folder / b0,
                *mask_args,
            ]
            if fit == "OLS":
                args += ["-ols"]
            command(
                root + "-fit",
                "dwi2tensor",
                args,
                [folder / selected, *([mask["path"]] if mask else [])],
                [tensor, predicted, b0],
            )
            products[(root, "tensor")] = dict(
                kind="tensor",
                path=str(folder / tensor),
                geometry=base["geometry"],
                declarations=TENSOR_DECLARATIONS,
            )
            outputs += [tensor, predicted, b0, selected, full, table]
            bindings.append(
                dict(
                    task=root,
                    algorithm=task.algorithm,
                    source=base,
                    mask=mask,
                    sampling=sampling,
                    scan=scan,
                    fit=fit,
                    iterations=iterations,
                    tensor=tensor,
                    predicted=predicted,
                    observed=selected,
                    b0=b0,
                    scheme=scheme[indices].tolist(),
                )
            )
        else:
            metrics = list(params["metrics"])
            if not metrics or any(m not in METRICS for m in metrics):
                raise ValueError("Unsupported tensor metric")
            # Eigenvalues also required to audit negative values and vector conventions.
            metrics = list(dict.fromkeys([*metrics, "EIGENVALUES"]))
            names = {m: root + "-" + m + ".mif" for m in metrics}
            common = [m for m in metrics if m != "EIGENVALUES"]
            if common:
                args = [base["path"], *mask_args, "-num", "1", "-modulate", "none"]
                for m in common:
                    args += [METRICS[m], folder / names[m]]
                command(
                    root + "-metrics",
                    "tensor2metric",
                    args,
                    [base["path"], *([mask["path"]] if mask else [])],
                    [names[m] for m in common],
                )
            command(
                root + "-eigenvalues",
                "tensor2metric",
                [
                    base["path"],
                    "-value",
                    folder / names["EIGENVALUES"],
                    "-num",
                    "1,2,3",
                    *mask_args,
                ],
                [base["path"], *([mask["path"]] if mask else [])],
                [names["EIGENVALUES"]],
            )
            outputs += list(names.values())
            bindings.append(
                dict(task=root, algorithm=task.algorithm, source=base, mask=mask, metrics=names)
            )
        # Every scientific product exports to the exact original voxel grid.
    exports = []
    for row in bindings:
        names = (
            [row["tensor"], row["b0"]]
            if row["algorithm"] == "dwi2tensor"
            else list(row["metrics"].values())
        )
        for name in names:
            target = name.removesuffix(".mif") + ".nii.gz"
            exports.append(
                dict(source=name, destination=target, geometry=row["source"]["geometry"])
            )
            outputs.append(target)
    outputs += ["qc-metrics.json", "qc-report.html", "qc-catalog.json", "scientific-selection.json"]
    return ExecutionPlan(
        dict(
            kind="mrtrix_dti_execution_plan",
            schema_version=1,
            artifacts=[a.to_dict() for a in artifacts],
            tasks=[t.to_dict() for t in tasks],
            parameters=dict(nthreads=nthreads),
            tools=tools,
            environment=_environment_contract(cancelled),
            output_directory=str(folder),
            commands=commands,
            generated_text=generated,
            bindings=bindings,
            exports=exports,
            outputs=outputs,
            extra_input_hashes=sources,
            scientific_limits=LIMITS,
            issues=proposal["issues"],
            compiler="tractosphere.dti/1",
            build=_revision(),
            source_module_sha256=hash_file(Path(__file__), cancelled),
            implementation_hashes={
                str(path.relative_to(Path(__file__).parents[2])): hash_file(path, cancelled)
                for path in (
                    Path(__file__),
                    Path(__file__).with_name("dti_report.py"),
                    Path(__file__).with_name("planning.py"),
                    Path(__file__).with_name("registry.py"),
                    Path(__file__).with_name("imports.py"),
                    Path(__file__).with_name("tensor_qc.py"),
                    Path(__file__).parents[1] / "execution.py",
                    Path(__file__).parents[1] / "process.py",
                    Path(__file__).parents[1] / "gradients.py",
                )
            },
        )
    )


class DTIStep(MRtrixStep):
    """Validated DTI argv inherits the existing resource/cancellation process runner."""

    def validate(self):
        """Only the recompiled adapter constructs these inputs/outputs and argv."""
        if any(not p.is_file() for p in self.inputs):
            raise ValueError("Missing DTI step input")
        if any(p.exists() or p.is_symlink() for p in self.outputs):
            raise ValueError("DTI output exists; overwriting is refused")


def _aligned(path, geometry):
    """Copy one small model/scalar image into the original grid, without resampling."""
    with image_data(path) as (affine, data):
        axes, flips = voxel_mapping(
            affine, data.shape, np.asarray(geometry["affine_mm"]), geometry["shape"]
        )
        array = np.asarray(data, dtype=np.float32)
        dims = (*axes, *range(3, array.ndim))
        return np.array(rearrange(array, dims, flips), copy=True)


def _tensor_matrix(tensor):
    """Independent coefficient-order verification only, never production fitting."""
    matrix = np.empty((*tensor.shape[:-1], 3, 3), dtype=float)
    matrix[..., 0, 0], matrix[..., 1, 1], matrix[..., 2, 2] = np.moveaxis(tensor[..., :3], -1, 0)
    matrix[..., 0, 1] = matrix[..., 1, 0] = tensor[..., 3]
    matrix[..., 0, 2] = matrix[..., 2, 0] = tensor[..., 4]
    matrix[..., 1, 2] = matrix[..., 2, 1] = tensor[..., 5]
    return matrix


def _assert_close(actual, expected, name, atol=2e-7, rtol=2e-5):
    """Float32 output + double-precision independent algebra; not a QC acceptance rule."""
    if not np.isfinite(actual).all() or not np.allclose(actual, expected, atol=atol, rtol=rtol):
        raise ValueError(f"{name} independent numerical invariant failed")


def verify_dti(folder, data, cancelled=lambda: False, progress=lambda _: None):
    """Verify outputs, independently audit metrics, and retain scientific concerns."""
    findings, measurements = list(LIMITS), []
    for export in data["exports"]:
        check_cancel(cancelled)
        values = _aligned(folder / export["source"], export["geometry"])
        if not np.isfinite(values).all():
            raise ValueError("Nonfinite model output; scientific derivative withheld")
        affine = np.asarray(export["geometry"]["affine_mm"])
        target = folder / export["destination"]
        vector = export["source"].endswith("-V1.mif")
        # NIfTI-1 reserves dimension 4 for time and dimension 5 for vectors.
        # Preserve MRtrix's 4D MIF, but encode VECTOR NIfTI as X,Y,Z,1,3.
        stored_values = values[..., None, :] if vector else values
        image = nib.Nifti1Image(stored_values, affine)
        image.set_qform(affine, 1)
        image.set_sform(affine, 1)
        image.header.set_xyzt_units("mm")
        # General vector, NOT scalar time series or displacement intent.
        if vector:
            image.header.set_intent("vector", name="unit V1 worldRAS")
        if target.exists():
            raise ValueError("Native-grid derivative already exists")
        nib.save(image, target)
        restored = nib.load(target)
        if not np.array_equal(np.asanyarray(restored.dataobj), stored_values) or not np.allclose(
            restored.affine, affine, atol=1e-5, rtol=0
        ):
            raise ValueError("Native-grid export failed lossless geometry/value verification")
    for row in data["bindings"]:
        check_cancel(cancelled)
        progress("Numerical verification: " + row["task"])
        geo = row["source"]["geometry"]
        mask = (
            _aligned(Path(row["mask"]["path"]), geo).astype(bool)
            if row["mask"]
            else np.ones(geo["shape"][:3], dtype=bool)
        )
        if not mask.any():
            raise ValueError("Fitting/evaluation mask is empty")
        tensor_path = (
            folder / row["tensor"]
            if row["algorithm"] == "dwi2tensor"
            else Path(row["source"]["path"])
        )
        tensor = _aligned(tensor_path, geo)[mask]
        if tensor.shape[-1] != 6 or not np.isfinite(tensor).all():
            raise ValueError("Tensor dimensions or finite-value check failed")
        matrix = _tensor_matrix(tensor)
        eigen, vectors = np.linalg.eigh(matrix)
        negative = int(np.count_nonzero(eigen < 0))
        record = dict(
            task=row["task"],
            evaluation_voxels=int(mask.sum()),
            negative_eigenvalues=negative,
            eigenvalue_min_mm2_s=float(eigen.min()),
            eigenvalue_max_mm2_s=float(eigen.max()),
            coefficient_order="D11,D22,D33,D12,D13,D23",
            frame="world RAS",
            diffusivity_units="mm2/s",
            tensor_validity=eigenvalue_findings(eigen),
        )
        if negative:
            findings.append(
                f"{row['task']}: {negative} negative eigenvalues; not repaired. Review tensor validity, noise, gradients and acquisition suitability."
            )
        if row["algorithm"] == "dwi2tensor":
            numerator, denominator, residual_sum, count, floored, nonpositive = (
                0.0,
                0.0,
                0.0,
                0,
                0,
                0,
            )
            per_volume = []
            with (
                produced_mif(folder / row["observed"]) as (_, aff, observed),
                produced_mif(folder / row["predicted"]) as (_, paff, predicted),
            ):
                if predicted.shape != observed.shape or not np.allclose(aff, paff):
                    raise ValueError("Predicted/observed geometry or volume correspondence failed")
                axes, flips = voxel_mapping(
                    np.asarray(geo["affine_mm"]), geo["shape"], aff, observed.shape
                )
                fitmask = rearrange(mask, axes, flips)
                maxima = np.zeros(observed.shape[:3], dtype=np.float32)
                for i in range(observed.shape[3]):
                    check_cancel(cancelled)
                    maxima = np.maximum(maxima, observed[..., i])
                s0 = _aligned(folder / row["b0"], geo)[mask]
                for i, (gx, gy, gz, b) in enumerate(row["scheme"]):
                    check_cancel(cancelled)
                    obs, pred = (
                        observed[..., i][fitmask].astype(float),
                        predicted[..., i][fitmask].astype(float),
                    )
                    if not np.isfinite(obs).all() or not np.isfinite(pred).all():
                        raise ValueError("Nonfinite predicted or observed signal")
                    # MRtrix model output must reproduce the coefficients in world coordinates.
                    native_pred = rearrange(
                        np.asarray(predicted[..., i]),
                        *voxel_mapping(
                            paff, predicted.shape, np.asarray(geo["affine_mm"]), geo["shape"]
                        ),
                    )[mask]
                    g = np.array([gx, gy, gz])
                    analytic = s0 * np.exp(-b * np.einsum("i,nij,j->n", g, matrix, g))
                    _assert_close(
                        native_pred,
                        analytic,
                        "Tensor coefficient order / predicted signal",
                        atol=0.002,
                        rtol=5e-5,
                    )
                    difference = pred - obs
                    ss = float(difference @ difference)
                    numerator += ss
                    denominator += float(obs @ obs)
                    residual_sum += float(difference.sum())
                    count += len(obs)
                    floored += int(np.count_nonzero(obs < 1e-6 * maxima[fitmask]))
                    nonpositive += int(np.count_nonzero(obs <= 0))
                    per_volume.append(
                        dict(
                            original_volume_index=row["sampling"][
                                "selected_original_volume_indices"
                            ][i],
                            b_s_mm2=b,
                            rmse=float(np.sqrt(ss / len(obs))),
                            mean_residual=float(difference.mean()),
                        )
                    )
            record.update(
                rmse=float(np.sqrt(numerator / count)),
                normalized_rmse=float(np.sqrt(numerator / denominator)) if denominator else None,
                mean_residual=residual_sum / count,
                per_volume=per_volume,
                nonpositive_observations=nonpositive,
                observations_below_mrtrix_floor=floored,
                sampling=row["sampling"],
                fit=row["fit"],
                iterations=row["iterations"],
            )
            if floored or nonpositive:
                findings.append(
                    f"{row['task']}: {floored} observations below MRtrix floor; {nonpositive} nonpositive observations. Review fit reliability; input was not repaired."
                )
            weighted = [g["b_median"] for g in row["sampling"]["selected_shells"] if not g["is_b0"]]
            if len(weighted) > 1:
                findings.append(
                    f"{row['task']}: multi-shell fit; Gaussian DTI interpretation requires review. No shell was excluded automatically."
                )
        else:
            md = eigen.mean(axis=1)
            denominator = np.sum(eigen**2, axis=1)
            fa = np.sqrt(
                np.divide(
                    1.5 * np.sum((eigen - md[:, None]) ** 2, axis=1),
                    denominator,
                    out=np.zeros_like(md),
                    where=denominator != 0,
                )
            )
            # Pinned MRtrix 3.0.5 tensor2FA preserves FA > 1; no repair or clipping.
            expected = dict(FA=fa, MD=md, AD=eigen[:, 2], RD=eigen[:, :2].mean(axis=1))
            order = np.argsort(-np.abs(eigen), axis=1, kind="stable")
            expected["EIGENVALUES"] = np.take_along_axis(eigen, order, axis=1)
            sums = eigen.sum(axis=1)
            expected.update(
                CL=np.divide(
                    eigen[:, 2] - eigen[:, 1], sums, out=np.zeros_like(sums), where=sums != 0
                ),
                CP=np.divide(
                    2 * (eigen[:, 1] - eigen[:, 0]), sums, out=np.zeros_like(sums), where=sums != 0
                ),
                CS=np.divide(3 * eigen[:, 0], sums, out=np.zeros_like(sums), where=sums != 0),
            )
            for metric, name in row["metrics"].items():
                actual = _aligned(folder / name, geo)[mask]
                if metric == "V1":
                    norm = np.linalg.norm(actual, axis=1)
                    _assert_close(norm, np.ones_like(norm), "Unit V1")
                    chosen = expected["EIGENVALUES"][:, 0]
                    # Eigenvectors are sign-indeterminate, and ties permit any vector in the eigenspace.
                    _assert_close(
                        np.einsum("nij,nj->ni", matrix, actual),
                        chosen[:, None] * actual,
                        "V1 eigen-equation",
                        atol=2e-9,
                        rtol=1e-5,
                    )
                else:
                    _assert_close(
                        actual,
                        expected[metric],
                        metric,
                        atol=2e-9 if metric in {"MD", "AD", "RD", "EIGENVALUES"} else 2e-7,
                        rtol=1e-5,
                    )
                record[metric] = dict(minimum=float(actual.min()), maximum=float(actual.max()))
            record["FA_above_one_unchanged"] = int(np.count_nonzero(fa > 1))
            record["tensor_validity"] = eigenvalue_findings(eigen, fa)
            if record["FA_above_one_unchanged"]:
                findings.append(
                    f"{row['task']}: {record['FA_above_one_unchanged']} FA values exceed 1 because the unconstrained tensor is not positive semidefinite. Values were not clipped; scientific interpretation requires review."
                )
        measurements.append(record)
    result = dict(
        technical="passed",
        execution="completed",
        automated_qc="failed"
        if any(
            m["negative_eigenvalues"] or m.get("FA_above_one_unchanged", 0) for m in measurements
        )
        else "pending",
        scientific_review="required",
        findings=findings,
        measurements=measurements,
        numerical_tolerances=dict(
            diffusivity_atol_mm2_s=2e-9,
            dimensionless_atol=2e-7,
            metric_rtol=1e-5,
            predicted_signal_atol=0.002,
            predicted_signal_rtol=5e-5,
            rationale="Independent binary64 algebra versus MRtrix float32 storage and eigen solver; validated with exact and noisy phantoms; not scientific acceptance thresholds.",
        ),
    )
    _publish_json(folder / "qc-metrics.json", result)
    from .dti_report import write_report

    write_report(folder, result, data)
    artifacts = [
        dict(kind=kind, label=label, path=name, sha256=hash_file(folder / name, cancelled))
        for name, kind, label in [
            ("qc-report.html", "report", "Portable DTI evidence"),
            ("qc-metrics.json", "metrics", "Tensor metrics and residuals"),
        ]
    ]
    _publish_json(
        folder / "qc-catalog.json",
        dict(
            schema_version=1,
            kind="dmri_qc_catalog",
            identity=dict(subject="Unspecified — see source identity in run manifest"),
            stages=[
                dict(
                    id="dti",
                    name="DTI reconstruction",
                    execution="completed",
                    technical="passed",
                    automated_qc=result["automated_qc"],
                    scientific_review="required",
                    findings=findings,
                    artifacts=artifacts,
                )
            ],
        ),
    )
    return result


class DTIAdapter:
    """Scientific task compiler/QC adapter; shared engine owns all execution."""

    def compile(self, task, inputs, output_directory):
        """Compile one independent task using exactly the same public DAG service."""
        return prepare_dti(tuple(inputs.values()), (task,), output_directory)

    def validate(self, task, inputs):
        """Expose Stage1 compatibility findings without executing commands."""
        return tuple(build_workflow(tuple(inputs.values()), (task,)).to_dict()["issues"])

    def qc(self, manifest):
        """Return recorded evidence; never rerun or confer acceptance."""
        return manifest["validation"]

    def run(self, plan, *, approval, cancelled=lambda: False, progress=lambda _: None):
        """Delegate approval, subprocesses, logs, monitoring and cancellation to core.execution."""
        data = plan.to_dict()
        artifacts = tuple(Artifact(a) for a in data["artifacts"])
        tasks = tuple(Task.from_dict(t) for t in data["tasks"])
        folder = Path(data["output_directory"])

        def recompile():
            """Revalidate original sources, binary identity and all scientific contracts."""
            return prepare_dti(
                artifacts,
                tasks,
                folder,
                nthreads=data["parameters"]["nthreads"],
                tool_directory=Path(data["tools"]["mrconvert"]["path"]).parent,
                cancelled=cancelled,
                progress=progress,
            )

        def setup(target):
            """Write only approved adapter-generated gradient text and source selection."""
            from .records import save_selection

            for name, text in data["generated_text"].items():
                with (target / name).open("x") as stream:
                    stream.write(text)
            save_selection(target / "scientific-selection.json", artifacts, tasks)

        def step(command):
            """Reuse MRtrixStep; never run imported arbitrary command text."""
            return DTIStep(
                command["step"],
                tuple(command["argv"]),
                tuple(map(Path, command["inputs"])),
                tuple(folder / p for p in command["outputs"]),
                data["parameters"],
            )

        def verify_step(command):
            """Validate every produced MIF and selected-volume gradient association."""
            row = next(
                b
                for b in data["bindings"]
                if command["step"]
                in {
                    b["task"] + suffix
                    for suffix in ("-import", "-select", "-fit", "-metrics", "-eigenvalues")
                }
            )
            geometry = row["source"]["geometry"]
            for name in command["outputs"]:
                with produced_mif(folder / name) as (fields, affine, values):
                    voxel_mapping(
                        np.asarray(geometry["affine_mm"]), geometry["shape"], affine, values.shape
                    )
                    if row["algorithm"] == "dwi2tensor":
                        components = (
                            6
                            if name == row["tensor"]
                            else 1
                            if name == row["b0"]
                            else geometry["shape"][3]
                            if command["step"].endswith("-import")
                            else len(row["sampling"]["selected_original_volume_indices"])
                        )
                    else:
                        metric = next(m for m, path in row["metrics"].items() if path == name)
                        components = 3 if metric in {"V1", "EIGENVALUES"} else 1
                    expected = values.shape[:3] + ((components,) if components > 1 else ())
                    if values.shape != expected or fields["datatype"][0] not in {
                        "Float32LE",
                        "Float32BE",
                    }:
                        raise ValueError(f"{name}: unexpected output dimensions or datatype")
                    try:
                        _scan(values, "output", cancelled, lambda _: None)
                    except ValueError as exc:
                        if "Nonfinite" in str(exc):
                            raise ValueError(
                                f"Nonfinite generated output {name}; derivatives withheld. "
                                "Original inputs were not modified. Inspect the failed fit and signal domain."
                            ) from exc
                        raise
            for row in data["bindings"]:
                if command["step"] not in {row["task"] + "-import", row["task"] + "-select"}:
                    continue
                source = row["source"]
                indices = (
                    row["sampling"]["selected_original_volume_indices"]
                    if command["step"].endswith("-select")
                    else list(range(source["geometry"]["shape"][3]))
                )
                with (
                    image_data(Path(source["path"])) as (original_affine, original),
                    produced_mif(folder / command["outputs"][0]) as (fields, affine, values),
                ):
                    axes, flips = voxel_mapping(
                        original_affine, original.shape, affine, values.shape
                    )
                    actual = np.array(
                        [
                            [float(v) for v in line.split(",")]
                            for line in fields.get("dw_scheme", [])
                        ]
                    )
                    _assert_close(
                        actual,
                        _world_scheme(source)[indices],
                        "Embedded gradient / volume correspondence",
                        atol=1e-8,
                        rtol=1e-8,
                    )
                    if values.shape[3] != len(indices):
                        raise ValueError("Selected volume count changed")
                    for out, index in enumerate(indices):
                        check_cancel(cancelled)
                        expected = rearrange(
                            np.asarray(original[..., index], dtype=np.float32), axes, flips
                        )
                        if not np.array_equal(expected, values[..., out]):
                            raise ValueError(
                                "DWI conversion/selection changed physical-voxel signal or volume order"
                            )
            return dict(
                technical="passed",
                coverage="Complete output payload; selection signals and gradients checked",
            )

        def sources():
            """Complete final hashing even after cancellation, without losing failure evidence."""
            for artifact in artifacts:
                artifact.assert_current()

        manifest = execute_artifact_task(
            plan,
            approval=approval,
            recompile=recompile,
            setup=setup,
            step_factory=step,
            verify_step=verify_step,
            verify=lambda target: verify_dti(target, data, cancelled, progress),
            verify_sources=sources,
            cancelled=cancelled,
            progress=progress,
        )
        if manifest["status"] in {"failed", "cancelled"}:
            from ..execution import _write_manifest
            from .dti_report import write_failure_report

            try:
                manifest["failure_evidence"] = write_failure_report(folder, manifest)
            except Exception as exc:
                manifest["failure_report_error"] = str(exc)
            _write_manifest(folder, manifest)
        return manifest
