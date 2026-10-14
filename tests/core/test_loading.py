"""Synthetic loader checks; no real subject data are opened or copied.

Run ``python -m pytest tests/core/test_loading.py``. Fixtures are generated in
pytest's temporary folders, and deliberately malformed headers are patched in
place there so a saving library cannot repair the condition under test.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import nibabel as nib
import numpy as np
import pytest

from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.loading import DatasetLoader, LoadProgress
from dmri_studio.core.validation import CHECK_CODES


def _files(
    tmp_path: Path,
    *,
    shape: tuple[int, ...] = (8, 9, 10, 4),
    compressed: bool = False,
    nifti2: bool = False,
    values: np.ndarray | None = None,
    sidecar: dict[str, Any] | None = None,
) -> DatasetPaths:
    """Create a small image with matched FSL gradients in a temporary folder."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    image_class = nib.Nifti2Image if nifti2 else nib.Nifti1Image
    data = np.ones(shape, dtype=np.float32) if values is None else values
    affine = np.diag([2.0, 2.0, 2.0, 1.0])
    image = image_class(data, affine)
    image.set_qform(affine, code=1)
    image.set_sform(affine, code=1)
    image.header.set_xyzt_units("mm")
    dwi = tmp_path / ("data.nii.gz" if compressed else "data.nii")
    nib.save(image, dwi)
    count = shape[3] if len(shape) >= 4 else 1
    bvals = np.full(count, 1000.0)
    bvals[0] = 0
    bvecs = np.zeros((3, count))
    bvecs[0, 1:] = 1
    bval, bvec = tmp_path / "bvals", tmp_path / "bvecs"
    np.savetxt(bval, bvals[None, :])
    np.savetxt(bvec, bvecs)
    json_path = tmp_path / "data.json" if sidecar is not None else None
    if json_path is not None:
        json_path.write_text(json.dumps(sidecar), encoding="utf-8")
    return DatasetPaths(dwi, bval, bvec, json_path)


def _patch(paths: DatasetPaths, mutate: Callable[[Any], None]) -> None:
    """Change a fixed header without letting nib.save normalize its fields."""
    with paths.dwi.open("r+b") as stream:
        prefix = stream.read(4)
        length = int.from_bytes(prefix, "little", signed=True)
        header_class = nib.Nifti1Header if length == 348 else nib.Nifti2Header
        stream.seek(0)
        header = header_class(stream.read(length), check=False)
        mutate(header)
        stream.seek(0)
        stream.write(header.binaryblock)


def _find(result: Any, code: str, status: str | None = None) -> list[Any]:
    """Select findings without depending on diagnostic wording or row order."""
    return [
        check
        for check in result.report.checks
        if check.code == code and (status is None or check.status == status)
    ]


@pytest.mark.parametrize(
    "compressed,nifti2", [(False, False), (True, False), (False, True), (True, True)]
)
def test_valid_nifti_variants_have_complete_reports(
    tmp_path: Path, compressed: bool, nifti2: bool
) -> None:
    """Both supported versions/compressions share the public load contract."""
    result = DatasetLoader().load(_files(tmp_path, compressed=compressed, nifti2=nifti2))
    assert result.report.can_load(), result.report.to_json()
    assert result.dataset is not None
    assert {c.code for c in result.report.checks} == set(CHECK_CODES)
    assert result.dataset.shape == (8, 9, 10, 4)
    assert result.dataset.default_preview_volume() == 0
    assert result.report.metadata["nifti_version"] == (2 if nifti2 else 1)
    assert len(result.report.sampled_regions) == 2
    assert result.report.metadata["sampled_voxel_count"] == 8 * 9 * 10 * 2
    assert bool(_find(result, "N05", "skipped")) == compressed
    assert json.loads(result.report.to_json())["coverage"] == "metadata_and_samples"
    result.dataset.close()


def test_single_volume_is_sampled_once(tmp_path: Path) -> None:
    """A structurally valid 4D singleton is not mistaken for a 3D image."""
    result = DatasetLoader().load(_files(tmp_path, shape=(2, 3, 4, 1)))
    assert result.report.can_load()
    assert len(result.report.sampled_regions) == 1
    assert result.report.metadata["sampled_voxel_count"] == 24


