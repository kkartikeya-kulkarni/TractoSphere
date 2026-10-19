"""Atlas rule preparation for the existing MRtrix tracking compiler/executor.

prepare_roi_filter writes separate exact binary label/union snapshots, then
returns an ExecutionPlan for explicit review. execute_plan is the only executor.
No display geometry, smoothing or preview streamline subset enters this module.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import nibabel as nib
import numpy as np

from .execution import _publish_json, hash_file
from .process import check_cancel
from .scientific import ImportRequest, Task, import_artifact
from .scientific.tracking import prepare_tracking
from .storage import require_local_processing


def prepare_roi_filter(
    tractogram,
    atlas,
    reference,
    folder,
    *,
    include=(),
    exclude=(),
    mode="OR",
    ends_only=False,
    declarations=None,
    nthreads=2,
    tool_directory=None,
    cancelled=lambda: False,
    progress=lambda _: None,
):
    """Build native full-source filtering with exact IDs and explicit space evidence.

    OR uses one exact union; AND/ordered use separate unmodified label masks.
    Exclusion uses a union because ANY exclusion rejects. Ordered waypoints follow
    the saved streamline vertex direction in installed MRtrix3 3.0.5. Endpoint
    tests cannot be combined with ordered waypoints. No -number preview cap.
    """
    folder = Path(folder).expanduser().resolve()
    tractogram, atlas, reference = (
        Path(p).expanduser().resolve() for p in (tractogram, atlas, reference)
    )
    require_local_processing([folder, tractogram, atlas, reference])
    if mode not in {"OR", "AND", "ORDERED"}:
        raise ValueError("Filter mode must be OR, AND or ORDERED")
    include = list(dict.fromkeys(include))
    exclude = list(dict.fromkeys(exclude))
    if not include and not exclude:
        raise ValueError("Assign inclusion or exclusion labels before filtering")
    if len(include) > 256:
        raise ValueError("At most 256 individual inclusion waypoints per operation")
    if any(type(i) is not int or i <= 0 for i in include + exclude):
        raise ValueError(
            "Use original positive integer atlas label IDs; background is not a parcel"
        )
    if mode == "ORDERED" and ends_only:
        raise ValueError("Ordered waypoints and endpoint-only filtering are distinct operations")
    decl = dict(declarations or {})
    if folder.exists():
        raise FileExistsError("Choose a new immutable filtering directory")
    if any(folder.is_relative_to(p.parent) for p in (tractogram, atlas, reference)):
        raise ValueError("Filtering outputs must be outside source directories")
    if shutil.disk_usage(folder.parent).free < tractogram.stat().st_size + 5 * 1024**3:
        raise ValueError(
            "Insufficient free storage: reserve source tractogram size plus 5 GiB headroom"
        )
    progress("Validating complete source tractogram, atlas labels and exact reference…")
    a = import_artifact(
        ImportRequest("atlas_labels", atlas, declarations=decl), cancelled=cancelled
    )
    common = {
        k: v
        for k, v in decl.items()
        if k
        not in {
            "qc_association",
            "reference_record",
            "registration_record",
            "registration_qc",
            "registration_diagnostic",
        }
    }
    refdecl = dict(common)
    from .registration_review import known_bundles

    qc_paths = (
        [decl["registration_qc"]] if decl.get("registration_qc") else known_bundles(a.to_dict())
    )
    if qc_paths:
        from .registration_qc import verify_bundle

        bundle, _ = verify_bundle(qc_paths[-1], cancelled)
        if hash_file(reference, cancelled) != hash_file(
            Path(bundle["reference"]["path"]), cancelled
        ):
            raise ValueError("Registration target differs from exact selected reference")
        refdecl = bundle["reference"]["declarations"]
    r = import_artifact(
        ImportRequest("anatomy", reference, declarations=refdecl), cancelled=cancelled
    )
    # Require exact array correspondence here; native adapters may otherwise accept
    # voxel-axis permutations, which would need explicit snapshot reindexing.
    ag, rg = a.to_dict()["geometry"], r.to_dict()["geometry"]
    if ag["shape"][:3] != rg["shape"][:3] or not np.allclose(
        ag["affine_mm"], rg["affine_mm"], atol=1e-5
    ):
        raise ValueError(
            "Atlas snapshot requires identical voxel indexing; use a separately verified derivative"
        )
    from .registration_review import require_registration

    eligibility = require_registration(a, r, cancelled=cancelled)
    ac = {ag.get(k) for k in ("qform_code", "sform_code")} - {0, None}
    rc = {rg.get(k) for k in ("qform_code", "sform_code")} - {0, None}
    if ac & {3, 4} and rc and not ac & rc:
        raise ValueError(
            "Atlas standard-space header conflicts with reference; review registration first"
        )
    t = import_artifact(
        ImportRequest("tractogram", tractogram, declarations=common), cancelled=cancelled
    )
    if tractogram.suffix.lower() != ".tck":
        raise ValueError(
            "Native filtering requires TCK. Use the existing verified TRK-to-TCK export; no implicit conversion."
        )
    image = nib.load(atlas)
    labels = np.asanyarray(image.dataobj)
    values = set(int(v) for v in np.unique(labels) if v > 0)
    unknown = set(include + exclude) - values
    if unknown:
        raise ValueError("IDs absent from atlas: " + ", ".join(map(str, sorted(unknown))))
    check_cancel(cancelled)
    folder.mkdir()
    inputs = folder / "inputs"
    inputs.mkdir()
    record = {
        "schema": "tractosphere.roi-filter/1",
        "source": str(tractogram),
        "source_sha256": hash_file(tractogram, cancelled),
        "source_streamlines": t.to_dict()["metadata"]["streamlines"],
        "evaluated_scope": "complete source file, independent of rendered preview",
        "atlas": str(atlas),
        "atlas_sha256": hash_file(atlas, cancelled),
        "reference": str(reference),
        "reference_sha256": hash_file(reference, cancelled),
        "include_ids": include,
        "exclude_ids": exclude,
        "mode": mode,
        "ends_only": bool(ends_only),
        "declarations": decl,
        "registration_eligibility": eligibility,
        "display_settings_used": False,
        "resampled": False,
        "mask_records": [],
    }
    # Keep the original atlas and hash-bound registration evidence in the native
    # plan even though tckedit consumes exact binary snapshots, not label voxels.
    artifacts = [t, r, a]
    bindings = {"tracts": t.id, "reference": r.id, "original_atlas": a.id}

    def snapshot(ids, role, index):
        check_cancel(cancelled)
        target = inputs / f"{role}-{index}.nii.gz"
        mask = np.isin(labels, ids).astype("u1")
        out = nib.Nifti1Image(mask, image.affine, image.header.copy())
        out.set_data_dtype("u1")
        out.set_qform(image.get_qform(), int(image.header["qform_code"]))
        out.set_sform(image.get_sform(), int(image.header["sform_code"]))
        nib.save(out, target)
        provenance = {
            **{k: v for k, v in record.items() if k != "mask_records"},
            "labels": ids,
            "operation": "exact label union" if len(ids) > 1 else "exact label equality",
            "mask_sha256": hash_file(target, cancelled),
            "voxels": int(mask.sum()),
        }
        sidecar = target.with_suffix(".json")
        _publish_json(sidecar, provenance)
        roi = import_artifact(
            ImportRequest("roi", target, sidecar=sidecar, declarations=common), cancelled=cancelled
        )
        artifacts.append(roi)
        bindings[f"{role}_{index}"] = roi.id
        record["mask_records"].append(
            {
                "path": str(target),
                "sha256": provenance["mask_sha256"],
                "ids": ids,
                "role": role,
                "voxels": provenance["voxels"],
            }
        )

    if include:
        if mode == "OR":
            snapshot(include, "include", 1)
        else:
            for index, label in enumerate(include, 1):
                snapshot([label], "ordered" if mode == "ORDERED" else "include", index)
    if exclude:
        snapshot(exclude, "exclude", 1)
    _publish_json(folder / "filter-configuration.json", record)
    task = Task(
        "roi-filter",
        "tckedit",
        bindings,
        {"min_length_mm": 0, "max_length_mm": 1000000, "ends_only": "on" if ends_only else "off"},
    )
    plan = prepare_tracking(
        tuple(artifacts),
        (task,),
        folder / "processing",
        nthreads=nthreads,
        tool_directory=tool_directory,
        cancelled=cancelled,
    )
    _publish_json(folder / "review-plan.json", plan.to_dict())
    return plan


def export_roi_result(result: dict, destination: Path, *, cancelled=lambda: False) -> Path:
    """Export an unchanged verified TCK and its QC/provenance without overwriting.

    Cancellation retains a labelled partial copy. The published TCK is linked
    exclusively only after its bytes match the validated derivative hash.
    """
    import os
    from uuid import uuid4

    if result.get("status") != "validated_pending_qc":
        raise ValueError("Only a verified completed filtering result can be exported")
    product = next(p["artifact"] for p in result["validation"]["products"] if p["role"] == "tracts")
    source = Path(product["path"])
    expected = product["sources"][0]["sha256"]
    destination = Path(destination).expanduser().absolute()
    sidecar = destination.with_suffix(".roi-filter.json")
    require_local_processing([source, destination])
    if destination.suffix.lower() != ".tck":
        raise ValueError("Export uses native TCK coordinates; choose a .tck destination")
    if destination.exists() or destination.is_symlink() or sidecar.exists():
        raise FileExistsError("Export target or provenance exists; choose a new name")
    if shutil.disk_usage(destination.parent).free < source.stat().st_size + 1024**3:
        raise ValueError("Export requires output size plus 1 GiB free headroom")
    if hash_file(source, cancelled) != expected:
        raise ValueError("Verified filtering output changed; export blocked")
    partial = destination.with_name(destination.name + "." + uuid4().hex + ".partial")
    with source.open("rb") as src, partial.open("xb") as dst:
        while chunk := src.read(8 * 1024**2):
            check_cancel(cancelled)
            dst.write(chunk)
    if hash_file(partial, cancelled) != expected or hash_file(source, cancelled) != expected:
        raise ValueError("Export integrity failed; diagnostic partial retained")
    check_cancel(cancelled)
    # Sidecar and output never replace another researcher's files.
    _publish_json(
        sidecar,
        {
            "schema": "tractosphere.roi-export/1",
            "sha256": expected,
            "source_derivative": str(source),
            "result": result,
        },
    )
    os.link(partial, destination)
    partial.unlink()
    return destination
