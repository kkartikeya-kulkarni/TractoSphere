"""Nonrecursive file suggestions only; scientific parsing belongs to core.

A folder drop fills a reviewable form. Ambiguous fields remain unresolved until
the user chooses; no candidate is silently selected and no load starts here.
"""

from pathlib import Path


def role_for_file(path: Path) -> str | None:
    """Recognize supported input naming without inspecting scientific contents."""
    name = path.name.lower()
    if name.endswith((".nii", ".nii.gz")):
        return "dwi"
    if name == "bvals" or path.suffix.lower() in (".bval", ".bvals"):
        return "bval"
    if name == "bvecs" or path.suffix.lower() in (".bvec", ".bvecs"):
        return "bvec"
    if path.suffix.lower() == ".json":
        return "json"
    return None


def detect_folder(folder: Path, image: Path | None = None) -> dict[str, list[Path]]:
    """Prefer stem-matched companions, including HCP's exact extensionless names."""
    candidates: dict[str, list[Path]] = {role: [] for role in ("dwi", "bval", "bvec", "json")}
    for path in sorted(folder.iterdir()):
        if path.is_file() and (role := role_for_file(path)):
            if path.name not in ("grad_dev.nii", "grad_dev.nii.gz"):
                candidates[role].append(path)
    if image is None and len(candidates["dwi"]) == 1:
        image = candidates["dwi"][0]
    if image is not None:
        stem = image.name.removesuffix(".gz").removesuffix(".nii")
        for role in ("bval", "bvec", "json"):
            names = {f"{stem}.{role}", f"{stem}.{role}s"}
            if stem == "data" and role != "json":
                names.add(f"{role}s")
            matches = [p for p in candidates[role] if p.name in names]
            if matches:
                candidates[role] = matches
            elif role == "json":
                # An unrelated JSON is never implicitly attached to an image.
                candidates[role] = []
    return candidates
