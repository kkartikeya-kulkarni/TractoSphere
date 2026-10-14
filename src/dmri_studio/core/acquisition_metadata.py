"""Read inherited BIDS acquisition evidence without changing source metadata.

This is a bounded JSON resolver, not a BIDS validator. Only ancestors of the
selected image are searched; sibling sessions and derivatives are never searched
for substitute parameters. Every effective field retains its exact source.
"""

from __future__ import annotations

import json
from pathlib import Path

import nibabel as nib
import numpy as np


def _object(path: Path) -> dict:
    """Read small strict metadata objects, rejecting duplicate keys and NaN."""
    if path.stat().st_size > 8 * 1024**2:
        raise ValueError(f"Acquisition JSON exceeds 8 MiB: {path}")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate metadata key {key}: {path}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"Nonfinite JSON value {value}: {path}")

    result = json.loads(path.read_text(), object_pairs_hook=pairs, parse_constant=invalid)
    if not isinstance(result, dict):
        raise ValueError(f"Acquisition metadata must be an object: {path}")
    return result


def entities(path: Path) -> tuple[dict[str, str], str]:
    """Parse filename entities, retaining labels exactly including zero padding."""
    name = path.name
    for extension in (".nii.gz", ".nii", ".json"):
        if name.endswith(extension):
            name = name[: -len(extension)]
            break
    parts = name.split("_")
    result = {}
    for part in parts[:-1]:
        if "-" not in part:
            raise ValueError(f"Not a BIDS entity filename: {path.name}")
        key, value = part.split("-", 1)
        if not key or not value or key in result:
            raise ValueError(f"Ambiguous BIDS entity filename: {path.name}")
        result[key] = value
    return result, parts[-1]


def inherited_metadata(image: Path, *, root: Path | None = None) -> dict:
    """Resolve applicable JSON from root to image, retaining hashes and origins.

    A dataset_description.json ancestor is required unless the caller explicitly
    supplies a dataset root. BIDS permits only one applicable metadata file per hierarchy level;
    ambiguous inheritance is refused even when values happen to agree.
    Symlinked annex payloads are allowed, but their lexical BIDS paths determine
    associations and ancestor searches.
    """
    from .execution import hash_file

    image = image.expanduser().absolute()
    if not image.is_file():
        raise ValueError(f"Missing acquisition image: {image}")
    target, suffix = entities(image)
    if root is None:
        root = next((p for p in image.parents if (p / "dataset_description.json").is_file()), None)
    else:
        root = root.expanduser().absolute()
    if root is None or root not in image.parents:
        raise ValueError(
            "An explicit BIDS dataset root or ancestor dataset_description.json is required"
        )
    for directory in image.relative_to(root).parts[:-1]:
        if directory.startswith(("sub-", "ses-")):
            key, label = directory.split("-", 1)
            if target.get(key) != label:
                raise ValueError(f"Filename {key} entity disagrees with acquisition directory")
    levels = [root]
    if image.parent != root:
        levels.extend(reversed(image.parent.parents[: image.parent.parents.index(root)]))
        levels.append(image.parent)
    effective, origins, sources, changes = {}, {}, [], []
    for level in levels:
        candidates = []
        for candidate in sorted(level.glob("*.json")):
            if candidate.name.startswith("."):
                continue
            try:
                fields, candidate_suffix = entities(candidate)
            except ValueError:
                continue
            if candidate_suffix == suffix and all(target.get(k) == v for k, v in fields.items()):
                candidates.append((fields, candidate))
        if len(candidates) > 1:
            raise ValueError(
                "Multiple applicable metadata files at one BIDS hierarchy level: "
                + ", ".join(str(path) for _, path in candidates)
            )
        for fields, path in candidates:
            value = _object(path)
            digest = hash_file(path)
            # Bind parsed values to the recorded bytes even if a concurrent edit
            # happened between the first read and hashing.
            if _object(path) != value or hash_file(path) != digest:
                raise ValueError(f"Metadata changed during inspection: {path}")
            sources.append({"path": str(path), "sha256": digest, "entities": fields})
            for key, item in value.items():
                if key in effective and effective[key] != item:
                    changes.append(
                        {"field": key, "previous_source": origins[key], "source": str(path)}
                    )
                effective[key], origins[key] = item, str(path)
    if not sources:
        raise ValueError(f"No applicable acquisition metadata: {image}")
    return {
        "image": str(image),
        "root": str(root),
        "metadata": effective,
        "field_sources": origins,
        "sources": sources,
        "overrides": changes,
    }


def slice_groups(image: nib.spatialimages.SpatialImage, metadata: dict) -> dict:
    """Derive groups only from documented timing, respecting negative slice axes.

    Missing timing is an explicit unavailable result, not an invented sequential
    order. Inconsistent supplied metadata is an error. CPU volume-to-volume EDDY
    does not acquire slice-to-volume capability merely because groups are known.
    """
    timing = metadata.get("SliceTiming")
    direction = metadata.get("SliceEncodingDirection")
    header_axis = image.header.get_dim_info()[2]
    if direction is not None and direction not in {"i", "i-", "j", "j-", "k", "k-"}:
        raise ValueError("Invalid SliceEncodingDirection")
    axis = "ijk".index(direction[0]) if direction else header_axis
    if header_axis is not None and direction and axis != header_axis:
        raise ValueError("SliceEncodingDirection conflicts with NIfTI dim_info")
    if timing is None or axis is None:
        return {
            "status": "unavailable",
            "reason": "SliceTiming or slice axis is undocumented",
            "slice_axis": axis,
            "groups": None,
        }
    if (
        not isinstance(timing, list)
        or len(timing) != image.shape[axis]
        or any(type(v) not in (float, int) for v in timing)
    ):
        raise ValueError("SliceTiming must contain one numeric time per documented slice")
    times = np.asarray(timing, dtype=float)
    if not np.isfinite(times).all() or (times < 0).any():
        raise ValueError("SliceTiming must be finite and nonnegative")
    repetition = metadata.get("RepetitionTime")
    if repetition is not None and (
        type(repetition) not in (int, float)
        or not np.isfinite(repetition)
        or repetition <= 0
        or (times >= repetition).any()
    ):
        raise ValueError("SliceTiming is incompatible with RepetitionTime")
    if direction and direction.endswith("-"):
        times = times[::-1]
    # Exact equality intentionally avoids silently merging close but distinct
    # slice times. A rounded/inconsistent timing export needs separate review.
    groups = [np.flatnonzero(times == time).tolist() for time in np.unique(times)]
    sizes = {len(group) for group in groups}
    if len(sizes) != 1:
        raise ValueError("Unequal simultaneous slice groups require acquisition review")
    multiband = metadata.get("MultibandAccelerationFactor")
    derived = next(iter(sizes))
    if multiband is not None and (type(multiband) not in (int, float) or multiband != derived):
        raise ValueError("MultibandAccelerationFactor conflicts with documented SliceTiming")
    return {
        "status": "derived",
        "slice_axis": axis,
        "groups": groups,
        "multiband_from_timing": derived,
        "declared_multiband": multiband,
        "source": "SliceTiming, SliceEncodingDirection/NIfTI dim_info; no timing inferred",
    }
