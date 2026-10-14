"""Saved QC must not hide changed evidence or promote incomplete results."""

import json

import pytest

from dmri_studio.core.qc import artifact, export_report, load_catalog, record_review


@pytest.fixture
def catalog_file(tmp_path):
    report = tmp_path / "qc.html"
    report.write_text("<html>Scientific review pending</html>")
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "kind": "dmri_qc_catalog",
                "identity": {"subject": "synthetic"},
                "stages": [
                    {
                        "id": "eddy",
                        "execution": "completed",
                        "technical": "passed",
                        "automated_qc": "pending",
                        "scientific_review": "required",
                        "findings": ["Focal concerns remain"],
                        "artifacts": [artifact(report, "report", "Report")],
                    }
                ],
            }
        )
    )
    return path


def test_changed_artifact_rejected(catalog_file):
    load_catalog(catalog_file)
    (catalog_file.parent / "qc.html").write_text("changed")
    with pytest.raises(ValueError, match="evidence changed"):
        load_catalog(catalog_file)


def test_export_exact_and_no_overwrite(catalog_file):
    catalog = load_catalog(catalog_file)
    entry = catalog.to_dict()["stages"][0]["artifacts"][0]
    dest = catalog_file.parent / "copy.html"
    export_report(catalog, entry, dest)
    assert dest.read_bytes() == (catalog_file.parent / "qc.html").read_bytes()
    with pytest.raises(FileExistsError):
        export_report(catalog, entry, dest)


def test_review_is_separate_and_bound(catalog_file):
    original = catalog_file.read_bytes()
    catalog = load_catalog(catalog_file)
    record = record_review(
        catalog, "eddy", "needs_review", "Researcher", "Investigate focal CNR", catalog_file.parent
    )
    assert json.loads(record.read_text())["catalog_sha256"] == catalog.digest
    assert catalog_file.read_bytes() == original
    with pytest.raises(ValueError):
        record_review(catalog, "eddy", "accepted", "", "", catalog_file.parent)
    value = json.loads(original)
    value["stages"][0]["technical"] = "failed"
    catalog_file.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="snapshot changed"):
        record_review(catalog, "eddy", "accepted", "Researcher", "Reason", catalog_file.parent)
    with pytest.raises(ValueError, match="cannot be accepted"):
        record_review(
            load_catalog(catalog_file),
            "eddy",
            "accepted",
            "Researcher",
            "Reason",
            catalog_file.parent,
        )


def test_import_cannot_claim_scientific_acceptance(catalog_file):
    value = json.loads(catalog_file.read_text())
    value["stages"][0]["scientific_review"] = "accepted"
    catalog_file.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="cannot confer"):
        load_catalog(catalog_file)


@pytest.mark.parametrize("decision", ["accepted", "rejected", "needs_review"])
def test_saved_review_reopens_without_changing_producer(catalog_file, decision):
    from dmri_studio.core.qc import load_review

    before = catalog_file.read_bytes()
    catalog = load_catalog(catalog_file)
    path = record_review(
        catalog,
        "eddy",
        decision,
        "SYNTHETIC TEST HARNESS",
        "Fixture only; not participant acceptance",
        catalog_file.parent,
    )
    loaded = load_review(catalog, path)
    assert loaded["decision"] == decision and "not authenticated" in loaded["authentication"]
    assert catalog_file.read_bytes() == before
    record = json.loads(path.read_text())
    record["catalog_sha256"] = "0" * 64
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="different or changed"):
        load_review(catalog, path)
