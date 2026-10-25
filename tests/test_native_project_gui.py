"""Native file selection, persistence, transitions and read-only inspection."""
import os
import errno
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


def _input_text(window):
    return window.input_summary.text() + "\n" + window.input_value.text()


def _parent_text(window):
    """Include secondary UI surfaces, not only the visible summary."""
    texts = [window.windowTitle(), window.statusBar().currentMessage()]
    for widget in window.findChildren(QWidget):
        texts.extend((widget.toolTip(), widget.statusTip(), widget.whatsThis()))
        if isinstance(widget, (QLabel, QLineEdit)):
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
    assert a.name in _input_text(window)
    assert b.name not in _input_text(window)


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
        source="environment", reason="실행 파일이 없거나 일반 파일이 아닙니다: C:/private/FreeCAD.exe", runtime_env={})])
    box = window.add_box("geometry")
    window.register_file(box.node_id, _file(tmp_path, "document.FCStd"))
    assert box.state == "ready"
    assert "프로그램 연결 필요" in box.subtitle
    assert window.input_summary.text() == "입력 파일"
    assert "실행 파일을 찾지 못했거나" in window.program_summary.text()
    assert not window.open_button.isEnabled()


def test_parent_summary_keeps_sources_states_and_actions_without_raw_details(window, tmp_path, app, monkeypatch):
    monkeypatch.setattr(window.workbench, 'programs', lambda: [SimpleNamespace(
        spec=SimpleNamespace(id='freecad', name='FreeCAD'), available=True)])
    box = window.add_box("geometry")
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    window.save_project(tmp_path / "project.json")
    cad = _file(tmp_path / "B", "작업 사본.FCStd")
    original_cad = _file(tmp_path / "A", "원본.FCStd")
    deck = _file(tmp_path / "A", "FEMMeshGmsh.inp")
    new_deck = _file(tmp_path / "B", "FEMMeshGmsh.inp")
    window.register_file(box.node_id, original_cad, units={"length": "mm"})
    window.register_file(box.node_id, deck, "output", {"force": "N"})
    window.register_file(box.node_id, cad, units={"length": "mm"})
    _select(window, box)
    window._selection_changed()
    window.show()
    app.processEvents()
    assert "B/작업 사본.FCStd" in _input_text(window)
    assert window.input_summary.text() == "입력 파일"
    assert window.input_units_button.text() == "단위…"
    assert "A/FEMMeshGmsh.inp" in (window.output_summary.text() + window.output_value.text())
    assert window.output_summary.text() == "출력 파일 갱신 필요"
    assert box.status_label == "출력 파일 갱신 필요"
    assert window.output_units_button.text() == "단위…"
    assert window.output_button.isVisible() and window.output_button.isEnabled()
    assert window.open_button.text() == "FreeCAD 열기"
    assert not window.program_summary.text()
    assert str(tmp_path) not in _input_text(window) + (window.output_summary.text() + window.output_value.text())
    assert not any(hasattr(window, name) for name in ("properties", "details_dialog", "details_toggle"))
    assert window.input_button.isEnabled() and window.output_button.isEnabled()
    window.register_file(box.node_id, new_deck, "output", {"force": "N"})
    assert "B/FEMMeshGmsh.inp" in (window.output_summary.text() + window.output_value.text())
    assert "A/FEMMeshGmsh.inp" not in (window.output_summary.text() + window.output_value.text())
    cad.write_text("changed CAD")
    window.refresh_files()
    assert window.input_summary.text() == "입력 파일 변경됨"
    cad.unlink()
    window.refresh_files()
    assert window.input_summary.text() == "입력 파일 누락"
    assert window.open_button.text() == "입력 파일 지정…"


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
    assert "열기 요청" in window.status.toolTip()
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


def test_parent_zoom_controls_change_schematic_scale(window, app):
    window.show()
    app.processEvents()
    assert window.zoom_percent.text() == '100%'
    initial = window.view.transform().m11()
    window.zoom_in_action.trigger()
    assert window.view.transform().m11() > initial
    assert window.zoom_percent.text() == '120%'
    window.zoom_out_action.trigger()
    assert window.view.transform().m11() == pytest.approx(initial)
    assert window.zoom_percent.text() == '100%'
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
    text = _input_text(window)
    assert "사용자 선언" not in text and "길이: mm" not in text
    assert window.input_units_button.isEnabled()
    assert window.input_summary.text() == "입력 파일" and deck.name in text
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
    assert window.input_summary.text() == "입력 파일 갱신 필요"
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
    assert window.input_units_button.text() == "단위…"
    assert "사용자 선언" not in _input_text(window)
    assert window.input_units_button.isEnabled()


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
    assert "열기 요청" in _parent_text(window)


