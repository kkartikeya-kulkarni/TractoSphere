"""Only unambiguous sidecars may be proposed; scientific import remains separate."""

from dmri_studio.core.input_associations import suggest_dwi_companions


def test_hcp_sibling_gradient_suggestion(tmp_path):
    image = tmp_path / "data.nii.gz"
    image.write_bytes(b"header placeholder")
    (tmp_path / "bvals").write_text("0 1000")
    (tmp_path / "bvecs").write_text("0 1\n0 0\n0 0")
    found = suggest_dwi_companions(image)
    assert found.bval == tmp_path / "bvals"
    assert found.bvec == tmp_path / "bvecs"
    assert found.sidecar is None
    assert "validation still run" in found.message


def test_conflicting_exact_and_hcp_siblings_are_not_guessed(tmp_path):
    image = tmp_path / "data.nii.gz"
    image.write_bytes(b"header placeholder")
    for name in ("bvals", "bvecs", "data.bval", "data.bvec"):
        (tmp_path / name).write_text("0")
    found = suggest_dwi_companions(image)
    assert found.bval is None and found.bvec is None
    assert "Multiple bval candidates" in found.message


def test_linked_gradient_is_not_silently_associated(tmp_path):
    image = tmp_path / "data.nii.gz"
    image.write_bytes(b"header placeholder")
    elsewhere = tmp_path / "other-acquisition-bvals"
    elsewhere.write_text("0 1000")
    (tmp_path / "bvals").symlink_to(elsewhere)
    (tmp_path / "bvecs").write_text("0 1\n0 0\n0 0")
    found = suggest_dwi_companions(image)
    assert found.bval is None
    assert found.bvec == tmp_path / "bvecs"


def test_bids_inheritance_uses_existing_discovery_contract(tmp_path):
    root = tmp_path / "study"
    dwi = root / "sub-01" / "dwi"
    dwi.mkdir(parents=True)
    (root / "dataset_description.json").write_text('{"Name":"Test","BIDSVersion":"1.9.0"}')
    image = dwi / "sub-01_dwi.nii.gz"
    image.write_bytes(b"header placeholder")
    (root / "dwi.bval").write_text("0 1000")
    (root / "dwi.bvec").write_text("0 1\n0 0\n0 0")
    (root / "dwi.json").write_text('{"PhaseEncodingDirection":"j"}')
    found = suggest_dwi_companions(image)
    assert found.bval == root / "dwi.bval"
    assert found.bvec == root / "dwi.bvec"
    assert found.sidecar == root / "dwi.json"


def test_multiple_inherited_json_sources_require_bids_route(tmp_path):
    root = tmp_path / "study"
    dwi = root / "sub-01" / "dwi"
    dwi.mkdir(parents=True)
    (root / "dataset_description.json").write_text('{"Name":"Test","BIDSVersion":"1.9.0"}')
    image = dwi / "sub-01_dwi.nii.gz"
    image.write_bytes(b"header placeholder")
    (root / "dwi.json").write_text('{"PhaseEncodingDirection":"j"}')
    (dwi / "sub-01_dwi.json").write_text('{"TotalReadoutTime":0.05}')
    found = suggest_dwi_companions(image)
    assert found.sidecar is None
    assert "Multiple inherited BIDS JSON" in found.message
