import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from apps.workbench_qt.main_window import MainWindow, TREE_GROUPS
from conftest import cantilever_model


@pytest.fixture(scope="module")
def app():
    return QApplication([])


def test_window_tree_and_check(app):
    window = MainWindow()
    window.set_model(cantilever_model())
    root = window.tree.topLevelItem(0)
    assert root.childCount() == len(TREE_GROUPS)
    window.run_check()
    assert "검사 통과" in window.status.text()
    assert not window.btn_goto.isEnabled()


def test_window_issue_navigation(app):
    model = cantilever_model()
    model.sections[0].material = "NOPE"
    window = MainWindow()
    window.set_model(model)
    window.run_check()
    assert window.btn_goto.isEnabled()
    window._goto_issue()
    current = window.tree.currentItem()
    assert current is not None
    assert "단면" in current.text(0)