def test_parent_translates_service_failures_without_moving_dumps_to_secondary_ui(
        window, tmp_path, monkeypatch):
    diagnostic = "request_id=internal-123 SHA256=" + "9" * 64 + " PID 654321 CAELAB_FREECAD=C:/private/tool.exe"
    monkeypatch.setattr(window.workbench, "programs", lambda: [SimpleNamespace(
        spec=SimpleNamespace(id="freecad", name="FreeCAD"), available=False,
        path="C:/private/tool.exe", source="environment", reason="실행 파일이 없거나 일반 파일이 아닙니다: " + diagnostic)])
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
    (OSError(errno.ENOSPC, "No space left on device", "/private/state.json"), "저장 공간이 부족"),
    (ValueError("connections: 순환 연결은 지원하지 않습니다."), "한 방향"),
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
    assert "first.FCStd" in window.input_value.toolTip()
    _select(window, second)
    assert not window.input_value.text()
    assert not window.input_value.toolTip()
    window.scene.clearSelection()
    assert not window.input_value.toolTip() and not window.output_value.toolTip()


def test_external_sources_remain_distinct_in_summary_tooltip_and_connection(window, tmp_path, monkeypatch):
    project_dir = tmp_path / 'project'
    project_dir.mkdir()
    assert window.save_project(project_dir / 'research.json')
    sources = []
    boxes = []
    for variant in ('Variant-A', 'Variant-B'):
        folder = tmp_path / 'Study' / variant / 'one' / 'two' / 'CAD'
        folder.mkdir(parents=True)
        path = _file(folder, 'model.FCStd')
        box = window.add_box('geometry')
        assert window.register_file(box.node_id, path)
        sources.append(path)
        boxes.append(box)
    for box, path in zip(boxes, sources):
        _select(window, box)
        assert f'{path.parts[-5]}/one/two/CAD/model.FCStd' in _input_text(window)
        assert window.input_value.toolTip() == str(path)
        assert str(path) in box.toolTip()
        assert window.project.nodes[box.node_id].files['input'].path == str(path)
    assert 'Variant-A/one/two/CAD/model.FCStd' in boxes[0].file_label
    assert 'Variant-B/one/two/CAD/model.FCStd' in boxes[1].file_label
    # Referencing the exact same file again is not a naming collision.
    duplicate = window.add_box('geometry')
    window.register_file(duplicate.node_id, sources[0])
    assert window._short_path(str(sources[0])) == 'Variant-A/one/two/CAD/model.FCStd'
    target = window.add_box('analysis')
    window.scene.clearSelection()
    boxes[0].setSelected(True)
    target.setSelected(True)
    def accept(dialog):
        source, role = dialog.findChildren(QComboBox)
        source.setCurrentIndex(source.findData(boxes[0].node_id))
        role.setCurrentIndex(role.findData('input'))
        assert any(label.text() == 'Variant-A/one/two/CAD/model.FCStd'
                   and label.toolTip() == str(sources[0]) for label in dialog.findChildren(QLabel))
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, 'exec', accept)
    window.connect_selected()
    assert window.workbench.input_file(window.project, target.node_id).path == str(sources[0])
    assert 'Variant-B/one/two/CAD/model.FCStd' in window.log.toPlainText()


def test_external_suffix_does_not_collide_with_project_relative_file(window, tmp_path):
    root = tmp_path / 'project'
    (root / 'CAD').mkdir(parents=True)
    (tmp_path / 'Study' / 'CAD').mkdir(parents=True)
    window.save_project(root / 'project.json')
    inside = _file(root / 'CAD', 'model.FCStd')
    outside = _file(tmp_path / 'Study' / 'CAD', 'model.FCStd')
    for path in (inside, outside):
        box = window.add_box('geometry')
        window.register_file(box.node_id, path)
    assert window._short_path(str(inside)) == 'CAD/model.FCStd'
    assert window._short_path(str(outside)) == 'Study/CAD/model.FCStd'
    past = tmp_path / 'unregistered' / 'CAD' / 'model.FCStd'
    assert window._short_path(str(past)) == str(past)