def test_relative_paths_survive_later_working_directory_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dataset belongs to its captured sources, not the caller's later cwd."""
    paths = _files(tmp_path / "inputs")
    monkeypatch.chdir(paths.dwi.parent)
    result = DatasetLoader().load(DatasetPaths("data.nii", "bvals", "bvecs"))
    assert result.report.can_load()
    monkeypatch.chdir(tmp_path)
    np.testing.assert_array_equal(result.dataset.read_slice(0), np.ones((8, 9)))
    result.dataset.close()


def test_no_b0_default_preview_uses_first_volume(tmp_path: Path) -> None:
    """Absence of b0 permits inspection with the documented first-volume fallback."""
    paths = _files(tmp_path)
    paths.bval.write_text("1000 1000 1000 1000")
    paths.bvec.write_text("1 1 1 1\n0 0 0 0\n0 0 0 0\n")
    result = DatasetLoader().load(paths)
    assert result.report.can_load()
    assert result.dataset.default_preview_volume() == 0
    assert _find(result, "G06", "warn")


def test_failed_voxel_reads_do_not_claim_sample_coverage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed I/O error produces failure, skipped finiteness, and honest coverage."""
    paths = _files(tmp_path)

    def failed_read(*args: Any, **kwargs: Any) -> None:
        """Simulate an unavailable source after successful header inspection."""
        raise OSError("simulated disk read failure")

    monkeypatch.setattr(nib.arrayproxy.ArrayProxy, "__getitem__", failed_read)
    result = DatasetLoader().load(paths)
    assert result.dataset is None
    assert result.report.status() == "fail"
    assert "voxel samples were not read" in result.report.coverage_message()
    assert "voxel samples were not read" in _find(result, "C03")[0].message


def test_only_exact_centered_blocks_are_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proxy instrumentation rejects every full-array conversion and excess read."""
    paths = _files(tmp_path, shape=(19, 22, 25, 5))
    requests: list[Any] = []
    original = nib.arrayproxy.ArrayProxy.__getitem__

    def getitem(proxy: Any, request: Any) -> Any:
        requests.append(request)
        return original(proxy, request)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Loader requested a complete image array")

    monkeypatch.setattr(nib.arrayproxy.ArrayProxy, "__getitem__", getitem)
    monkeypatch.setattr(nib.arrayproxy.ArrayProxy, "__array__", forbidden)
    monkeypatch.setattr(nib.Nifti1Image, "get_fdata", forbidden)
    result = DatasetLoader().load(paths)
    assert result.report.can_load(), result.report.to_json()
    assert requests == [
        (slice(1, 17), slice(3, 19), slice(4, 20), 0),
        (slice(1, 17), slice(3, 19), slice(4, 20), 4),
    ]
    assert result.report.metadata["sampled_voxel_count"] == 8192


@pytest.mark.parametrize("role", ["dwi", "bval", "bvec"])
def test_missing_required_path_is_reported(tmp_path: Path, role: str) -> None:
    """A missing reference is a finding, not a construction traceback."""
    paths = _files(tmp_path)
    supplied = paths.supplied()
    supplied[role] = None
    result = DatasetLoader().load(DatasetPaths(**supplied))
    assert result.dataset is None
    assert _find(result, "F01", "fail")
    assert result.report.complete


@pytest.mark.parametrize("kind", ["missing", "directory", "empty", "broken_symlink"])
def test_unusable_files_are_reported(tmp_path: Path, kind: str) -> None:
    """Required roles must be actual readable nonempty regular files."""
    paths = _files(tmp_path)
    bad = tmp_path / "bad"
    if kind == "directory":
        bad.mkdir()
    elif kind == "empty":
        bad.touch()
    elif kind == "broken_symlink":
        bad.symlink_to(tmp_path / "absent")
    result = DatasetLoader().load(DatasetPaths(paths.dwi, bad, paths.bvec))
    assert _find(result, "F02", "fail")
    assert result.dataset is None


