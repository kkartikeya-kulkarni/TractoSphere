"""Cloud storage cannot bypass local execution or cryptographic restore checks."""

import hashlib

import pytest

from dmri_studio.core.storage import (
    require_local_processing,
    restore_archived_file,
    verify_remote_copy,
)


def test_streaming_and_symlinked_paths_block_processing(tmp_path, monkeypatch):
    cloud = tmp_path / "CloudStorage" / "GoogleDrive-research"
    cloud.mkdir(parents=True)
    link = tmp_path / "local-looking"
    link.symlink_to(cloud, target_is_directory=True)
    for path in [cloud / "new-run", link / "data.nii"]:
        with pytest.raises(ValueError, match="local staged"):
            require_local_processing([path])
    custom = tmp_path / "mount"
    custom.mkdir()
    monkeypatch.setenv("DMRI_STREAMING_ROOTS", str(custom))
    with pytest.raises(ValueError, match="local staged"):
        require_local_processing([custom / "out"])
    require_local_processing([tmp_path / "local-data"])


def test_remote_proof_then_restore_no_delete(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"unchanged scientific bytes")
    metadata = {
        "id": "remote-file",
        "trashed": False,
        "size": str(source.stat().st_size),
        "sha256Checksum": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    with pytest.raises(ValueError, match="account mismatch"):
        verify_remote_copy(
            source,
            metadata,
            expected_account="lab@university.edu",
            actual_account="personal@gmail.com",
        )
    with pytest.raises(ValueError, match="not all been verified"):
        verify_remote_copy(
            source,
            metadata | {"sha256Checksum": None},
            expected_account="lab",
            actual_account="lab",
        )
    entry = verify_remote_copy(source, metadata, expected_account="lab", actual_account="lab")
    assert not entry["deletion_authorized"]
    target = restore_archived_file(entry, source, tmp_path / "restored")
    assert target.read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match="overwrite refused"):
        restore_archived_file(entry, source, target)
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity failed"):
        restore_archived_file(entry, source, tmp_path / "different")
    assert not (tmp_path / "different").exists() and (tmp_path / "different.partial").exists()
