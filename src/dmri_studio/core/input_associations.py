"""Read-only suggestions for DWI companions; never establish acquisition identity.

The importer still validates every selected file and gradient. This module only
offers unambiguous paths beside a selected image or within a declared BIDS root.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DwiCompanions:
    bval: Path | None = None
    bvec: Path | None = None
    sidecar: Path | None = None
    message: str = ""


def suggest_dwi_companions(image: Path) -> DwiCompanions:
    """Return only uniquely associated files; report unsupported ambiguity.

    Exact image-stem sidecars work in any folder. HCP's ``data.nii.gz`` plus
    ``bvals``/``bvecs`` is recognized only in the same folder. BIDS inheritance
    is delegated to the existing batch discovery contract when an actual BIDS
    root is present; multiple inherited JSON records cannot be represented by
    the single-file importer and remain an explicit Batch/manifest route.
    """
    image = Path(image).expanduser().absolute()
    if not image.name.endswith((".nii", ".nii.gz")) or not image.is_file():
        return DwiCompanions(message="Select an existing NIfTI DWI before looking for companions.")
    stem = image.name[:-7] if image.name.endswith(".nii.gz") else image.stem
    parent = image.parent
    exact = {
        role: parent / (stem + ext)
        for role, ext in (("bval", ".bval"), ("bvec", ".bvec"), ("sidecar", ".json"))
    }
    hcp = {"bval": parent / "bvals", "bvec": parent / "bvecs"} if stem == "data" else {}
    choices: dict[str, set[Path]] = {role: set() for role in exact}
    for role in choices:
        for path in (exact.get(role), hcp.get(role)):
            if path is not None and path.is_file() and not path.is_symlink():
                choices[role].add(path)

    notes = []
    root = next(
        (p for p in (parent, *parent.parents) if (p / "dataset_description.json").is_file()), None
    )
    if root is not None:
        from .batch.discovery import inheritance

        for role, ext in (("bval", ".bval"), ("bvec", ".bvec"), ("sidecar", ".json")):
            try:
                paths, _ = inheritance(root, image, ext)
            except (OSError, ValueError) as exc:
                notes.append(f"{role}: BIDS association needs review ({exc}).")
                choices[role].clear()
                continue
            if role == "sidecar" and len(paths) > 1:
                choices[role].clear()
                notes.append(
                    "Multiple inherited BIDS JSON records apply. Use BIDS discovery or an "
                    "explicit manifest so the merged metadata and source records are preserved."
                )
            elif paths:
                # BIDS inheritance defines the most specific gradient source.
                choices[role] = {paths[-1]}
    selected = {}
    for role, paths in choices.items():
        if len(paths) > 1:
            notes.append(f"Multiple {role} candidates exist; choose the matching file explicitly.")
        elif paths:
            selected[role] = next(iter(paths))
    if selected:
        notes.insert(
            0,
            "Suggested companions are file associations only; gradient and acquisition validation still run on import.",
        )
    else:
        notes.insert(0, "No unique gradient pair found. Choose b-values and b-vectors explicitly.")
    return DwiCompanions(
        selected.get("bval"), selected.get("bvec"), selected.get("sidecar"), "\n".join(notes)
    )
