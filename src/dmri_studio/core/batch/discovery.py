"""Read-only study discovery. Explicit identities and root-contained references.

BIDS support is a conservative DWI/anatomy discovery subset, not a BIDS validator.
Entities are parsed as complete key/value tokens; inheritance uses entity subsets
and ancestor directories. Ambiguous associations remain findings, never guesses.
"""

from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..scientific.records import read_json

ROLES = (
    "transform",
    "template",
    "registration_reference",
    "qc_association",
    "reference_record",
    "space_review",
    "dwi",
    "bval",
    "bvec",
    "json",
    "mask",
    "t1",
    "fod",
    "tensor",
    "tractogram",
    "atlas",
    "five_tt",
    "gmwmi",
    "fa",
    "weights",
    "reference",
    "selection",
    "manifest",
)


def identity(value: str, *, empty: bool = False) -> str:
    """Identifiers are path components, never filesystem paths."""
    if empty and value == "":
        return value
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError(f"Invalid study/subject/session/acquisition identifier: {value!r}")
    return value


def contained(root: Path, value: str | Path, *, exists: bool = True) -> Path:
    root = Path(root).resolve(strict=True)
    path = Path(value).expanduser()
    path = (path if path.is_absolute() else root / path).resolve(strict=exists)
    if not path.is_relative_to(root):
        raise ValueError(f"Path escapes authorized dataset root: {value}")
    if exists and not path.is_file():
        raise ValueError(f"Not a readable file: {path}")
    return path


@dataclass(frozen=True)
class Derivative:
    """One processing experiment, distinct from its source acquisition."""

    id: str
    kind: str
    manifest: str
    selection: str

    def __post_init__(self):
        identity(self.id)
        if not self.kind:
            raise ValueError("Derivative kind must be explicit")


@dataclass(frozen=True)
class Acquisition:
    subject_id: str
    session_id: str = ""
    acquisition_id: str = "default"
    paths: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    declarations: dict[str, Any] = field(default_factory=dict)
    findings: tuple[str, ...] = ()
    derivatives: dict[str, Derivative] = field(default_factory=dict)

    def __post_init__(self):
        identity(self.subject_id)
        object.__setattr__(self, "findings", tuple(self.findings))
        derivatives = {
            key: value if isinstance(value, Derivative) else Derivative(**value)
            for key, value in self.derivatives.items()
        }
        if any(key != value.id for key, value in derivatives.items()):
            raise ValueError("Derivative key and experiment identity differ")
        object.__setattr__(self, "derivatives", derivatives)
        identity(self.session_id, empty=True)
        identity(self.acquisition_id)
        if set(self.paths) - set(ROLES):
            raise ValueError("Unsupported file role")

    @property
    def key(self):
        # Empty session cannot collide with a named session.
        return f"{self.subject_id}/{'session-' + self.session_id if self.session_id else 'no-session'}/{self.acquisition_id}"

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Study:
    study_id: str
    root: str
    acquisitions: tuple[Acquisition, ...]
    source: dict = field(default_factory=dict)

    def __post_init__(self):
        identity(self.study_id)
        keys = [a.key for a in self.acquisitions]
        owners = {}
        for acquisition in self.acquisitions:
            for role, path in acquisition.paths.items():
                if role in {"dwi", "tensor", "fod", "tractogram", "t1", "five_tt"}:
                    resolved = str(Path(path).resolve())
                    if resolved in owners and owners[resolved] != acquisition.subject_id:
                        raise ValueError(
                            "The same subject-specific file is associated with different subjects"
                        )
                    owners[resolved] = acquisition.subject_id
        if len(set(keys)) != len(keys):
            raise ValueError(
                "Duplicate subject/session/acquisition identity; subjects may have multiple acquisitions"
            )

    def to_dict(self):
        return {"schema": "tractosphere.study/1", **asdict(self)}

    @classmethod
    def from_dict(cls, data):
        if data.get("schema") != "tractosphere.study/1":
            raise ValueError("Not a TractoSphere study")
        return cls(
            data["study_id"],
            data["root"],
            tuple(Acquisition(**a) for a in data["acquisitions"]),
            data.get("source", {}),
        )