def test_same_file_or_hardlink_cannot_fill_multiple_roles(tmp_path: Path) -> None:
    """Identity checks catch aliasing even when the selected paths differ."""
    paths = _files(tmp_path)
    alias = tmp_path / "alias"
    alias.hardlink_to(paths.bval)
    result = DatasetLoader().load(DatasetPaths(paths.dwi, paths.bval, alias))
    assert _find(result, "F03", "fail")


@pytest.mark.parametrize(
    "names", [(), ("grad_dev.nii",), ("grad_dev.nii.gz",), ("grad_dev.nii", "grad_dev.nii.gz")]
)
def test_gradient_deviation_detection_is_presence_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]
) -> None:
    """Even invalid deviation contents are never opened, parsed, or applied."""
    paths = _files(tmp_path)
    for name in names:
        (tmp_path / name).write_bytes(b"Not imaging data")
    original = Path.open

    def guarded(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.name.startswith("grad_dev"):
            raise AssertionError("Gradient deviation file was opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    result = DatasetLoader().load(paths)
    assert result.report.can_load()
    checks = _find(result, "F05", "info")
    assert len(checks) == 1
    assert tuple(Path(value).name for value in checks[0].observed) == names


@pytest.mark.parametrize("shape", [(4, 4, 4), (4, 4, 4, 2, 1)])
def test_loader_does_not_promote_or_squeeze_dimensions(
    tmp_path: Path, shape: tuple[int, ...]
) -> None:
    """NIfTI interpretation must preserve a declared four-dimensional series."""
    result = DatasetLoader().load(_files(tmp_path, shape=shape))
    assert _find(result, "N01", "fail")
    assert result.dataset is None


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("pixdim", 0.0, "N03"),
        ("pixdim", -2.0, "N03"),
        ("pixdim", np.nan, "N03"),
        ("bitpix", 64, "N02"),
        ("intent_code", 1007, "N02"),
        ("vox_offset", 0.0, "N05"),
        ("vox_offset", 352.5, "N05"),
        ("vox_offset", np.nan, "N05"),
        ("scl_slope", np.inf, "N06"),
        ("scl_inter", np.inf, "N06"),
        ("qform_code", 123, "A01"),
        ("sform_code", 123, "A01"),
    ],
)
def test_invalid_raw_fields_cannot_be_repaired_into_success(
    tmp_path: Path, field: str, value: Any, code: str
) -> None:
    """Validate original on-disk fields before convenience-reader repairs."""
    paths = _files(tmp_path)

    def mutate(header: Any) -> None:
        if field == "pixdim":
            header[field][1] = value
        else:
            header[field] = value

    _patch(paths, mutate)
    before = paths.dwi.read_bytes()
    result = DatasetLoader().load(paths)
    assert _find(result, code, "fail"), result.report.to_json()
    assert result.dataset is None
    assert paths.dwi.read_bytes() == before


@pytest.mark.parametrize("nifti2", [False, True])
def test_truncated_payload_fails_without_full_read(tmp_path: Path, nifti2: bool) -> None:
    """The uncompressed length check covers missing bytes beyond sample regions."""
    paths = _files(tmp_path, nifti2=nifti2)
    with paths.dwi.open("r+b") as stream:
        stream.truncate(paths.dwi.stat().st_size - 1)
    result = DatasetLoader().load(paths)
    assert _find(result, "N05", "fail")


def test_extension_envelope_rejects_record_overlapping_voxels(tmp_path: Path) -> None:
    """Malformed extension size cannot cause a read through the image payload."""
    paths = _files(tmp_path)
    with paths.dwi.open("r+b") as stream:
        stream.seek(348)
        stream.write(b"\x01\x00\x00\x00")
    result = DatasetLoader().load(paths)
    assert _find(result, "N05", "fail")


def test_valid_extension_is_retained_and_loads(tmp_path: Path) -> None:
    """A legal extension enlarges the data offset without becoming image data."""
    paths = _files(tmp_path)
    image = nib.load(paths.dwi)
    data = np.asarray(image.dataobj).copy()
    replacement = nib.Nifti1Image(data, image.affine, image.header)
    replacement.header.extensions.append(nib.nifti1.Nifti1Extension(6, b"synthetic comment"))
    nib.save(replacement, paths.dwi)
    result = DatasetLoader().load(paths)
    assert result.report.can_load(), result.report.to_json()
    assert result.report.metadata["data_offset"] > 352