@pytest.mark.skipif(os.name != 'nt', reason='Windows drive letters are not Linux path components')
def test_external_sources_on_different_drives_are_distinct(window, tmp_path):
    from caelab.project import FileReference
    window.save_project(tmp_path / 'project.json')
    paths = ['R:/Study/CAD/model.FCStd', 'S:/Study/CAD/model.FCStd']
    for path in paths:
        box = window.add_box('geometry')
        window.project.nodes[box.node_id].files['input'] = FileReference(path)
    assert window._short_path(paths[0]) == paths[0]
    assert window._short_path(paths[1]) == paths[1]


def test_save_as_inside_records_explains_destination_without_claiming_missing_history(window, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    original = tmp_path / 'research.json'
    assert window.save_project(original)
    saved = original.read_bytes()
    target = tmp_path / 'research.runs' / 'nested.json'
    assert not window.save_project(target)
    for text in (warnings[-1], window.status.toolTip(), window.log.toPlainText()):
        assert '폴더 밖의 새 프로젝트 위치' in text
        assert '없거나 온전히' not in text
    assert window.project.path == original and original.read_bytes() == saved
    assert not target.exists()


@pytest.mark.parametrize('source,action', [
    ('복사 준비 중 대화 목록이 변경되었습니다. 다시 저장하세요.', '대화 목록이 바뀌었습니다'),
    ('복사 중 실행/대화/자산 파일이 변경되었습니다. 다시 저장하세요.', '자료가 바뀌었습니다'),
    ('검사 중 저장소 파일이 변경되었습니다: C:/private/research.runs/job.json', '확인하는 동안 파일이 바뀌었습니다'),
    ('[WinError 5] 액세스가 거부되었습니다', '접근할 권한'),
    ('PermissionError: [WinError 5] Access is denied: C:/private/snapshot.FCStd', '접근할 권한'),
])
def test_exact_service_and_serialized_os_errors_keep_recovery(window, monkeypatch, source, action):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    window._error('작업을 마치지 못했습니다', source)
    for text in (warnings[-1], window.status.toolTip(), window.log.toPlainText()):
        assert action in text
        assert 'C:/private' not in text and 'WinError' not in text and 'job.json' not in text


@pytest.mark.parametrize('filename', ['input.FCStd', 'snapshot.FCStd', 'nodes.input.FCStd'])
def test_missing_file_type_wins_over_filename_keywords(window, monkeypatch, filename):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    error = FileNotFoundError(errno.ENOENT, 'No such file or directory', filename)
    window._error('입력 파일을 열지 못했습니다', error)
    assert '파일을 다시 선택' in warnings[-1]
    assert '기록이 없거나' not in warnings[-1] and '연결 정보를 읽지' not in warnings[-1]


def test_invalid_program_connection_configuration_keeps_actionable_cause(window, tmp_path, monkeypatch):
    from caelab.connections import PROGRAMS, _status
    executable = tmp_path / 'FreeCAD.exe'
    executable.write_bytes(b'not executed')
    monkeypatch.setenv('CAELAB_FREECAD', str(executable))
    spec = next(program for program in PROGRAMS if program.id == 'freecad')
    status = _status(spec, {'runtime_env': {'freecad': []}}, '', 'windows')
    assert not status.available
    monkeypatch.setattr(window.workbench, 'programs', lambda: [status])
    box = window.add_box('geometry')
    assert window.register_file(box.node_id, _file(tmp_path, 'model.FCStd'))
    for text in (window.program_summary.text(),):
        assert '연결 설정' in text
        assert 'runtime_env' not in text and str(executable) not in text
    assert '형식이 올바르지 않습니다' in window.program_summary.text()
    assert not window.open_button.isEnabled()


def test_open_newer_project_preserves_wrapped_version_cause(window, tmp_path, monkeypatch):
    import json
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args[2]))
    original = window.project
    path = tmp_path / 'newer-version.json'
    source = json.dumps({'schema_version': 999, 'name': 'Different version',
                         'nodes': {}, 'connections': []}).encode('utf-8')
    path.write_bytes(source)
    assert not window.open_project(path, confirm=False)
    assert window.project is original and path.read_bytes() == source
    for text in (warnings[-1], window.status.toolTip(), window.log.toPlainText()):
        assert '호환되는 버전에서 다시 여세요' in text
        assert 'schema_version' not in text and str(path) not in text


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
def test_internal_result_file_remains_selectable_without_receipt_metadata(window, tmp_path, kind):
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
    text = _input_text(window)
    assert "model.frd" in text and "A/FEMMeshGmsh.inp" not in text
    assert "2026-10-24" not in text and "기준 입력" not in text
    assert run_id not in text and "project.runs" not in text
    assert window.workbench.input_file(window.project, box.node_id).path == str(result)
    assert not window.input_units_button.isEnabled()
    assert not window.edit_units()
    assert run_id not in window.input_value.toolTip()
    assert "이 파일의 계산" not in text and "선택한 계산:" not in text
    (folder / "job.json").unlink()
    window._selection_changed()
    assert window.input_value.text() == "model.frd"
    assert "A/FEMMeshGmsh.inp" not in _input_text(window)
    assert run_id not in _input_text(window)


