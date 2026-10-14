"""Archive integrity contracts and local-only processing storage boundaries.

Archival metadata never rewrites scientific provenance. A restored image must
match its recorded bytes. Cloud synchronization status alone cannot authorize
local deletion; this module deliberately has no delete operation.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def cloud_storage_path(path: Path) -> bool:
    """Recognize macOS File Provider/Drive paths and explicitly configured roots.

    Check lexical and resolved locations so symlinks cannot hide streamed input.
    Other network filesystems are not classified as cloud by their names alone.
    DMRI_STREAMING_ROOTS may list additional mount roots using os.pathsep.
    """
    roots = [
        Path(p).expanduser().resolve()
        for p in os.environ.get("DMRI_STREAMING_ROOTS", "").split(os.pathsep)
        if p
    ]
    for candidate in (path.expanduser().absolute(), path.expanduser().resolve()):
        if (
            "CloudStorage" in candidate.parts
            or any(part.startswith("GoogleDrive-") for part in candidate.parts)
            or "DriveFS" in candidate.parts
        ):
            return True
        if any(candidate == root or root in candidate.parents for root in roots):
            return True
    return False


def require_local_processing(paths) -> None:
    """Reject streaming inputs/output before launching scientific commands."""
    for value in paths:
        if value and cloud_storage_path(Path(value)):
            raise ValueError(
                "Processing requires local staged files, not Google Drive/File Provider streaming storage: "
                + str(value)
                + ". Restore and verify required inputs in a local workspace first."
            )


def verify_remote_copy(
    local_path: Path, metadata: dict, *, expected_account: str, actual_account: str
) -> dict:
    """Bind a server file ID, length and server SHA-256 to unchanged local bytes.

    Caller must supply actual authenticated files.get/download evidence. Local
    cache size, a green sync icon or an MD5 alone is insufficient for this contract.
    Missing server SHA requires an independently downloaded SHA before promotion.
    """
    from .execution import hash_file

    if actual_account.casefold() != expected_account.casefold():
        raise ValueError("Archive account mismatch")
    before = local_path.stat()
    digest = hash_file(local_path)
    after = local_path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    ):
        raise ValueError("Local file changed while verifying archive")
    if (
        not metadata.get("id")
        or metadata.get("trashed") is not False
        or int(metadata.get("size", -1)) != before.st_size
        or metadata.get("sha256Checksum") != digest
    ):
        raise ValueError("Remote file presence, length and SHA-256 have not all been verified")
    return {
        "original": str(local_path.absolute()),
        "remote_id": metadata["id"],
        "account": actual_account,
        "sha256": digest,
        "bytes": before.st_size,
        "mtime_ns": before.st_mtime_ns,
        "status": "remote_integrity_verified",
        "deletion_authorized": False,
    }


def restore_archived_file(entry: dict, readable_cloud_file: Path, local_target: Path) -> Path:
    """Stream one requested archived file to a new local path, checking its SHA.

    The caller obtains an authenticated cloud download or Drive-desktop path.
    No MRI command runs from that path. An incomplete restore remains labelled
    .partial for diagnosis and never replaces an existing target.
    """
    if entry.get("status") != "remote_integrity_verified":
        raise ValueError("Restore requires a remotely verified archive entry")
    require_local_processing([local_target])
    if local_target.exists() or local_target.is_symlink():
        raise ValueError("Restore target exists; overwrite refused")
    partial = local_target.with_name(local_target.name + ".partial")
    digest = hashlib.sha256()
    count = 0
    with readable_cloud_file.open("rb") as source, partial.open("xb") as destination:
        while block := source.read(8 * 1024**2):
            destination.write(block)
            count += len(block)
            digest.update(block)
    if count != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
        raise ValueError("Restore integrity failed; partial file retained without promotion")
    # Exclusive link/publish avoids a replace race; this is our own temporary file.
    os.link(partial, local_target)
    partial.unlink()
    return local_target