@pytest.mark.parametrize(
    "slope,intercept,expected",
    [(2.0, 3.0, 5.0), (-2.0, 3.0, 1.0), (0.0, np.inf, 1.0), (np.nan, np.inf, 1.0)],
)
def test_proxy_scaling_is_applied_exactly_once(
    tmp_path: Path, slope: float, intercept: float, expected: float
) -> None:
    """Raw undefined scaling ignores intercept; finite active scaling is preserved."""
    paths = _files(tmp_path)
    _patch(
        paths, lambda h: (h.__setitem__("scl_slope", slope), h.__setitem__("scl_inter", intercept))
    )
    result = DatasetLoader().load(paths)
    assert result.report.can_load(), result.report.to_json()
    assert result.report.metadata["sample_minimum"] == expected
    assert result.report.metadata["sample_maximum"] == expected


def test_invalid_active_qform_is_not_hidden_by_valid_sform(tmp_path: Path) -> None:
    """Both coded transforms are validated, even when sform has precedence."""
    paths = _files(tmp_path)
    _patch(paths, lambda h: h.__setitem__("quatern_b", 2.0))
    result = DatasetLoader().load(paths)
    assert _find(result, "A01", "fail")


def test_fallback_geometry_is_explicitly_unverified(tmp_path: Path) -> None:
    """Missing transform codes remain loadable without invented anatomy labels."""
    paths = _files(tmp_path)
    _patch(paths, lambda h: (h.__setitem__("qform_code", 0), h.__setitem__("sform_code", 0)))
    result = DatasetLoader().load(paths)
    assert result.report.can_load(), result.report.to_json()
    assert _find(result, "A02", "warn")
    assert result.dataset.axis_codes == (None, None, None)


@pytest.mark.parametrize("units,factor", [("unknown", None), ("meter", 1000.0), ("micron", 0.001)])
def test_units_are_recorded_without_native_geometry_change(
    tmp_path: Path, units: str, factor: float | None
) -> None:
    """Only known unit codes authorize millimetre presentation metadata."""
    paths = _files(tmp_path)
    _patch(paths, lambda h: h.set_xyzt_units(units))
    result = DatasetLoader().load(paths)
    assert result.report.can_load(), result.report.to_json()
    assert result.dataset.voxel_sizes_native == (2.0, 2.0, 2.0)
    assert result.dataset.voxel_sizes_mm() == (None if factor is None else (2 * factor,) * 3)


def test_qform_sform_disagreement_uses_corner_displacement(tmp_path: Path) -> None:
    """Two individually valid transforms can disagree without blocking inspection."""
    paths = _files(tmp_path)
    _patch(paths, lambda h: h.__setitem__("qoffset_x", 0.6))
    result = DatasetLoader().load(paths)
    assert result.report.can_load()
    assert _find(result, "A03", "warn")
    assert result.dataset.affine_source == "sform"


@pytest.mark.parametrize(
    "value,code,status",
    [(np.nan, "V02", "fail"), (np.inf, "V02", "fail"), (-0.1, "V03", "info"), (0.0, "V03", "warn")],
)
def test_sample_value_severities(tmp_path: Path, value: float, code: str, status: str) -> None:
    """Negative finite signal is informational; sampled nonfinite signal blocks."""
    paths = _files(tmp_path, values=np.full((8, 9, 10, 4), value, dtype=np.float32))
    result = DatasetLoader().load(paths)
    assert _find(result, code, status)
    assert result.report.can_load() == np.isfinite(value)
    if value < 0:
        assert not _find(result, "V03", "warn")


@pytest.mark.parametrize(
    "document", ['{"a":1,"a":2}', "[1,2]", '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}', "{bad"]
)
def test_invalid_json_is_reported_without_losing_independent_checks(
    tmp_path: Path, document: str
) -> None:
    """Selected malformed metadata fails, but gradients and samples still run."""
    paths = _files(tmp_path, sidecar={})
    paths.json.write_text(document, encoding="utf-8")
    result = DatasetLoader().load(paths)
    assert _find(result, "J01", "fail")
    assert _find(result, "G07", "pass")
    assert _find(result, "V01", "pass")


