"""Behavioral contract for Step B, using actual core results and Qt events."""

import threading
from dataclasses import replace
from pathlib import Path

import nibabel as nib
import numpy as np
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent

from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.core.preview_geometry import native_plane
from dmri_studio.gui.discovery import detect_folder


def load(window: object, qtbot: object, folder: Path) -> object:
    """Load through the same form and worker used by the desktop application."""
    window.page.fill_folder(folder)
    qtbot.mouseClick(window.page.load_button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: window.load_thread is None, timeout=10000)
    qtbot.waitUntil(
        lambda: window.page.preview.thread is None and not window.page.preview.pending,
        timeout=10000,
    )
    return window.dataset


def test_loading_and_preview(window: object, qtbot: object, dataset_folder: Path) -> None:
    """Core evidence appears intact; only the selected plane is previewed."""
    dataset = load(window, qtbot, dataset_folder)
    assert dataset is not None
    assert window.page.state == "loaded"
    assert dataset.summary()["b0_count"] == 1
    assert window.page.preview.slider.value() == 0
    assert "b = 0" in window.page.preview.volume_label.text()
    assert window.page.preview.canvas.image is not None
    assert len(window.logs.records) > 40
    assert "sampled voxels" in window.page.banner_detail.text()
    assert window.page.preview.sphere.groups[0].volume_indices == (1, 2)
    window.page.preview.slider.setValue(3)
    qtbot.waitUntil(lambda: window.page.preview.canvas.image is not None, timeout=5000)
    assert "Volume 4 / 4 · b = 2000" in window.page.preview.volume_label.text()


def test_failed_replacement_retains_dataset(
    window: object, qtbot: object, dataset_folder: Path, tmp_path: Path
) -> None:
    """Reject a mismatched candidate without closing or replacing active data."""
    original = load(window, qtbot, dataset_folder)
    bad = tmp_path / "wrong.bval"
    bad.write_text("0 1000")
    window.page.show_form()
    window.page.fields["bval"].setText(str(bad))
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None)
    assert window.dataset is original
    assert window.page.state == "failed"
    assert window.page.failure.report.counts()["fail"] > 0
    assert original.read_slice(0).shape == (8, 10)
    assert "retained" in window.page.banner_detail.text()


def test_cancellation_and_responsiveness(
    window: object, qtbot: object, dataset_folder: Path
) -> None:
    """A cancellation flag interrupts between stages; the Qt loop stays live."""
    original = load(window, qtbot, dataset_folder)
    entered, release = threading.Event(), threading.Event()
    real = DatasetLoader()

    class ControlledLoader:
        """Hold the worker at a known checkpoint until the test permits progress."""

        def load(self, paths: object, **kwargs: object) -> object:
            """Only the worker blocks; cancellation remains callable on the UI thread."""
            entered.set()
            release.wait(5)
            return real.load(paths, **kwargs)

    window.loader = ControlledLoader()
    window.page.show_form()
    window.start_load()
    qtbot.waitUntil(entered.is_set)
    assert window.page.busy
    window.toggle_theme()
    assert window.theme.name == "light"
    window.cancel_load()
    window.page.update_elapsed()
    assert "Cancelling" in window.page.stage.text()
    release.set()
    qtbot.waitUntil(lambda: window.load_thread is None)
    assert window.dataset is original
    assert window.page.state == "cancelled"
    assert window.page.failure.report.cancelled
    assert window.page.load_button.isEnabled()