@pytest.mark.parametrize("kind", ["geometry", "mesh", "results"])
@pytest.mark.parametrize("interaction", ["button", "double_click"])
def test_missing_input_default_assigns_file_without_failed_launch(window, app, tmp_path, monkeypatch, kind, interaction):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt
    box = window.add_box(kind)
    missing = _file(tmp_path, "removed.native")
    window.register_file(box.node_id, missing)
    missing.unlink()
    window.refresh_files()
    before = window.project.nodes[box.node_id].files['input']
    requests = []
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: requests.append(args[1]) or ('', ''))
    monkeypatch.setattr(window.workbench, 'open_program', lambda *args: pytest.fail('default must assign missing input'))
    window.show()
    app.processEvents()
    if interaction == 'button':
        window.open_button.click()
    else:
        QTest.mouseDClick(window.view.viewport(), Qt.LeftButton,
                         pos=window.view.mapFromScene(box.sceneBoundingRect().center()))
    assert len(requests) == 1
    assert window.project.nodes[box.node_id].files['input'] is before
    assert box.state == 'missing' and not window.workbench.jobs.launches
    assert window.open_button.text() in {'입력 파일 지정…', '결과 파일 지정…'}


def test_empty_default_can_assign_and_subsequently_open_actual_file(window, tmp_path, monkeypatch):
    box = window.add_box('geometry')
    cad = _file(tmp_path, 'document.FCStd')
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: (str(cad), ''))
    monkeypatch.setattr(window, '_ask_file_units', lambda *args: {'length': 'mm'})
    window.open_button.click()
    assert window.workbench.input_file(window.project, box.node_id).path == str(cad)
    calls = []
    monkeypatch.setattr(window.workbench, 'open_program', lambda project, node: calls.append(node) or SimpleNamespace(state='opened'))
    # The actual program is mocked as available here; availability is a separate condition.
    monkeypatch.setattr(window.workbench, 'programs', lambda: [SimpleNamespace(spec=SimpleNamespace(id='freecad', name='FreeCAD'), available=True)])
    window._selection_changed()
    window.open_button.click()
    assert calls == [box.node_id]
    assert box.state != 'done'


def test_connected_file_commands_follow_final_owner_and_units_preserve_edges(window, tmp_path, monkeypatch):
    first, middle, last = [window.add_box(kind) for kind in ('geometry', 'mesh', 'analysis')]
    deck = _file(tmp_path, 'shared.inp')
    inactive = _file(tmp_path, 'inactive.inp')
    window.register_file(first.node_id, deck, 'output', {'length': 'mm'})
    window.register_file(middle.node_id, inactive)
    window.connect(first.node_id, middle.node_id, 'output')
    window.connect(middle.node_id, last.node_id, 'input')
    edges = list(window.project.connections)
    reference = window.project.nodes[first.node_id].files['output']
    revision = reference.revision
    reference.stale = True
    deck.write_text('external change not adopted')
    _select(window, last)
    assert window.owner_button.isHidden() is False
    assert window.owner_button.text() == 'FreeCAD 출력으로 이동'
    assert window.input_button.text() == '직접 파일로…'
    window.select_input_owner()
    assert window.boxes[first.node_id].isSelected()
    _select(window, last)
    seen = []
    monkeypatch.setattr(window, '_ask_file_units', lambda path, current: seen.append((path, dict(current))) or {'length': 'm'})
    assert window.edit_units()
    assert seen == [(str(deck), {'length': 'mm'})]
    assert reference.units == {'length': 'm'} and reference.unit_source == 'user'
    assert reference.path == str(deck) and reference.revision == revision and reference.stale
    assert window.project.connections == edges
    assert window.project.nodes[middle.node_id].files['input'].path == str(inactive)
    assert window.workbench.input_file(window.project, last.node_id) is reference
    assert window.project.nodes[last.node_id].state == 'stale'