def import_manifest(path: Path, root: Path, study_id: str) -> Study:
    """CSV/TSV files are data only; unknown columns retained as metadata."""
    path, root = Path(path), Path(root).resolve(strict=True)
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError("Manifest exceeds 16 MiB metadata limit")
    result = []
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream, delimiter="\t" if path.suffix.lower() == ".tsv" else ",")
        fields = reader.fieldnames or []
        if len(fields) != len(set(fields)) or "subject_id" not in fields:
            raise ValueError("Manifest requires unique columns including subject_id")
        for number, row in enumerate(reader, 2):
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"Malformed manifest row {number}")
            paths, findings = {}, []
            for role in ROLES:
                if value := row.get(role + "_path", "").strip():
                    try:
                        paths[role] = str(contained(root, value))
                    except (OSError, ValueError) as exc:
                        findings.append(str(exc))
            declarations = read_json(row.get("declarations", "").strip() or "{}")
            result.append(
                Acquisition(
                    row["subject_id"].strip(),
                    row.get("session_id", "").strip(),
                    row.get("acquisition_id", "").strip() or "default",
                    paths,
                    {"manifest_row": number, "columns": row},
                    declarations,
                    tuple(findings),
                )
            )
    return Study(
        study_id, str(root), tuple(result), {"method": "manifest", "path": str(path.resolve())}
    )


def manifest_template(path: Path):
    with Path(path).open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, delimiter="\t" if Path(path).suffix == ".tsv" else ",")
        writer.writerow(
            [
                "subject_id",
                "session_id",
                "acquisition_id",
                *[r + "_path" for r in ROLES],
                "declarations",
            ]
        )


def entities(path: Path) -> tuple[dict[str, str], str]:
    name = path.name
    for extension in (".nii.gz", ".nii", ".json", ".bval", ".bvec"):
        if name.endswith(extension):
            name = name[: -len(extension)]
            break
    tokens = name.split("_")
    values = {}
    for token in tokens[:-1]:
        match = re.fullmatch(r"([a-zA-Z][a-zA-Z0-9]*)-([a-zA-Z0-9]+)", token)
        if not match or match[1] in values:
            raise ValueError(f"Invalid or duplicate BIDS entity: {path.name}")
        values[match[1]] = match[2]
    if not re.fullmatch("[a-zA-Z0-9]+", tokens[-1]):
        raise ValueError(f"Invalid BIDS suffix: {path.name}")
    return values, tokens[-1]


def inheritance(root: Path, image: Path, extension: str) -> tuple[list[Path], dict]:
    """Resolve applicable ancestor sidecars; reject incomparable conflicting rules."""
    target, suffix = entities(image)
    selected, metadata = [], {}
    parents = [image.parent, *image.parent.parents]
    parents = list(reversed(parents[: parents.index(root) + 1]))
    for parent in parents:
        matches = []
        for candidate in sorted(parent.glob("*" + extension)):
            try:
                values, candidate_suffix = entities(candidate)
            except ValueError:
                continue
            if candidate_suffix == suffix and values.items() <= target.items():
                contained(root, candidate)
                matches.append((values, candidate))
        matches.sort(key=lambda item: len(item[0]))
        for i, (values, candidate) in enumerate(matches):
            for other, other_path in matches[:i]:
                if other == values:
                    raise ValueError(
                        f"Duplicate BIDS inheritance scope: {candidate.name}, {other_path.name}"
                    )
                if not (other.items() <= values.items() or values.items() <= other.items()):
                    raise ValueError(
                        f"Ambiguous BIDS inheritance: {candidate.name}, {other_path.name}"
                    )
            selected.append(candidate)
            if extension == ".json":
                value = read_json(candidate.read_text())
                if not isinstance(value, dict):
                    raise ValueError("BIDS metadata must be an object")
                metadata.update(value)
    return selected, metadata