def test_drop_folder_and_ambiguity(
    window: object, qtbot: object, dataset_folder: Path, monkeypatch: object
) -> None:
    """Local drops fill fields; ambiguous images require an explicit selection."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(dataset_folder))])
    event = QDropEvent(
        QPointF(10, 10),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    window.page.drop_zone.dropEvent(event)
    assert window.page.paths().dwi == dataset_folder / "data.nii"
    assert window.page.paths().json is None
    assert window.dataset is None
    (dataset_folder / "another.nii").write_bytes(b"candidate")
    choices = []

    def choose(role: str, candidates: list[Path]) -> None:
        """Simulate cancelling the user choice, never defaulting to the first file."""
        choices.append((role, candidates))
        return None

    monkeypatch.setattr(window.page, "choose_candidate", choose)
    window.page.fill_folder(dataset_folder)
    assert choices[0][0] == "DWI image"
    assert not window.page.fields["dwi"].text()
    assert not window.page.load_button.isEnabled()


def test_detection_is_nonrecursive_and_stem_matched(dataset_folder: Path) -> None:
    """HCP companions and BIDS-style stems work without participant traversal."""
    (dataset_folder / "grad_dev.nii").write_bytes(b"not a DWI")
    (dataset_folder / "unrelated.json").write_text("{}")
    nested = dataset_folder / "nested"
    nested.mkdir()
    (nested / "hidden.nii").write_bytes(b"not inspected")
    candidates = detect_folder(dataset_folder)
    assert candidates["dwi"] == [dataset_folder / "data.nii"]
    assert candidates["json"] == []
    (dataset_folder / "data.json").write_text("{}")
    assert detect_folder(dataset_folder)["json"] == [dataset_folder / "data.json"]


def test_theme_preserves_state(window: object, qtbot: object, dataset_folder: Path) -> None:
    """Theme changes leave active data, slice selection and logs untouched."""
    dataset = load(window, qtbot, dataset_folder)
    window.page.preview.slider.setValue(2)
    qtbot.waitUntil(lambda: window.page.preview.canvas.image is not None)
    count = len(window.logs.records)
    window.toggle_theme()
    assert window.theme.name == "light"
    assert window.dataset is dataset
    assert window.page.preview.slider.value() == 2
    assert len(window.logs.records) == count
    assert window.settings.get("theme") == "light"
    window.toggle_theme()
    assert window.theme.name == "dark"
    assert window.nav_buttons[2][0].isEnabled()  # History/planning is implemented.
    assert all(
        not nav.isEnabled()
        for nav, name in window.nav_buttons[1:]
        if name in {"Reconstruction", "Connectivity"}
    )


def test_geometry_normal_and_fallback(dataset_folder: Path) -> None:
    """Select actual plane normals including shear; do not assume file axis 2."""
    result = DatasetLoader().load(
        DatasetPaths(
            dataset_folder / "data.nii", dataset_folder / "bvals", dataset_folder / "bvecs"
        )
    )
    with result.dataset as dataset:
        axis, angle, title = native_plane(dataset)
        assert axis == 2 and angle == 0 and title == "Axial preview"
        swapped = np.array([[0, 1, 0, 0], [0, 0, 1, 0], [1, 0, 0, 0], [0, 0, 0, 1]], dtype=float)
        assert native_plane(replace(dataset, image=dataset._image, affine=swapped))[0] == 0
        oblique = np.array([[1, 0, 0.3, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float)
        assert (
            "no resampling"
            in native_plane(replace(dataset, image=dataset._image, affine=oblique))[2]
        )
        assert (
            "unverified"
            in native_plane(replace(dataset, image=dataset._image, affine_source="fallback"))[2]
        )


def test_preview_failure_keeps_summary(window: object, qtbot: object, dataset_folder: Path) -> None:
    """A source changed after load produces a local preview error, not data loss."""
    dataset = load(window, qtbot, dataset_folder)
    with (dataset_folder / "bvals").open("a") as handle:
        handle.write("\n")
    window.page.preview.slider.setValue(1)
    qtbot.waitUntil(
        lambda: "source changed" in window.page.preview.range_label.text(), timeout=5000
    )
    assert window.dataset is dataset
    assert window.page.details.isVisible()
    assert "reload" in window.page.preview.canvas.message


def test_rapid_slider_coalesces_and_rejects_stale(
    window: object, qtbot: object, dataset_folder: Path, monkeypatch: object
) -> None:
    """Only the latest requested volume may paint after a blocked earlier read."""
    from dmri_studio.core.dataset import DwiDataset

    dataset = load(window, qtbot, dataset_folder)
    original = DwiDataset.read_slice
    entered, release = threading.Event(), threading.Event()
    reads = []

    def slow(self: object, volume: int, **kwargs: object) -> object:
        """Hold one slice to exercise queuing and stale-result handling."""
        reads.append(volume)
        if volume == 1:
            entered.set()
            release.wait(5)
        return original(self, volume, **kwargs)

    monkeypatch.setattr(DwiDataset, "read_slice", slow)
    window.page.preview.slider.setValue(1)
    qtbot.waitUntil(entered.is_set)
    window.page.preview.slider.setValue(2)
    window.page.preview.slider.setValue(3)
    release.set()
    qtbot.waitUntil(
        lambda: window.page.preview.thread is None and not window.page.preview.pending, timeout=5000
    )
    assert reads == [1, 3, 3, 3]
    assert window.page.preview.slider.value() == 3
    assert window.dataset is dataset


def test_warning_and_no_b0(window: object, qtbot: object, dataset_folder: Path) -> None:
    """Core warning status remains loadable and default preview fallback is explicit."""
    (dataset_folder / "bvals").write_text("1000 1000 1000 2000")
    (dataset_folder / "bvecs").write_text("1 1 0 0\n0 0 1 0\n0 0 0 1")
    dataset = load(window, qtbot, dataset_folder)
    assert dataset is not None
    assert window.page.state == "warning"
    assert "No b0-like volume" in window.page.preview.legend.text()


def test_preferences_and_drawer(window: object, qtbot: object) -> None:
    """Sidebar and drawer preferences are persisted, with copyable real logs."""
    window.toggle_sidebar()
    assert window.sidebar.width() == 52
    assert window.settings.get("sidebar_collapsed") is True
    window.toggle_logs()
    assert window.logs.open
    assert window.settings.get("drawer_open") is True
    window.record("Actual shell event")
    window.logs.copy_all()
    from PySide6.QtWidgets import QApplication

    assert "Actual shell event" in QApplication.clipboard().text()
    window.logs.clear()
    assert not window.logs.records


def test_stale_load_result_cannot_replace(
    window: object, qtbot: object, dataset_folder: Path
) -> None:
    """An older request ID is discarded, including its candidate resource."""
    import pytest

    from dmri_studio.core.dataset import DatasetClosedError

    original = load(window, qtbot, dataset_folder)
    stale = DatasetLoader().load(original.paths)
    window.load_result(window.request - 1, stale)
    assert window.dataset is original
    with pytest.raises(DatasetClosedError):
        stale.dataset.read_slice(0)


def test_safe_close_during_worker(window: object, qtbot: object, dataset_folder: Path) -> None:
    """Closing requests cancellation but waits without blocking the main loop."""
    entered, release = threading.Event(), threading.Event()

    class HeldLoader:
        """A bounded blocked I/O stand-in used to exercise shutdown."""

        def load(self, paths: object, **kwargs: object) -> object:
            """Observe cancellation only after the simulated blocking read ends."""
            entered.set()
            release.wait(5)
            return DatasetLoader().load(paths, **kwargs)

    window.loader = HeldLoader()
    window.page.fill_folder(dataset_folder)
    window.start_load()
    qtbot.waitUntil(entered.is_set)
    window.close()
    assert window.closing and window.cancel_event.is_set()
    assert window.isVisible()
    release.set()
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=10000)
    assert window.dataset is None


def test_reopen_preferences(window: object, qtbot: object) -> None:
    """A new shell restores theme, geometry, sidebar and expanded drawer state."""
    from dmri_studio.gui.main_window import MainWindow

    window.toggle_theme()
    window.toggle_sidebar()
    window.toggle_logs()
    window.resize(1100, 740)
    settings, theme = window.settings, window.theme
    window.close()
    replacement = MainWindow(theme, settings)
    qtbot.addWidget(replacement)
    replacement.show()
    assert replacement.logs.open
    assert replacement.sidebar_collapsed
    assert replacement.width() == max(
        replacement.minimumWidth(), min(1100, replacement.screen().availableGeometry().width())
    )
    assert replacement.theme.name == "light"
    assert replacement.dataset is None
    replacement.close()


def test_semantic_colors_update_without_stylesheet_replacement(
    window: object, qtbot: object, dataset_folder: Path
) -> None:
    """Check rendered card pixels to catch Qt stylesheet palette caching regressions."""
    from PySide6.QtWidgets import QApplication

    load(window, qtbot, dataset_folder)
    stylesheet = QApplication.instance().styleSheet()
    for name in ("light", "dark", "light"):
        window.theme.apply(name)
        image = window.page.preview.grab().toImage()
        # A point in the card's padding, away from its rounded edge or text.
        scale = image.devicePixelRatio()
        assert (
            image.pixelColor(int(16 * scale), int(16 * scale)).name()
            == window.theme.color("bg.surface").name()
        )
        assert QApplication.instance().styleSheet() == stylesheet


def test_display_axes_and_constant_slice(window: object) -> None:
    """Pixel positions prove the disclosed transpose/vertical flip, without resampling."""
    canvas = window.page.preview.canvas
    plane = np.array([[0, 10, 20], [30, 40, 50]], dtype=float)
    text = canvas.set_plane(plane, (1, 2))
    image = canvas.image
    assert image.width() == 2 and image.height() == 3
    assert image.pixelColor(0, 2).red() == 0
    assert image.pixelColor(1, 0).red() == 255
    assert canvas.aspect == 1 / 3
    assert "min/max" in text
    canvas.set_plane(np.zeros((3, 4)), (1, 1))
    assert canvas.image is not None and "Constant plane" in canvas.message


def test_native_dialog_adapter_and_quoted_paths(
    window: object, monkeypatch: object, dataset_folder: Path
) -> None:
    """Native dialog selections persist browse folders; quoting is not shell syntax."""
    from PySide6.QtWidgets import QFileDialog

    monkeypatch.setattr(
        QFileDialog, "getOpenFileName", lambda *args: (str(dataset_folder / "data.nii"), "")
    )
    window.page.browse("dwi")
    assert window.settings.folder("dwi") == str(dataset_folder)
    window.page.fields["bval"].setText(f'"{dataset_folder / "bvals"}"')
    window.page.fields["bvec"].setText(str(dataset_folder / "bvecs"))
    assert window.page.paths().bval == dataset_folder / "bvals"
    assert window.page.load_button.isEnabled()
    window.page.fields["dwi"].setText(str(dataset_folder / "missing.nii"))
    window.page.check_field("dwi")
    assert "not found" in window.page.errors["dwi"].text()
    assert not window.page.load_button.isEnabled()


def test_command_list_only_existing_actions(window: object, qtbot: object) -> None:
    """The modal list filters and dispatches implemented shell actions only."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QListWidget

    observed = []

    def choose_theme() -> None:
        """Interact after the command dialog enters its native event loop."""
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, QDialog)
        listing = dialog.findChild(QListWidget)
        observed.extend(listing.item(i).text() for i in range(listing.count()))
        query = dialog.findChild(QLineEdit)
        query.setText("theme")
        query.returnPressed.emit()

    QTimer.singleShot(0, choose_theme)
    window.commands()
    assert len(observed) == 8
    assert "Check scientific dependencies" in observed
    assert "Open saved preprocessing runs" in observed
    assert "Open saved preprocessing QC" in observed
    assert "Open preprocessing plan" not in observed  # No input loaded; saved QC stays available.
    assert window.theme.name == "light"


