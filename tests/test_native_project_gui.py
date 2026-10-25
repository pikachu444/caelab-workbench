"""Native file selection, persistence, transitions and read-only inspection."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import subprocess
import urllib.request
from types import SimpleNamespace

import pytest
pytest.importorskip("PySide6")
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog,
                               QLabel, QLineEdit, QMessageBox, QTextEdit, QWidget)

from apps.workbench_qt.project_window import ProjectWindow
from apps.workbench_qt.native_analysis import NativeAnalysisWindow


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Cancel)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unexpected launch"))
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("unexpected network"))
    instance = ProjectWindow()
    yield instance
    instance._dirty = False
    instance.close()


def _file(tmp_path, name, content="native file"):
    target = tmp_path / name
    target.write_text(content, encoding="utf-8")
    return target


def _select(window, box):
    window.scene.clearSelection()
    box.setSelected(True)


def _parent_text(window):
    """Include secondary UI surfaces, not only the visible summary."""
    texts = [window.windowTitle(), window.statusBar().currentMessage()]
    for widget in window.findChildren(QWidget):
        texts.extend((widget.toolTip(), widget.statusTip(), widget.whatsThis()))
        if isinstance(widget, QLabel):
            texts.append(widget.text())
        elif isinstance(widget, QTextEdit):
            texts.append(widget.toPlainText())
    for box in window.boxes.values():
        texts.extend((box.title, box.subtitle, box.file_label, box.detail, box.toolTip()))
    return "\n".join(texts)


def test_start_is_empty_and_file_commands_exist(window):
    assert window.project.nodes == {}
    assert window.boxes == {}
    assert not hasattr(window, "model")
    assert set(window.file_actions) == {"new", "open", "save", "save_as"}
    assert window.empty_hint.isVisible()
    text = window.log.toPlainText() + window.status.text()
    assert not any(token in text for token in ("demo.FCStd", "cantilever", "99개", "40개"))


def test_multiple_same_kind_keep_distinct_files(window, tmp_path):
    first, second = window.add_box("geometry"), window.add_box("geometry")
    assert first.node_id != second.node_id
    a, b = _file(tmp_path, "입력 A.FCStd"), _file(tmp_path, "입력 B.FCStd")
    assert window.register_file(first.node_id, a)
    assert window.register_file(second.node_id, b)
    assert window.project.nodes[first.node_id].files["input"].path == str(a)
    assert window.project.nodes[second.node_id].files["input"].path == str(b)
    _select(window, first)
    assert a.name in window.input_summary.text()
    assert b.name not in window.input_summary.text()


def test_selected_boxes_connect_the_file_shown_in_the_dialog(window, tmp_path, monkeypatch):
    geometry, analysis = window.add_box("geometry"), window.add_box("analysis")
    deck = _file(tmp_path, "선택 덱.inp")
    window.register_file(geometry.node_id, deck, "output")
    geometry.setSelected(True)
    analysis.setSelected(True)
    assert window.connect_action.isEnabled()
    def accept(dialog):
        source, role = dialog.findChildren(QComboBox)
        source.setCurrentIndex(source.findData(geometry.node_id))
        role.setCurrentIndex(role.findData("output"))
        assert any(deck.name in label.text() for label in dialog.findChildren(QLabel))
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", accept)
    window.connect_selected()
    assert len(window.connectors) == len(window.project.connections) == 1
    assert window.workbench.input_file(window.project, analysis.node_id).path == str(deck)
    replacement = _file(tmp_path, "직접 선택.inp")
    window.register_file(analysis.node_id, replacement)
    assert window.project.connections == [] and window.connectors == []
    assert window.workbench.input_file(window.project, analysis.node_id).path == str(replacement)


def test_program_missing_and_input_ready_are_shown_separately(window, tmp_path, monkeypatch):
    monkeypatch.setattr(window.workbench, "programs", lambda: [SimpleNamespace(
        spec=SimpleNamespace(id="freecad", name="FreeCAD"), available=False, path=None,
        source="environment", reason="설정 경로에 실행 파일이 없습니다", runtime_env={})])
    box = window.add_box("geometry")
    window.register_file(box.node_id, _file(tmp_path, "document.FCStd"))
    assert box.state == "ready"
    assert "연결 확인 필요" in box.subtitle
    assert "준비됨" in window.input_summary.text()
    assert "실행 파일을 찾지 못했거나" in window.program_summary.text()
    assert "연결 설정" in window.next_action.text()
    assert not window.open_button.isEnabled()


def test_parent_summary_keeps_sources_states_and_actions_without_raw_details(window, tmp_path, app):
    box = window.add_box("geometry")
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    window.save_project(tmp_path / "project.json")
    cad = _file(tmp_path / "B", "작업 사본.FCStd")
    deck = _file(tmp_path / "A", "FEMMeshGmsh.inp")
    new_deck = _file(tmp_path / "B", "FEMMeshGmsh.inp")
    window.register_file(box.node_id, cad, units={"length": "mm"})
    window.register_file(box.node_id, deck, "output", {"force": "N"})
    window.project.nodes[box.node_id].files["output"].stale = True
    _select(window, box)
    window._selection_changed()
    window.show()
    app.processEvents()
    assert "B/작업 사본.FCStd" in window.input_summary.text()
    assert "준비됨" in window.input_summary.text()
    assert "길이: mm" in window.input_summary.text()
    assert "A/FEMMeshGmsh.inp" in window.output_summary.text()
    assert "오래됨" in window.output_summary.text()
    assert "힘: N" in window.output_summary.text()
    assert "출력 파일" in window.next_action.text()
    assert "이미 작성" in window.next_action.text()
    assert window.open_button.text() == "FreeCAD 열기"
    assert "입력 상태: 준비됨" not in window.program_summary.text()
    assert str(tmp_path) not in window.input_summary.text() + window.output_summary.text()
    assert not any(hasattr(window, name) for name in ("properties", "details_dialog", "details_toggle"))
    assert window.input_button.isEnabled() and window.output_button.isEnabled()
    window.register_file(box.node_id, new_deck, "output", {"force": "N"})
    assert "B/FEMMeshGmsh.inp" in window.output_summary.text()
    assert "A/FEMMeshGmsh.inp" not in window.output_summary.text()
    cad.write_text("changed CAD")
    window.refresh_files()
    assert "원본 변경됨" in window.input_summary.text()
    cad.unlink()
    window.refresh_files()
    assert "파일 누락" in window.input_summary.text()
    assert "누락" in window.next_action.text()


def test_parent_task_history_is_collapsed_without_hiding_current_failure(window, app, tmp_path, monkeypatch):
    window.show()
    app.processEvents()
    window._message("첫 작업")
    window._message("파일을 읽지 못했습니다: 누락된 입력입니다")
    assert "누락된 입력" in window.status.text()
    assert not window.log.isVisible()
    box = window.add_box("geometry")
    window.register_file(box.node_id, _file(tmp_path, "model.FCStd"))
    monkeypatch.setattr(window.workbench, "open_program", lambda *args: SimpleNamespace(
        state="opened", pid=12345, error=""))
    window.open_box(box)
    assert "열기를 요청" in window.status.toolTip()
    assert "실제 파일 표시" in window.status.toolTip()
    assert "PID" not in window.status.toolTip() + window.log.toPlainText()
    window.resize(1800, 1100)
    app.processEvents()
    assert window.history_dock.height() <= 100
    window.history_toggle.click()
    app.processEvents()
    assert window.log.isVisible()
    assert "첫 작업" in window.log.toPlainText()
    assert "누락된 입력" in window.log.toPlainText()
    window.history_toggle.click()
    app.processEvents()
    assert not window.log.isVisible()
    assert window.history_dock.height() <= 100


def test_parent_zoom_controls_change_schematic_scale(window):
    initial = window.view.transform().m11()
    window.zoom_in_action.trigger()
    assert window.view.transform().m11() > initial
    window.zoom_out_action.trigger()
    assert window.view.transform().m11() == pytest.approx(initial)
    window.zoom_in_action.trigger()
    window.reset_view()
    assert window.view.transform().m11() == 1


def test_parent_reuses_file_status_cache_while_rereading_selection(window, tmp_path, monkeypatch):
    import caelab.project as project_module
    box = window.add_box("geometry")
    window.register_file(box.node_id, _file(tmp_path, "large.FCStd"))
    _select(window, box)
    window._selection_changed()
    monkeypatch.setattr(project_module, "file_revision", lambda path: pytest.fail("rehashed unchanged input"))
    window._selection_changed()
    window._selection_changed()


def test_fcstd_to_authored_inp_to_calculix_survives_save_and_save_as(window, tmp_path):
    geometry, other = window.add_box("geometry"), window.add_box("geometry")
    analysis = window.add_box("analysis")
    cad = _file(tmp_path, "실제 형상.FCStd", "external native document")
    deck = _file(tmp_path, "FreeCAD 작성 덱.inp", "*HEADING\nselected native deck\n")
    unrelated = _file(tmp_path, "다른 모델.inp", "different input")
    assert window.register_file(geometry.node_id, cad)
    assert window.register_file(geometry.node_id, deck, "output", {"length": "mm", "force": "N"})
    assert window.register_file(other.node_id, unrelated, "output")
    assert window.connect(geometry.node_id, analysis.node_id, "output")
    assert window.workbench.input_file(window.project, analysis.node_id).path == str(deck)
    original_ids = set(window.boxes)
    saved = tmp_path / "프로젝트.json"
    assert window.save_project(saved)
    window._message("다른 실행의 오래된 메시지")
    assert window.open_project(saved)
    assert set(window.boxes) == original_ids
    assert "다른 실행의 오래된 메시지" not in window.log.toPlainText()
    reference = window.workbench.input_file(window.project, analysis.node_id)
    assert reference.path == str(deck) and reference.units == {"length": "mm", "force": "N"}
    assert reference.unit_source == "user"
    destination = tmp_path / "다른 폴더"
    destination.mkdir()
    assert window.save_project(destination / "다른 이름.json")
    assert window.open_project(destination / "다른 이름.json")
    assert window.workbench.input_file(window.project, analysis.node_id).path == str(deck)
    _select(window, window.boxes[analysis.node_id])
    text = window.input_summary.text()
    assert "사용자 선언" in text and "길이: mm" in text and "힘: N" in text
    assert "연결 입력" in text and deck.name in text
    assert analysis.node_id not in text and reference.revision not in text
    assert cad.read_text(encoding="utf-8") == "external native document"


def test_changed_or_missing_file_marks_downstream_stale(window, tmp_path):
    geometry, analysis = window.add_box("geometry"), window.add_box("analysis")
    cad = _file(tmp_path, "원본.FCStd")
    deck = _file(tmp_path, "작성.inp", "*HEADING\nfirst\n")
    window.register_file(geometry.node_id, cad)
    window.register_file(geometry.node_id, deck, "output")
    window.connect(geometry.node_id, analysis.node_id, "output")
    revision = window.project.nodes[geometry.node_id].files["input"].revision
    cad.write_text("externally changed", encoding="utf-8")
    window.refresh_files()
    assert window.boxes[geometry.node_id].state == "changed"
    assert window.boxes[analysis.node_id].state == "stale"
    assert window.project.nodes[geometry.node_id].files["input"].revision == revision
    _select(window, window.boxes[analysis.node_id])
    assert "오래됨" in window.input_summary.text()
    cad.unlink()
    window.refresh_files()
    assert window.boxes[geometry.node_id].state == "missing"


def test_cancelled_file_selection_save_as_and_project_switch_keep_current(window, tmp_path, monkeypatch):
    box = window.add_box("geometry")
    current = window.project
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    assert not window.choose_file()
    assert not window.save_project_as()
    assert not window.open_project()
    assert not window.new_project()
    assert window.project is current and window._dirty
    assert window.project.nodes[box.node_id].files == {}
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()


def test_cancelled_unit_dialog_does_not_register_selected_file(window, tmp_path, monkeypatch):
    box = window.add_box("analysis")
    deck = _file(tmp_path, "input.inp")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(deck), ""))
    monkeypatch.setattr(QDialog, "exec", lambda *args: QDialog.Rejected)
    assert not window.choose_file()
    assert window.project.nodes[box.node_id].files == {}


def test_broken_open_and_failed_save_preserve_project_selection_and_dirty_state(window, tmp_path, monkeypatch):
    box = window.add_box("analysis")
    current = window.project
    broken = _file(tmp_path, "broken.json", "{ broken document")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Discard)
    assert not window.open_project(broken)
    assert window.project is current and window.boxes[box.node_id].isSelected()
    assert window._dirty
    def failed_save(*args):
        raise OSError("disk full")
    monkeypatch.setattr(window.workbench, "save_native_project", failed_save)
    assert not window.save_project(tmp_path / "save.json")
    assert window.project is current and window.project.path is None and window._dirty
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.Save)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(tmp_path / "save.json"), ""))
    assert not window.new_project()
    assert "저장 공간이 부족" in window.log.toPlainText()
    assert "disk full" not in window.log.toPlainText()


def test_file_units_are_entered_in_named_fields_and_empty_fields_stay_unknown(window, tmp_path, monkeypatch):
    box = window.add_box("analysis")
    deck = _file(tmp_path, "units.inp")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(deck), ""))
    def accept(dialog):
        dialog.findChild(QLineEdit, "unit_length").setText("mm")
        dialog.findChild(QLineEdit, "unit_force").setText("N")
        assert all("length=" not in label.text() for label in dialog.findChildren(QLabel))
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, "exec", accept)
    assert window.choose_file()
    reference = window.workbench.input_file(window.project, box.node_id)
    assert reference.units == {"length": "mm", "force": "N"}
    assert "길이: mm" in window.input_summary.text()
    assert "사용자 선언" in window.input_summary.text()
    assert "힘: N" in window.input_summary.text()
    assert "응력:" not in window.input_summary.text()


def test_default_view_keeps_original_scale_in_a_small_window(window, app):
    for kind in ("geometry", "analysis", "results", "expert"):
        window.add_box(kind)
    window.resize(960, 640)
    window.show()
    app.processEvents()
    assert window.view.transform().m11() == 1.0
    window.fit_schematic()
    assert window.view.transform().m11() < 1.0
    window.reset_view()
    assert window.view.transform().m11() == 1.0


def test_file_open_passes_actual_file_to_workbench_process(window, tmp_path, monkeypatch):
    exe = _file(tmp_path, "FreeCAD.exe")
    exe.chmod(0o755)
    cad = _file(tmp_path, "모델 한글.FCStd")
    monkeypatch.setenv("CAELAB_FREECAD", str(exe))
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda command, **kwargs:
                        calls.append((command, kwargs)) or SimpleNamespace(pid=41))
    box = window.add_box("geometry")
    window.register_file(box.node_id, cad)
    window.open_box(box)
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert str(cad) in command and str(exe) == command[0]
    assert Path(kwargs["cwd"]) == tmp_path
    assert isinstance(kwargs["env"], dict)
    assert "PID" not in _parent_text(window)
    assert str(exe) not in _parent_text(window)
    assert "열기를 요청" in _parent_text(window)


def test_parent_translates_service_failures_without_moving_dumps_to_secondary_ui(
        window, tmp_path, monkeypatch):
    diagnostic = "request_id=internal-123 SHA256=" + "9" * 64 + " PID 654321 CAELAB_FREECAD=C:/private/tool.exe"
    monkeypatch.setattr(window.workbench, "programs", lambda: [SimpleNamespace(
        spec=SimpleNamespace(id="freecad", name="FreeCAD"), available=False,
        path="C:/private/tool.exe", source="environment", reason="실행 파일이 없습니다: " + diagnostic)])
    box = window.add_box("geometry")
    # A user filename is not an internal identifier and must remain intact.
    filename = "9" * 64 + ".FCStd"
    cad = _file(tmp_path, filename)
    window.register_file(box.node_id, cad)
    node = window.project.nodes[box.node_id]
    node.state, node.reason = "failed", diagnostic
    window.project.issues[:] = [diagnostic]
    window._reflect_node(node.id)
    window._selection_changed()
    window._message("프로젝트 확인\n" + "\n".join(window._project_notes()))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    monkeypatch.setattr(window.workbench, "open_program", lambda *args: SimpleNamespace(
        state="failed", error=diagnostic))
    window.open_box(box)
    text = _parent_text(window) + "\n" + "\n".join(warnings)
    assert filename in text
    for internal in ("request_id", "internal-123", "SHA256=", "PID", "654321", "CAELAB_FREECAD", "C:/private"):
        assert internal not in text
    assert "원인을 확인하지 못했습니다" in text
    assert "설치와 프로그램 연결 설정" in text
    assert node.reason == diagnostic and window.project.issues == [diagnostic]
    assert warnings


@pytest.mark.parametrize("error,action", [
    (PermissionError("C:/private SHA256=123"), "접근할 권한"),
    (OSError("disk full /private/state.json"), "저장 공간이 부족"),
    (ValueError("connections: 순환 연결은 지원하지 않습니다. internal-id"), "한 방향"),
    (ValueError("지원하지 않는 프로젝트 schema_version: {'secret': 'dump'}"), "호환되는 버전"),
    (ValueError("기대 실행 저장소가 없습니다: /private/internal-id.runs"), "기록 폴더"),
    (FileNotFoundError("/private/input.inp"), "파일을 다시 선택"),
])
def test_known_problem_keeps_corrective_action_without_raw_details(window, monkeypatch, error, action):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    window._error("선택한 작업을 마치지 못했습니다", error)
    assert action in warnings[0] and action in window.status.toolTip()
    assert str(error) not in warnings[0]


def test_deselection_clears_file_tooltips(window, tmp_path):
    first, second = window.add_box("geometry"), window.add_box("geometry")
    window.register_file(first.node_id, _file(tmp_path, "first.FCStd"))
    _select(window, first)
    assert "first.FCStd" in window.input_summary.toolTip()
    _select(window, second)
    assert "선택 파일 없음" in window.input_summary.text()
    assert not window.input_summary.toolTip()
    window.scene.clearSelection()
    assert not window.input_summary.toolTip() and not window.output_summary.toolTip()


def test_results_without_a_file_never_launch_empty_paraview(window):
    box = window.add_box("results")
    window.open_box(box)
    assert "열지 못했습니다" in window.status.text()
    assert box.state == "failed"


def test_native_input_window_uses_saved_selected_deck_and_never_runs(window, tmp_path):
    box = window.add_box("analysis")
    deck = _file(tmp_path, "설계.inp", "*HEADING\nuser selected deck\n")
    window.register_file(box.node_id, deck, units={"length": "mm"})
    saved = tmp_path / "입력 확인.json"
    window.save_project(saved)
    inspector = NativeAnalysisWindow(saved, box.node_id)
    assert inspector.source.isReadOnly() and inspector.details.isReadOnly()
    assert inspector.source.toPlainText() == deck.read_text(encoding="utf-8")
    assert str(deck) in inspector.details.toPlainText()
    assert "단위 출처: 사용자 선언" in inspector.details.toPlainText()
    assert box.node_id not in inspector.details.toPlainText()
    assert not hasattr(inspector, "expert_panel")
    deck.unlink()
    inspector.refresh_input()
    assert inspector.source.toPlainText() == ""
    assert "파일 누락" in inspector.status.text()
    assert "읽지 못했습니다" in inspector.details.toPlainText()
    inspector.close()


def test_cli_project_argument_reaches_project_window(monkeypatch):
    from apps.workbench_qt import __main__ as entry
    calls = []
    class App:
        def __init__(self, args): pass
        def setStyle(self, style): pass
        def exec(self): return 27
    class Window:
        def __init__(self, workbench, project_path=None): calls.append(project_path)
        def show(self): pass
    monkeypatch.setattr(entry, "QApplication", App)
    monkeypatch.setattr(entry, "ProjectWindow", Window)
    assert entry.main(["연구 프로젝트.json"]) == 27
    assert calls == ["연구 프로젝트.json"]


@pytest.mark.parametrize("kind", ["results", "expert"])
def test_internal_result_summary_uses_its_receipt_without_diagnostic_id(window, tmp_path, kind):
    from caelab.native.run_store import NativeRun, RunStore
    box = window.add_box(kind)
    assert window.save_project(tmp_path / "project.json")
    run_id = "a" * 32
    folder = window.project.run_store / run_id
    folder.mkdir()
    result = _file(folder, "model.frd")
    source = tmp_path / "A" / "FEMMeshGmsh.inp"
    source.parent.mkdir()
    source.write_text("native input")
    RunStore(window.project.run_store).write(NativeRun(
        id=run_id, node_id="other-analysis", workdir=folder, state="completed",
        input_path=str(source), created_at="2026-10-24T09:18:36Z"))
    assert window.register_file(box.node_id, result)
    _select(window, box)
    window._selection_changed()
    text = window.input_summary.text()
    assert "model.frd" in text and "A/FEMMeshGmsh.inp" in text
    from apps.workbench_qt.project_window import _request_time
    assert _request_time("2026-10-24T09:18:36Z") in text
    assert run_id not in text and "project.runs" not in text
    assert window.workbench.input_file(window.project, box.node_id).path == str(result)
    assert run_id not in window.input_summary.toolTip()
    assert "이 파일의 계산" in text and "선택한 계산:" not in text
    (folder / "job.json").unlink()
    window._selection_changed()
    assert "실행 출처 미확인" in window.input_summary.text()
    assert "A/FEMMeshGmsh.inp" not in window.input_summary.text()
    assert run_id not in window.input_summary.text()
