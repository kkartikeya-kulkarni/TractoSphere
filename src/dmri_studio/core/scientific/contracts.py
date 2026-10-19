"""Immutable input contracts shared by Python, CLI, GUI and future scheduling.

Requests are explicit user declarations, not inferred processing history. An
artifact binds original source bytes and inspection coverage without copying MRI.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..execution import hash_file
from ..validation import freeze_json, json_compatible

KINDS = (
    "spatial_transform",
    "dwi",
    "tensor",
    "fod",
    "mask",
    "anatomy",
    "atlas_labels",
    "atlas_probability",
    "roi",
    "5tt",
    "gmwmi",
    "tractogram",
    "response",
    "tissue_scalar",
    "density",
    "connectome_matrix",
    "streamline_weights",
)


def validate_declarations(value: Any) -> None:
    """Reject non-JSON/nonfinite configuration instead of formatting it as evidence."""
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Scientific configuration must contain finite numbers")
        return
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("Scientific configuration keys must be strings")
        for item in value.values():
            validate_declarations(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            validate_declarations(item)
        return
    raise ValueError("Scientific configuration must use JSON types, not arbitrary objects")


@dataclass(frozen=True)
class ImportRequest:
    """Explicit artifact type and scientific convention; no filename guessing."""

    kind: str
    path: Path
    bval: Path | None = None
    bvec: Path | None = None
    sidecar: Path | None = None
    declarations: Any = field(default_factory=dict)
    provenance: str = "External input; processing history and scientific acceptance unknown."

    def __post_init__(self) -> None:
        """Freeze the contract and reject ambiguous or unsupported input meaning."""
        if self.kind not in KINDS:
            raise ValueError(f"Unsupported scientific input kind: {self.kind}")
        for name in ("path", "bval", "bvec", "sidecar"):
            if (value := getattr(self, name)) is not None:
                object.__setattr__(self, name, Path(value).expanduser().absolute())
        if not isinstance(self.declarations, Mapping):
            raise ValueError("Declarations must be an object")
        validate_declarations(self.declarations)
        for key in (
            "space_id",
            "subject_id",
            "session_id",
            "processing_state",
            "normalization",
            "tensor_layout",
            "tensor_frame",
            "tensor_units",
            "sh_basis",
            "sh_frame",
            "tissue",
            "channels",
            "tractogram_id",
        ):
            if key in self.declarations and not isinstance(self.declarations[key], str):
                raise ValueError(f"{key} must be an explicit text declaration")
        history = self.declarations.get("inherited_processing_history")
        if history is not None and (
            not isinstance(history, Mapping) or not isinstance(history.get("claims"), (list, tuple))
        ):
            raise ValueError(
                "Inherited history must contain reported claims; it is not authenticated evidence"
            )
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError(
                "Record a provenance statement, including unknown history when appropriate"
            )
        object.__setattr__(self, "declarations", freeze_json(self.declarations))


@dataclass(frozen=True)
class Artifact:
    """Verified structure plus declarations; never a scientific acceptance claim."""

    payload: Any

    def __post_init__(self) -> None:
        """Freeze the contract and reject ambiguous or unsupported input meaning."""
        object.__setattr__(self, "payload", freeze_json(self.payload))

    @property
    def id(self) -> str:
        """Return the immutable source/declaration content identifier."""
        return self.payload["id"]

    @property
    def kind(self) -> str:
        """Return the explicitly selected scientific artifact type."""
        return self.payload["kind"]

    def to_dict(self) -> dict:
        """Return detached JSON-compatible contract evidence for review or persistence."""
        return json_compatible(self.payload)

    def assert_current(self, cancelled=lambda: False) -> None:
        """Rehash every bound image, gradient, label/provenance sidecar before reuse."""
        for source in self.payload["sources"]:
            path = Path(source["path"])
            if hash_file(path, cancelled) != source["sha256"]:
                raise ValueError(f"Imported source changed; reimport {path.name}")


def artifact_id(kind: str, sources: list[dict], declarations: dict, provenance: str) -> str:
    """Identify bytes AND declared scientific meaning without using timestamps."""
    raw = json.dumps([kind, sources, declarations, provenance], sort_keys=True, allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]