def test_existing_unit_dialog_prefills_and_cancel_or_same_value_does_not_mutate(window, tmp_path, monkeypatch):
    box = window.add_box('geometry')
    cad = _file(tmp_path, 'document.FCStd')
    window.register_file(box.node_id, cad, units={'length': 'mm', 'force': 'N'})
    reference = window.project.nodes[box.node_id].files['input']
    reference.unit_source = 'imported'
    window._dirty = False
    before = dict(vars(reference))
    def reject(dialog):
        assert dialog.findChild(QLineEdit, 'unit_length').text() == 'mm'
        assert dialog.findChild(QLineEdit, 'unit_force').text() == 'N'
        assert dialog.findChild(QLineEdit, 'unit_stress').text() == ''
        return QDialog.Rejected
    monkeypatch.setattr(QDialog, 'exec', reject)
    assert not window.edit_units()
    monkeypatch.setattr(window, '_ask_file_units', lambda *args: dict(reference.units))
    assert window.edit_units()
    assert vars(reference) == before and not window._dirty


def test_direct_file_alternative_is_explicit_and_cancellation_keeps_connection(window, tmp_path, monkeypatch):
    source, target = window.add_box('geometry'), window.add_box('analysis')
    deck, replacement = _file(tmp_path, 'old.inp'), _file(tmp_path, 'new.inp')
    window.register_file(source.node_id, deck, 'output', {'force': 'N'})
    window.connect(source.node_id, target.node_id)
    edges = list(window.project.connections)
    _select(window, target)
    def choose(*args):
        assert args[1] == '상위 연결 대신 사용할 입력 파일'
        return str(replacement), ''
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', choose)
    monkeypatch.setattr(window, '_ask_file_units', lambda *args: None)
    assert not window.choose_file()
    assert window.project.connections == edges
    assert window.workbench.input_file(window.project, target.node_id).path == str(deck)
    monkeypatch.setattr(window, '_ask_file_units', lambda *args: {})
    assert window.choose_file()
    assert window.project.connections == []
    assert window.workbench.input_file(window.project, target.node_id).path == str(replacement)


def test_two_targets_show_connection_context_and_arrow_destination(window, tmp_path, monkeypatch):
    first, second = window.add_box('geometry'), window.add_box('geometry')
    for box, filename in ((first, 'A.FCStd'), (second, 'B.FCStd')):
        window.register_file(box.node_id, _file(tmp_path, filename))
    first.setSelected(True)
    second.setSelected(True)
    assert window.selection_name.text() == '상자 연결'
    assert 'A.FCStd' in window.program_summary.text() and 'B.FCStd' in window.program_summary.text()
    assert not window.selection_connect_button.isHidden() and window.open_button.isHidden()
    def accept(dialog):
        source, role = dialog.findChildren(QComboBox)
        source.setCurrentIndex(source.findData(first.node_id))
        role.setCurrentIndex(role.findData('input'))
        assert 'A.FCStd' in source.currentText()
        assert any('B.FCStd' in label.text() for label in dialog.findChildren(QLabel))
        return QDialog.Accepted
    monkeypatch.setattr(QDialog, 'exec', accept)
    window.selection_connect_button.click()
    connector = window.connectors[0]
    assert connector.src is first and connector.dst is second
    assert connector.path().pointAtPercent(1) == second.left_anchor()
    assert window.workbench.input_file(window.project, second.node_id).path.endswith('A.FCStd')