def test_ambiguous_layout_chooser_uses_core_evidence(
    window: object, qtbot: object, dataset_folder: Path
) -> None:
    """A 3×3 report exposes explicit core policy selection without GUI parsing."""
    image = nib.Nifti1Image(np.ones((4, 4, 4, 3), np.float32), np.eye(4))
    image.header.set_xyzt_units("mm")
    nib.save(image, dataset_folder / "data.nii")
    (dataset_folder / "bvals").write_text("0 1000 1000")
    (dataset_folder / "bvecs").write_text("0 1 0\n0 0 1\n0 0 0")
    load(window, qtbot, dataset_folder)
    assert window.page.state == "warning"
    window.page.show_form()
    assert window.page.layout_choice.isVisible()
    window.page.layout_choice.setCurrentIndex(1)
    window.start_load()
    qtbot.waitUntil(lambda: window.load_thread is None)
    assert window.dataset.validation.policy.bvec_layout == "3xN"
    assert not any(c.code == "G03" and c.status == "warn" for c in window.dataset.validation.checks)


def test_clear_and_unload_are_distinct(window: object, qtbot: object, dataset_folder: Path) -> None:
    """Clearing a candidate retains data; explicit Unload releases it."""
    import pytest

    from dmri_studio.core.dataset import DatasetClosedError

    original = load(window, qtbot, dataset_folder)
    window.page.show_form()
    window.page.clear()
    assert window.dataset is original
    window.unload()
    assert window.dataset is None and window.page.state == "empty"
    with pytest.raises(DatasetClosedError):
        original.read_slice(0)


def test_cancel_after_worker_completed_still_discards_candidate(
    window: object, qtbot: object, dataset_folder: Path
) -> None:
    """A cancel click queued beside completion must not activate or certify a candidate."""
    import pytest

    from dmri_studio.core.dataset import DatasetClosedError

    original = load(window, qtbot, dataset_folder)
    candidate = DatasetLoader().load(original.paths)
    window.cancel_event.set()
    window.load_result(window.request, candidate)
    assert window.dataset is original
    assert window.page.failure.report.cancelled
    assert window.page.failure.report.status() == "incomplete"
    assert "incomplete" in window.page.banner_detail.text()
    with pytest.raises(DatasetClosedError):
        candidate.dataset.read_slice(0)
