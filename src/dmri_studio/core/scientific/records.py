"""Exclusive serialized task selections; restoring always reinspects original inputs.

These are proposal records, not completed engine manifests or resumable runs.
Never trust stored geometry, measurements or command text as executable evidence.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..execution import _publish_json
from ..process import check_cancel
from .contracts import Artifact, ImportRequest
from .imports import import_artifact
from .planning import Task


def read_json(text: str) -> Any:
    """Reject duplicate keys and nonstandard NaN/Infinity in configuration files."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        """Reject duplicate keys rather than silently replace a scientific choice."""
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"Duplicate scientific configuration key: {key}")
            result[key] = value
        return result

    def invalid(token: str) -> None:
        """Refuse nonstandard nonfinite JSON configuration values."""
        raise ValueError(f"Nonfinite JSON value is not permitted: {token}")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)


def save_selection(path: Path, artifacts: tuple[Artifact, ...], tasks: tuple[Task, ...]) -> None:
    """Publish a new selection without overwriting sources, prior plans or runs."""
    _publish_json(
        Path(path).expanduser().absolute(),
        dict(
            schema="tractosphere.scientific-selection/1",
            artifacts=[a.to_dict() for a in artifacts],
            tasks=[t.to_dict() for t in tasks],
        ),
    )


def restore_selection(
    path: Path, *, cancelled=lambda: False, progress=lambda _: None
) -> tuple[tuple[Artifact, ...], tuple[Task, ...]]:
    """Reimport selected files and compare content IDs; no process can be resumed."""
    path = Path(path).expanduser().absolute()
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError("Selection exceeds the 16MiB metadata limit")
    data = read_json(path.read_text(encoding="utf-8"))
    if data.get("schema") != "tractosphere.scientific-selection/1":
        raise ValueError("Choose a scientific selection record, not an execution or QC manifest")
    artifacts = []
    for saved in data["artifacts"]:
        check_cancel(cancelled)
        request = ImportRequest(**saved["request"])
        actual = import_artifact(request, cancelled=cancelled, progress=progress)
        if actual.id != saved["id"]:
            raise ValueError(
                "Source bytes or declared meaning changed; import the changed input explicitly"
            )
        artifacts.append(actual)
    tasks = tuple(Task.from_dict(t) for t in data["tasks"])
    return tuple(artifacts), tasks