def discover_bids(root: Path, study_id: str) -> Study:
    root = Path(root).resolve(strict=True)
    description = read_json(contained(root, "dataset_description.json").read_text())
    if description.get("DatasetType", "raw") != "raw":
        raise ValueError(
            "Derivative/study datasets require explicit manifest roles and processing provenance"
        )
    if not description.get("BIDSVersion") or not description.get("Name"):
        raise ValueError("BIDS dataset_description requires Name and BIDSVersion")
    result = []
    # Deliberately bounded standard layouts; never descend into derivatives or symlink directories.
    for subject in sorted(root.glob("sub-*")):
        if subject.is_symlink() or not subject.is_dir():
            continue
        sessions = [subject, *sorted(subject.glob("ses-*"))]
        for session in sessions:
            if session.is_symlink() or not session.is_dir():
                continue
            dwi_dir = session / "dwi"
            if dwi_dir.is_symlink():
                continue
            for image in sorted(dwi_dir.glob("*.nii*")):
                if not image.name.endswith((".nii", ".nii.gz")):
                    continue
                values, suffix = entities(image)
                if suffix != "dwi":
                    continue
                sid = subject.name[4:]
                ses = session.name[4:] if session != subject else ""
                findings, paths, metadata = (
                    [],
                    {"dwi": str(contained(root, image))},
                    {"entities": values, "processing_state": "raw"},
                )
                if values.get("sub") != sid or values.get("ses", "") != ses:
                    findings.append("BIDS filename subject/session disagrees with its directory")
                if "space" in values or "desc" in values:
                    findings.append(
                        "Derivative entities found in raw layout; explicit import required"
                    )
                for extension, role in ((".json", "json"), (".bval", "bval"), (".bvec", "bvec")):
                    try:
                        sidecars, merged = inheritance(root, image, extension)
                        if extension == ".json":
                            metadata["inherited_metadata"] = merged
                            metadata["metadata_sources"] = [str(p) for p in sidecars]
                            # Multiple JSON sources stay separate; no fake flattened source file.
                            if len(sidecars) == 1:
                                paths[role] = str(sidecars[0])
                        elif sidecars:
                            paths[role] = str(sidecars[-1])
                        else:
                            findings.append(f"Missing {role} for DWI")
                    except (OSError, ValueError) as exc:
                        findings.append(str(exc))
                anatomical = []
                anat_dir = session / "anat"
                if not anat_dir.is_symlink():
                    for t1 in sorted(anat_dir.glob("*_T1w.nii*")):
                        e, s = entities(t1)
                        if s == "T1w" and e.get("sub") == sid and e.get("ses", "") == ses:
                            anatomical.append(contained(root, t1))
                if len(anatomical) == 1:
                    paths["t1"] = str(anatomical[0])
                elif len(anatomical) > 1:
                    metadata["anatomy_candidates"] = [str(p) for p in anatomical]
                    metadata["anatomy_association"] = "ambiguous; select explicitly"
                aid = (
                    "_".join(
                        f"{k}-{v}" for k, v in sorted(values.items()) if k not in {"sub", "ses"}
                    )
                    or "default"
                )
                result.append(
                    Acquisition(
                        sid, ses, aid, paths, metadata, {"processing_state": "raw"}, tuple(findings)
                    )
                )
    return Study(
        study_id,
        str(root),
        tuple(result),
        {
            "method": "bids",
            "description": description,
            "scope": "DWI/anatomy discovery, not full BIDS validation",
        },
    )


def discover_custom(root: Path, study_id: str, mapping: dict) -> Study:
    """Each acquisition directory is an explicit unit. Multiple matches block that role."""
    root = Path(root).resolve(strict=True)
    allowed = {"subject_pattern", "session_pattern", "acquisition_pattern", "files", "declarations"}
    if set(mapping) - allowed or set(mapping.get("files", {})) - set(ROLES):
        raise ValueError("Unsupported custom mapping fields")
    for pattern in [
        mapping.get("subject_pattern", "*"),
        mapping.get("session_pattern", ""),
        mapping.get("acquisition_pattern", ""),
        *mapping.get("files", {}).values(),
    ]:
        if Path(pattern).is_absolute() or ".." in Path(pattern).parts or "**" in pattern:
            raise ValueError(
                "Mapping patterns must be bounded, relative and cannot contain ** or .."
            )
    result = []
    for subject in sorted(root.glob(mapping.get("subject_pattern", "*"))):
        if not subject.is_dir() or subject.is_symlink():
            continue
        sessions = (
            sorted(subject.glob(mapping["session_pattern"]))
            if mapping.get("session_pattern")
            else [subject]
        )
        for session in sessions:
            if not session.is_dir() or session.is_symlink():
                continue
            acquisitions = (
                sorted(session.glob(mapping["acquisition_pattern"]))
                if mapping.get("acquisition_pattern")
                else [session]
            )
            for acquisition in acquisitions:
                if not acquisition.is_dir() or acquisition.is_symlink():
                    continue
                paths, findings = {}, []
                for role, pattern in mapping.get("files", {}).items():
                    matches = sorted(acquisition.glob(pattern))
                    if len(matches) != 1:
                        findings.append(
                            f"{role}: {len(matches)} candidates; explicit association required"
                        )
                    else:
                        try:
                            paths[role] = str(contained(acquisition, matches[0]))
                        except (OSError, ValueError) as exc:
                            findings.append(str(exc))
                result.append(
                    Acquisition(
                        subject.name,
                        session.name if session != subject else "",
                        acquisition.name if acquisition != session else "default",
                        paths,
                        {"custom_mapping": mapping},
                        mapping.get("declarations", {}),
                        tuple(findings),
                    )
                )
    return Study(study_id, str(root), tuple(result), {"method": "custom", "mapping": mapping})


def associated_path(acquisition, role, field):
    """Named processing derivatives do not masquerade as independent acquisitions."""
    if role.startswith("derivative:"):
        derivative = acquisition.derivatives[role.removeprefix("derivative:")]
        return getattr(derivative, field)
    return acquisition.paths[role]