def test_narrow_workspace_docks_can_close_reopen_and_fit(window, app):
    for kind in ('geometry', 'analysis', 'results', 'expert'):
        window.add_box(kind)
    window.resize(960, 680)
    window.show()
    app.processEvents()
    original_width = window.view.viewport().width()
    for dock in (window.toolbox_dock, window.settings_dock):
        dock.close()
    app.processEvents()
    assert window.view.viewport().width() > original_width + 200
    view_menu = window.view_menu
    for dock in (window.toolbox_dock, window.settings_dock):
        assert dock.toggleViewAction() in view_menu.actions()
        dock.toggleViewAction().trigger()
        assert dock.isVisible()
    app.processEvents()
    window.fit_schematic()
    visible = window.view.mapToScene(window.view.viewport().rect()).boundingRect()
    assert visible.contains(window.scene.itemsBoundingRect())
    window.reset_view()
    assert window.view.transform().m11() == 1


def test_recorded_calculation_output_is_not_a_manual_output_setting(window, tmp_path, monkeypatch):
    box = window.add_box('analysis')
    assert window.save_project(tmp_path / 'project.json')
    folder = window.project.run_store / ('a' * 32)
    folder.mkdir()
    output = _file(folder, 'model.frd')
    window.register_file(box.node_id, output, 'output')
    assert window.output_value.text() == 'model.frd'
    assert window.output_summary.text().startswith('선택 계산 결과')
    assert not window.output_button.isEnabled() and not window.output_units_button.isEnabled()
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *args: pytest.fail('protected output chooser opened'))
    assert not window.choose_file('output')
    assert window.open_button.text() == '해석창 열기'
    assert window.project.nodes[box.node_id].files['output'].path == str(output)



def test_canvas_controls_stay_fixed_and_show_real_scale_for_all_commands(window, app):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence, QTransform
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QToolBar
    for kind in ('geometry', 'analysis', 'results', 'expert'):
        window.add_box(kind)
    window.resize(960, 680)
    window.show()
    window.activateWindow()
    app.processEvents()
    panel = window.view.controls
    assert panel.parentWidget() is window.view.viewport()
    assert not hasattr(panel, 'scene')
    bounds = window.scene.itemsBoundingRect()
    panel_size = panel.size()
    assert [a.text() for a in window.view_menu.actions() if a in (
        window.zoom_in_action, window.zoom_out_action, window.fit_action, window.reset_view_action
    )] == ['확대', '축소', '화면에 맞추기', '100%로 보기']
    toolbar = next(bar for bar in window.findChildren(QToolBar) if bar.windowTitle() == '프로젝트')
    for action in (window.zoom_in_action, window.zoom_out_action, window.fit_action, window.reset_view_action):
        assert action not in toolbar.actions()
    for action in toolbar.actions():
        widget = toolbar.widgetForAction(action)
        if widget:
            assert widget.isVisible() and toolbar.rect().contains(widget.geometry())
    assert window.zoom_in_action.shortcut() == QKeySequence.ZoomIn
    QTest.keySequence(window.view, QKeySequence.ZoomIn)
    app.processEvents()
    assert window.zoom_percent.text() == '120%'
    QTest.mouseClick(window.zoom_in_button, Qt.LeftButton)
    assert window.zoom_percent.text() == '144%'
    assert '144%' in window.zoom_percent.accessibleName()
    bar = window.view.horizontalScrollBar()
    bar.setFocus()
    QTest.keyClick(bar, Qt.Key_Home)
    assert bar.value() == bar.minimum()
    assert window.zoom_percent.text() == '144%'
    QTest.mouseClick(window.zoom_out_button, Qt.LeftButton)
    assert window.zoom_percent.text() == '120%'
    window.view.setTransform(QTransform.fromScale(.735, .735))
    assert window.zoom_percent.text() == '73.5%'
    QTest.mouseClick(window.fit_button, Qt.LeftButton)
    assert float(window.zoom_percent.text()[:-1]) == pytest.approx(window.view.transform().m11()*100, abs=.051)
    assert window.view.transform().m11() < 1
    assert window.scene.itemsBoundingRect() == bounds
    assert panel.size() == panel_size and window.view.viewport().rect().contains(panel.geometry())
    QTest.mouseClick(window.zoom_percent, Qt.LeftButton)
    assert window.zoom_percent.text() == '100%' and window.view.transform().m11() == 1
    window.zoom_in_action.trigger()
    window.reset_view_action.trigger()
    assert window.zoom_percent.text() == '100%'


