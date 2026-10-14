"""Synthetic gradient checks; run with ``python -m pytest tests/core/test_gradients.py``.

All input files are tiny text fixtures under pytest's temporary directory.
No real images or participant data are needed or copied into the repository.
"""

import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest

from dmri_studio.core.gradients import GradientParseError, GradientTable
from dmri_studio.core.validation import ValidationPolicy


def files(tmp_path: Path, bvals: str, bvecs: str, *, layout: str = "auto") -> GradientTable:
    """Write independent b-value/vector fixtures and invoke the public parser."""
    value_path, vector_path = tmp_path / "bvals", tmp_path / "bvecs"
    value_path.write_text(bvals, encoding="utf-8")
    vector_path.write_text(bvecs, encoding="utf-8")
    return GradientTable.from_files(value_path, vector_path, layout=layout)


def checks(table: GradientTable, expected: int | None = None, **policy_options: object) -> tuple:
    """Validate without assuming a NIfTI exists in these focused tests."""
    return table.validate(expected_volumes=expected, policy=ValidationPolicy(**policy_options))


def statuses(results: tuple, code: str) -> set[str]:
    """Collect all statuses when a check has separate interpretation evidence."""
    return {result.status for result in results if result.code == code}


def test_fsl_bom_whitespace_preserves_values_and_complete_coverage(tmp_path: Path) -> None:
    """Ordinary FSL parsing changes storage layout, never scientific values."""
    table = files(tmp_path, "\ufeff0\t1.001e3\n\n", "0 0.6\n0 0.8\n0 0\n")
    np.testing.assert_array_equal(table.bvals, [0, 1001])
    np.testing.assert_array_equal(table.bvecs, [[0, 0, 0], [0.6, 0.8, 0]])
    assert table.source_layout == "3xN"
    result = checks(table, 2)
    assert {item.code for item in result} == {f"G{i:02d}" for i in range(1, 11)}
    assert not any(item.status in ("fail", "warn") for item in result)
    assert statuses(result, "G10") == {"info"}


@pytest.mark.parametrize("bvec_text, layout", [("1\n0\n0", "3xN"), ("1 0 0", "Nx3")])
def test_single_volume_bvec_layouts(tmp_path: Path, bvec_text: str, layout: str) -> None:
    """Singleton dimensions are preserved instead of squeezed unpredictably."""
    table = files(tmp_path, "1000", bvec_text)
    assert table.source_layout == layout
    assert table.bvecs.shape == (1, 3)
    assert not any(item.status == "fail" for item in checks(table, 1))


def test_column_bvals_and_n_by_three_report_interpretation(tmp_path: Path) -> None:
    """Accepted alternative text layouts leave an explicit report record."""
    table = files(tmp_path, "0\n1000\n", "0 0 0\n0 1 0")
    assert "info" in statuses(checks(table, 2), "G02")
    assert "info" in statuses(checks(table, 2), "G03")


def test_ambiguous_three_by_three_requires_explicit_override(tmp_path: Path) -> None:
    """No norm heuristic silently picks a layout for an ambiguous matrix."""
    text = "0 1 0\n0 0 1\n0 0 0"
    automatic = files(tmp_path, "0 1000 1000", text)
    explicit = files(tmp_path, "0 1000 1000", text, layout="Nx3")
    assert automatic.source_layout == "3xN"
    assert "warn" in statuses(checks(automatic, 3), "G03")
    assert "warn" not in statuses(checks(explicit, 3), "G03")
    np.testing.assert_array_equal(automatic.bvecs, explicit.bvecs.T)
    explicit_fsl = files(tmp_path, "0 1000 1000", text, layout="3xN")
    assert "warn" not in statuses(checks(explicit_fsl, 3), "G03")


@pytest.mark.parametrize(
    "text", ["0, 1000", "bvals 0 1000", "0 1000 # comment", "0 1_000", "0 1d3"]
)
def test_strict_text_syntax_and_location(tmp_path: Path, text: str) -> None:
    """Comments, headers, alternate delimiters, and Python literals are rejected."""
    with pytest.raises(GradientParseError) as caught:
        files(tmp_path, text, "0 1\n0 0\n0 0")
    failures = [item for item in caught.value.checks if item.status == "fail"]
    assert failures[0].code == "G01"
    assert "line 1" in failures[0].message
    assert failures[0].source.endswith("bvals")
    assert {item.code for item in caught.value.checks} == {f"G{i:02d}" for i in range(1, 11)}


