"""Read-only recovery of engine attempts and verified output/history handoffs.

No tool execution or in-place retry occurs here. A running snapshot is only the
last published record, not proof that a process is alive. Failed attempts remain
available for logs/QC but never become eligible downstream handoffs.
"""

from __future__ import annotations

from pathlib import Path

from .execution import hash_file
from .process import check_cancel
from .qc import _json
from .workflow import HistoryClaim, ProcessingHistory

MANIFESTS = {
    "registration_execution_plan": "manifest.json",
    "mrtrix_sift2_execution_plan": "manifest.json",
    "mrtrix_anatomy_execution_plan": "manifest.json",
    "mrtrix_tracking_execution_plan": "manifest.json",
    "mrtrix_connectome_execution_plan": "manifest.json",
    "mrtrix_csd_execution_plan": "manifest.json",
    "mrtrix_dti_execution_plan": "manifest.json",
    "mrtrix_execution_plan": "manifest.json",
    "mrtrix_native_execution_plan": "native-manifest.json",
    "fsl_correction_plan": "correction-manifest.json",
    "mrtrix_ants_bias_plan": "bias-manifest.json",
    "hcp_matched_combination_plan": "combination-manifest.json",
    "fsl_anatomy_plan": "anatomy-manifest.json",
}


def inspect_run(path: Path, *, cancelled=lambda: False, progress=lambda _: None) -> dict:
    """Verify a saved engine attempt; expose continuations only after all hashes pass."""
    path = path.expanduser().resolve(strict=True)
    digest = hash_file(path, cancelled)
    record = _json(path)
    plan = record.get("plan", {})
    kind = plan.get("kind")
    if kind not in MANIFESTS:
        raise ValueError(
            "Unsupported producer manifest; use its saved QC catalog or an explicit producer adapter"
        )
    folder = Path(plan["output_directory"]).resolve(strict=True)
    if path.parent != folder:
        raise ValueError(
            "Processing manifest must be inside its recorded exclusive attempt directory"
        )
    status = record.get("status", "unknown")
    finished = status in {"validated", "validated_pending_qc", "failed", "cancelled"}
    steps = {s["step"]: s for s in record.get("steps", [])}
    rows = [
        {
            "step": c["step"],
            "status": steps.get(c["step"], {}).get("status", "pending"),
            "return_code": steps.get(c["step"], {}).get("return_code"),
            "elapsed_seconds": steps.get(c["step"], {}).get("elapsed_seconds"),
            "command": c.get("display", ""),
            "log": steps.get(c["step"], {}).get("log"),
        }
        for c in plan.get("commands", [])
    ]
    result = {
        "manifest": str(path),
        "manifest_sha256": digest,
        "kind": kind,
        "status": status,
        "steps": rows,
        "products": [],
        "recovery": None,
        "handoff": {},
        "qc_catalog": str(folder / "qc-catalog.json")
        if (folder / "qc-catalog.json").is_file()
        else None,
        "record": record,
        "scientific_acceptance": "pending",
        "verified_hashes": 0,
        "notice": "Read-only saved evidence. Scientific review remains separate from execution and numerical checks.",
    }
    if (
        kind in {"mrtrix_dti_execution_plan", "mrtrix_csd_execution_plan"}
        and status in {"failed", "cancelled"}
        and (folder / "failure-qc-catalog.json").is_file()
    ):
        result["qc_catalog"] = str(folder / "failure-qc-catalog.json")
    if not finished:
        result["notice"] = (
            "Recorded running/incomplete status; process liveness is not established. Refresh to read newer evidence. No restart or duplicate job is offered."
        )
        return result
    successful = (
        status in {"validated", "validated_pending_qc"} and record.get("inputs_unchanged") is True
    )
    checks = {}
    for name, entry in record.get("outputs", {}).items():
        output = (folder / name).resolve(strict=True)
        if not output.is_relative_to(folder):
            raise ValueError("Output escapes its recorded attempt")
        checks[str(output)] = entry["sha256"]
    if successful:
        if not checks:
            raise ValueError("Completed producer has no output hashes")
        for role, entry in plan.get("inputs", {}).items():
            checks[plan["input_paths"][role]] = entry["sha256"]
        checks.update(plan.get("extra_input_hashes", {}))
    for name, expected in checks.items():
        check_cancel(cancelled)
        progress("Verifying saved evidence: " + name)
        if hash_file(Path(name), cancelled) != expected:
            raise ValueError("Saved producer evidence changed: " + name)
    if hash_file(path, cancelled) != digest:
        raise ValueError("Attempt record changed during inspection; refresh")
    result["verified_hashes"] = len(checks)
    if not successful:
        from .checkpoints import native_recovery

        result["recovery"] = native_recovery(path, record, cancelled=cancelled)
        if result["recovery"]:
            result["verified_hashes"] = len(
                set(checks) | set(result["recovery"]["product"]["extra_hashes"])
            )
        result["notice"] = (
            "Attempt failed or was cancelled. A verified native prefix is available for continuation in a new attempt; scientific review remains pending."
            if result["recovery"]
            else "Attempt failed or was cancelled. Saved diagnostics remain available; no verified downstream handoff is offered."
        )
        return result
    if kind == "registration_execution_plan":
        if plan.get("operation") == "atlas_qc":
            from .registration_qc import verify_bundle

            verify_bundle(folder / "atlas-qc.json", cancelled)
            result["products"] = record["validation"]["products"]
        elif "registered.nii.gz" in record["outputs"]:
            result["products"] = [
                dict(
                    kind="registration_alignment",
                    path=str(folder / "registered.nii.gz"),
                    sha256=record["outputs"]["registered.nii.gz"]["sha256"],
                    reference=plan["reference"]["image"],
                    reference_sha256=plan["reference"]["sha256"],
                    categorical=plan.get("bound", {}).get("moving", {}).get("kind")
                    in {"atlas_labels", "roi"},
                    label="Inspect registered result / fixed reference",
                )
            ]
        return result
    if kind in {
        "mrtrix_connectome_execution_plan",
        "mrtrix_anatomy_execution_plan",
        "mrtrix_sift2_execution_plan",
    }:
        result["products"] = record["validation"]["products"]
        return result
    if kind == "mrtrix_tracking_execution_plan":
        for row in plan["bindings"]:
            for role, name in row["outputs"].items():
                if role in {"density", "reference"}:
                    continue
                result["products"].append(
                    dict(
                        label=name,
                        path=str(folder / name),
                        kind="tractogram" if role == "tracts" else "scalar",
                        sha256=record["outputs"][name]["sha256"],
                        reference=str(folder / row["outputs"]["reference"])
                        if role == "tracts" and "reference" in row["outputs"]
                        else None,
                        subject_id=row["source"]["declarations"].get("subject_id"),
                        space_id=row["source"]["declarations"].get("space_id"),
                        reference_sha256=record["outputs"][row["outputs"]["reference"]]["sha256"]
                        if role == "tracts" and "reference" in row["outputs"]
                        else None,
                    )
                )
        return result
    if kind == "mrtrix_csd_execution_plan":
        result["notice"] = (
            "CSD numerical verification passed; scientific review remains required. FOD products open typed metadata; scalar coefficient/compartment previews remain explicitly labelled."
        )
        for row in plan["bindings"]:
            for role, name in row["outputs"].items():
                product_kind = (
                    "fod"
                    if role == "wm_fod"
                    else "response"
                    if role.endswith("_response")
                    else "tissue_scalar"
                )
                result["products"].append(
                    dict(
                        label=name,
                        path=str(folder / name),
                        kind=product_kind,
                        sha256=record["outputs"][name]["sha256"],
                        tissue="wm" if role == "wm_fod" else role.removesuffix("_response"),
                        normalization="completed"
                        if row["algorithm"] == "mtnormalise"
                        else "not_done",
                        sh_basis="mrtrix3_real_symmetric",
                        sh_frame="world_ras",
                    )
                )
        for export in plan["exports"]:
            name = export["destination"]
            result["products"].append(
                dict(
                    label=name,
                    kind="scalar",
                    path=str(folder / name),
                    sha256=record["outputs"][name]["sha256"],
                    scalar_meaning="FOD zeroth coefficient C00 (not a glyph)"
                    if export["kind"] == "fod_c00"
                    else "Tissue-compartment amplitude (not a calibrated probability)",
                )
            )
        return result
    if kind == "mrtrix_dti_execution_plan":
        result["notice"] = (
            "DTI numerical verification passed; scientific review remains required. Open scalar/vector products in Diffusion Models, or load the exported scalar maps in Image Viewer."
        )
        result["dti_products"] = plan["exports"]
        for export in plan["exports"]:
            name = export["destination"]
            if name.endswith(("-tensor.nii.gz", "-EIGENVALUES.nii.gz")):
                continue
            product = {
                "label": name,
                "path": str(folder / name),
                "kind": "scalar",
                "sha256": record["outputs"][name]["sha256"],
            }
            if name.endswith("-V1.nii.gz"):
                fa = name.replace("-V1.nii.gz", "-FA.nii.gz")
                if fa not in record["outputs"]:
                    continue
                product.update(
                    kind="dti_vector",
                    fa=str(folder / fa),
                    fa_sha256=record["outputs"][fa]["sha256"],
                )
            result["products"].append(product)
        return result
    prior = ProcessingHistory.from_dict(plan["history"])
    done = set()
    if kind == "mrtrix_native_execution_plan":
        done = set(plan["operations"]) & {"denoise", "gibbs"}
    elif kind == "fsl_correction_plan":
        done = {"susceptibility", "eddy", "motion", "interpolation"}
    elif kind == "mrtrix_ants_bias_plan":
        done = {"bias"}
    claims = tuple(c for c in prior.claims if c.stage not in done) + tuple(
        HistoryClaim(
            s,
            True,
            "inferred",
            f"Completion derived from hash-checked executed producer {path}; SHA256 {digest}",
            "high",
            record.get("finished_at", "")[:10],
        )
        for s in sorted(done)
    )
    history = ProcessingHistory(claims, True).to_dict()

    def add(label, paths, selected_history=history):
        """Only propose images and gradients already bound by verified evidence."""
        paths = {k: str(v) for k, v in paths.items() if v}
        if not {"dwi", "bval", "bvec"} <= set(paths):
            raise ValueError("Output handoff lacks gradients")
        if not all(str(Path(v).resolve()) in checks for v in paths.values()):
            raise ValueError("Output handoff is not bound by the producer hashes")
        result["products"].append(
            {
                "label": label,
                "paths": paths,
                "sha256": {
                    role: checks[str(Path(value).resolve())] for role, value in paths.items()
                },
                "history": selected_history,
                "source_manifest": str(path),
                "source_sha256": digest,
            }
        )

    if kind == "mrtrix_native_execution_plan":
        operation = "gibbs" if "gibbs" in plan["operations"] else "denoise"
        paths = {
            "dwi": folder / (operation + ".nii"),
            "bval": folder / "native.bval",
            "bvec": folder / "native.bvec",
            "json": plan["input_paths"].get("json"),
        }
        add("Native " + operation + " output", paths)
        result["handoff"]["native_record"] = str(path)
    elif kind == "fsl_correction_plan":
        gate = record.get("validation", {}).get("correction_gate", {})
        if gate.get("technical_checks_passed") is not True:
            raise ValueError("Correction gate does not authorize downstream handoff")
        add(
            "EDDY-corrected native DWI",
            {
                "dwi": folder / "correction/eddy.nii.gz",
                "bval": folder / "correction/corrected.bval",
                "bvec": folder / "correction/corrected.bvec",
            },
        )
        result["handoff"].update(
            correction_record=str(folder / "eddy-producer.json"),
            correction_gate=str(folder / "correction-gate.json"),
            fitting_mask=str(folder / "evaluation/mask.nii.gz"),
        )
    elif kind == "mrtrix_ants_bias_plan":
        add(
            "Final native N4 DWI",
            {
                "dwi": folder / "bias-corrected.nii",
                "bval": folder / "corrected.bval",
                "bvec": folder / "corrected.bvec",
            },
        )
        result["handoff"].update(diffusion_manifest=str(path), parent_manifest=str(path))
    elif kind == "hcp_matched_combination_plan":
        add("All corrected parent measurements", plan["input_paths"], prior.to_dict())
        add(
            "Separate matched AP/PA derivative",
            {
                "dwi": folder / "combined.nii.gz",
                "bval": folder / "combined.bval",
                "bvec": folder / "combined.bvec",
            },
        )
    else:
        add("Unchanged native diffusion context", plan["input_paths"])
        if kind == "fsl_anatomy_plan" and plan.get("anatomical_operation") == "anatomy":
            result["handoff"]["anatomy_manifest"] = str(path)
    return result


def load_verified_product(
    product, loader, *, policy, cancelled=lambda: False, progress=lambda _: None
):
    """Bind the normal loader to a saved output snapshot; changed bytes void history."""
    from .dataset import DatasetPaths

    paths = DatasetPaths(**product["paths"])
    expected = product["sha256"]
    if set(expected) != set(paths.supplied()):
        raise ValueError("Saved output hash roles do not match its selected inputs")

    def verify():
        if hash_file(Path(product["source_manifest"]), cancelled) != product["source_sha256"]:
            raise ValueError("Saved producer changed; reopen the run before loading history")
        for path, digest in product.get("extra_hashes", {}).items():
            if hash_file(Path(path), cancelled) != digest:
                raise ValueError("External provenance changed; review it before loading")
        for role, path in paths.supplied().items():
            if hash_file(path, cancelled) != expected[role]:
                raise ValueError(
                    "Saved output changed; cannot attach its earlier processing history"
                )

    verify()
    result = loader.load(paths, policy=policy, cancelled=cancelled, progress=progress)
    try:
        verify()
    except BaseException:
        if result.dataset is not None:
            result.dataset.close()
        raise
    return result
