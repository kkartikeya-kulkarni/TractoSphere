"""Qt-free scientific scene data. Coordinates are voxel-centred RAS+ millimetres.

No implicit registration, coordinate flipping, source overwrites or acceptance.
Edits are explicit in-memory derivatives; tract indices always refer to loaded IDs.
"""

from dataclasses import dataclass, field
from pathlib import Path

import nibabel as nib
import numpy as np


@dataclass
class ImageVolume:
    """Read one 3D volume at a time from an immutable NIfTI proxy."""

    path: Path
    image: object = field(init=False)
    affine_source: str = field(init=False)
    source_fingerprint: object = field(init=False, repr=False)

    def __post_init__(self):
        self.path = Path(self.path).resolve()
        from .dataset import SourceFingerprint

        self.source_fingerprint = SourceFingerprint.capture(self.path)
        self.image = nib.load(self.path, mmap=True)
        if len(self.image.shape) not in (3, 4):
            raise ValueError("Viewer supports scalar 3D/4D NIfTI images")
        if not np.isfinite(self.affine).all() or abs(np.linalg.det(self.affine[:3, :3])) < 1e-10:
            raise ValueError("Invalid or singular image affine")
        self.affine_source = (
            "sform"
            if self.image.header["sform_code"]
            else "qform"
            if self.image.header["qform_code"]
            else "fallback"
        )

    @property
    def affine(self):
        """Convert documented physical units to mm for the shared scene only."""
        affine = self.image.affine.copy()
        factor = {"mm": 1, "meter": 1000, "micron": 0.001}.get(
            self.image.header.get_xyzt_units()[0], 1
        )
        affine[:3] *= factor
        return affine

    @property
    def physical_units_known(self):
        return self.image.header.get_xyzt_units()[0] in {"mm", "meter", "micron"}

    @property
    def shape(self):
        return self.image.shape

    @property
    def count(self):
        return self.shape[3] if len(self.shape) == 4 else 1

    def volume(self, index=0):
        if not 0 <= index < self.count:
            raise IndexError(index)
        return np.asarray(
            self.image.dataobj[..., index] if len(self.shape) == 4 else self.image.dataobj,
            dtype=np.float32,
        )

    def world(self, indices):
        return nib.affines.apply_affine(self.affine, indices)

    def voxels(self, points):
        return nib.affines.apply_affine(np.linalg.inv(self.affine), points)


