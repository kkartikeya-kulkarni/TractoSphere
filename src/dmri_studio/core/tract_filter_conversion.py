"""Explicit, geometry-bound TRK to TCK derivative for validated filtering.

This is a format conversion, not anatomical registration or scientific approval.
The original TRK and reference are immutable. Attribute-bearing TRK cannot be
converted because TCK would silently discard its scalars or properties.
"""

import json
import os
import tempfile
from pathlib import Path

import nibabel as nib
import numpy as np

from dmri_studio import __version__

from .execution import hash_file
from .process import check_cancel
from .scene import ImageVolume, TractGroup
from .scientific import ImportRequest, import_artifact
from .tract_index import IndexedStreamlines


def convert_verified_trk(
    source, reference, destination, *, cancelled=lambda: False, progress=lambda _: None
):
    """Export every source streamline in RAS+ mm and verify the full round trip."""
    chosen = Path(destination).expanduser().absolute()
    destination = chosen.parent.resolve() / chosen.name
    source_path = Path(source.payload["path"]).resolve()
    reference_path = Path(reference.payload["path"]).resolve()
    sidecar = destination.with_suffix(".conversion.json")
    if source.kind != "tractogram" or source_path.suffix.lower() != ".trk":
        raise ValueError("Select a scientifically imported TRK tractogram")
    if reference.kind != "anatomy" or not str(reference_path).lower().endswith((".nii", ".nii.gz")):
        raise ValueError("Select an imported NIfTI reference image for the TRK grid")
    if destination.suffix.lower() != ".tck":
        raise ValueError("Choose a new .tck derivative path")
    if (
        destination.exists()
        or destination.is_symlink()
        or sidecar.exists()
        or sidecar.is_symlink()
        or not destination.parent.is_dir()
    ):
        raise ValueError("Choose a new TCK and conversion-record path in an existing directory")
    if any(destination.is_relative_to(path.parent) for path in (source_path, reference_path)):
        raise ValueError("Save the derivative outside original scientific input directories")
    for key in ("subject_id", "space_id"):
        left = source.payload["declarations"].get(key)
        right = reference.payload["declarations"].get(key)
        if not left or left != right:
            raise ValueError(f"TRK conversion requires one declared matching {key}")
    sessions = {item.payload["declarations"].get("session_id") for item in (source, reference)} - {
        None,
        "",
    }
    if len(sessions) > 1:
        raise ValueError("TRK and reference declare different sessions")
    check_cancel(cancelled)
    source.assert_current(cancelled)
    reference.assert_current(cancelled)
    image = ImageVolume(reference_path)
    if len(image.shape) != 3 or not image.physical_units_known or image.affine_source == "fallback":
        raise ValueError("TRK conversion needs a 3D reference with documented physical geometry")
    progress("Checking TRK header and every original streamline…")
    indexed = IndexedStreamlines(source_path, cancelled=cancelled)
    output_created = False
    sidecar_created = False
    try:
        header = indexed.header
        if indexed.scalar_count or indexed.property_count:
            raise ValueError(
                "TRK contains per-point or per-streamline attributes that TCK cannot preserve"
            )
        if tuple(int(v) for v in header["dimensions"]) != tuple(image.shape[:3]) or not np.allclose(
            np.asarray(header["voxel_to_rasmm"], float), image.affine, atol=1e-5, rtol=0
        ):
            raise ValueError(
                "TRK header grid differs from the selected reference; no transform is inferred"
            )
        group = TractGroup(source_path, indexed, range(len(indexed)), len(indexed))
        group.export(destination, image, cancelled=cancelled)
        output_created = True
        progress("Verifying every converted RAS+ streamline…")
        converted = IndexedStreamlines(destination, cancelled=cancelled)
        try:
            if len(converted) != len(indexed):
                raise ValueError("Converted TCK changed the streamline count")
            for i in range(len(indexed)):
                check_cancel(cancelled)
                if indexed.counts[i] != converted.counts[i] or not np.allclose(
                    indexed[i], converted[i], atol=1e-4, rtol=0
                ):
                    raise ValueError("Converted TCK changed streamline coordinates")
        finally:
            converted.close()
        source.assert_current(cancelled)
        reference.assert_current(cancelled)
        record = dict(
            schema="tractosphere.trk-to-tck/1",
            converter="TractGroup.export full-source RAS+ round trip",
            tractosphere_version=__version__,
            nibabel_version=nib.__version__,
            source=str(source_path),
            source_sha256=next(
                s["sha256"]
                for s in source.payload["sources"]
                if Path(s["path"]).resolve() == source_path
            ),
            reference=str(reference_path),
            reference_sha256=next(
                s["sha256"]
                for s in reference.payload["sources"]
                if Path(s["path"]).resolve() == reference_path
            ),
            output=str(destination),
            output_sha256=hash_file(destination, cancelled),
            streamlines=len(indexed),
            coordinates="RAS+ mm; all points compared within 0.0001 mm",
            scientific_acceptance="unassigned",
        )
        with tempfile.NamedTemporaryFile(
            "w", dir=sidecar.parent, suffix=".json", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, indent=2)
        try:
            os.link(temporary, sidecar)
            sidecar_created = True
        finally:
            temporary.unlink(missing_ok=True)
        check_cancel(cancelled)
        return import_artifact(
            ImportRequest(
                "tractogram",
                destination,
                sidecar=sidecar,
                declarations=dict(source.payload["declarations"]),
                provenance="Geometry-bound full-source TRK to TCK conversion; anatomical correctness and scientific acceptance remain unverified.",
            ),
            cancelled=cancelled,
            progress=progress,
        )
    except BaseException:
        if output_created:
            destination.unlink(missing_ok=True)
        if sidecar_created:
            sidecar.unlink(missing_ok=True)
        raise
    finally:
        indexed.close()
