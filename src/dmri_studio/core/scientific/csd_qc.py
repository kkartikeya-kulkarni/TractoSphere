"""CSD structural verification, measured QC and portable evidence.

No solver or repairs. SH coefficients are not FA: negative higher-order
coefficients are normal. Finite sampled lobes/compartments are descriptive
evidence and never confer automatic scientific acceptance.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import nibabel as nib
import numpy as np

from .._mif_checks import produced_mif, rearrange, voxel_mapping
from ..execution import _publish_json, hash_file
from ..process import check_cancel
from .dti import _aligned, _world_scheme, image_data
from .imports import _scan
from .responses import read_response, response_rows


def _scalar(values):
    """Remove only a singleton component axis, never resample voxels."""
    return values[..., 0] if values.ndim == 4 and values.shape[3] == 1 else values


def quant(values):
    """Report full-domain percentiles; do not discard scientific outliers."""
    a = np.asarray(values)
    if not np.isfinite(a).all():
        raise ValueError("Nonfinite QC values")
    return (
        dict(
            zip(
                ("min", "p1", "p5", "median", "p95", "p99", "max"),
                np.percentile(a, [0, 1, 5, 50, 95, 99, 100]).tolist(),
                strict=True,
            )
        )
        if a.size
        else None
    )


def compartment_statistics(values):
    """Separate raw negatives from a documented Float32 roundoff review floor."""
    values = np.asarray(values)
    tolerance = float(
        32 * np.finfo("f4").eps * max(float(np.abs(values).max()), np.finfo("f4").tiny)
    )
    return dict(
        distribution=quant(values),
        negative_voxels=int((values < 0).sum()),
        negative_beyond_roundoff_voxels=int((values < -tolerance).sum()),
        roundoff_absolute_tolerance=tolerance,
        roundoff_policy="32 Float32 epsilons times maximum masked absolute compartment amplitude; numerical review heuristic, not scientific acceptance",
        units="Response-dependent compartment amplitude, not an automatically calibrated probability",
    )


def normalization_identity(folder, row, cancelled):
    """Check upstream source/field scaling, in bounded per-coefficient reads.

    The pinned upstream writer uses balance_factor * input / field. Text balance
    diagnostics have limited serialization precision: balanced comparisons use
    1e-5 relative to maximum expected amplitude; unbalanced ones use 16 Float32
    epsilons. These are numerical integration tolerances, not scientific gates.
    """
    checks = {}
    factors = np.loadtxt(folder / row["normalization_checks"][2], ndmin=1)
    for index, role in enumerate(("wm_fod", "gm", "csf")):
        with (
            image_data(Path(row["input_bindings"][role]["path"])) as (iaff, original),
            produced_mif(folder / row["outputs"][role]) as (_, oaff, output),
        ):
            axes, flips = voxel_mapping(iaff, original.shape, oaff, output.shape)
            field = _scalar(
                _aligned(
                    folder / row["normalization_checks"][0],
                    dict(shape=output.shape, affine_mm=oaff),
                )
            )
            if not np.isfinite(field).all() or np.any(field <= 0):
                raise ValueError("Normalization field must be positive and finite")
            factor = float(factors[index]) if row["parameters"]["balanced"] == "on" else 1.0
            error = scale = 0.0
            count = original.shape[3] if original.ndim == 4 else 1
            for component in range(count):
                check_cancel(cancelled)
                source = original[..., component] if original.ndim == 4 else original
                expected = factor * rearrange(np.asarray(source, dtype=float), axes, flips) / field
                actual = output[..., component] if output.ndim == 4 else output
                error = max(error, float(np.abs(expected - actual).max()))
                scale = max(scale, float(np.abs(expected).max()))
            tolerance = (
                1e-5 if row["parameters"]["balanced"] == "on" else 16 * np.finfo("f4").eps
            ) * max(scale, np.finfo("f4").tiny)
            if error > tolerance:
                raise ValueError("Normalization source/field scaling failed: " + role)
            checks[role] = dict(
                maximum_absolute_difference=error,
                absolute_tolerance=float(tolerance),
                factor=factor,
            )
    return checks


def verify_step(folder, data, command, cancelled):
    """Validate all output bytes/types before any downstream task can use them."""
    row = next(r for r in data["bindings"] if command["task"] == r["task"])
    geometry = row["source"]["geometry"]
    measurements = {}
    for name in command["outputs"]:
        check_cancel(cancelled)
        path = folder / name
        if name.endswith(".txt"):
            if name.endswith("-normalization-factors.txt"):
                values = np.loadtxt(path, ndmin=1)
                if values.size != 3 or not np.isfinite(values).all() or np.any(values <= 0):
                    raise ValueError("Invalid normalization balance factors")
                measurements[name] = values.tolist()
            else:
                tissue = next(
                    k.removesuffix("_response") for k, n in row["outputs"].items() if n == name
                )
                expected = sorted(g["b_median"] for g in row["sampling"]["selected_shells"])
                meta = read_response(path, tissue)
                response_rows(
                    meta,
                    row["sampling"]["selected_shells"],
                    tissue,
                    row["parameters"]["lmax"] if tissue == "wm" else 0,
                    single=row["algorithm"] != "dwi2response.dhollander",
                )
                if len(meta["shells"]) != len(expected):
                    raise ValueError("Estimated response row count changed")
                measurements[name] = meta
            continue
        with produced_mif(path) as (fields, affine, values):
            voxel_mapping(
                np.asarray(geometry["affine_mm"]), geometry["shape"], affine, values.shape
            )
            mask_output = name == row.get("voxels") or name.endswith("-normalization-inliers.mif")
            if not mask_output and fields["datatype"] not in (["Float32LE"], ["Float32BE"]):
                raise ValueError(name + ": scientific coefficients/signals require Float32 storage")
            if command["kind"] == "selection":
                components = command["components"]
            elif name == row.get("voxels"):
                components = 3 if row["algorithm"] == "dwi2response.dhollander" else 1
            elif name == row.get("predicted"):
                components = len(row["sampling"]["selected_original_volume_indices"])
            elif name in row.get("normalization_checks", []):
                components = 1
            else:
                role = next(k for k, v in row["outputs"].items() if v == name)
                lmax = row["parameters"].get("lmax", row["source"]["metadata"].get("lmax"))
                components = (lmax + 1) * (lmax + 2) // 2 if role == "wm_fod" else 1
            if components == 1:
                if not (values.ndim == 3 or values.ndim == 4 and values.shape[3] == 1):
                    raise ValueError(name + ": expected isotropic/singleton image")
            elif values.ndim != 4 or values.shape[3] != components:
                raise ValueError(name + ": unexpected SH/volume/component count")
            stats = _scan(values, "output", cancelled, lambda _: None)
            if name == row.get("voxels"):
                if stats["minimum"] < 0 or stats["maximum"] > 1:
                    raise ValueError("Response selection is not a binary tissue-selection image")
                counts = (
                    [int(np.count_nonzero(values[..., i])) for i in range(components)]
                    if components > 1
                    else [int(np.count_nonzero(values))]
                )
                if not all(counts):
                    raise ValueError("Response estimation produced an empty tissue selection")
                stats["selected_voxels"] = counts
            measurements[name] = stats
            if command["kind"] == "selection":
                source = row["source"]
                indices = row["sampling"]["selected_original_volume_indices"]
                actual = np.array(
                    [[float(v) for v in line.split(",")] for line in fields.get("dw_scheme", [])]
                )
                if actual.shape != (len(indices), 4) or not np.allclose(
                    actual, _world_scheme(source)[indices], atol=1e-8, rtol=1e-8
                ):
                    raise ValueError("Selected DWI gradient/ordering correspondence failed")
                with image_data(Path(source["path"])) as (original_affine, original):
                    axes, flips = voxel_mapping(
                        original_affine, original.shape, affine, values.shape
                    )
                    for out, index in enumerate(indices):
                        check_cancel(cancelled)
                        expected = rearrange(
                            np.asarray(original[..., index], dtype="f4"), axes, flips
                        )
                        if not np.array_equal(expected, values[..., out]):
                            raise ValueError("CSD shell selection changed source signals")
    return dict(
        technical="passed",
        coverage="Complete output payload and explicit spatial/type/response contracts",
        measurements=measurements,
    )


def _fod_measurements(path, mask, lmax, cancelled):
    """Bounded actual SH sampling and whole-mask coefficient statistics."""
    from .csd import sh_design

    n = 72
    k = np.arange(n)
    z = 1 - 2 * (k + 0.5) / n
    phi = k * (np.pi * (3 - np.sqrt(5)))
    dirs = np.c_[np.sqrt(1 - z * z) * np.cos(phi), np.sqrt(1 - z * z) * np.sin(phi), z]
    basis = sh_design(dirs, lmax)
    minima = np.zeros(mask.shape, "f4")
    maxima = minima.copy()
    norm = minima.copy()
    c00 = minima.copy()
    negative = 0
    negative_roundoff = 0
    sample_count = 0
    coefficient_min = np.inf
    coefficient_max = -np.inf
    coefficient_abs_sum = 0.0
    coefficient_count = 0
    with produced_mif(path) as (_, _, values):
        # mask is already in this output's logical grid.
        for x in range(0, mask.shape[0], 16):
            for y in range(0, mask.shape[1], 16):
                for z in range(0, mask.shape[2], 16):
                    check_cancel(cancelled)
                    sl = (slice(x, x + 16), slice(y, y + 16), slice(z, z + 16))
                    domain = mask[sl]
                    if not domain.any():
                        continue
                    block = np.asarray(values[sl], dtype=float)
                    coeff = block[domain]
                    amplitude = coeff @ basis.T
                    scale = np.maximum(
                        np.linalg.norm(coeff, axis=1), np.max(abs(amplitude), axis=1)
                    )
                    tol = 32 * np.finfo("f4").eps * scale
                    negative += int((amplitude < 0).sum())
                    negative_roundoff += int((amplitude < -tol[:, None]).sum())
                    sample_count += amplitude.size
                    c00[sl][domain] = coeff[:, 0]
                    norm[sl][domain] = np.linalg.norm(coeff, axis=1)
                    minima[sl][domain] = amplitude.min(1)
                    maxima[sl][domain] = amplitude.max(1)
                    coefficient_min = min(coefficient_min, float(coeff.min()))
                    coefficient_max = max(coefficient_max, float(coeff.max()))
                    coefficient_abs_sum += float(abs(coeff).sum())
                    coefficient_count += coeff.size
    return dict(
        lmax=lmax,
        coefficients=(lmax + 1) * (lmax + 2) // 2,
        coefficient_min=coefficient_min,
        coefficient_max=coefficient_max,
        coefficient_mean_absolute=coefficient_abs_sum / coefficient_count,
        C00=quant(c00[mask]),
        SH_norm=quant(norm[mask]),
        sampled_minimum=quant(minima[mask]),
        sampled_peak=quant(maxima[mask]),
        sample_directions=dirs.tolist(),
        sample_count=sample_count,
        negative_samples_raw=negative,
        negative_samples_beyond_roundoff=negative_roundoff,
        negative_C00_voxels=int((c00[mask] < 0).sum()),
        roundoff_policy="32 Float32 epsilons times max(SH norm, sampled absolute amplitude) per voxel; not a scientific acceptance rule",
        interpretation="Negative higher-order coefficients are not invalid by themselves. Sampling is finite directional QC, not a glyph rendering or full continuous-positivity proof.",
    ), c00


def verify_csd(folder, data, cancelled, progress):
    """Measured CSD-specific QC and verified scalar exports, without repairs."""
    from .csd import LIMITS

    findings = []
    measurements = []
    for row in data["bindings"]:
        check_cancel(cancelled)
        progress("CSD numerical/QC verification: " + row["task"])
        geo = row["source"]["geometry"]
        mask = _aligned(Path(row["mask"]["path"]), geo).astype(bool)
        record = dict(
            task=row["task"],
            algorithm=row["algorithm"],
            mask_voxels=int(mask.sum()),
            parameters=row["parameters"],
        )
        if row.get("signal_inspection"):
            record["signal"] = row["signal_inspection"]
            if row["signal_inspection"]["any_nonpositive_signal_voxels"]:
                findings.append(
                    f"{row['task']}: {row['signal_inspection']['any_nonpositive_signal_voxels']} masked voxels contain nonpositive selected signal; source and mask require review, no values changed."
                )
        if row["algorithm"].startswith("dwi2response"):
            record["responses"] = {}
            for role, name in row["outputs"].items():
                record["responses"][role] = read_response(
                    folder / name, role.removesuffix("_response")
                )
            with produced_mif(folder / row["voxels"]) as (_, aff, values):
                axes, flips = voxel_mapping(
                    np.asarray(geo["affine_mm"]), geo["shape"], aff, values.shape
                )
                domain = rearrange(mask, axes, flips)
                count = values.shape[3] if values.ndim == 4 else 1
                record["response_selected_voxels"] = (
                    [int(np.count_nonzero(values[..., i])) for i in range(count)]
                    if count > 1
                    else [int(np.count_nonzero(values))]
                )
                if any(
                    (np.asarray(values[..., i] if count > 1 else _scalar(values)) > 0)[
                        ~domain
                    ].any()
                    for i in range(count)
                ):
                    raise ValueError("Response voxel selection escapes supplied mask")
            record["selection_channel_order"] = (
                "CSF, GM, single-fibre WM" if count == 3 else "single-fibre WM"
            )
        for role, name in row["outputs"].items():
            if not name.endswith(".mif"):
                continue
            with produced_mif(folder / name) as (_, aff, values):
                axes, flips = voxel_mapping(
                    np.asarray(geo["affine_mm"]), geo["shape"], aff, values.shape
                )
                domain = rearrange(mask, axes, flips)
                if role == "wm_fod":
                    lmax = row["parameters"].get("lmax", row["source"]["metadata"].get("lmax"))
                    stats, _ = _fod_measurements(folder / name, domain, lmax, cancelled)
                    record["wm_fod"] = stats
                    if stats["negative_samples_beyond_roundoff"]:
                        findings.append(
                            f"{row['task']}: {stats['negative_samples_beyond_roundoff']}/{stats['sample_count']} sampled FOD amplitudes are negative beyond roundoff; assess constraint-grid/fit behavior. SH coefficients unchanged."
                        )
                else:
                    scalar = _scalar(values)
                    stats = compartment_statistics(scalar[domain])
                    record[role] = stats
                    if stats["negative_beyond_roundoff_voxels"]:
                        findings.append(
                            f"{row['task']}: {role} has {stats['negative_beyond_roundoff_voxels']} negative compartment values beyond roundoff ({stats['negative_voxels']} raw negatives); not clipped."
                        )
        if row.get("predicted"):
            residual = []
            ss = energy = 0.0
            observations = 0
            with (
                image_data(Path(row["selected"])) as (aff, observed),
                produced_mif(folder / row["predicted"]) as (_, paff, pred),
            ):
                axes, flips = voxel_mapping(aff, observed.shape, paff, pred.shape)
                pmask = rearrange(
                    mask,
                    *voxel_mapping(np.asarray(geo["affine_mm"]), geo["shape"], paff, pred.shape),
                )
                for i, index in enumerate(row["sampling"]["selected_original_volume_indices"]):
                    check_cancel(cancelled)
                    o = rearrange(np.asarray(observed[..., i], dtype=float), axes, flips)[pmask]
                    p = np.asarray(pred[..., i], dtype=float)[pmask]
                    r = p - o
                    numerator = float(r @ r)
                    denominator = float(o @ o)
                    ss += numerator
                    energy += denominator
                    observations += len(r)
                    residual.append(
                        dict(
                            original_volume_index=index,
                            b_s_mm2=row["sampling"]["selected_bvalues"][i],
                            RMSE=float(np.sqrt(numerator / len(r))),
                        )
                    )
            record["residuals"] = dict(
                RMSE=float(np.sqrt(ss / observations)),
                normalized_RMSE=float(np.sqrt(ss / energy)) if energy else None,
                per_volume=residual,
                interpretation="In-sample tensor-independent MSMT signal prediction; not proof of biological tissue validity",
            )
        if row.get("normalization_checks"):
            norm, inside, factors = row["normalization_checks"]
            a = _scalar(_aligned(folder / norm, geo))
            b = _scalar(_aligned(folder / inside, geo))
            v = np.loadtxt(folder / factors, ndmin=1)
            record["normalization"] = dict(
                field=quant(a[mask]),
                source_field_scaling=normalization_identity(folder, row, cancelled),
                inlier_voxels=int((b > 0).sum()),
                balance_factors=v.tolist(),
                balanced=row["parameters"]["balanced"],
                reference_default_SH_DC=1 / np.sqrt(4 * np.pi),
                note="Upstream robust normalizer may exclude outliers internally; original mask unchanged. Normalized amplitudes are a new explicit derivative.",
            )
        measurements.append(record)
    for export in data["exports"]:
        check_cancel(cancelled)
        geo = export["geometry"]
        # Export one scalar at a time. Never materialize a full large FOD merely
        # to extract its zeroth-order coefficient.
        with produced_mif(folder / export["source"]) as (_, aff, values):
            scalar = np.asarray(values[..., 0] if values.ndim == 4 else values, dtype="f4")
            axes, flips = voxel_mapping(
                aff, values.shape, np.asarray(geo["affine_mm"]), geo["shape"]
            )
            scalar = np.array(rearrange(scalar, axes, flips), copy=True)
        image = nib.Nifti1Image(scalar, np.asarray(geo["affine_mm"]))
        image.header.set_xyzt_units("mm")
        image.set_sform(image.affine, 1)
        image.set_qform(image.affine, 1)
        target = folder / export["destination"]
        if target.exists():
            raise ValueError("Scalar export already exists")
        nib.save(image, target)
        check = nib.load(target)
        if not np.array_equal(np.asanyarray(check.dataobj), scalar) or not np.allclose(
            check.affine, image.affine, atol=1e-5, rtol=0
        ):
            raise ValueError("CSD scalar export value/geometry verification failed")
    result = dict(
        execution="completed",
        technical="passed",
        automated_qc="pending" if findings else "passed",
        automated_qc_measurements_completed=True,
        scientific_review="required",
        scientific_acceptance="not_established",
        findings=findings,
        scientific_limits=LIMITS,
        measurements=measurements,
    )
    _publish_json(folder / "qc-metrics.json", result)
    write_report(folder, data, result)
    artifacts = [
        dict(kind=kind, label=label, path=name, sha256=hash_file(folder / name, cancelled))
        for name, kind, label in [
            ("qc-report.html", "report", "CSD/response/normalization scientific evidence"),
            ("qc-metrics.json", "metrics", "CSD-specific numerical measurements"),
        ]
    ]
    identity = next(
        (
            a["declarations"].get("subject_id")
            for a in data["artifacts"]
            if a["declarations"].get("subject_id")
        ),
        "Unspecified — source identity in manifest",
    )
    _publish_json(
        folder / "qc-catalog.json",
        dict(
            schema_version=1,
            kind="dmri_qc_catalog",
            identity=dict(subject=identity),
            stages=[
                dict(
                    id="csd",
                    name="CSD / response / normalization",
                    execution="completed",
                    technical="passed",
                    automated_qc=result["automated_qc"],
                    scientific_review="required",
                    findings=findings
                    or [
                        "Numerical checks completed. Anatomical, response and scientific interpretation remain subject to review."
                    ],
                    artifacts=artifacts,
                )
            ],
        ),
    )
    return result


def write_report(folder, data, result):
    """Portable report exposes measurements and limitations, never acceptance."""

    def e(s):
        """Escape user-controlled provenance for the local evidence document."""
        return html.escape(str(s))

    lines = [
        '<!doctype html><meta charset="utf-8"><title>CSD scientific evidence</title><style>body{font:16px/1.6 system-ui;max-width:1000px;margin:40px auto;padding:0 20px}pre{white-space:pre-wrap;overflow-wrap:anywhere}table{border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #ddd;text-align:left}</style><h1>CSD reconstruction evidence</h1><p>Execution completed · Numerical verification passed · QC measurements recorded · Scientific review required</p><p>No automatic scientific acceptance. FOD SH, tensor metrics and scalar compartments are distinct scientific representations.</p><h2>Findings</h2><ul>'
    ]
    lines += ["<li>" + e(x) + "</li>" for x in result["findings"]]
    lines += [
        "</ul><h2>Scientific limitations</h2><ul>",
        *["<li>" + e(x) + "</li>" for x in result["scientific_limits"]],
        "</ul><h2>Measurements</h2>",
    ]
    for row in result["measurements"]:
        lines += [
            "<h3>"
            + e(row["task"])
            + "</h3><pre>"
            + e(json.dumps(row, indent=2, allow_nan=False))
            + "</pre>"
        ]
    lines += [
        "<h2>Exact commands</h2><pre>"
        + e("\n".join(c["display"] for c in data["commands"]))
        + "</pre><p>See manifest.json for immutable input/output hashes, response associations, SH order, tool/library/runtime identity, timing, resource records and approval digest. Source images and responses are unchanged.</p>"
    ]
    with (folder / "qc-report.html").open("x", encoding="utf-8") as f:
        f.write("\n".join(lines))


def failure_report(folder, manifest):
    """Retain failed/cancelled evidence without registering partial FOD products."""
    name = "failure-report.html"
    state = manifest["status"]
    message = html.escape(manifest.get("error", state))
    with (folder / name).open("x", encoding="utf-8") as f:
        f.write(
            '<!doctype html><meta charset="utf-8"><title>CSD incomplete attempt</title><h1>CSD attempt '
            + html.escape(state)
            + "</h1><p>"
            + message
            + "</p><p>Incomplete outputs are not verified derivatives. Review manifest.json and logs. Original data preserved; scientific acceptance not established.</p>"
        )
    _publish_json(
        folder / "failure-qc-catalog.json",
        dict(
            schema_version=1,
            kind="dmri_qc_catalog",
            identity=dict(subject="See source identity in manifest"),
            stages=[
                dict(
                    id="csd-incomplete",
                    name="Incomplete CSD attempt",
                    execution="failed" if state == "failed" else "pending",
                    technical="failed" if state == "failed" else "pending",
                    automated_qc="unavailable",
                    scientific_review="required",
                    findings=[manifest.get("error", state)],
                    artifacts=[
                        dict(
                            kind="report",
                            label="Failed/cancelled CSD evidence",
                            path=name,
                            sha256=hash_file(folder / name),
                        )
                    ],
                )
            ],
        ),
    )
    return dict(report=name, catalog="failure-qc-catalog.json")
