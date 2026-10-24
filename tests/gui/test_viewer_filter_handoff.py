"""Viewer source reuse never substitutes a rendered subset for scientific input."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

from dmri_studio.core.execution import hash_file
from dmri_studio.core.scene import ImageVolume, TractGroup
from dmri_studio.core.scientific import ImportRequest, import_artifact
from dmri_studio.gui.scientific_viewer import ScientificViewer


def _case(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "roi_fixture", Path(__file__).parents[1] / "core/test_roi_filtering.py"
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    return fixture.roi_case.__wrapped__(tmp_path)


def _context(group, reference, *, displayed=2):
    return dict(
        source=str(group.path),
        reference=str(reference.path),
        total=group.total,
        displayed=displayed,
        edited=bool(group.hidden),
        expected_hash=None,
        source_signature=group.streamlines.signature,
    )


def _imported(source, declarations):
    return (
        import_artifact(
            ImportRequest("tractogram", source / "tracks.tck", declarations=declarations)
        ),
        import_artifact(
            ImportRequest("anatomy", source / "reference.nii.gz", declarations=declarations)
        ),
    )


def test_viewer_action_transfers_selected_full_source_and_rejects_changed_file(tmp_path):
    source, _ = _case(tmp_path)
    group = TractGroup.load(source / "tracks.tck")
    reference = ImageVolume(source / "reference.nii.gz")
    emitted = []
    status = SimpleNamespace(setText=lambda value: emitted.append(("status", value)))
    fake = SimpleNamespace(
        current_group=lambda: group,
        reference=reference,
        density=SimpleNamespace(value=lambda: 2),
        filter_tracks_requested=SimpleNamespace(
            emit=lambda value: emitted.append(("handoff", value))
        ),
        status=status,
    )
    try:
        ScientificViewer.request_filter_tracks(fake)
        context = emitted[-1][1]
        assert context["source"] == str(group.path)
        assert context["total"] == group.total == 4
        assert context["displayed"] == 2
        assert context["source_signature"] == group.streamlines.signature
        group.streamlines.signature = (0, 0)
        ScientificViewer.request_filter_tracks(fake)
        assert emitted[-1][0] == "status"
        assert "changed after loading" in emitted[-1][1]
    finally:
        group.streamlines.close()


def test_handoff_selects_only_matching_record_and_invalidates_old_approval(
    window, tmp_path, monkeypatch
):
    source, declarations = _case(tmp_path)
    tracts, reference = _imported(source, declarations)
    other = import_artifact(
        ImportRequest(
            "tractogram",
            source / "tracks.tck",
            declarations={**declarations, "space_id": "another-reviewed-space"},
        )
    )
    # The same path with two conflicting scientific records is ambiguous.
    group = TractGroup.load(source / "tracks.tck")
    image = ImageVolume(source / "reference.nii.gz")
    viewer = SimpleNamespace(
        current_group=lambda: group,
        reference=image,
        status=SimpleNamespace(setText=lambda _: None),
        inspector=SimpleNamespace(isHidden=lambda: False),
    )
    window.scientific_viewer = viewer
    try:
        page = window.scientific
        page.artifacts = [tracts, other, reference]
        page.refresh()
        panel = page.tracking
        panel.guided.select_task("filter")
        panel.tracts.setCurrentIndex(panel.tracts.findData(tracts.id))
        panel.plan = SimpleNamespace(digest="old-approved-plan")
        panel.approval.setChecked(True)
        assert panel.approval.isChecked()
        window.filter_viewer_tracks(_context(group, image))
        assert panel.tracts.currentData() is None
        assert panel.plan is None and not panel.approval.isChecked()
        assert "Multiple scientific records" in page.status.text()
        assert (
            "complete source file" in panel.guided.viewer_source_notice.text()
            or "all streamlines" in panel.guided.viewer_source_notice.text()
        )

        page.artifacts = [tracts, reference]
        page.refresh()
        window.filter_viewer_tracks(_context(group, image))
        assert panel.operation.currentData() == "filter"
        assert panel.tracts.currentData() == tracts.id
        assert panel.reference.currentData() == reference.id
        assert panel.guided.filter_tracts.currentData() == tracts.id
        assert panel.guided.filter_reference.currentData() == reference.id
    finally:
        window.scientific_viewer = None
        group.streamlines.close()


def test_missing_viewer_source_opens_locked_import_and_selects_after_inspection(
    window, qtbot, tmp_path, monkeypatch
):
    source, declarations = _case(tmp_path)
    group = TractGroup.load(source / "tracks.tck")
    image = ImageVolume(source / "reference.nii.gz")
    window.scientific_viewer = SimpleNamespace(
        current_group=lambda: group,
        reference=image,
        status=SimpleNamespace(setText=lambda _: None),
        inspector=SimpleNamespace(isHidden=lambda: False),
    )
    try:
        page = window.scientific
        reviewed = []
        monkeypatch.setattr(
            page, "browse_viewer_input", lambda kind, path: reviewed.append((kind, path))
        )
        window.filter_viewer_tracks(_context(group, image))
        assert reviewed == [("tractogram", group.path)]
        assert page.tracking.tracts.currentData() is None
        page.import_request(ImportRequest("tractogram", group.path, declarations=declarations))
        qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
        assert page.tracking.tracts.currentData() is not None, page.status.text()
        assert page.tracking.reference.currentData() is None
        assert "reference image" in page.tracking.guided.viewer_source_notice.text()
        page.import_request(ImportRequest("anatomy", image.path, declarations=declarations))
        qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
        assert page.tracking.reference.currentData() is not None, page.status.text()
    finally:
        window.scientific_viewer = None
        group.streamlines.close()


def test_viewer_import_reuses_exact_path_and_existing_record(window, tmp_path, monkeypatch):
    from dmri_studio.gui.scientific_planning import ImportDialog

    source, declarations = _case(tmp_path)
    window.focus_tracking_plan()
    page = window.scientific
    path = source / "tracks.tck"
    dialog = ImportDialog(page, initial_kind="tractogram", viewer_path=path)
    try:
        assert dialog.paths["path"].text() == str(path.resolve())
        assert dialog.paths["path"].isReadOnly()
        assert not dialog.kind.isEnabled()
        assert "already open in the Viewer" in dialog.purpose.text()
    finally:
        dialog.close()
    artifact = import_artifact(ImportRequest("tractogram", path, declarations=declarations))
    page.artifacts.append(artifact)
    reviewed = []
    monkeypatch.setattr(page, "review_input_declarations", reviewed.append)
    page.browse_viewer_input("tractogram", path)
    assert reviewed == [artifact.id]
    assert len(page.artifacts) == 1


def test_viewer_source_change_blocks_plan_without_touching_original(window, tmp_path):
    source, declarations = _case(tmp_path)
    tracts, reference = _imported(source, declarations)
    original = hash_file(source / "tracks.tck")
    window.focus_tracking_plan()
    page = window.scientific
    page.artifacts = [tracts, reference]
    page.refresh()
    panel = page.tracking
    stat = (source / "tracks.tck").stat()
    panel.use_viewer_filter_source(
        source / "tracks.tck",
        source / "reference.nii.gz",
        total=4,
        displayed=2,
        edited=False,
        source_signature=(stat.st_size + 1, stat.st_mtime_ns),
    )
    panel.output.setText(str(tmp_path / "filtered"))
    panel.prepare()
    assert page.thread is None
    assert panel.plan is None
    assert "changed after loading" in page.status.text()
    assert hash_file(source / "tracks.tck") == original


def test_trk_handoff_requires_explicit_verified_conversion(window, qtbot, tmp_path, monkeypatch):
    import nibabel as nib

    source, declarations = _case(tmp_path)
    tck = nib.streamlines.load(source / "tracks.tck")
    trk = source / "tracks.trk"
    header = nib.streamlines.TrkFile.create_empty_header()
    header["voxel_to_rasmm"] = nib.load(source / "reference.nii.gz").affine
    header["dimensions"] = (12, 12, 12)
    header["voxel_sizes"] = (1, 1, 1)
    nib.streamlines.save(nib.streamlines.TrkFile(tck.tractogram, header), trk)
    artifact = import_artifact(ImportRequest("tractogram", trk, declarations=declarations))
    reference = import_artifact(
        ImportRequest("anatomy", source / "reference.nii.gz", declarations=declarations)
    )
    window.focus_tracking_plan()
    page = window.scientific
    page.artifacts = [artifact, reference]
    page.refresh()
    panel = page.tracking
    panel.use_viewer_filter_source(
        trk, source / "reference.nii.gz", total=4, displayed=2, edited=False
    )
    assert panel.tracts.currentData() == artifact.id
    assert "TRK is viewable" in panel.guided.viewer_source_notice.text()
    panel.output.setText(str(tmp_path / "trk-filter"))
    panel.prepare()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert panel.plan is None
    assert "TCK only" in page.status.text()
    output = tmp_path / "derivatives"
    output.mkdir()
    from dmri_studio.gui import tracking as tracking_module

    monkeypatch.setattr(
        tracking_module,
        "QFileDialog",
        SimpleNamespace(getSaveFileName=lambda *_args: (str(output / "converted.tck"), "")),
    )
    assert panel.guided.convert_trk_button.isEnabled()
    panel.guided.convert_trk_button.click()
    qtbot.waitUntil(lambda: page.thread is None, timeout=15000)
    assert "coordinate-verified TCK" in page.status.text(), page.status.text()
    converted = next(
        a for a in page.artifacts if a.payload["path"] == str(output / "converted.tck")
    )
    assert panel.tracts.currentData() == converted.id
    assert "selected TCK derivative" in panel.guided.viewer_source_notice.text()
    assert trk.is_file()


def test_original_tracks_action_rechecks_source_and_reference_hashes(window, tmp_path, monkeypatch):
    source, declarations = _case(tmp_path)
    window.focus_tracking_plan()
    page = window.scientific
    panel = page.tracking
    output = tmp_path / "verified-attempt"
    output.mkdir()
    reference = output / "reference.nii.gz"
    reference.write_bytes((source / "reference.nii.gz").read_bytes())
    record = {
        "status": "validated_pending_qc",
        "inputs_unchanged": True,
        "plan": {
            "output_directory": str(output),
            "bindings": [
                {
                    "algorithm": "tckedit",
                    "source": {
                        "path": str(source / "reference.nii.gz"),
                        "declarations": declarations,
                    },
                    "input_bindings": {
                        "tracts": {
                            "path": str(source / "tracks.tck"),
                            "declarations": declarations,
                            "sources": [
                                {
                                    "path": str(source / "tracks.tck"),
                                    "sha256": hash_file(source / "tracks.tck"),
                                }
                            ],
                        },
                    },
                    "outputs": {"reference": reference.name},
                }
            ],
        },
        "outputs": {reference.name: {"sha256": hash_file(reference)}},
    }
    panel.manifest = record
    panel.update_actions()
    assert panel.guided.original_button.isEnabled()
    seen = []
    monkeypatch.setattr(
        page, "start", lambda kind, fn: seen.append((kind, fn(lambda: False, lambda _: None)))
    )
    panel.open_original()
    assert seen[0][0] == "tracking-original-view"
    assert seen[0][1]["path"] == str(source / "tracks.tck")
    assert seen[0][1]["reference"] == str(reference)
    reference.write_bytes(b"changed")
    import pytest

    with pytest.raises(ValueError, match="Verified reference changed"):
        panel.open_original()
    (source / "tracks.tck").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Original tractogram changed"):
        panel.open_original()


def test_original_tracks_handoff_emits_after_worker_finishes(window, qtbot, tmp_path):
    source, declarations = _case(tmp_path)
    window.focus_tracking_plan()
    page = window.scientific
    panel = page.tracking
    output = tmp_path / "verified-attempt"
    output.mkdir()
    reference = output / "reference.nii.gz"
    reference.write_bytes((source / "reference.nii.gz").read_bytes())
    panel.manifest = {
        "status": "validated_pending_qc",
        "inputs_unchanged": True,
        "plan": {
            "output_directory": str(output),
            "bindings": [
                {
                    "algorithm": "tckedit",
                    "source": {
                        "path": str(source / "reference.nii.gz"),
                        "declarations": declarations,
                    },
                    "input_bindings": {
                        "tracts": {
                            "path": str(source / "tracks.tck"),
                            "sources": [
                                {
                                    "path": str(source / "tracks.tck"),
                                    "sha256": hash_file(source / "tracks.tck"),
                                }
                            ],
                            "declarations": declarations,
                        },
                    },
                    "outputs": {"reference": reference.name},
                }
            ],
        },
        "outputs": {reference.name: {"sha256": hash_file(reference)}},
    }
    panel.view_requested.disconnect(window.open_scientific_product)
    seen = []
    panel.view_requested.connect(seen.append)
    panel.open_original()
    qtbot.waitUntil(lambda: page.thread is None and len(seen) == 1, timeout=15000)
    assert seen[0]["path"] == str(source / "tracks.tck")
    assert seen[0]["reference"] == str(reference)
