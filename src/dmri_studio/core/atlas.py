"""Anatomical atlas import with explicit spatial association and lazy region masks.

FSL XML semantics follow the published atlas_xml specification: Label indices
are voxel IDs; probabilistic indices are zero-based volumes; summary IDs add one.
No registration is estimated here. All sampling creates visualization derivatives.
"""

import colorsys
import csv
import hashlib
import json
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

import nibabel as nib
import numpy as np

from .scene import ImageVolume


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(8 * 1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def stem(path):
    name = Path(path).name
    return name[:-7] if name.endswith(".nii.gz") else Path(name).stem


def xml_images(path):
    """Resolve FSL paths relative to XML, including its conventional leading '/'."""
    root = ET.parse(path).getroot()
    choices = []
    for node in root.findall("./header/images/*"):
        if node.tag not in ("imagefile", "summaryimagefile") or not node.text:
            continue
        base = Path(path).parent / node.text.strip().lstrip("/")
        for suffix in ("", ".nii.gz", ".nii"):
            candidate = Path(str(base) + suffix)
            if candidate.is_file() and candidate not in choices:
                choices.append(candidate)
                break
    return choices


def find_table(path):
    """Only associate metadata by exact sidecar or explicit FSL image reference."""
    path = Path(path)
    for ext in (".tsv", ".csv", ".xml", ".json"):
        candidate = path.parent / (stem(path) + ext)
        if candidate.is_file():
            return candidate
    for folder in (path.parent, path.parent.parent):
        for candidate in sorted(folder.glob("*.xml")):
            try:
                if path.resolve() in [p.resolve() for p in xml_images(candidate)]:
                    return candidate
            except (ET.ParseError, OSError):
                continue
    return None


def label_table(path, probability=False):
    """Read FSL XML, index/name TSV/CSV, JSON Labels, or FreeSurfer-style LUT."""
    if path is None:
        return {}, {}, None
    path = Path(path)
    names, colors = {}, {}
    title = None
    if path.suffix.lower() == ".xml":
        root = ET.parse(path).getroot()
        title = root.findtext("./header/name")
        probabilistic = root.findtext("./header/type", "").lower() == "probabilistic"
        for item in root.findall("./data/label"):
            value = int(item.attrib["index"])
            if probabilistic and not probability:
                value += 1
            names[value] = (item.text or "").strip() or f"Region {value}"
    elif path.suffix.lower() in (".tsv", ".csv"):
        with path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter="\t" if path.suffix.lower() == ".tsv" else ","):
                key = next((row[k] for k in ("index", "label", "id") if k in row), None)
                if key is None or not str(key).strip():
                    raise ValueError("Label table needs index/label/id and name columns")
                value = int(key)
                names[value] = row.get("name") or f"Region {value}"
                if row.get("color", "").startswith("#") and len(row["color"]) == 7:
                    colors[value] = tuple(int(row["color"][i : i + 2], 16) / 255 for i in (1, 3, 5))
    elif path.suffix.lower() == ".json":
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        for key, value in record.get("Labels", {}).items():
            names[int(key)] = value if isinstance(value, str) else value["name"]
        title = record.get("AtlasName")
    else:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            parts = line.split("#", 1)[0].split()
            if not parts:
                continue
            value = int(parts[0])
            names[value] = parts[1]
            if len(parts) >= 5:
                colors[value] = tuple(float(v) / 255 for v in parts[2:5])
    return names, colors, title


def geometry_record(image):
    h = image.image.header
    q, qc = image.image.get_qform(coded=True)
    s, sc = image.image.get_sform(coded=True)
    if qc and sc and not np.allclose(q, s, atol=1e-3):
        raise ValueError(
            "Conflicting qform/sform: resolve the atlas/reference geometry before import"
        )
    if not image.physical_units_known or not (qc or sc):
        raise ValueError("Atlas alignment requires documented spatial units and a coded affine")
    return dict(
        shape=list(image.shape),
        affine_mm=image.affine.tolist(),
        qform=q.tolist() if qc else None,
        sform=s.tolist() if sc else None,
        qform_code=int(qc),
        sform_code=int(sc),
        orientation=list(nib.aff2axcodes(image.affine)),
        voxel_sizes_mm=nib.affines.voxel_sizes(image.affine).tolist(),
        units=h.get_xyzt_units()[0],
    )


