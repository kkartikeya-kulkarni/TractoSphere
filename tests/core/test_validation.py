"""Test report truthfulness and policy boundaries; no imaging files are needed."""

import json
from dataclasses import FrozenInstanceError

import pytest

from dmri_studio.core.validation import CHECK_CODES, CheckResult, ValidationPolicy, ValidationReport


def complete_checks() -> tuple[CheckResult, ...]:
    """Represent a complete checklist for aggregate-status tests."""
    return tuple(CheckResult(code, "pass", "Checked") for code in CHECK_CODES)


@pytest.mark.parametrize(
    "field,value",
    [
        ("b0_max", -1),
        ("b0_max", float("nan")),
        ("b0_max", True),
        ("unit_norm_atol", float("inf")),
        ("zero_norm_atol", -1),
        ("shell_span", -1),
        ("sample_edge", 0),
        ("sample_edge", 1.5),
        ("sample_edge", True),
        ("bvec_layout", "guess"),
    ],
)
def test_invalid_policies_rejected(field: str, value: object) -> None:
    """Bad configuration fails before any input is opened."""
    with pytest.raises(ValueError):
        ValidationPolicy(**{field: value})


def test_default_policy_and_boundary_zero() -> None:
    """Recorded defaults match the approved contract; zero tolerances are exact."""
    assert ValidationPolicy().b0_max == 50
    assert ValidationPolicy().unit_norm_atol == 0.01
    assert ValidationPolicy(sample_edge=1, b0_max=0, shell_span=0).sample_edge == 1


@pytest.mark.parametrize(
    "extra,expected,loadable",
    [
        ("info", "pass", True),
        ("warn", "warn", True),
        ("fail", "fail", False),
    ],
)
def test_severity_aggregation(extra: str, expected: str, loadable: bool) -> None:
    """Warnings permit inspection; information never creates a warning banner."""
    report = ValidationReport(complete_checks() + (CheckResult("V03", extra, "Sample finding"),))
    assert report.status() == expected
    assert report.can_load() is loadable
    assert len(report.by_status(extra)) == 1
    assert report.counts()[extra] == 1


def test_incomplete_and_cancelled_are_never_passes() -> None:
    """A missing checklist entry or interrupted run cannot masquerade as valid."""
    assert ValidationReport(complete_checks()[:-1]).status() == "incomplete"
    assert not ValidationReport(()).can_load()
    assert ValidationReport(complete_checks(), cancelled=True).status() == "incomplete"
    assert ValidationReport(complete_checks(), complete=False).status() == "incomplete"
    assert not ValidationReport(
        tuple(CheckResult(code, "skipped", "Not run") for code in CHECK_CODES)
    ).can_load()


def test_coverage_distinguishes_failed_and_partial_sampling() -> None:
    """Failed inputs must never claim metadata and sampled voxels were checked."""
    unread = ValidationReport((CheckResult("V01", "skipped", "No image"),))
    assert "voxel samples were not read" in unread.to_dict()["coverage_message"]
    partial = ValidationReport(
        (CheckResult("V01", "pass", "First block"), CheckResult("V01", "fail", "Last block"))
    )
    assert "sampling incomplete" in partial.coverage_message()


def test_report_is_deeply_immutable_and_strict_json() -> None:
    """Consumers cannot revise evidence, even through a nested original list."""
    observed = {"values": [float("nan"), float("inf"), -float("inf")]}
    finding = CheckResult("G04", "fail", "Invalid numbers", observed=observed)
    metadata = {"shape": [2, 3, 4, 5]}
    report = ValidationReport(complete_checks() + (finding,), metadata=metadata)
    observed["values"].clear()
    metadata["shape"].clear()
    encoded = report.to_json()
    parsed = json.loads(encoded, parse_constant=lambda value: pytest.fail(value))
    assert parsed["metadata"]["shape"] == [2, 3, 4, 5]
    assert parsed["checks"][-1]["observed"]["values"] == ["NaN", "Infinity", "-Infinity"]
    assert parsed["policy"]["sample_edge"] == 16
    assert "full voxel scan not performed" in parsed["coverage_message"]
    assert parsed["unperformed_checks"]
    with pytest.raises(TypeError):
        report.metadata["new"] = 1
    with pytest.raises(FrozenInstanceError):
        report.cancelled = True
    parsed["metadata"]["shape"][0] = 999
    assert report.metadata["shape"][0] == 2


def test_invalid_finding_status_rejected() -> None:
    """Typos in scientific severity cannot silently disappear from counts."""
    with pytest.raises(ValueError):
        CheckResult("X", "maybe", "Unrecognized")
