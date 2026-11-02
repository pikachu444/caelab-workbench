"""Qt action-to-JobManager checks with explicit Python solver-process probes.

These checks exercise real Qt widgets and owned subprocesses. They are not
native-screen quality checks or actual OpenCourant execution evidence.
"""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QRect, Qt
from PySide6.QtWidgets import QTabWidget

from apps.workbench_qt.native_analysis import NativeAnalysisWindow
from test_radioss_runs import _actual_bundles, _assert_output_reference, _input_pair, _project, _sha, radioss_probe
from caelab.workbench import Workbench


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pump(app, window, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        window.refresh_runs()
        if predicate():
            # The worker can finish between refresh_runs and the predicate's
            # fresh service query. Display that now-terminal receipt before
            # checking the user's visible labels and controls.
            window.refresh_runs()
            app.processEvents()
            return
        time.sleep(.01)
    raise AssertionError("GUI did not reach the requested run state: " + window.run_status.text())


def _selected(window):
    return window.workbench.selected_native_run(window.project, window.node_id)


def _status_fits(window):
    label = window.run_status
    rect = label.contentsRect()
    needed = label.fontMetrics().boundingRect(QRect(0, 0, rect.width(), 10000),
                                               Qt.TextWordWrap, label.text()).height()
    assert rect.height() >= needed, (rect.height(), needed, label.text())


def test_radioss_gui_actions_show_stage_cancel_retry_and_honest_partial(app, tmp_path, monkeypatch, radioss_probe):
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    wb, project, node = _project(tmp_path, _input_pair(tmp_path / "source"))
    window = NativeAnalysisWindow(project.path, node.id, workbench=wb)
    try:
        window.show()
        radioss_probe["mode"] = "engine_wait"
        assert window.run_action.isEnabled()
        window.run_action.trigger()
        _pump(app, window, lambda: bool(_selected(window)) and any(
            stage["name"] == "engine" and stage["state"] == "running"
            for stage in _selected(window).snapshot.get("stages", [])))
        assert "engine" in window.run_status.text().lower()
        assert window.cancel_action.isEnabled() and not window.run_action.isEnabled()
        cancelled_id = _selected(window).id
        window.cancel_action.trigger()
        _pump(app, window, lambda: _selected(window).finished_at is not None)
        assert _selected(window).state == "cancelled"
        assert "취소" in window.run_status.text()
        _status_fits(window)
        assert not window.cancel_action.isEnabled() and window.run_action.isEnabled()

        radioss_probe["mode"] = "normal"
        window.run_action.trigger()
        _pump(app, window, lambda: _selected(window).id != cancelled_id and _selected(window).finished_at is not None)
        completed = _selected(window)
        assert completed.state == "completed" and completed.parse_state == "partial"
        _assert_output_reference(window.project, window.project.nodes[window.node_id], completed)
        assert window.run_selector.count() == 2
        assert "일부" in window.run_status.text()
        _status_fits(window)
        assert window.kpi_disp_value.text() == "미확인"
        assert window.result_text.toPlainText()
        assert completed.result["warnings"]
        assert not warnings

        window.run_selector.setCurrentIndex(window.run_selector.findData(cancelled_id))
        app.processEvents()
        assert _selected(window).id == cancelled_id
        assert "취소" in window.run_status.text()
    finally:
        window.close()
        app.processEvents()


def test_radioss_gui_source_change_marks_selected_run_stale_without_rewriting_snapshot(app, tmp_path, monkeypatch, radioss_probe):
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail(str(args[2])))
    source = _input_pair(tmp_path / "source")
    wb, project, node = _project(tmp_path, source)
    window = NativeAnalysisWindow(project.path, node.id, workbench=wb)
    try:
        window.show()
        window.run_action.trigger()
        _pump(app, window, lambda: bool(_selected(window)) and _selected(window).finished_at is not None)
        run = _selected(window)
        assert run.state == "completed"
        preserved = (run.workdir / "material.inc").read_bytes()
        (source.parent / "material.inc").write_bytes(b"# externally changed include\n")
        window.refresh_input()
        window.refresh_runs()
        assert _selected(window).stale
        assert "오래된 입력" in window.run_status.text()
        assert (run.workdir / "material.inc").read_bytes() == preserved
    finally:
        window.close()
        app.processEvents()


@pytest.mark.skipif(not os.environ.get("CAELAB_OPENCOURANT"),
                    reason="Set CAELAB_OPENCOURANT explicitly for real Qt-action-to-OpenCourant checks.")
@pytest.mark.parametrize("source", _actual_bundles(), ids=lambda path: path.stem)
def test_actual_radioss_gui_action_runs_solver_and_reopens_receipt(app, tmp_path, monkeypatch, source):
    """Real installed solver; QT_QPA_PLATFORM determines offscreen vs X11 scope."""
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    wb, project, node = _project(tmp_path, source.resolve())
    window = NativeAnalysisWindow(project.path, node.id, workbench=wb)
    try:
        window.show()
        app.processEvents()
        assert window.run_action.isEnabled()
        window.run_action.trigger()
        _pump(app, window, lambda: bool(_selected(window)) and _selected(window).finished_at is not None, timeout=65)
        run = _selected(window)
        assert window.grab().save(str(tmp_path / "actual-run-window.png"))
        assert run.state == "completed", run.error + "\n" + window.log.toPlainText()
        assert [(stage["name"], stage["state"], stage["returncode"])
                for stage in run.snapshot["stages"]] == [("starter", "completed", 0), ("engine", "completed", 0)]
        assert "starter" in window.run_status.text().lower() and "engine" in window.run_status.text().lower()
        assert run.parse_state == "partial" and "일부" in window.run_status.text()
        _assert_output_reference(window.project, window.project.nodes[window.node_id], run)
        _status_fits(window)
        assert window.kpi_disp_value.text() == "미확인" and run.result["warnings"]
        assert not warnings
        for item in run.snapshot["files"]:
            assert _sha(item["source_path"]) == item["original_sha256"]
        assert run.snapshot["outputs"] and run.snapshot["artifacts"]
        result_tabs = next(tabs for tabs in window.findChildren(QTabWidget)
                           if any(tabs.tabText(index) == "결과" for index in range(tabs.count())))
        result_tabs.setCurrentIndex(next(index for index in range(result_tabs.count()) if result_tabs.tabText(index) == "결과"))
        app.processEvents()
        assert window.grab().save(str(tmp_path / "actual-result-window.png"))
        saved = wb.save_native_project(window.project, project.path)
        window.close()
        app.processEvents()
        window = NativeAnalysisWindow(saved, node.id, workbench=Workbench())
        window.show()
        app.processEvents()
        window.refresh_runs()
        restored = _selected(window)
        assert restored.id == run.id and restored.snapshot == run.snapshot
        assert "일부" in window.run_status.text() and window.run_selector.count() == 1
        assert window.grab().save(str(tmp_path / "actual-reopened-window.png"))
    finally:
        window.close()
        app.processEvents()