def space_hint(image):
    codes = {int(image.image.header[k]) for k in ("qform_code", "sform_code")}
    if 4 in codes:
        return "MNI"
    if 3 in codes:
        return "Talairach"
    if 1 in codes or "sub-" in str(image.path):
        return "native/subject"
    return "unspecified"


class Atlas:
    """One source image, compact shared labels, at most three cached masks."""

    def __init__(
        self,
        path,
        reference,
        *,
        table=None,
        kind="auto",
        threshold=0.25,
        probability_scale=1.0,
        same_space=False,
        transform=None,
    ):
        if kind not in ("auto", "labels", "probability"):
            raise ValueError("Atlas interpretation must be auto, labels or probability")
        self.image = ImageVolume(Path(path))
        self.reference = reference
        if not str(path).lower().endswith((".nii", ".nii.gz")):
            raise ValueError("Unsupported atlas format: choose NIfTI or an FSL XML image entry")
        self.geometry = {
            "atlas": geometry_record(self.image),
            "reference": geometry_record(reference),
        }
        source_space, target_space = space_hint(self.image), space_hint(reference)
        self.geometry.update(source_space=source_space, reference_space=target_space)
        matrix = np.eye(4)
        self.transform = None
        if transform:
            record = json.loads(Path(transform).read_text(encoding="utf-8"))
            if (
                record.get("schema") != "tractosphere.verified-affine/1"
                or record.get("verified") is not True
                or not record.get("evidence")
                or record.get("source_sha256") != sha256(path)
                or record.get("reference_sha256") != sha256(reference.path)
            ):
                raise ValueError(
                    "Transform must be verified, document its evidence and match atlas/reference SHA-256"
                )
            matrix = np.asarray(record["matrix_rasmm"], float)
            if (
                matrix.shape != (4, 4)
                or not np.isfinite(matrix).all()
                or not np.allclose(matrix[3], [0, 0, 0, 1])
                or abs(np.linalg.det(matrix[:3, :3])) < 1e-10
            ):
                raise ValueError("Invalid source-world to reference-world affine")
            self.transform = record
        else:
            if source_space != target_space and "unspecified" not in (source_space, target_space):
                raise ValueError(
                    f"Atlas/reference space mismatch ({source_space} → {target_space}). Required verified registration transform unavailable; resampling alone is not registration."
                )
            if self.image.path != reference.path and not same_space:
                raise ValueError(
                    "Confirm atlas and reference already occupy the same anatomical world space, or supply a verified transform. Matching dimensions/affines alone are insufficient."
                )
        self.mapping = np.linalg.inv(self.image.affine) @ np.linalg.inv(matrix) @ reference.affine
        self.direct = self.image.shape[:3] == reference.shape[:3] and np.allclose(
            self.mapping, np.eye(4), atol=1e-6
        )
        self.probability = len(self.image.shape) == 4 or kind == "probability"
        if kind == "labels" and len(self.image.shape) != 3:
            raise ValueError(
                "Discrete atlas must be a 3D label map; 4D maps require probabilistic mode"
            )
        if not 0 < threshold <= 1 or probability_scale not in (1, 100):
            raise ValueError("Probability threshold must be >0 and ≤1; scale must be 1 or 100")
        self.threshold, self.scale = float(threshold), float(probability_scale)
        self.table = Path(table) if table else find_table(path)
        self.names, self.colors, title = label_table(self.table, self.probability)
        if self.probability and self.table and self.table.suffix.lower() == ".xml":
            self.scale = 100.0  # Documented FSL probability convention.
        self.name = title or stem(path)
        self.source_sha256 = sha256(path)
        identity = json.dumps(
            dict(
                source=self.source_sha256,
                geometry=self.geometry,
                matrix=matrix.tolist(),
                threshold=self.threshold,
                scale=self.scale,
                probability=self.probability,
            ),
            sort_keys=True,
        )
        self.atlas_id = hashlib.sha256(identity.encode()).hexdigest()
        self.cache = OrderedDict()
        self.records = []
        self.omitted = []
        self.labels = None
        if not self.probability:
            values = np.asarray(self.image.image.dataobj)
            if (
                not np.isfinite(values).all()
                or np.any(values < 0)
                or not np.allclose(values, np.rint(values), atol=1e-6, rtol=0)
            ):
                raise ValueError(
                    "Not a discrete label map. Continuous probability images require explicit probabilistic mode and scale."
                )
            ids = np.unique(values)
            ids = ids[ids > 0]
            if len(ids) > 4096 or (len(ids) and ids[-1] > np.iinfo(np.int32).max):
                raise ValueError(
                    "Too many or invalid label IDs: this may be a continuous anatomical image, not an atlas"
                )
            self.labels = self.sample(values.astype(np.int32), 0)
            self.labels.setflags(write=False)
            counts = dict(zip(*np.unique(self.labels, return_counts=True), strict=True))
            for key in ids:
                self.add_record(int(key), int(counts.get(key, 0)))
        else:
            for key in range(self.image.count):
                self.add_record(key, int(np.count_nonzero(self.mask(key))))
        if not self.records:
            raise ValueError(
                "Atlas contains no nonempty regions in the reference grid at this threshold. Check space, transform and threshold."
            )
        self.warnings = []
        missing = [r["label_id"] for r in self.records if r["label_id"] not in self.names]
        if missing:
            self.warnings.append(
                f"Names unavailable for {len(missing)} regions; original IDs are displayed"
            )
        if self.omitted:
            self.warnings.append(
                f"{len(self.omitted)} empty regions omitted after sampling/threshold"
            )

    def sample(self, data, order):
        if self.direct:
            return data
        # Metadata-only imports remain available without the optional viewer SciPy extra.
        from scipy.ndimage import affine_transform

        return affine_transform(
            data,
            self.mapping[:3, :3],
            self.mapping[:3, 3],
            output_shape=self.reference.shape[:3],
            order=order,
            mode="constant",
            cval=0,
            prefilter=False,
        )

    def mask(self, key):
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        if self.probability:
            data = self.image.volume(key)
            if not np.isfinite(data).all() or np.any(data < 0) or np.any(data > self.scale + 1e-5):
                raise ValueError(
                    f"Probability map {key} has invalid values for scale 0–{self.scale:g}"
                )
            result = self.sample(data, 1) >= self.threshold * self.scale
        else:
            result = self.labels == key
        result = np.asarray(result, np.uint8)
        result.setflags(write=False)
        self.cache[key] = result
        while len(self.cache) > 3:
            self.cache.popitem(last=False)
        return result

    def add_record(self, key, count):
        if not count:
            self.omitted.append(key)
            return
        color = self.colors.get(key, colorsys.hsv_to_rgb((key * 0.61803398875) % 1, 0.65, 0.95))
        self.records.append(
            dict(
                label_id=key,
                name=self.names.get(key, f"Region {key}"),
                name_available=key in self.names and bool(self.names[key].strip()),
                voxel_count=count,
                volume_mm3=count * abs(np.linalg.det(self.reference.affine[:3, :3])),
                color=color,
            )
        )

    def regions(self):
        for record in self.records:
            yield AtlasRegion(
                self,
                dict(
                    record,
                    atlas_name=self.name,
                    atlas_source=str(self.image.path),
                    atlas_id=self.atlas_id,
                    source_sha256=self.source_sha256,
                    atlas_kind="probability" if self.probability else "labels",
                    probability_threshold=self.threshold if self.probability else None,
                    probability_scale=self.scale if self.probability else None,
                    label_table=str(self.table) if self.table else None,
                    geometry=self.geometry,
                    transform=self.transform,
                    path=str(self.image.path),
                    visible=False,
                    opacity=0.3,
                    surface_quality="Smooth",
                    surface_mode="Surface",
                    selected=False,
                    edited=False,
                    surface_built=False,
                    role="Display",
                ),
            )


