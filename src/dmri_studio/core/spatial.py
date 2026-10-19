"""Hash-bound image spaces and native transform records; no registration solver.

FLIRT matrices retain FSL scaled-voxel-mm semantics. MRtrix matrices retain
output-to-input RAS sampling semantics. Nonlinear fields are never inverted as
matrices. These records describe provenance, not automatic anatomical acceptance.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import nibabel as nib
import numpy as np

from .atlas import geometry_record
from .execution import _publish_json, hash_file
from .scene import ImageVolume
from .scientific.records import read_json

SPACES = (
    "DWI_NATIVE",
    "T1_NATIVE",
    "T2_NATIVE",
    "MNI_TEMPLATE",
    "ATLAS_NATIVE",
    "OTHER_REGISTERED_SPACE",
)
FORMATS = {
    "fsl_flirt": "FSL scaled voxel mm; moving-to-reference",
    "fsl_coefficients": "FSL FNIRT coefficients; reference-to-moving sampling",
    "fsl_relative": "FSL relative displacement mm; reference-to-moving sampling",
    "mrtrix_linear": "MRtrix RAS mm; reference-to-moving sampling",
    "itk_affine_text": "ITK LPS mm affine with fixed center; reference-to-moving sampling",
}


@dataclass(frozen=True)
class ImageSpace:
    """A named frame bound to its actual reference image, subject and acquisition."""

    kind: str
    id: str
    subject: str
    acquisition: str
    image: str
    sha256: str
    session: str = ""

    @classmethod
    def create(cls, path, kind, space_id, subject, acquisition, session=""):
        if kind not in SPACES or not all(
            isinstance(s, str) and s.strip() for s in (space_id, subject, acquisition)
        ):
            raise ValueError("Explicit space kind, ID, subject and acquisition are required")
        path = Path(path).resolve(strict=True)
        image = nib.load(path)
        geometry_record(ImageVolume(path))
        if image.header.get_xyzt_units()[0] != "mm":
            raise ValueError(
                "Registration reference requires explicit millimeter units; header rewriting is not a conversion"
            )
        if image.ndim != 3:
            raise ValueError("Space reference must be a 3D NIfTI, not the DWI time series")
        return cls(kind, space_id, subject, acquisition, str(path), hash_file(path), session)

    def verify(self):
        current = self.create(
            self.image, self.kind, self.id, self.subject, self.acquisition, self.session
        )
        if current != self:
            raise ValueError("Spatial reference changed")

    def to_dict(self):
        return dict(self.__dict__)


def matrix(path):
    a = np.loadtxt(path)
    if a.shape == (3, 4):
        a = np.vstack((a, [0, 0, 0, 1]))
    if (
        a.shape != (4, 4)
        or not np.isfinite(a).all()
        or not np.allclose(a[3], [0, 0, 0, 1], atol=1e-8)
        or np.linalg.det(a[:3, :3]) <= 1e-8
    ):
        raise ValueError(
            "Transform must be a finite nonsingular proper affine; reflections require separate review"
        )
    return a


def validate_transform_file(path, fmt):
    path = Path(path)
    if fmt not in FORMATS:
        raise ValueError(
            "Unsupported transform format (ANTs binary/composite/deformation import is not yet verified)"
        )
    if fmt in {"fsl_flirt", "mrtrix_linear"}:
        return dict(matrix=matrix(path).tolist())
    if fmt == "itk_affine_text":
        text = path.read_text()
        if (
            not text.startswith("#Insight Transform File V1.0")
            or text.count("Transform:") != 1
            or "AffineTransform_double_3_3" not in text
        ):
            raise ValueError(
                "Only a single explicit 3D ITK text affine is supported; binary .mat/HDF5 cannot be guessed"
            )
        rows = dict(
            line.split(":", 1)
            for line in text.splitlines()
            if ":" in line and not line.startswith("#")
        )
        p = np.fromstring(rows.get("Parameters", ""), sep=" ")
        c = np.fromstring(rows.get("FixedParameters", ""), sep=" ")
        if (
            p.size != 12
            or c.size != 3
            or not np.isfinite(np.r_[p, c]).all()
            or np.linalg.det(p[:9].reshape(3, 3)) <= 1e-8
        ):
            raise ValueError("Invalid ITK affine parameters/center")
        return dict(parameters=p.tolist(), center_lps_mm=c.tolist())
    image = nib.load(path)
    if image.ndim != 4 or image.shape[-1] != 3:
        raise ValueError("FSL warp/coefficient file must have three components")
    intent = int(image.header["intent_code"])
    if fmt == "fsl_coefficients" and intent not in {2007, 2008, 2009}:
        raise ValueError(
            "Not a recognized FSL coefficient intent; do not infer a warp from its filename"
        )
    for z in range(image.shape[2]):
        if not np.isfinite(np.asarray(image.dataobj[:, :, z, :])).all():
            raise ValueError("Nonfinite deformation coefficients")
    return dict(shape=list(image.shape), intent_code=intent)


def transform_record(
    path,
    source,
    target,
    fmt,
    *,
    software,
    version,
    evidence,
    review="QC_REVIEW_REQUIRED",
    parameters=None,
):
    """Build an explicit import record. evidence must describe established direction."""
    if not evidence or not software or not version:
        raise ValueError(
            "Transform direction/software/version evidence is required; unknown direction blocks import"
        )
    if review not in {"QC_REVIEW_REQUIRED", "ACCEPTED_BY_RESEARCHER", "REJECTED"}:
        raise ValueError("Unknown spatial review status")
    source.verify()
    target.verify()
    if source.id == target.id:
        raise ValueError(
            "Source and target space IDs must differ; prevents accidental double registration"
        )
    if (
        source.subject != target.subject
        and source.kind != "MNI_TEMPLATE"
        and target.kind != "MNI_TEMPLATE"
    ):
        raise ValueError("Cannot transfer subject-specific transforms between subjects")
    if (
        source.kind != "MNI_TEMPLATE"
        and target.kind != "MNI_TEMPLATE"
        and source.session != target.session
    ):
        raise ValueError(
            "Native transform source and target sessions differ; explicit cross-session registration is unsupported"
        )
    path = Path(path).resolve(strict=True)
    info = validate_transform_file(path, fmt)
    if fmt == "fsl_relative":
        field, reference = nib.load(path), nib.load(target.image)
        if field.shape[:3] != reference.shape or not np.allclose(
            field.affine, reference.affine, rtol=0, atol=1e-4
        ):
            raise ValueError(
                "Relative warp grid does not match the declared target reference; no warp convention/grid conversion is inferred"
            )
    return dict(
        schema="tractosphere.spatial-transform/1",
        source=source.to_dict(),
        target=target.to_dict(),
        file=str(path),
        sha256=hash_file(path),
        format=fmt,
        convention=FORMATS[fmt],
        units="mm",
        direction="source_to_target",
        software=software,
        version=version,
        evidence=evidence,
        review=review,
        parameters=parameters or {},
        inspection=info,
    )


def load_transform(path):
    path = Path(path)
    if path.stat().st_size > 1024**2:
        raise ValueError("Transform record exceeds 1 MiB")
    record = read_json(path.read_text())
    if (
        record.get("schema") != "tractosphere.spatial-transform/1"
        or record.get("direction") != "source_to_target"
    ):
        raise ValueError("Explicit source-to-target spatial record required")
    if record.get("review") == "REJECTED":
        raise ValueError("Rejected transform cannot be applied")
    for key in ("source", "target"):
        ImageSpace(**record[key]).verify()
        record[key] = ImageSpace(**record[key]).to_dict()
    if record.get("convention") != FORMATS.get(record.get("format")) or record.get("units") != "mm":
        raise ValueError("Transform coordinate convention is inconsistent")
    if hash_file(Path(record["file"])) != record["sha256"]:
        raise ValueError("Transform bytes changed")
    transform_record(
        record["file"],
        ImageSpace(**record["source"]),
        ImageSpace(**record["target"]),
        record["format"],
        software=record.get("software"),
        version=record.get("version"),
        evidence=record.get("evidence"),
        review=record.get("review"),
        parameters=record.get("parameters"),
    )
    if not record.get("evidence"):
        raise ValueError("Direction provenance is missing")
    return record


def chain(transforms):
    """Validate order by exact intermediate reference identity, never a filename."""
    if not transforms:
        raise ValueError("Empty transform chain")
    for a, b in zip(transforms, transforms[1:], strict=False):
        if a["target"] != b["source"]:
            raise ValueError("Transform chain has a mismatched intermediate space/image/subject")
    if transforms[0]["source"]["id"] == transforms[-1]["target"]["id"]:
        raise ValueError("Circular transform chain is not an atlas registration")
    return transforms[0]["source"], transforms[-1]["target"]


def save_transform(path, record):
    _publish_json(Path(path), record)


def import_transform(request, cancelled=lambda: False, progress=lambda _: None):
    """Import into the same scientific artifact inventory, including all evidence hashes."""
    from .scientific.contracts import Artifact, artifact_id

    record = load_transform(request.path)
    paths = [
        request.path,
        Path(record["file"]),
        Path(record["source"]["image"]),
        Path(record["target"]["image"]),
    ]
    sources = [
        dict(path=str(p), sha256=hash_file(p, cancelled), bytes=p.stat().st_size)
        for p in dict.fromkeys(paths)
    ]
    declarations = dict(request.declarations)
    participant = (
        record["target"]["subject"]
        if record["source"]["kind"] == "MNI_TEMPLATE"
        else record["source"]["subject"]
    )
    if declarations.get("subject_id", participant) != participant:
        raise ValueError("Transform subject differs from the declared batch participant")
    declarations.setdefault("subject_id", participant)
    scope = record["target"] if record["target"]["kind"] != "MNI_TEMPLATE" else record["source"]
    for frame in (record["source"], record["target"]):
        if frame["kind"] == "DWI_NATIVE":
            scope = frame
    for declaration, key in (("session_id", "session"), ("acquisition_id", "acquisition")):
        if declarations.get(declaration, scope[key]) != scope[key]:
            raise ValueError(
                "Transform " + declaration + " differs from the declared batch identity"
            )
        declarations.setdefault(declaration, scope[key])
    return Artifact(
        dict(
            schema="tractosphere.artifact/1",
            kind="spatial_transform",
            path=str(request.path),
            id=artifact_id(request.kind, sources, declarations, request.provenance),
            sources=sources,
            declarations=declarations,
            metadata=record,
            geometry=None,
            verification=record["inspection"],
            provenance=dict(statement=request.provenance, scientific_acceptance="unassigned"),
            request=dict(
                kind=request.kind,
                path=str(request.path),
                declarations=declarations,
                provenance=request.provenance,
            ),
        )
    )
