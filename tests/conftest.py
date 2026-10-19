"""Keep controlled scientific reviews separate from researcher application state."""

import pytest


@pytest.fixture(autouse=True)
def isolated_registration_review_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACTOSPHERE_REVIEW_DIRECTORY", str(tmp_path / "review-index"))