def save_new_image(path, data, reference):
    """Exclusive derivative write, preserving geometry; never overwrite a source."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    header = reference.image.header.copy()
    header.set_data_dtype(data.dtype)
    image = reference.image.__class__(data, reference.image.affine, header)
    image.set_qform(reference.image.get_qform(), int(header["qform_code"]))
    image.set_sform(reference.image.get_sform(), int(header["sform_code"]))
    nib.save(image, path)


@dataclass
class TractGroup:
    """Bounded lazy import with immutable source IDs and reversible selected edits."""

    path: Path
    streamlines: list
    source_ids: np.ndarray
    total: int
    visible: bool = True
    color: tuple = (0.3, 0.75, 0.95)
    selected: set = field(default_factory=set)
    hidden: set = field(default_factory=set)
    deleted: set = field(default_factory=set)
    cuts: dict = field(default_factory=dict)
    undo_stack: list = field(default_factory=list)
    redo_stack: list = field(default_factory=list)
    _lengths: object = field(default=None, repr=False)
    weights: object = field(default=None, repr=False)
    weight_record: object = field(default=None, repr=False)

    @classmethod
    def load(cls, path, limit=None, *, cancelled=lambda: False):
        """Reservoir sample deterministically; full source is never materialized."""
        path = Path(path).resolve()
        if path.suffix.lower() not in (".tck", ".trk"):
            raise ValueError("Supported tract formats: .tck and .trk")
        if limit is None:
            from .tract_index import IndexedStreamlines

            indexed = IndexedStreamlines(path, cancelled=cancelled)
            group = cls(path, indexed, range(len(indexed)), len(indexed))
            group._lengths = indexed.lengths
            return group
        if limit < 1:
            raise ValueError("Streamline limit must be positive")
        source = nib.streamlines.load(path, lazy_load=True)
        rng = np.random.default_rng(0)
        streams, ids, total = [], [], 0
        for i, line in enumerate(source.tractogram.to_world().streamlines):
            total = i + 1
            if len(line) < 2 or not np.isfinite(line).all():
                raise ValueError(f"Invalid streamline {i}")
            pick = i if i < limit else int(rng.integers(i + 1))
            if pick < limit:
                value = np.asarray(line, dtype=np.float32).copy()
                if i < limit:
                    streams.append(value)
                    ids.append(i)
                else:
                    streams[pick] = value
                    ids[pick] = i
        order = np.argsort(ids)
        group = cls(path, [streams[i] for i in order], np.asarray(ids)[order], total)
        group.statistics()  # Populate immutable source lengths in the loading worker.
        return group

    def line(self, i):
        return self.cuts.get(i, self.streamlines[i])

    def checkpoint(self):
        self.undo_stack.append((set(self.hidden), set(self.deleted), dict(self.cuts)))
        self.undo_stack = self.undo_stack[-20:]
        self.redo_stack.clear()

    def undo(self, redo=False):
        source, target = (
            (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        )
        if source:
            target.append((set(self.hidden), set(self.deleted), dict(self.cuts)))
            self.hidden, self.deleted, self.cuts = source.pop()

    def edit(self, operation, plane_origin=None, plane_normal=None, keep="Keep beginning"):
        """Only selected loaded tracts are affected. Cut keeps the first segment."""
        selected = self.selected - self.deleted
        if not selected:
            raise ValueError("Select streamlines first (Ctrl-click or ROI selection)")
        self.checkpoint()
        if operation == "hide":
            self.hidden.update(selected)
        elif operation == "delete":
            self.deleted.update(selected)
        elif operation == "cut":
            normal = np.asarray(plane_normal, float)
            for i in selected:
                line = self.line(i)
                d = (line - plane_origin) @ normal
                crossings = np.flatnonzero(
                    ((d[:-1] <= 0) & (d[1:] > 0)) | ((d[:-1] >= 0) & (d[1:] < 0))
                )
                if len(crossings):
                    k = crossings[0]
                    crossing = line[k] + (line[k + 1] - line[k]) * d[k] / (d[k] - d[k + 1])
                    before = np.vstack((line[: k + 1], crossing))
                    after = np.vstack((crossing, line[k + 1 :]))
                    choose_after = keep == "Keep end" or (
                        keep == "Keep longer part"
                        and np.linalg.norm(np.diff(after, axis=0), axis=1).sum()
                        > np.linalg.norm(np.diff(before, axis=0), axis=1).sum()
                    )
                    result = after if choose_after else before
                    result = result[
                        np.r_[True, np.linalg.norm(np.diff(result, axis=0), axis=1) > 1e-7]
                    ]
                    if len(result) >= 2:
                        self.cuts[i] = result
        else:
            raise ValueError(operation)

    def select_roi(self, roi, reference):
        """Select intersections, sampling each segment at <= half voxel spacing."""
        self.selected.clear()
        for i in range(len(self.streamlines)):
            if i in self.deleted:
                continue
            vox = reference.voxels(self.line(i))
            for a, b in zip(vox[:-1], vox[1:], strict=False):
                n = max(2, int(np.ceil(np.linalg.norm(b - a) * 2)) + 1)
                points = np.rint(np.linspace(a, b, n)).astype(int)
                valid = np.all((points >= 0) & (points < np.array(roi.shape)), axis=1)
                p = points[valid]
                if len(p) and roi[tuple(p.T)].any():
                    self.selected.add(i)
                    break

    def statistics(self):
        if self._lengths is None:
            self._lengths = np.asarray(
                [np.linalg.norm(np.diff(line, axis=0), axis=1).sum() for line in self.streamlines]
            )
        lengths = self._lengths.copy() if self.cuts or self.deleted else self._lengths
        for i, line in self.cuts.items():
            lengths[i] = np.linalg.norm(np.diff(line, axis=0), axis=1).sum()
        if self.deleted:
            lengths = np.delete(lengths, sorted(self.deleted))
        return {
            "source_count": self.total,
            "loaded_count": len(self.streamlines),
            "retained_loaded_count": len(self.streamlines) - len(self.deleted),
            "selected_count": len(self.selected),
            "mean_length_mm": float(lengths.mean()) if len(lengths) else None,
            "min_length_mm": float(lengths.min()) if len(lengths) else None,
            "max_length_mm": float(lengths.max()) if len(lengths) else None,
            "sampled": self.total > len(self.streamlines),
        }

    def display_ids(self, limit):
        """Evenly sample visible IDs without constructing a full Python index list."""
        total = len(self.streamlines)
        excluded = self.hidden | self.deleted
        count = total - len(excluded)
        if count <= 0:
            return []
        ranks = np.linspace(0, count - 1, min(count, limit), dtype=np.int64)
        excluded_sorted = np.array(sorted(excluded), dtype=np.int64)
        ids = ranks.copy()
        if len(excluded_sorted):
            # Each excluded index shifts the visible rank that follows it.
            removed_ranks = excluded_sorted - np.arange(len(excluded_sorted))
            ids += np.searchsorted(removed_ranks, ranks, side="right")
        return ids.tolist()

    def export(self, path, reference, *, cancelled=lambda: False):
        """Export retained loaded streamlines only, in RAS+ mm. Source untouched."""
        path = Path(path)
        if path.exists():
            raise FileExistsError(path)
        if path.suffix.lower() not in (".tck", ".trk"):
            raise ValueError("Export requires .tck or .trk")
        if hasattr(self.streamlines, "verify_source"):
            self.streamlines.verify_source()
        from nibabel.streamlines.tractogram import LazyTractogram, TractogramItem

        from .execution import check_cancel

        def items():
            for i in range(len(self.streamlines)):
                check_cancel(cancelled)
                if i in self.deleted:
                    continue
                original = self.streamlines.item(i) if hasattr(self.streamlines, "item") else None
                dps = original.data_for_streamline if original else {}
                dpp = original.data_for_points if original else {}
                if i in self.cuts and dpp:
                    source = self.streamlines[i]
                    source_arc = np.r_[
                        0, np.cumsum(np.linalg.norm(np.diff(source, axis=0), axis=1))
                    ]
                    first = self.line(i)[0]
                    delta = np.diff(source, axis=0)
                    fraction = np.clip(
                        np.sum((first - source[:-1]) * delta, axis=1)
                        / np.maximum(np.sum(delta * delta, axis=1), 1e-20),
                        0,
                        1,
                    )
                    projected = source[:-1] + fraction[:, None] * delta
                    j = int(np.argmin(np.linalg.norm(projected - first, axis=1)))
                    offset = source_arc[j] + fraction[j] * (source_arc[j + 1] - source_arc[j])
                    arc = (
                        offset
                        + np.r_[0, np.cumsum(np.linalg.norm(np.diff(self.line(i), axis=0), axis=1))]
                    )
                    dpp = {
                        k: np.column_stack(
                            [np.interp(arc, source_arc, v[:, c]) for c in range(v.shape[1])]
                        )
                        for k, v in dpp.items()
                    }
                # TCK cannot encode TRK attributes. Refuse silent metadata loss.
                if path.suffix.lower() == ".tck" and (dps or dpp):
                    raise ValueError(
                        "This tractogram has scalar/property metadata; export TRK to preserve it"
                    )
                yield TractogramItem(self.line(i), dps, dpp)

        first = next(items(), None)
        tractogram = LazyTractogram(
            streamlines=lambda: (item.streamline for item in items()),
            data_per_streamline={
                k: (lambda key=k: (i.data_for_streamline[key] for i in items()))
                for k in (first.data_for_streamline if first else {})
            },
            data_per_point={
                k: (lambda key=k: (i.data_for_points[key] for i in items()))
                for k in (first.data_for_points if first else {})
            },
            affine_to_rasmm=np.eye(4),
        )
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=path.suffix, delete=False) as f:
            temporary = Path(f.name)
        try:
            self._write_export(temporary, reference, tractogram)
            os.link(temporary, path)  # exclusive even if another process creates the target
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_export(path, reference, tractogram):
        if path.suffix.lower() == ".trk":
            header = nib.streamlines.TrkFile.create_empty_header()
            header["voxel_to_rasmm"] = reference.affine
            header["dimensions"] = reference.shape[:3]
            header["voxel_sizes"] = nib.affines.voxel_sizes(reference.affine)
            header["voxel_order"] = "".join(nib.aff2axcodes(reference.affine))
            nib.streamlines.save(nib.streamlines.TrkFile(tractogram, header), path)
        else:
            nib.streamlines.save(tractogram, path)
