"""Real tool output may include blank lines without disrupting monitoring."""

import pytest

from dmri_studio.gui.components import LogDrawer
from dmri_studio.gui.theme import Theme


@pytest.mark.parametrize("message", ["", "\n", "\n\n"])
def test_blank_tool_log_is_retained_without_header_exception(qtbot, qapp, message):
    """Keep exact log records and continue rendering subsequent tool progress."""
    drawer = LogDrawer(Theme(qapp, "dark"))
    qtbot.addWidget(drawer)
    record = (0.0, "INFO", "eddy", message)
    drawer.append(record)
    assert drawer.records[-1] == record
    assert not drawer._rendering
    drawer.append((1.0, "INFO", "eddy", "Iteration 1"))
    assert "Iteration 1" in drawer.header.text()
    assert "Iteration 1" in drawer.text.toPlainText()