def test_both_parse_failures_are_retained(tmp_path: Path) -> None:
    """One malformed file must not hide independent problems in the other."""
    with pytest.raises(GradientParseError) as caught:
        files(tmp_path, "oops", "also_bad")
    failures = [item for item in caught.value.checks if item.status == "fail"]
    assert len(failures) == 2
    assert {Path(item.source).name for item in failures} == {"bvals", "bvecs"}


@pytest.mark.parametrize(
    "values,vectors,code",
    [
        ("0 1000\n1000 2000", "0 1\n0 0\n0 0", "G02"),
        ("\n", "0 1\n0 0\n0 0", "G02"),
        ("0 1000", "0 1\n0\n0 0", "G01"),
        ("0 1000", "", "G03"),
        ("0 1000", "0 1\n0 0", "G03"),
    ],
)
def test_invalid_shapes(tmp_path: Path, values: str, vectors: str, code: str) -> None:
    """Shape failures stay structured and are not guessed or reshaped."""
    with pytest.raises(GradientParseError) as caught:
        files(tmp_path, values, vectors)
    assert "fail" in statuses(caught.value.checks, code)


def test_explicit_layout_must_match_shape(tmp_path: Path) -> None:
    """An explicit user choice is respected instead of silently overridden."""
    with pytest.raises(GradientParseError) as caught:
        files(tmp_path, "0 1000", "0 1\n0 0\n0 0", layout="Nx3")
    assert "fail" in statuses(caught.value.checks, "G03")


def test_encoding_error_and_missing_file_are_structured(tmp_path: Path) -> None:
    """Filesystem and UTF-8 problems are expected input errors."""
    bad = tmp_path / "bad"
    bad.write_bytes(b"\xff")
    with pytest.raises(GradientParseError) as caught:
        GradientTable.from_files(bad, tmp_path / "missing")
    assert len([item for item in caught.value.checks if item.status == "fail"]) == 2


def test_nonfinite_tokens_parse_then_fail_with_volume_indices(tmp_path: Path) -> None:
    """NaN/Inf are scientific invalidity with volume context, not dropped tokens."""
    table = files(tmp_path, "NaN 1000", "0 inf\n0 0\n0 0")
    result = checks(table, 2)
    finite_check = next(item for item in result if item.code == "G04")
    assert finite_check.status == "fail"
    assert finite_check.volume_indices == (0, 1)
    assert statuses(result, "G07") == {"skipped"}


def test_count_mismatch_never_broadcasts_vectors() -> None:
    """Different vector/value counts yield failures rather than NumPy errors."""
    table = GradientTable(np.array([0, 1000]), np.array([[1, 0, 0]]))
    result = checks(table, 3)
    assert "fail" in statuses(result, "G02")
    assert "fail" in statuses(result, "G03")
    assert statuses(result, "G07") == {"skipped"}
    assert "fail" in statuses(checks(table), "G03")


def test_readonly_copies_and_frozen_metadata() -> None:
    """Neither original arrays nor re-enabled flags can mutate stored gradients."""
    values, vectors = np.array([0.0, 1000]), np.array([[0.0, 0, 0], [1.0, 0, 0]])
    table = GradientTable(values, vectors)
    values[0] = 123
    vectors[1] = 0
    assert table.bvals[0] == 0
    assert table.bvecs[1, 0] == 1
    with pytest.raises(ValueError):
        table.bvecs[1, 0] = 0
    with pytest.raises(ValueError):
        table.bvals.setflags(write=True)
    with pytest.raises(FrozenInstanceError):
        table.source_layout = "3xN"


