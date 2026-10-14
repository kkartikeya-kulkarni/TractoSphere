"""Validate explicit matched-measurement evidence before optional AP/PA combination.

Direction/b-value similarity alone is insufficient. A pairing specification binds
the corrected parent, original gradient table, acquisition evidence and a complete
volume mapping. This does not make an approximate combined encoding ground truth.
"""

from pathlib import Path

import numpy as np

from .execution import hash_file
from .qc import _json

HCP_HELPER_SHA256 = "93619bb4c07cea5d7f3e67be22f299de4ef588c2bf8f74fcf88fa0b107a30835"
HCP_COMMIT = "faec8f3ec23697018a6cdc0824569d4082e6201e"


def validate_pairing(dataset, path: Path, *, cancelled=lambda: False) -> dict:
    """Check every pair and source without modifying data or inventing metadata."""
    path = path.expanduser().resolve(strict=True)
    digest = hash_file(path, cancelled)
    spec = _json(path)
    if spec.get("schema_version") != 1 or spec.get("kind") != "dmri_matched_AP_PA":
        raise ValueError("Supply a version1 dmri_matched_AP_PA pairing specification")
    if not spec.get("subject") or not spec.get("session") or not spec.get("rationale"):
        raise ValueError("Pairing requires subject/session identity and its scientific rationale")
    checks = {str(path): digest}
    for role in ("dwi", "bval", "bvec"):
        source = dataset.paths.supplied()[role]
        actual = hash_file(source, cancelled)
        if spec.get("parent_sha256", {}).get(role) != actual:
            raise ValueError(f"Pairing is not bound to this parent {role}")
    pairs = spec.get("pairs")
    if (
        not isinstance(pairs, list)
        or len(pairs) < 2
        or any(
            not isinstance(p, list)
            or len(p) != 2
            or any(type(v) is not int or not 0 <= v < dataset.n_volumes for v in p)
            for p in pairs
        )
    ):
        raise ValueError("Supply explicit in-range zero-based pairs")
    flattened = [i for pair in pairs for i in pair]
    if len(set(flattened)) != len(flattened):
        raise ValueError("A parent volume may occur in only one AP/PA pair")
    omitted = spec.get("unpaired", [])
    if not isinstance(omitted, list) or any(
        not isinstance(x, dict)
        or type(x.get("index")) is not int
        or not str(x.get("reason", "")).strip()
        for x in omitted
    ):
        raise ValueError("Every unpaired parent volume needs an explicit reason")
    indices = [x["index"] for x in omitted]
    if (
        len(indices) != len(set(indices))
        or set(flattened) & set(indices)
        or set(flattened + indices) != set(range(dataset.n_volumes))
    ):
        raise ValueError("Pairing must account for every parent volume, including extra references")
    lengths = spec.get("series_lengths")
    if (
        not isinstance(lengths, list)
        or not lengths
        or any(type(x) is not int or x < 1 for x in lengths)
        or sum(lengths) != len(pairs)
    ):
        raise ValueError("Recorded matched series lengths must sum to the pair count")
    original = spec.get("nominal_gradients", {})
    nominal = {}
    for role in ("bval", "bvec"):
        entry = original.get(role, {})
        source = (path.parent / entry.get("path", "")).resolve(strict=True)
        actual = hash_file(source, cancelled)
        if actual != entry.get("sha256"):
            raise ValueError("Original gradient provenance changed")
        checks[str(source)] = actual
        nominal[role] = np.loadtxt(source, ndmin=2)
    bvals = nominal["bval"].ravel()
    vectors = nominal["bvec"]
    if (
        bvals.shape != (dataset.n_volumes,)
        or vectors.shape != (3, dataset.n_volumes)
        or not np.isfinite(vectors).all()
        or not np.array_equal(bvals, dataset.gradients.bvals)
    ):
        raise ValueError(
            "Original gradients must match the corrected parent's volume order and b-values"
        )
    a, b = np.asarray(pairs).T
    weighted = bvals[a] > dataset.validation.policy.b0_max
    if not weighted.any() or not np.array_equal(bvals[a], bvals[b]):
        raise ValueError(
            "Matched full diffusion measurements with identical original b-values are required; calibration-only b0s do not qualify"
        )
    norms = np.linalg.norm(vectors, axis=0)
    if not np.allclose(norms[np.unique(np.r_[a[weighted], b[weighted]])], 1, atol=1e-4, rtol=0):
        raise ValueError("Nominal weighted gradients are not unit length; no automatic repair")
    dot = np.sum(vectors[:, a[weighted]] * vectors[:, b[weighted]], axis=0) / (
        norms[a[weighted]] * norms[b[weighted]]
    )
    if not np.allclose(abs(dot), 1, atol=1e-8, rtol=0):
        raise ValueError(
            "Original paired diffusion directions are not the same encoding up to sign"
        )
    evidence = spec.get("evidence", [])
    if not evidence:
        raise ValueError("Pairing requires acquisition or verified historical source evidence")
    for entry in evidence:
        source = (path.parent / entry["path"]).resolve(strict=True)
        if (
            not str(entry.get("description", "")).strip()
            or hash_file(source, cancelled) != entry["sha256"]
        ):
            raise ValueError("Pairing acquisition evidence missing or changed")
        checks[str(source)] = entry["sha256"]
    acquisition = spec.get("acquisition", {})
    basis = acquisition.get("basis")
    if basis == "documented_protocol":
        metadata = []
        for key in ("A_json", "B_json"):
            source = (path.parent / acquisition.get(key, "")).resolve(strict=True)
            if str(source) not in checks:
                raise ValueError("Acquisition JSON must be included in hash-bound pairing evidence")
            metadata.append(_json(source))
        first, second = metadata
        pa, pb = first.get("PhaseEncodingDirection"), second.get("PhaseEncodingDirection")
        if pa not in {"i", "i-", "j", "j-", "k", "k-"} or pb != (
            pa[:-1] if pa.endswith("-") else pa + "-"
        ):
            raise ValueError(
                "Documented acquisition directions must be opposite on the same native axis"
            )
        for field in ("EchoTime", "RepetitionTime", "TotalReadoutTime"):
            values = [x.get(field) for x in metadata]
            if (
                any(type(v) not in (float, int) or not np.isfinite(v) or v <= 0 for v in values)
                or values[0] != values[1]
            ):
                raise ValueError(
                    f"Matched documented acquisitions require equal positive {field}; do not borrow another protocol"
                )
        if not acquisition.get("same_geometry_and_reconstruction_evidence") or not acquisition.get(
            "between_run_scaling_evidence"
        ):
            raise ValueError(
                "Document matching geometry/reconstruction and justified between-run intensity scaling before combining"
            )
        limits = [
            "Input metadata and declared association must be reviewed; numerical pairing does not prove anatomical fidelity."
        ]
    elif basis == "verified_historical_investigation":
        audit_path = (path.parent / acquisition.get("audit", "")).resolve(strict=True)
        if str(audit_path) not in checks:
            raise ValueError("Historical pairing audit must be hash-bound evidence")
        audit = _json(audit_path)
        if (
            audit.get("all_checks_passed") is not True
            or not audit.get("checks")
            or any(v is not True for v in audit["checks"].values())
        ):
            raise ValueError("Historical pairing audit has unresolved checks")
        audited = [
            [p["prepared_PA_zero_based"], p["prepared_AP_zero_based"]]
            for p in audit.get("pairs", [])
        ]
        if audited != pairs or not acquisition.get("documented_limitations"):
            raise ValueError(
                "Pairing must match the actual historical audit and retain its metadata limitations"
            )
        limits = list(acquisition["documented_limitations"])
        if not all(isinstance(x, str) for x in limits):
            raise ValueError("Historical limitations must be explicit text")
        limits.append(
            "Historical-scheme reproduction is an investigation derivative, not validation of acquisition equivalence or scientific acceptance."
        )
    else:
        raise ValueError(
            "Pairing needs documented protocol eligibility or an explicit verified historical investigation"
        )
    rotated = dataset.gradients.bvecs.T
    nr = np.linalg.norm(rotated, axis=0)
    if not np.allclose(nr[np.unique(np.r_[a[weighted], b[weighted]])], 1, atol=1e-4, rtol=0):
        raise ValueError("Corrected paired gradients are not unit length")
    cos = np.sum(rotated[:, a[weighted]] * rotated[:, b[weighted]], axis=0) / (
        nr[a[weighted]] * nr[b[weighted]]
    )
    angles = np.degrees(np.arccos(np.clip(abs(cos), 0, 1)))
    if hash_file(path, cancelled) != digest:
        raise ValueError("Pairing specification changed during validation")
    return {
        "specification": spec,
        "source": str(path),
        "source_hashes": checks,
        "pairs": pairs,
        "weighted_pair_angles_deg": angles.tolist(),
        "unpaired_parent_volumes_retained": omitted,
        "scientific_limits": limits,
        "scientific_acceptance": "pending",
    }