def test_selection_docks_and_reshow_keep_current_scale_and_view_position(window, app):
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest
    for kind in ('geometry', 'analysis', 'results'):
        window.add_box(kind)
    window.scene.setSceneRect(-2000, -2000, 4000, 4000)
    window.resize(960, 680)
    window.show()
    app.processEvents()
    window.view.scale(2, 2)
    window.view.centerOn(QPointF(240, 170))
    before = window.view.mapToScene(window.view.viewport().rect().center())
    for box in window.boxes.values():
        _select(window, box)
        assert window.zoom_percent.text() == '200%'
    for dock in (window.toolbox_dock, window.settings_dock):
        dock.close()
    app.processEvents()
    for dock in (window.toolbox_dock, window.settings_dock):
        dock.toggleViewAction().trigger()
    app.processEvents()
    window.hide()
    window.show()
    app.processEvents()
    after = window.view.mapToScene(window.view.viewport().rect().center())
    assert window.zoom_percent.text() == '200%' and window.view.transform().m11() == 2
    assert abs(after.x()-before.x()) < 1 and abs(after.y()-before.y()) < 1
    window.zoom_percent.click()
    restored = window.view.mapToScene(window.view.viewport().rect().center())
    assert abs(restored.x()-after.x()) < 1 and abs(restored.y()-after.y()) < 1
    assert window.zoom_percent.text() == '100%'
    for _ in range(3):
        window.zoom_percent.click()
        assert window.view.mapToScene(window.view.viewport().rect().center()) == restored
    # A newly initialized project starts at 100%; re-showing one does not reset it.
    window._dirty = False
    assert window.new_project()
    assert window.zoom_percent.text() == '100%'


def test_input_owner_move_identifies_final_input_without_breaking_chain(window, tmp_path):
    first, other, last = [window.add_box(kind) for kind in ('geometry', 'geometry', 'analysis')]
    for name, box in (('A', first), ('B', other)):
        folder = tmp_path/name
        folder.mkdir()
        window.register_file(box.node_id, _file(folder, 'model.FCStd'))
    window.connect(first.node_id, other.node_id, 'input')
    window.connect(other.node_id, last.node_id, 'input')
    edges = list(window.project.connections)
    _select(window, last)
    assert window.owner_button.text() == 'FreeCAD 입력으로 이동'
    window.owner_button.click()
    assert first.isSelected() and not other.isSelected()
    assert 'A/model.FCStd' in window.input_value.text().replace('\\', '/')
    assert window.project.connections == edges


def test_generic_stale_file_does_not_invent_a_model_change_or_calculated_result(window, tmp_path):
    box = window.add_box('analysis')
    window.save_project(tmp_path/'project.json')
    folder = window.project.run_store/'manual'
    folder.mkdir()
    deck = _file(folder, 'model.inp')
    window.register_file(box.node_id, deck)
    reference = window.project.nodes[box.node_id].files['input']
    reference.stale = True
    window._reflect_node(box.node_id)
    window._selection_changed()
    assert window.input_summary.text() == '입력 파일 갱신 필요'
    assert box.status_label == '입력 파일 갱신 필요'
    assert '이전 입력의 결과' not in _input_text(window)
    for text in (window.input_summary.text(), box.status_label):
        assert not any(word in text for word in ('오래됨', '형상 변경', '상위 작업 변경', 'B 미반영'))


@pytest.mark.parametrize('state', ['running', 'failed'])
def test_file_warnings_do_not_replace_execution_or_failure_status(window, tmp_path, state):
    from apps.workbench_qt.project_window import STATE_LABELS
    box = window.add_box('geometry')
    window.register_file(box.node_id, _file(tmp_path, 'source.FCStd'))
    window.register_file(box.node_id, _file(tmp_path, 'output.inp'), 'output')
    node = window.project.nodes[box.node_id]
    node.state, node.reason = state, '[Errno 13] Permission denied: private source'
    node.files['input'].stale = True
    window._reflect_node(node.id)
    window._selection_changed()
    assert box.state == state
    assert (box.status_label or STATE_LABELS[box.state]) == {'running': '실행 중', 'failed': '실패'}[state]
    assert window.input_summary.text() == '입력 파일 갱신 필요'
    # A ready output likewise must not clear another important node state.
    node.files['input'].stale = False
    window._reflect_node(node.id)
    assert box.state == state and not box.status_label



