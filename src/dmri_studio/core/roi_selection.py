"""Pure selection rules for original atlas label IDs, independent of display/filtering."""

from __future__ import annotations

import re
from collections.abc import Iterable


def parse_ids(text: str, available: Iterable[int], *, limit: int = 10000) -> list[int]:
    """Parse comma-separated IDs/ranges, preserving first occurrence and entered order."""
    if not text.strip():
        raise ValueError("Enter label IDs, for example 1, 3, 7-12.")
    result: list[int] = []
    seen: set[int] = set()
    allowed = set(available)
    for token in text.split(","):
        match = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", token)
        if not match:
            raise ValueError(f"Invalid label expression: {token!r}. Use IDs and ascending ranges.")
        start = int(match[1])
        end = int(match[2] or start)
        if end < start or end - start + 1 > limit:
            raise ValueError("Use ascending ranges of at most 10,000 labels.")
        for value in range(start, end + 1):
            if value not in seen:
                result.append(value)
                seen.add(value)
        if len(result) > limit:
            raise ValueError("Selection exceeds the 10,000-label safety limit.")
    unknown = [value for value in result if value not in allowed]
    if unknown:
        raise ValueError("IDs absent from this atlas: " + ", ".join(map(str, unknown[:30])))
    return result


def select_names(query: str, names: dict[int, str]) -> list[int]:
    """Case-insensitive partial name matches; caller provides only actual table names."""
    fragments = [part.strip().casefold() for part in query.split(",") if part.strip()]
    if not fragments:
        raise ValueError("Enter an anatomical name or comma-separated name fragments.")
    return [key for key, name in names.items() if any(f in name.casefold() for f in fragments)]


def apply_selection(current: Iterable[int], requested: Iterable[int], mode: str) -> list[int]:
    """Apply replace/add/remove while retaining a reproducible waypoint order."""
    current, requested = list(dict.fromkeys(current)), list(dict.fromkeys(requested))
    if mode == "replace":
        return requested
    if mode == "add":
        return list(dict.fromkeys(current + requested))
    if mode == "remove":
        return [key for key in current if key not in set(requested)]
    raise ValueError("Selection mode must be replace, add or remove.")
