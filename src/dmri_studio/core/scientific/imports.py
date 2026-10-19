"""Strict, read-only scientific imports; run in a worker for large local inputs.

NIfTI DWI reuses DatasetLoader. Other images are scanned in bounded slabs.
MIF intentionally reuses the tested, uncompressed single-file Float32/Bit reader;
unsupported MIF storage is refused, never silently converted. No external MRI
command, normalization, canonicalization or resampling occurs during import.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict
from itertools import product
from pathlib import Path

import nibabel as nib
import numpy as np

from .._mif_checks import produced_mif
from ..atlas import find_table, geometry_record, label_table
from ..dataset import DatasetPaths, SourceFingerprint
from ..execution import hash_file
from ..gradients import GradientTable
from ..loading import DatasetLoader
from ..process import check_cancel
from ..scene import ImageVolume
from ..tract_index import IndexedStreamlines
from ..validation import ValidationPolicy, json_compatible, utc_now
from .contracts import Artifact, ImportRequest, artifact_id


def _gradients(table: GradientTable, count: int) -> dict:
    """One centralized validator, preserving input frame, volume order and values."""
    policy = ValidationPolicy()
    checks = table.validate(expected_volumes=count, policy=policy)
    if any(c.status == "fail" for c in checks):
        raise ValueError(
            "Invalid gradients: " + "; ".join(c.message for c in checks if c.status == "fail")
        )
    return dict(
        convention=table.convention,
        bvals=table.bvals.tolist(),
        bvecs=table.bvecs.tolist(),
        norms=table.norms().tolist(),
        shells=[s.to_dict() for s in table.shells()],
        checks=[c.to_dict() for c in checks],
        policy=asdict(policy),
    )


def _scan(array, kind: str, cancelled: Callable, progress: Callable) -> dict:
    """Read all components in <=~8MiB slabs; never load a complete 4D dataset."""
    shape = tuple(array.shape)
    channels = int(np.prod(shape[3:])) if len(shape) > 3 else 1
    if channels > 1_000_000:
        raise ValueError("Component count exceeds bounded-import limits")
    # A volume-contiguous MIF must not be read as thousands of tiny spatial
    # tiles spanning every timepoint: that cycles through gigabytes of mapped
    # pages per tile on ordinary workstations. Keep <=~8MiB numeric blocks but
    # finish each volume first. Interleaved/component-fast storage and coupled
    # probability/channel checks retain the existing spatial-tile traversal.
    edge = max(1, int((1_000_000 // max(1, channels)) ** (1 / 3)))
    strides = getattr(array, "strides", ())
    volume_major = (
        len(shape) == 4
        and kind in {"dwi", "output"}
        and len(strides) == 4
        and abs(strides[3]) >= max(abs(s) for s in strides[:3])
    )
    if volume_major:
        width = min(shape[0], 1_000_000)
        height = min(shape[1], max(1, 1_000_000 // width))
        depth = max(1, 1_000_000 // (width * height))
        starts = (
            (x, y, z, volume)
            for volume in range(shape[3])
            for z in range(0, shape[2], depth)
            for y in range(0, shape[1], height)
            for x in range(0, shape[0], width)
        )
    else:
        starts = product(*(range(0, n, edge) for n in shape[:3]))
    labels, counts, nonzero = set(), {}, 0
    low, high = float("inf"), float("-inf")
    for position in starts:
        check_cancel(cancelled)
        x, y, z = position[:3]
        if volume_major:
            volume = position[3]
            block = np.asarray(
                array[x : x + width, y : y + height, z : z + depth, volume], dtype=np.float64
            )
        else:
            block = np.asarray(
                array[x : x + edge, y : y + edge, z : z + edge, ...], dtype=np.float64
            )
        if not np.isfinite(block).all():
            raise ValueError("Nonfinite image values; source was not repaired")
        low, high = min(low, float(block.min())), max(high, float(block.max()))
        nonzero += int(np.count_nonzero(block))
        if kind in {"mask", "roi", "atlas_labels"}:
            if np.any(block < 0) or not np.equal(block, np.rint(block)).all():
                raise ValueError(
                    "Discrete masks/labels require nonnegative integer values; continuous probabilities are a different type"
                )
            if kind == "mask" and np.any((block != 0) & (block != 1)):
                raise ValueError("Brain mask must be binary; no threshold was applied")
            ids, sizes = np.unique(block, return_counts=True)
            for value, count in zip(ids, sizes, strict=True):
                if value > 0:
                    labels.add(int(value))
                    counts[str(int(value))] = counts.get(str(int(value)), 0) + int(count)
            if len(labels) > 10000:
                raise ValueError("More than 10,000 labels; explicit specialized import needed")
        if kind == "density" and low < 0:
            raise ValueError("Density contributions cannot be negative")
        if kind in {"5tt", "gmwmi", "atlas_probability"} and (low < 0 or high > 1):
            raise ValueError("Probabilities must be in [0,1]; no scale or normalization guessed")
        if kind == "5tt":
            sums = block.sum(axis=3)
            if not np.all((np.abs(sums) <= 1e-3) | (np.abs(sums - 1) <= 1e-3)):
                raise ValueError(
                    "5TT channels must sum to 1 in brain or 0 outside (tolerance 0.001)"
                )
        progress(
            f"Inspecting {kind} volume {volume + 1}/{shape[3]}: "
            f"{min(z + depth, shape[2])}/{shape[2]} slices"
            if volume_major
            else f"Inspecting {kind} voxel slabs: {min(z + edge, shape[2])}/{shape[2]} slices"
        )
    if (
        kind in {"mask", "roi", "atlas_labels", "atlas_probability", "5tt", "gmwmi", "fod"}
        and not nonzero
    ):
        raise ValueError("Input contains no nonzero region/signal")
    return dict(
        coverage="All image values scanned in bounded slabs; scientific quality not assessed",
        minimum=low,
        maximum=high,
        nonzero_values=nonzero,
        label_ids=sorted(labels),
        label_voxel_counts=counts,
    )


def _dimensions(kind: str, shape: tuple, declarations: dict, dtype) -> dict:
    """Require explicit tensor/SH/5TT semantics in addition to component counts."""
    result = {}
    if kind in {"mask", "roi", "atlas_labels", "anatomy", "gmwmi"} and len(shape) != 3:
        raise ValueError(f"{kind} requires a 3D image")
    if kind == "tissue_scalar" and not (len(shape) == 3 or len(shape) == 4 and shape[3] == 1):
        raise ValueError("Tissue compartment requires 3D or singleton-component 4D storage")
    if kind == "dwi" and len(shape) != 4:
        raise ValueError("DWI requires 4D image and matching gradients")
    if kind == "tensor":
        if len(shape) != 4 or shape[3] != 6:
            raise ValueError(
                "Stage 1 tensor contract requires six 4D components; other encodings need a future explicit adapter"
            )
        if declarations.get("tensor_layout") not in {
            "mrtrix_D11_D22_D33_D12_D13_D23",
            "fsl_Dxx_Dxy_Dxz_Dyy_Dyz_Dzz",
        }:
            raise ValueError("Declare the tensor component order explicitly")
        if (
            declarations.get("tensor_frame") not in {"world_ras", "fsl_image_axes"}
            or declarations.get("tensor_units") != "mm2/s"
        ):
            raise ValueError(
                "Declare tensor frame and units mm2/s; no convention conversion is performed"
            )
    if kind == "fod":
        if len(shape) != 4:
            raise ValueError("FOD requires 4D SH coefficient image")
        orders = [order for order in range(2, 32, 2) if (order + 1) * (order + 2) // 2 == shape[3]]
        if (
            not orders
            or declarations.get("sh_basis") != "mrtrix3_real_symmetric"
            or declarations.get("sh_frame") != "world_ras"
        ):
            raise ValueError(
                "Declare MRtrix3 real symmetric SH in world RAS with a valid even-order coefficient count (lmax>=2)"
            )
        if declarations.get("tissue") != "wm":
            raise ValueError(
                "Declare WM FOD tissue; isotropic compartments are tissue_scalar inputs"
            )
        result["lmax"] = orders[0]
    if kind == "density":
        if not (len(shape) == 3 or len(shape) == 4 and shape[3] == 3):
            raise ValueError("Density requires 3D scalar or three world-direction channels")
        result["channel_semantics"] = (
            "scalar streamline contributions"
            if len(shape) == 3
            else "absolute world RAS LR/AP/SI directional contributions"
        )
    if kind == "5tt":
        if (
            len(shape) != 4
            or shape[3] != 5
            or declarations.get("channels") != "cortical_gm,subcortical_gm,wm,csf,pathology"
        ):
            raise ValueError("Declare the exact MRtrix 5TT five-channel order")
        if np.dtype(dtype).kind != "f" or np.dtype(dtype).itemsize != 4:
            raise ValueError("MRtrix 5TT contract requires 32-bit floating-point storage")
    if kind == "atlas_probability":
        threshold = declarations.get("threshold")
        if (
            len(shape) != 4
            or isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not 0 < threshold <= 1
        ):
            raise ValueError(
                "Probability atlas requires 4D region maps and an explicit threshold in (0,1]; percent-scale data must be converted separately"
            )
    return result


def import_artifact(
    request: ImportRequest, *, cancelled=lambda: False, progress=lambda _: None
) -> Artifact:
    """Import independently of preprocessing; preserve unknown external provenance."""
    check_cancel(cancelled)
    if request.kind == "spatial_transform":
        from ..spatial import import_transform

        return import_transform(request, cancelled=cancelled, progress=progress)
    if request.kind == "connectome_matrix":
        from .connectome import import_bundle

        return import_bundle(request, cancelled=cancelled, progress=progress)
    declarations = json_compatible(request.declarations)
    if not isinstance(declarations, dict):
        raise ValueError("Declarations must be a JSON object")
    if declarations.get("processing_state", "unknown") not in {"raw", "preprocessed", "unknown"}:
        raise ValueError("processing_state must be raw, preprocessed or unknown")
    paths = [p for p in (request.path, request.bval, request.bvec, request.sidecar) if p]
    if request.kind != "dwi" and (request.bval or request.bvec):
        raise ValueError("Gradient sidecars belong only to DWI inputs")
    original = {p: SourceFingerprint.capture(p) for p in paths}
    for p in paths:
        if not p.is_file():
            raise ValueError(f"Input is not a readable file: {p.name}")
    record = dict(
        schema="tractosphere.artifact/1",
        kind=request.kind,
        path=str(request.path),
        declarations=declarations,
        provenance=dict(
            statement=request.provenance,
            verification="User declaration; not authenticated processing evidence",
            scientific_acceptance="unknown",
        ),
        imported_at=utc_now(),
        request=dict(
            kind=request.kind,
            path=str(request.path),
            bval=str(request.bval) if request.bval else None,
            bvec=str(request.bvec) if request.bvec else None,
            sidecar=str(request.sidecar) if request.sidecar else None,
            declarations=declarations,
            provenance=request.provenance,
        ),
        geometry=None,
        gradients=None,
        metadata={},
        verification={},
    )
    suffix = str(request.path).lower()
    if request.kind == "tractogram":
        if not suffix.endswith((".tck", ".trk")):
            raise ValueError("Supported tractography imports: .tck and .trk")
        check_cancel(cancelled)
        index = IndexedStreamlines(request.path, cancelled=cancelled)
        try:
            if not len(index):
                raise ValueError("Empty tractogram")
            record["metadata"] = dict(
                streamlines=len(index),
                coordinate_convention="RAS+ mm",
                bounds_min=index.lower.tolist(),
                bounds_max=index.upper.tolist(),
                anatomical_alignment="Unknown until associated reference/space evidence is reviewed",
            )
            record["verification"] = dict(
                coverage="All streamline records indexed; finite coordinates and payload structure checked; anatomical matching not established"
            )
        finally:
            index.close()
    elif request.kind == "response":
        from .responses import read_response

        record["metadata"] = read_response(
            request.path, declarations.get("tissue"), declarations.get("shells")
        )
        record["verification"] = dict(
            coverage="Complete finite response text, shell rows and tissue coefficient structure inspected; acquisition association requires validation"
        )
    elif request.kind == "streamline_weights":
        if request.path.stat().st_size > 32 * 1024**2:
            raise ValueError("Text contract exceeds 32MiB; specialized streaming importer needed")
        from .sift2 import read_weights

        values = read_weights(request.path)
        record["metadata"] = dict(
            rows=len(values),
            columns=1,
            tissue=declarations.get("tissue"),
            shells=declarations.get("shells"),
        )
        record["verification"] = dict(
            coverage="Complete numeric text inspected; source/model association unverified"
        )
        if declarations.get("weight_record"):
            # A reusable weight vector must bind exact tract bytes/order. Bare
            # numeric imports remain inspectable but cannot authorize weighting.
            from .records import read_json

            sidecar = Path(declarations["weight_record"]).expanduser().absolute()
            if sidecar.stat().st_size > 1024**2:
                raise ValueError("Oversized weight identity record")
            association = read_json(sidecar.read_text())
            if (
                association.get("schema") != "tractosphere.streamline-weights/1"
                or association.get("representation") != "weight_factors"
                or association.get("ordering") != "exact source file streamline order"
                or association.get("streamlines") != len(values)
                or association.get("weights_sha256") != hash_file(request.path, cancelled)
                or Path(association.get("weights", "")).resolve() != request.path.resolve()
            ):
                raise ValueError(
                    "Weight identity record does not describe these exact weight factors"
                )
            for path, digest in (
                (sidecar, None),
                (Path(association["tractogram"]), association["tractogram_sha256"]),
            ):
                original[path] = SourceFingerprint.capture(path)
                if digest and hash_file(path, cancelled) != digest:
                    raise ValueError(
                        "Weight source tractogram changed; ordering association invalid"
                    )
                paths.append(path)
            record["metadata"]["weight_record"] = association
            record["verification"]["coverage"] = (
                "Finite nonnegative factors; exact source byte/order and sidecar hash binding verified; scientific acceptance unassigned"
            )
    else:
        mif = suffix.endswith(".mif")
        if not mif and not suffix.endswith((".nii", ".nii.gz")):
            raise ValueError(
                "Supported image contracts: NIfTI or tested uncompressed single-file MIF; no automatic conversion"
            )
        if mif and (request.bval or request.bvec or request.sidecar):
            raise ValueError(
                "MIF import uses its embedded raw dw_scheme; sidecar override is not supported"
            )
        if request.kind == "dwi" and not mif:
            if not request.bval or not request.bvec:
                raise ValueError("NIfTI DWI requires matching bval and bvec files")
            loaded = DatasetLoader().load(
                DatasetPaths(request.path, request.bval, request.bvec, request.sidecar),
                cancelled=cancelled,
            )
            if loaded.dataset is None:
                raise ValueError(
                    "DWI validation failed: "
                    + "; ".join(c.message for c in loaded.report.checks if c.status == "fail")
                )
            try:
                record["gradients"] = _gradients(loaded.dataset.gradients, loaded.dataset.n_volumes)
                record["verification"] = loaded.report.to_dict()
                record["metadata"]["acquisition"] = json_compatible(loaded.dataset.sidecar or {})
            finally:
                loaded.dataset.close()
        if mif:
            # The shared Bit reader expands packed bits; bound that exception to
            # 64MiB rather than claiming arbitrary-size packed input is lazy.
            with request.path.open("rb") as handle:
                header = handle.read(min(request.path.stat().st_size, 1_048_576)).split(
                    b"END\n", 1
                )[0]
            if b"datatype: Bit" in header and request.path.stat().st_size > 8 * 1024**2:
                raise ValueError(
                    "Packed Bit MIF imports are limited to 8MiB; explicit external conversion is required for larger masks"
                )
            context = produced_mif(
                request.path, allow_integer=request.kind in {"atlas_labels", "roi"}
            )
        else:
            image = ImageVolume(request.path)
            geometry = geometry_record(image)
            context = nullcontext((None, image.affine, image.image.dataobj))
        with context as (fields, affine, array):
            shape = tuple(array.shape)
            if mif:
                record["metadata"]["embedded_header"] = {
                    key: values for key, values in fields.items() if key != "dw_scheme"
                }
                geometry = dict(
                    shape=list(shape),
                    affine_mm=affine.tolist(),
                    voxel_sizes_mm=nib.affines.voxel_sizes(affine).tolist(),
                    orientation=list(nib.aff2axcodes(affine)),
                    units="mm",
                    affine_source="MIF transform + vox; signed layout preserved",
                )
                if request.kind == "dwi":
                    scheme = np.array(
                        [[float(x) for x in row.split(",")] for row in fields.get("dw_scheme", [])]
                    )
                    if scheme.ndim != 2 or scheme.shape != (shape[3] if len(shape) == 4 else 0, 4):
                        raise ValueError("MIF embedded dw_scheme missing or not one row per volume")
                    record["gradients"] = _gradients(
                        GradientTable(
                            scheme[:, 3],
                            scheme[:, :3],
                            source_layout="Nx3",
                            convention="mrtrix_world_ras",
                        ),
                        shape[3],
                    )
            record["geometry"] = geometry
            dtype = array.dtype
            record["metadata"].update(
                shape=list(shape),
                dtype=str(dtype),
                **_dimensions(request.kind, shape, declarations, dtype),
            )
            if request.kind != "dwi" or mif:
                record["verification"] = _scan(array, request.kind, cancelled, progress)
            if request.kind in {"roi", "atlas_labels"}:
                table = find_table(request.path)
                names = {}
                if table:
                    original[table] = SourceFingerprint.capture(table)
                    paths.append(table)
                    names, _, title = label_table(table)
                    record["metadata"].update(label_table=str(table), atlas_name=title)
                record["metadata"]["region_names"] = {
                    str(i): names.get(i, f"Region {i}") for i in record["verification"]["label_ids"]
                }
                voxel_mm3 = abs(float(np.linalg.det(affine[:3, :3])))
                record["metadata"]["region_volumes_mm3"] = {
                    i: count * voxel_mm3
                    for i, count in record["verification"]["label_voxel_counts"].items()
                }
    if request.kind == "atlas_labels" and declarations.get("registration_record"):
        from .records import read_json

        review_path = Path(declarations["registration_record"]).expanduser().resolve()
        if review_path.stat().st_size > 1024**2:
            raise ValueError("Registration review record exceeds 1 MiB")
        review = read_json(review_path.read_text())
        if review.get("schema") != "tractosphere.registered-atlas-review/1" or review.get(
            "atlas_sha256"
        ) != hash_file(request.path, cancelled):
            raise ValueError("Registration review does not bind this exact atlas")
        target = review.get("reference_sha256", "")
        if (
            not isinstance(target, str)
            or len(target) != 64
            or any(c not in "0123456789abcdef" for c in target)
        ):
            raise ValueError("Registration review requires an exact target reference SHA-256")
        if (
            review.get("spatial_review") not in {"reviewed", "rejected", "needs_review"}
            or not isinstance(review.get("evidence_hashes"), dict)
            or not review["evidence_hashes"]
        ):
            raise ValueError("Registration spatial review/evidence is incomplete")
        evidence = {
            str(review_path): hash_file(review_path, cancelled),
            **review["evidence_hashes"],
        }
        for name, digest in evidence.items():
            path = Path(name)
            original[path] = SourceFingerprint.capture(path)
            if hash_file(path, cancelled) != digest:
                raise ValueError("Registration evidence changed: " + path.name)
            paths.append(path)
        record["metadata"]["registration_review"] = review
    for key, schema in (
        ("reference_record", "tractosphere.diffusion-reference/1"),
        ("qc_association", "tractosphere.registered-atlas-qc-association/1"),
    ):
        if declarations.get(key):
            from .records import read_json

            side = Path(declarations[key]).resolve(strict=True)
            if side.stat().st_size > 8 * 1024**2:
                raise ValueError("Spatial provenance record exceeds 8 MiB")
            evidence = read_json(side.read_text())
            if evidence.get("schema") != schema or evidence.get("sha256") != hash_file(
                request.path, cancelled
            ):
                raise ValueError("Spatial provenance does not bind the selected image")
            for name, digest in {
                str(side): hash_file(side),
                **evidence.get("evidence_hashes", {}),
            }.items():
                path = Path(name)
                original[path] = SourceFingerprint.capture(path)
                if hash_file(path, cancelled) != digest:
                    raise ValueError("Spatial provenance evidence changed")
                paths.append(path)
            record["metadata"][key] = evidence
    sources = []
    for path in dict.fromkeys(paths):
        check_cancel(cancelled)
        progress(f"Hashing unchanged source {path.name}")
        sources.append(
            dict(path=str(path), size_bytes=path.stat().st_size, sha256=hash_file(path, cancelled))
        )
        if not original[path].unchanged(path):
            raise ValueError(f"Source changed during import: {path.name}")
    record["sources"] = sources
    record["id"] = artifact_id(request.kind, sources, declarations, request.provenance)
    if request.kind == "atlas_labels":
        from ..registration_review import registration_eligibility

        record["metadata"]["registration_eligibility"] = registration_eligibility(
            record, cancelled=cancelled
        )
    return Artifact(record)