class AtlasRegion(dict):
    """Compatible ROI dictionary; materialize a private mask only on explicit edit."""

    def __init__(self, atlas, metadata):
        super().__init__(metadata)
        self.atlas = atlas

    def __getitem__(self, key):
        if key == "data" and not dict.__contains__(self, "data"):
            return self.atlas.mask(dict.__getitem__(self, "label_id"))
        return super().__getitem__(key)


def intersects_mask(line, mask, reference):
    """Exact voxel-cell segment traversal, including sparse segment crossings.

    Cells are centred on integer voxels; bounding faces lie at n+0.5. Probe
    crossings and intervals; no screen projection, streamline resampling or
    modification. Boundary cells use the half-open floor(voxel+0.5) convention.
    """
    points = reference.voxels(line)
    shape = np.asarray(mask.shape)
    vertices = np.floor(points + 0.5).astype(int)
    vertices = vertices[((vertices >= 0) & (vertices < shape)).all(axis=1)]
    if len(vertices) and mask[tuple(vertices.T)].any():
        return True
    for a, b in zip(points[:-1], points[1:], strict=True):
        delta = b - a
        lo, hi = 0.0, 1.0
        for axis in range(3):
            if abs(delta[axis]) < 1e-12:
                if a[axis] < -0.5 or a[axis] > shape[axis] - 0.5:
                    lo, hi = 1, 0
                    break
            else:
                t = sorted(
                    ((-0.5 - a[axis]) / delta[axis], (shape[axis] - 0.5 - a[axis]) / delta[axis])
                )
                lo, hi = max(lo, t[0]), min(hi, t[1])
        if lo > hi:
            continue
        ts = [lo, hi]
        for axis in range(3):
            if abs(delta[axis]) < 1e-12:
                continue
            left, right = sorted((a[axis] + lo * delta[axis], a[axis] + hi * delta[axis]))
            faces = (
                np.arange(
                    max(0, int(np.ceil(left - 0.5))),
                    min(shape[axis] - 1, int(np.floor(right - 0.5))) + 1,
                )
                + 0.5
            )
            ts.extend(((faces - a[axis]) / delta[axis]).tolist())
        ts = np.unique(ts)
        probes = np.r_[ts, (ts[:-1] + ts[1:]) / 2]
        indices = np.floor(a + probes[:, None] * delta + 0.5).astype(int)
        valid = ((indices >= 0) & (indices < shape)).all(axis=1)
        indices = indices[valid]
        if len(indices) and mask[tuple(indices.T)].any():
            return True
    return False


def filter_streamlines(group, rois, reference, require_all=True):
    """Include all/any inclusion ROIs and reject any exclusion; keep source IDs."""
    includes = [r["data"] for r in rois if r.get("role") == "Include"]
    excludes = [r["data"] for r in rois if r.get("role") == "Exclude"]
    if not includes and not excludes:
        raise ValueError("Assign at least one ROI an Include or Exclude role")
    selected = set()
    for index in range(len(group.streamlines)):
        if index in group.deleted:
            continue
        line = group.line(index)
        if any(intersects_mask(line, mask, reference) for mask in excludes):
            continue
        hits = [intersects_mask(line, mask, reference) for mask in includes]
        if not includes or (all(hits) if require_all else any(hits)):
            selected.add(index)
    return selected
