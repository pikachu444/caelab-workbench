"""GUI unit tests and screenshot validation for MDO Studio."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication
from apps.workbench_qt.mdo_studio.window import MDOStudioWindow
from apps.workbench_qt.theme import toggle_theme_mode, current_theme_mode


@pytest.fixture
def app():
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


def test_mdo_studio_window_creation(app):
    win = MDOStudioWindow()
    assert win.windowTitle().startswith("CAELab MDO Studio")
    assert win.tdp_model.rowCount() == 25
    assert win.tdp_model.columnCount() > 5
    assert win.viz_tabs.count() == 3

    # Select row and check setup dock
    win.tdp_table.selectRow(0)
    assert win.setup_widget.lbl_title.text().startswith("DP_0001")
    assert win.setup_widget.btn_apply_current.isEnabled()

    win.close()


def test_mdo_studio_theme_toggle(app):
    win = MDOStudioWindow()
    initial_mode = win.is_dark

    win._on_toggle_theme()
    assert win.is_dark != initial_mode

    # Toggle back
    win._on_toggle_theme()
    assert win.is_dark == initial_mode
    win.close()
