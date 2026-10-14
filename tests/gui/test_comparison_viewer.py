"""Existing Preview composition: mapping, native navigation and worker teardown."""

from pathlib import Path

import pytest

from dmri_studio.core.comparison import validate_comparison
from dmri_studio.core.dataset import DatasetPaths
from dmri_studio.core.loading import DatasetLoader
from dmri_studio.gui.comparison import ComparisonWindow
from dmri_studio.gui.theme import Theme


def test_mapped_navigation_and_difference(qapp, qtbot, dataset_folder: Path):
    """Corresponding indices need not be equal; display must stay synchronized."""
    paths = DatasetPaths(
        dataset_folder / "data.nii", dataset_folder / "bvals", dataset_folder / "bvecs"
    )
    datasets = {k: DatasetLoader().load(paths).dataset for k in ["left", "right"]}
    manifest = {
        "modes": [
            {
                "label": "Synthetic mapping",
                "left": "left",
                "right": "right",
                "pairs": [[0, 1], [2, 3]],
            }
        ]
    }
    widget = ComparisonWindow(Theme(qapp), datasets, manifest)
    widget.show()
    p = widget.panel
    try:
        qtbot.waitUntil(lambda: not p.left.pending and not p.right.pending, timeout=10000)
        assert (p.left.slider.value(), p.right.slider.value()) == (0, 1)
        p.slider.setValue(1)
        p.left.set_position((2, 3, 4))
        qtbot.waitUntil(lambda: not p.left.pending and not p.right.pending, timeout=10000)
        assert p.right.position == (2, 3, 4)
        assert (p.left.slider.value(), p.right.slider.value()) == (2, 3)
        p.display.setCurrentText("Difference")
        assert not p.right.canvas.image.isNull()
        p.orientation.setCurrentText("Sagittal")
        assert p.left.expanded == p.right.expanded == "sagittal"
        p.display.setCurrentText("Overlay")
        p.opacity.setValue(1)
        p.display.setCurrentText("Side by side")
        assert "Displayed-plane metrics" in p.metrics.text()
        with pytest.raises(ValueError):
            validate_comparison(datasets["left"], datasets["right"], [(0, 999)])
    finally:
        widget.close()
        qtbot.waitUntil(lambda: p.left.thread is None and p.right.thread is None, timeout=10000)
        widget.close()
        widget.deleteLater()