@pytest.mark.parametrize(
    "norm, expected_status",
    [(0.99, "pass"), (1.01, "pass"), (0.989999, "fail"), (1.010001, "fail"), (0, "fail")],
)
def test_weighted_norm_inclusive_tolerance_and_preservation(
    norm: float, expected_status: str
) -> None:
    """Exact tolerance limits are accepted without silently normalizing vectors."""
    table = GradientTable(np.array([0, 1000]), np.array([[0, 0, 0], [norm, 0, 0]]))
    assert statuses(checks(table, 2), "G07") == {expected_status}
    assert table.bvecs[1, 0] == norm


@pytest.mark.parametrize(
    "norm,status",
    [
        (0, "pass"),
        (1e-6, "pass"),
        (1.0001e-6, "warn"),
        (0.99, "pass"),
        (1.01, "pass"),
        (0.8, "warn"),
    ],
)
def test_b0_norm_policy(norm: float, status: str) -> None:
    """Unusual b0 norms warn while preserving the supplied vectors."""
    table = GradientTable(np.array([50, 1000]), np.array([[norm, 0, 0], [1, 0, 0]]))
    assert statuses(checks(table, 2), "G08") == {status}


def test_b0_threshold_all_b0_and_no_b0_are_load_permitting() -> None:
    """The b0 threshold is inclusive, and sampling readiness is deferred."""
    table = GradientTable(np.array([50, 50.000001]), np.array([[0, 0, 0], [1, 0, 0]]))
    assert table.b0_mask().tolist() == [True, False]
    for bvalue in (0, 1000):
        single = GradientTable(np.array([bvalue]), np.array([[1, 0, 0]]))
        result = checks(single, 1)
        assert statuses(result, "G06") == {"warn"}
        assert not any(item.status == "fail" for item in result)


def test_negative_and_high_b_values_have_distinct_outcomes() -> None:
    """Negative values fail; very large values only request unit verification."""
    negative = GradientTable(np.array([-0.01, 1000]), np.array([[0, 0, 0], [1, 0, 0]]))
    assert statuses(checks(negative), "G05") == {"fail"}
    for value, status in [(20_000, "pass"), (20_000.001, "warn")]:
        high = GradientTable(np.array([value]), np.array([[1, 0, 0]]))
        assert statuses(checks(high), "G05") == {status}


def test_shell_bins_are_not_transitive_and_keep_original_indices() -> None:
    """1000/1100 share a bin; 1190 starts the next despite being near 1100."""
    values = np.array([1190, 0, 1100, 1000, 50, 2300, 2250], dtype=float)
    table = GradientTable(values, np.ones((values.size, 3)))
    groups = table.shells()
    assert [group.volume_indices for group in groups] == [(1, 4), (2, 3), (0,), (5, 6)]
    assert [group.b_median for group in groups] == [25, 1050, 1190, 2275]
    assert groups[0].is_b0 and not groups[1].is_b0
    assert sum(group.count for group in groups) == values.size
    json.dumps([group.to_dict() for group in groups], allow_nan=False)


def test_zero_span_exact_groups_and_large_finite_values() -> None:
    """Repeated b-values can form exact bins without overflow in the median."""
    table = GradientTable(np.array([1000, 1000, 1001, 1e308, 1e308]), np.ones((5, 3)))
    groups = table.shells(max_span=0)
    assert [group.count for group in groups] == [2, 1, 2]
    assert groups[-1].b_median == 1e308
    json.dumps([group.to_dict() for group in groups], allow_nan=False)


@pytest.mark.parametrize("values", [[np.nan], [np.inf], [-1]])
def test_invalid_shell_inputs_raise(values: list[float]) -> None:
    """Shell helper never produces misleading groups for invalid b-values."""
    with pytest.raises(ValueError):
        GradientTable(np.array(values), np.ones((1, 3))).shells()


def test_direct_helper_arguments_are_validated() -> None:
    """Public helpers reject invalid thresholds even outside a loader policy."""
    table = GradientTable(np.array([1000]), np.array([[1, 0, 0]]))
    with pytest.raises(ValueError):
        table.b0_mask(b0_max=-1)
    with pytest.raises(ValueError):
        table.shells(max_span=np.nan)
    with pytest.raises(ValueError):
        checks(table, 0)
