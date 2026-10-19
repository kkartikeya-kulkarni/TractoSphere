"""Descriptive tissue/endpoint checks on actual physical streamline coordinates.

These measurements do not reproduce ACT's internal state machine or establish
biological validity. No coordinates or original tissue fractions are changed.
"""

from pathlib import Path

import nibabel as nib
import numpy as np

from ..process import check_cancel
from ..tract_index import IndexedStreamlines
from .dti import image_data


def sample_fractions(points, data, affine):
    """Trilinear continuous fractions at world RAS+ points, zero beyond image.

    Non-isotropic, flipped/permuted and oblique affines are handled by the full
    inverse matrix. Out-of-FOV locations remain a separate QC classification.
    """
    vox = nib.affines.apply_affine(np.linalg.inv(affine), points)
    lower = np.floor(vox).astype(int)
    frac = vox - lower
    out = np.zeros((len(vox), 5), dtype="f8")
    for corner in np.ndindex(2, 2, 2):
        ijk = lower + corner
        valid = ((ijk >= 0) & (ijk < np.asarray(data.shape[:3]))).all(axis=1)
        weight = np.prod(np.where(np.array(corner), frac, 1 - frac), axis=1)
        out[valid] += np.asarray(data[tuple(ijk[valid].T)]) * weight[valid, None]
    inside = ((vox >= -0.5) & (vox < np.asarray(data.shape[:3]) - 0.5)).all(axis=1)
    return out, inside, vox


def endpoint_qc(tractogram, five_tt, output_map=None, *, cancelled=lambda: False):
    """Stream endpoints and saved-vertex tissue observations; retain all tracks."""
    with image_data(Path(five_tt)) as (affine, data):
        # A NIfTI ArrayProxy cannot perform advanced point indexing. Materialize
        # only the five anatomical channels once (not per streamline); native
        # MIF stays a read-only memory-mapped view. Bound this independent QC.
        if np.prod(data.shape) * 4 > 512 * 1024**2:
            raise ValueError("ACT endpoint tissue QC exceeds its 512 MiB image budget")
        data = np.asanyarray(data)
        density = np.zeros(data.shape[:3], dtype="f4")
        values, outside, csf_tracks, background_tracks = [], 0, 0, 0
        tracks = IndexedStreamlines(Path(tractogram), cancelled=cancelled)
        try:
            for i in range(len(tracks)):
                check_cancel(cancelled)
                line = tracks[i]
                tissue, inside, _ = sample_fractions(line, data, affine)
                csf_tracks += int((tissue[:, 3] > 0.5).any())
                background_tracks += int((tissue.sum(axis=1) < 0.5).any() or not inside.all())
                endpoints, ok, vox = sample_fractions(line[[0, -1]], data, affine)
                values.extend(endpoints)
                outside += int((~ok).sum())
                rounded = np.floor(vox[ok] + 0.5).astype(int)
                np.add.at(density, tuple(rounded.T), 1)
        finally:
            tracks.close()
        a = np.asarray(values)
        if not len(a):
            raise ValueError("No endpoints to evaluate")
        background = a.sum(axis=1) < 0.5
        classes = np.where(background, 5, a.argmax(axis=1))
        if output_map is not None:
            path = Path(output_map)
            if path.exists():
                raise ValueError("Endpoint QC derivative exists; overwrite refused")
            im = nib.Nifti1Image(density, affine)
            im.header.set_xyzt_units("mm")
            im.set_sform(affine, 1)
            im.set_qform(affine, 1)
            nib.save(im, path)
        return dict(
            endpoints=len(a),
            outside_field_endpoints=outside,
            dominant_tissue_counts=dict(
                zip(
                    ("cortical_gm", "subcortical_gm", "wm", "csf", "pathology", "background"),
                    np.bincount(classes, minlength=6).tolist(),
                    strict=True,
                )
            ),
            mean_endpoint_fractions=a.mean(axis=0).tolist(),
            endpoint_fraction_percentiles=np.percentile(a, [5, 50, 95], axis=0).tolist(),
            endpoints_gm_fraction_at_least_half=int((a[:, :2].sum(axis=1) >= 0.5).sum()),
            streamlines_with_any_saved_vertex_csf_gt_half=csf_tracks,
            streamlines_with_background_or_outside_saved_vertex=background_tracks,
            endpoint_map=str(output_map) if output_map else None,
            definitions="Trilinear original continuous 5TT fractions at saved world-RAS vertices. Dominant tissue among five channels if sum>=0.5; otherwise background. Diagnostic 0.5 thresholds are descriptive, not ACT acceptance rules or expert anatomical labels.",
            scientific_acceptance="unassigned",
        )