def _natural_scene_for_dock_boundary(window, app, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    monkeypatch.setattr(window.workbench, 'programs', lambda: [])
    for kind, pos in zip(('geometry', 'analysis', 'results', 'expert'),
                         ((40, 40), (300, 40), (560, 40), (300, 240))):
        window.add_box(kind).setPos(*pos)
    window.resize(960, 680)
    window.show()
    app.processEvents()
    window.zoom_in_button.click()
    app.processEvents()
    bar = window.view.horizontalScrollBar()
    bar.setFocus()
    QTest.keyClick(bar, Qt.Key_End)
    assert bar.value() == bar.maximum() and bar.maximum() > bar.minimum()
    assert window.scene.sceneRect().width() == 730
    assert window.view.transform().m11() == pytest.approx(1.2)
    return bar


@pytest.mark.parametrize('closed,restored', [
    (('toolbox_dock',), ('toolbox_dock',)),
    (('settings_dock',), ('settings_dock',)),
    (('toolbox_dock', 'settings_dock'), ('settings_dock', 'toolbox_dock')),
    (('settings_dock', 'toolbox_dock'), ('toolbox_dock', 'settings_dock')),
])
@pytest.mark.parametrize('position', ['end', 'middle'])
def test_natural_scene_dock_clamp_restores_user_scroll(window, app, monkeypatch, closed, restored, position):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    bar = _natural_scene_for_dock_boundary(window, app, monkeypatch)
    if position == 'middle':
        bar.setValue((bar.minimum() + bar.maximum()) // 2)
    wanted = bar.value()
    before = window.view.mapToScene(window.view.viewport().rect().center())
    scene = window.scene.sceneRect()
    for name in closed:
        getattr(window, name).close()
        app.processEvents()
    for name in restored:
        getattr(window, name).toggleViewAction().trigger()
        app.processEvents()
    assert bar.value() == wanted
    assert window.view.mapToScene(window.view.viewport().rect().center()) == before
    assert window.view.transform().m11() == pytest.approx(1.2)
    assert window.zoom_percent.text() == '120%'
    assert window.scene.sceneRect() == scene


@pytest.mark.parametrize('action', ['pan', 'zoom', 'fit', '100%'])
def test_explicit_view_action_while_docks_closed_replaces_previous_pan(window, app, monkeypatch, action):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    bar = _natural_scene_for_dock_boundary(window, app, monkeypatch)
    old = bar.value()
    window.toolbox_dock.close()
    app.processEvents()
    if action == 'pan':
        bar.setFocus()
        QTest.keyClick(bar, Qt.Key_Home)
        wanted = bar.value()
        assert wanted != old
    else:
        window.settings_dock.close()
        app.processEvents()
        assert bar.minimum() == bar.maximum()
        {'zoom': window.zoom_in_button, 'fit': window.fit_button, '100%': window.zoom_percent}[action].click()
        app.processEvents()
        wanted = bar.value()
    scale = window.view.transform().m11()
    for dock in (window.settings_dock, window.toolbox_dock):
        if not dock.isVisible():
            dock.toggleViewAction().trigger()
            app.processEvents()
    assert bar.value() == max(bar.minimum(), min(bar.maximum(), wanted))
    assert bar.value() != old
    assert window.view.transform().m11() == scale



def test_real_window_resize_adopts_native_noanchor_clamp_instead_of_old_dock_memory(window, app, monkeypatch):
    bar = _natural_scene_for_dock_boundary(window, app, monkeypatch)
    old = bar.value()
    window.resize(1280, 820)
    app.processEvents()
    clamped = bar.value()
    assert clamped < old
    window.resize(960, 680)
    app.processEvents()
    assert bar.value() == clamped
    assert window.view.transform().m11() == pytest.approx(1.2)
    window.toolbox_dock.close()
    app.processEvents()
    window.toolbox_dock.toggleViewAction().trigger()
    app.processEvents()
    assert bar.value() == clamped