def test_optional_acquisition_problems_are_warnings_and_preserved(tmp_path: Path) -> None:
    """Optional metadata errors do not silently change or block an inspectable DWI."""
    source = {
        "PhaseEncodingDirection": "AP",
        "TotalReadoutTime": True,
        "EffectiveEchoSpacing": [0.01],
        "EchoTime": 2,
        "RepetitionTime": 1,
        "ReconMatrixPE": False,
        "Custom": {"key": "retain me"},
    }
    result = DatasetLoader().load(_files(tmp_path, sidecar=source))
    assert result.report.can_load()
    assert all(_find(result, code, "warn") for code in ("J02", "J03", "J04", "J05"))
    assert result.dataset.sidecar["Custom"]["key"] == "retain me"
    assert result.dataset.sidecar["PhaseEncodingDirection"] == "AP"


@pytest.mark.parametrize("trt,warns", [(0.09, False), (0.091, True)])
def test_readout_relation_uses_acquisition_matrix(tmp_path: Path, trt: float, warns: bool) -> None:
    """ReconMatrixPE, rather than postprocessed image shape, determines timing."""
    source = {
        "PhaseEncodingDirection": "j-",
        "TotalReadoutTime": trt,
        "EffectiveEchoSpacing": 0.01,
        "ReconMatrixPE": 10,
        "EchoTime": 0.08,
        "RepetitionTime": 2.0,
    }
    result = DatasetLoader().load(_files(tmp_path, sidecar=source))
    assert result.report.can_load()
    assert bool(_find(result, "J04", "warn")) == warns


def test_sources_changed_during_load_prevent_activation(tmp_path: Path) -> None:
    """End-of-load fingerprints catch modifications after gradient parsing."""
    paths = _files(tmp_path)

    def changed(progress: LoadProgress) -> None:
        if progress.stage == "summary":
            with paths.bval.open("a") as stream:
                stream.write("\n")

    result = DatasetLoader().load(paths, progress=changed)
    assert _find(result, "C01", "fail")
    assert result.dataset is None


def test_symlink_retarget_during_load_is_detected(tmp_path: Path) -> None:
    """Fingerprints follow original selections rather than only canonical paths."""
    paths = _files(tmp_path)
    other = tmp_path / "other_bvals"
    other.write_bytes(paths.bval.read_bytes())
    link = tmp_path / "selected_bvals"
    link.symlink_to(paths.bval)

    def changed(progress: LoadProgress) -> None:
        if progress.stage == "summary":
            link.unlink()
            link.symlink_to(other)

    result = DatasetLoader().load(DatasetPaths(paths.dwi, link, paths.bvec), progress=changed)
    assert _find(result, "C01", "fail")


@pytest.mark.parametrize("cancel_stage", ["files", "samples", "summary"])
def test_cancellation_is_incomplete_not_success(tmp_path: Path, cancel_stage: str) -> None:
    """Cancellation retains completed evidence while discarding the candidate."""
    state = {"cancel": False}

    def progress(message: LoadProgress) -> None:
        if message.stage == cancel_stage:
            state["cancel"] = True

    result = DatasetLoader().load(
        _files(tmp_path), progress=progress, cancelled=lambda: state["cancel"]
    )
    assert result.dataset is None
    assert result.report.cancelled
    assert not result.report.complete
    assert result.report.status() == "incomplete"
    assert not result.report.can_load()
    if cancel_stage in ("files", "samples"):
        assert not _find(result, "V01", "pass")


def test_independent_json_and_gradient_errors_survive_bad_image(tmp_path: Path) -> None:
    """One malformed component does not prevent independent diagnostic work."""
    paths = _files(tmp_path, sidecar={})
    paths.dwi.write_bytes(b"not an image")
    paths.bval.write_text("bad tokens", encoding="utf-8")
    paths.json.write_text("[1,2]", encoding="utf-8")
    result = DatasetLoader().load(paths)
    assert _find(result, "F04", "fail")
    assert _find(result, "G01", "fail")
    assert _find(result, "J01", "fail")
    assert result.report.complete
    assert {c.code for c in result.report.checks} == set(CHECK_CODES)
