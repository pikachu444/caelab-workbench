"""Recorded-run selection and explicit execution in Qt (mocked job service)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
pytest.importorskip("PySide6")
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from apps.workbench_qt.native_analysis import NativeAnalysisWindow
from caelab.workbench import Workbench


@pytest.fixture(autouse=True)
def isolated_theme(tmp_path, monkeypatch):
    from apps.workbench_qt.theme import set_theme_mode
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', str(tmp_path / 'ui.ini'))
    set_theme_mode('dark')
    try:
        yield
    finally:
        set_theme_mode('dark')


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def analysis(app, tmp_path, monkeypatch):
    wb = Workbench()
    project = wb.new_native_project("선택 실행 검사")
    node = wb.add_program_node(project, "analysis", "calculix")
    deck = tmp_path / "입력 A.inp"
    deck.write_text("*HEADING\nA\n", encoding="utf-8")
    wb.set_node_file(project, node.id, deck, units={"length": "mm"})
    wb.save_native_project(project, tmp_path / "연구.json")
    state = SimpleNamespace(runs=[], selected=None, submitted=[], cancelled=[], close=True)
    monkeypatch.setattr(wb, "native_runs", lambda *args: list(state.runs), raising=False)
    monkeypatch.setattr(wb, "selected_native_run", lambda *args: state.selected, raising=False)
    def select(project, node_id, run_id):
        state.selected = next(run for run in state.runs if run.id == run_id)
    monkeypatch.setattr(wb, "select_native_run", select, raising=False)
    def submit(project, node_id, timeout):
        state.submitted.append((wb.input_file(project, node_id).path, timeout))
        run = make_run(tmp_path, "new", "running", "pending", {})
        state.runs.append(run)
        state.selected = run
        return run
    monkeypatch.setattr(wb, "submit_native", submit, raising=False)
    monkeypatch.setattr(wb, "cancel_native_run", lambda run_id: state.cancelled.append(run_id), raising=False)
    monkeypatch.setattr(wb, "close", lambda: state.close, raising=False)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    window = NativeAnalysisWindow(project.path, node.id, wb)
    yield window, state
    state.close = True
    window.close()


def make_run(root, identifier, state, parse_state, result):
    directory = root / identifier
    directory.mkdir(exist_ok=True)
    log = directory / "solver.log"
    log.write_text(f"log for {identifier}", encoding="utf-8")
    return SimpleNamespace(id=identifier, state=state, parse_state=parse_state,
                           result=result, node_id="", created_at="2026-10-24 09:00",
                           input_path=str(root / f"{identifier}.inp"), input_revision="a" * 64,
                           units={"length": "mm"}, workdir=directory, log_path=str(log),
                           error="", stale=False, stale_reason="")


def result(value):
    return {"summary": {"frame_index": 0}, "warnings": [], "frames": [
        {"index": 0, "step": 1, "increment": 1, "value": 1.0, "analysis_type": 0,
         "fields": [{"name": "DISP", "components": [{"name": "D1"}, {"name": "D2"}, {"name": "D3"}],
                     "coverage": {"record_count": 4, "mesh_node_count": 4}}],
         "summary": {"max_displacement": value}, "vtu_file": "results/frame-000001.vtu"}]}


def test_viewing_old_run_preserves_selection_while_new_run_is_active(analysis, tmp_path):
    window, state = analysis
    assert state.submitted == []
    old = make_run(tmp_path, "old", "completed", "readable", result(123.5))
    state.runs = [old]
    state.selected = old
    window.refresh_runs()
    assert "123.5 mm" in window.result_text.toPlainText()
    window.start_run()
    assert len(state.submitted) == 1
    assert state.selected.id == "new"
    assert "123.5" not in window.result_text.toPlainText()
    assert "new.inp" in window.run_source.text()
    window.run_selector.setCurrentIndex(window.run_selector.findData("old"))
    window.refresh_runs()
    assert state.selected is old
    assert window.log.toPlainText() == "log for old"
    assert "123.5 mm" in window.result_text.toPlainText()
    # Cancel the owned running job even when a past run is selected for viewing.
    window.cancel_run()
    assert state.cancelled == ["new"]
    assert state.submitted[0][0].endswith("입력 A.inp")


@pytest.mark.parametrize("terminal,parsed", [("failed", "failed"), ("cancelled", "missing"),
                                             ("completed", "unsupported"), ("interrupted", "pending")])
def test_new_failed_or_unreadable_run_never_shows_old_success(analysis, tmp_path, terminal, parsed):
    window, state = analysis
    previous = make_run(tmp_path, "success", "completed", "readable", result(876.5))
    current = make_run(tmp_path, "current", terminal, parsed, {})
    state.runs, state.selected = [previous, current], previous
    window.refresh_runs()
    assert "876.5" in window.result_text.toPlainText()
    window.run_selector.setCurrentIndex(window.run_selector.findData("current"))
    assert "876.5" not in window.result_text.toPlainText()
    assert window.log.toPlainText() == "log for current"
    assert "판독한 결과 프레임이 없습니다" in window.result_text.toPlainText()
    assert "current.inp" in window.run_source.text()


def test_changed_project_requires_explicit_reload_before_running_other_input(analysis, tmp_path):
    window, state = analysis
    other = tmp_path / "입력 B.inp"
    other.write_text("*HEADING\nB\n", encoding="utf-8")
    disk_project = window.workbench.open_native_project(window.project.path)
    window.workbench.set_node_file(disk_project, window.node_id, other)
    window.workbench.save_native_project(disk_project)
    window.refresh_runs()
    assert not window.run_action.isEnabled()
    assert "프로젝트가 변경" in window.status.text()
    window.start_run()
    assert state.submitted == []
    assert "입력 A.inp" in window.input_heading.text()
    window.reload_project()
    assert "입력 B.inp" in window.input_heading.text()
    window.start_run()
    assert state.submitted[0][0] == str(other)


def test_same_time_frames_have_distinct_selection_and_values(analysis, tmp_path):
    window, state = analysis
    parsed = result(1.25)
    parsed["frames"].append({**parsed["frames"][0], "index": 1, "step": 2,
                             "summary": {"max_displacement": 2.5}})
    parsed["summary"]["frame_index"] = 1
    parsed["warnings"] = ["PVD 축은 프레임 번호입니다."]
    run = make_run(tmp_path, "frames", "completed", "readable", parsed)
    state.runs, state.selected = [run], run
    window.refresh_runs()
    assert "2.5 mm" in window.result_text.toPlainText()
    window.frame_selector.setCurrentIndex(0)
    window.refresh_runs()
    assert window.frame_selector.currentData() == 0
    assert "1.25 mm" in window.result_text.toPlainText()
    assert "프레임 번호" in window.result_text.toPlainText()


def test_close_waits_for_owned_process_termination(analysis):
    window, state = analysis
    state.close = False
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert window.timer.isActive()
    assert "종료하고 있습니다" in window.run_status.text()
    state.close = True
    window.closeEvent(event)
    assert event.isAccepted()
    assert not window.timer.isActive()


def test_polling_keeps_log_scroll_and_allows_cancel_while_reading(analysis, app, tmp_path):
    window, state = analysis
    run = make_run(tmp_path, "reading", "completed", "reading", {})
    Path(run.log_path).write_text("\n".join(f"line {i}" for i in range(200)), encoding="utf-8")
    state.runs, state.selected = [run], run
    window._owned_run_ids.add(run.id)
    window.show()
    window.refresh_runs()
    app.processEvents()
    scroll = window.log.verticalScrollBar()
    scroll.setValue(5)
    with Path(run.log_path).open("a", encoding="utf-8") as stream:
        stream.write("\nappended line")
    window.refresh_runs()
    assert scroll.value() == 5
    assert not window.run_action.isEnabled()
    assert window.cancel_action.isEnabled()
    assert "결과 판독 중" in window.active_status.text()
    window.cancel_run()
    assert state.cancelled == [run.id]


def test_dat_only_sets_are_visible_without_inventing_frd_or_reaction(analysis, tmp_path):
    window, state = analysis
    parsed = {"frames": [], "summary": {}, "warnings": [], "dat": {
        "status": "readable", "frame": {"step": None, "increment": None, "value": 1.0},
        "max_displacement": .25, "sets": {"SUPPORT": {"total_force": [1., 2., 3.]}}, "warnings": []}}
    run = make_run(tmp_path, "dat-only", "completed", "readable", parsed)
    run.units["force"] = "N"
    state.runs, state.selected = [run], run
    window.refresh_runs()
    text = window.result_text.toPlainText()
    assert "DAT 판독 절점 중 최대 변위: 0.25 mm" in text
    assert "집합 SUPPORT" in text and "절점력 합계 원값: (1, 2, 3) N" in text
    assert "반력" not in text
    assert window.frame_selector.count() == 0


def test_parent_updates_connected_result_when_other_window_selects_failed_run(app, tmp_path):
    from apps.workbench_qt.project_window import ProjectWindow
    from caelab.native.run_store import NativeRun, RunStore
    from caelab.project import file_revision

    wb = Workbench()
    project = wb.new_native_project()
    node = wb.add_program_node(project, "analysis", "calculix")
    output_node = wb.add_program_node(project, "results", "paraview")
    deck = tmp_path / "source.inp"
    deck.write_text("*HEADING\nsource\n")
    wb.set_node_file(project, node.id, deck)
    wb.save_native_project(project, tmp_path / "project.json")
    store = RunStore(project.run_store)
    success = NativeRun("a" * 32, node.id, store.root / ("a" * 32),
                        state="completed", parse_state="readable", input_path=str(deck),
                        input_revision=file_revision(deck), unit_source="user")
    success.workdir.mkdir(parents=True)
    raw = success.workdir / "model.frd"
    raw.write_text("saved result")
    success.snapshot["outputs"] = {"model.frd": file_revision(raw)}
    store.write(success)
    wb.select_native_run(project, node.id, success.id)
    wb.connect_nodes(project, node.id, output_node.id)
    wb.save_native_project(project)
    window = ProjectWindow(workbench=wb, project_path=project.path)
    try:
        result_box = window.boxes[output_node.id]
        assert result_box.state == "ready"
        assert result_box.file_label == "model.frd"
        failure = NativeRun("b" * 32, node.id, store.root / ("b" * 32), state="failed",
                            input_path=str(deck), error="solver failed", unit_source="user")
        store.write(failure)
        store.select(node.id, failure.id)  # independent analysis window updates disk only
        window._poll_runs()
        assert window.boxes[node.id].state == "failed"
        assert result_box.state == "stale"
        assert result_box.file_label == "선택 파일 없음"
        assert "model.frd" not in result_box.title + result_box.toolTip()
        assert "입력 파일 없음" in result_box.toolTip()
        assert "output" not in window.project.nodes[node.id].files
    finally:
        window._dirty = False
        window.close()


@pytest.mark.parametrize("kind,program_id", [("results", "paraview"), ("expert", "expert")])
def test_parent_distinguishes_changed_run_selection_from_stale_input(app, tmp_path, kind, program_id):
    from apps.workbench_qt.project_window import ProjectWindow
    from caelab.native.run_store import NativeRun, RunStore
    from caelab.project import file_revision

    wb = Workbench()
    project = wb.new_native_project()
    analysis_node = wb.add_program_node(project, "analysis", "calculix")
    consumer = wb.add_program_node(project, kind, program_id)
    deck = tmp_path / "source.inp"
    deck.write_text("*HEADING\nsource\n")
    wb.set_node_file(project, analysis_node.id, deck)
    wb.connect_nodes(project, analysis_node.id, consumer.id)
    wb.save_native_project(project, tmp_path / "project.json")
    store = RunStore(project.run_store)
    window = ProjectWindow(workbench=wb, project_path=project.path)
    try:
        run = NativeRun("a" * 32, analysis_node.id, store.root / ("a" * 32),
                        state="completed", parse_state="readable", input_path=str(deck),
                        input_revision=file_revision(deck), unit_source="user")
        run.workdir.mkdir(parents=True)
        raw = run.workdir / "model.frd"
        raw.write_text("first selected output")
        run.snapshot["outputs"] = {"model.frd": file_revision(raw)}
        store.write(run)
        store.select(analysis_node.id, run.id)
        window._poll_runs()
        box = window.boxes[consumer.id]
        box.setSelected(True)
        window._selection_changed()
        assert box.status_label == "선택 자료 변경"
        text = (window.input_summary.text() + window.input_value.text()) + window.program_summary.text()
        assert window.input_summary.text() == "입력 파일"
        assert "준비됨" not in text and "연결 입력" not in text
        assert "기존 표시·대화" not in text
        assert "오래됨" not in (window.input_summary.text() + window.input_value.text())
        # Selection never certifies an external display/discussion as completed.
        assert window.project.nodes[consumer.id].state == "stale"
        assert not wb.jobs.launches

        reference = wb.input_file(window.project, consumer.id)
        reference.stale = True
        window._reflect_node(consumer.id)
        window._selection_changed()
        assert box.status_label == "이전 입력의 결과"
        assert "이전 입력의 결과" in window.input_summary.text()
        reference.stale = False
        raw.write_text("externally changed output")
        window._reflect_node(consumer.id)
        window._selection_changed()
        assert box.state == "changed" and box.status_label == "입력 파일 변경됨"
        assert window.input_summary.text() == "입력 파일 변경됨"
        raw.unlink()
        window._reflect_node(consumer.id)
        window._selection_changed()
        assert box.state == "missing" and box.status_label == "입력 파일 누락"
        assert window.input_summary.text() == "입력 파일 누락"
    finally:
        window._dirty = False
        window.close()


@pytest.mark.parametrize("state,process_label", [
    ("completed", "계산 종료 · 결과 읽기 실패"), ("failed", "계산 실패"), ("cancelled", "취소됨"),
])
def test_parent_keeps_input_process_and_readability_separate(app, tmp_path, monkeypatch, state, process_label):
    from apps.workbench_qt.project_window import ProjectWindow
    from caelab.native.run_store import NativeRun, RunStore
    from caelab.project import file_revision

    wb = Workbench()
    project = wb.new_native_project()
    node = wb.add_program_node(project, "analysis", "calculix")
    deck = tmp_path / "current.inp"
    deck.write_text("*HEADING\nnative input\n")
    wb.set_node_file(project, node.id, deck, units={"length": "mm"})
    wb.save_native_project(project, tmp_path / "project.json")
    store = RunStore(project.run_store)
    run = NativeRun("c" * 32, node.id, store.root / ("c" * 32), state=state,
                    parse_state="failed", input_path=str(deck), input_revision=file_revision(deck),
                    error="request_id=private PID 12345 SHA256=internal", units={"length": "mm"},
                    unit_source="user")
    store.write(run)
    store.select(node.id, run.id)
    receipt = (run.workdir / "job.json").read_bytes()
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("unexpected process"))
    window = ProjectWindow(workbench=wb, project_path=project.path)
    try:
        box = window.boxes[node.id]
        box.setSelected(True)
        window._poll_runs()
        window._selection_changed()
        assert window.input_summary.text() == "입력 파일"
        assert window.input_units_button.isEnabled()
        assert process_label in window.output_summary.text()
        assert not window.run_summary.text()
        other = window.add_box('geometry')
        other.setSelected(True)
        window._poll_runs()
        assert not box.isSelected()
        if state == 'completed':
            assert box.state == 'completed'
            assert box.status_label == '계산 종료 · 결과 읽기 실패'
        else:
            assert box.state == state
        text = (window.program_summary.text() + window.run_summary.text() + box.toolTip()
                + box.detail + window.log.toPlainText() + window.status.toolTip())
        assert not any(token in text for token in ("request_id", "PID", "SHA256", run.id))
        assert (run.workdir / "job.json").read_bytes() == receipt
        assert not wb.jobs.launches
    finally:
        window._dirty = False
        window.close()


@pytest.mark.parametrize('parse_state,label', [
    ('reading', '결과 읽는 중'), ('partial', '일부 결과'),
    ('missing', '계산 종료 · 결과 없음'),
    ('unsupported', '계산 종료 · 읽을 수 없는 결과'),
])
def test_unselected_analysis_card_distinguishes_completed_result_states(app, tmp_path, monkeypatch, parse_state, label):
    from apps.workbench_qt.project_window import ProjectWindow
    wb = Workbench()
    project = wb.new_native_project()
    node = wb.add_program_node(project, 'analysis', 'calculix')
    wb.save_native_project(project, tmp_path / 'project.json')
    run = make_run(tmp_path, 'saved', 'completed', parse_state, {})
    run.node_id = node.id
    monkeypatch.setattr(wb, 'selected_native_run', lambda *args: run)
    window = ProjectWindow(wb)
    try:
        window.project = project
        window._show_project()
        other = window.add_box('geometry')
        assert other.isSelected()
        window._poll_runs()
        box = window.boxes[node.id]
        assert box.state == 'completed' and box.status_label == label
        assert not box.isSelected() and run.state == 'completed'
    finally:
        window._dirty = False
        window.close()


@pytest.mark.parametrize("current_state", ["completed", "failed", "cancelled"])
def test_input_status_follows_polled_and_selected_run_without_reading_deck(
        app, tmp_path, monkeypatch, current_state):
    from caelab.native.run_store import NativeRun, RunStore
    from caelab.project import file_revision

    wb = Workbench()
    project = wb.new_native_project("입력 B와 저장 실행 A/B")
    node = wb.add_program_node(project, "analysis", "calculix")
    deck = tmp_path / "B.inp"
    deck.write_text("*HEADING\ncurrent B\n", encoding="utf-8")
    wb.set_node_file(project, node.id, deck, units={"length": "mm"})
    wb.save_native_project(project, tmp_path / "project.json")
    store = RunStore(project.run_store)
    old = NativeRun("a" * 32, node.id, store.root / ("a" * 32),
                    state="completed", parse_state="readable",
                    input_path=str(tmp_path / "A.inp"), units={"length": "mm"},
                    unit_source="user")
    current = NativeRun("b" * 32, node.id, store.root / ("b" * 32),
                        state=current_state, parse_state="readable" if current_state == "completed" else "failed",
                        input_path=str(deck), input_revision=file_revision(deck),
                        units={"length": "mm"}, unit_source="user",
                        error="" if current_state == "completed" else "current run stopped")
    store.write(old)
    store.write(current)
    store.select(node.id, old.id)
    window = NativeAnalysisWindow(project.path, node.id, wb)
    try:
        assert "입력 상태: 오래됨" in window.status.text()
        preview = window.source.toPlainText()
        original_open = Path.open
        def forbid_deck_read(path, *args, **kwargs):
            if path == deck:
                pytest.fail("Run polling/selection must not reread the input preview")
            return original_open(path, *args, **kwargs)
        monkeypatch.setattr(Path, "open", forbid_deck_read)

        # A completed/failed receipt can replace the previously viewed A on disk.
        store.select(node.id, current.id)
        window.refresh_runs()
        expected = "입력 준비" if current_state == "completed" else "실패"
        assert window.project.nodes[node.id].state == ("ready" if current_state == "completed" else "failed")
        assert f"입력 상태: {expected}" in window.status.text()
        if current_state != "completed":
            assert "current run stopped" in window.status.text()

        window.run_selector.setCurrentIndex(window.run_selector.findData(old.id))
        assert "입력 상태: 오래됨" in window.status.text()
        assert "현재 선택 입력" in window.status.text()
        window.run_selector.setCurrentIndex(window.run_selector.findData(current.id))
        assert f"입력 상태: {expected}" in window.status.text()
        assert window.source.toPlainText() == preview
        assert not window._owned_run_ids
    finally:
        window.close()


def test_deck_outline_filters_and_jumps_without_polling_rebuild(analysis, monkeypatch):
    from PySide6.QtCore import Qt
    window, state = analysis
    deck = Path(window.workbench.input_file(window.project, window.node_id).path)
    deck.write_text('** comment\n*HEADING\ninspection\n*MATERIAL, NAME=STEEL\n*ELASTIC\n210000, .3\n*STEP\n*STATIC\n*BOUNDARY\n1, 1, 3\n*END STEP\n', encoding='utf-8')
    window.refresh_input()
    outline = window.deck_outline
    titles = [outline.topLevelItem(i).text(0) for i in range(outline.topLevelItemCount())]
    assert titles == ['*HEADING', '*MATERIAL (STEEL)', '*ELASTIC', '*STEP']
    step = outline.topLevelItem(3)
    assert step.childCount() == 3 and step.child(2).text(0) == '*END STEP'
    window._jump_to_deck_line(step.child(1))
    assert window.source.textCursor().blockNumber() == 8
    window.section_jump.setCurrentIndex(window.section_jump.findData('material'))
    assert outline.topLevelItem(0).isHidden() and not outline.topLevelItem(1).isHidden()
    window.section_jump.setCurrentIndex(window.section_jump.findData('bc_load'))
    assert not step.isHidden() and step.child(0).isHidden() and not step.child(1).isHidden()
    preview = window.source.toPlainText()
    monkeypatch.setattr(window, '_rebuild_deck_outline', lambda: pytest.fail('polling rebuilt outline'))
    window.refresh_runs()
    assert window.source.toPlainText() == preview and step.data(0, Qt.UserRole) == 7


def test_result_tables_hide_run_store_and_clear_all_values_on_empty_or_error(analysis, app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QTabWidget
    window, state = analysis
    data = result(2.5)
    data['frames'][0]['fields'][0]['summary'] = {'components': [{'name': 'D1', 'minimum': -1.25, 'maximum': 2.5}]}
    data['dat'] = {'status': 'readable', 'frame': {'step': 2, 'increment': 3, 'value': 4},
                   'sets': {'SUPPORT': {'nodal_forces': {1: [1, 2, 3]}, 'total_force': [1., 2., 3.]}}, 'warnings': []}
    run = make_run(tmp_path, 'a' * 32, 'completed', 'readable', data)
    run.workdir = window.project.run_store / ('a' * 32)
    run.input_path = str(run.workdir / 'model.inp')
    run.units['force'] = 'N'
    state.runs, state.selected = [run], run
    window.refresh_runs()
    assert window.field_table.rowCount() == 1 and window.dat_table.rowCount() == 2
    assert window.kpi_disp_value.text() == '2.5 mm'
    assert window.field_table.item(0, 2).text() == '-1.25'
    assert window.dat_table.item(0, 1).text() == '절점력 합계 원값'
    assert '단계 2 / 증분 3 / 값 4' in window.dat_frame_label.text()
    assert '대응 여부 미확인' in window.dat_frame_label.text()
    text = window.result_text.toPlainText() + window.run_source.text() + window.run_source.toolTip() + window.log.toolTip()
    assert all(token not in text for token in (str(run.workdir), '.runs', run.id, '반력'))
    assert window.frame_selector.itemText(0).endswith('[최종]')
    for tabs in window.findChildren(QTabWidget):
        for index in range(tabs.count()):
            if tabs.tabText(index) == '결과':
                tabs.setCurrentIndex(index)
    window.show()
    for width, height in ((1050, 760), (800, 650)):
        window.resize(width, height)
        app.processEvents()
        assert window.dat_table.rowViewportPosition(0) + window.dat_table.rowHeight(0) <= window.dat_table.viewport().height()
    for failure in (False, True):
        if failure:
            state.selected = run
            window.refresh_runs()
            monkeypatch.setattr(window.workbench, 'native_runs', lambda *args: (_ for _ in ()).throw(OSError('private path')))
        else:
            state.selected = None
        window.refresh_runs()
        assert window.field_table.rowCount() == window.dat_table.rowCount() == 0
        assert window.dat_container.isHidden() and window.result_text.toPlainText() == ''
        assert window.kpi_disp_value.text() == '미확인' and window.result_unit_badge.text() == '자료 없음'


def test_native_theme_toggle_and_log_summary_preserve_original_log(analysis, tmp_path):
    from apps.workbench_qt.theme import current_theme_mode
    window, state = analysis
    run = make_run(tmp_path, 'log', 'failed', 'failed', {})
    raw = '*ERROR problem\n*WARNING cutback\nSTEP 1 INCREMENT 2\n'
    Path(run.log_path).write_text(raw, encoding='utf-8')
    state.runs, state.selected = [run], run
    window.refresh_runs()
    before = window.styleSheet()
    window.theme_action.trigger()
    assert current_theme_mode() == 'light' and window.styleSheet() != before
    assert window.theme_action.text() == '다크 모드'
    assert window.log.toPlainText() == raw and '오류 1 / 경고 1' in window.log_summary_badge.text()


def test_native_diagnostic_payload_stays_out_of_status_and_error_dialogs(analysis, tmp_path, monkeypatch):
    window, state = analysis
    payload = '[Errno 2] C:/private/project.runs/' + 'a' * 32 + '/model.inp PID 123 SHA256=' + 'b' * 64
    run = make_run(tmp_path, 'bad', 'failed', 'failed', {})
    run.error, run.stale, run.stale_reason = payload, True, payload
    state.runs, state.selected = [run], run
    window.refresh_runs()
    assert '확인하세요' in window.run_status.text()
    seen = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda parent, title, text: seen.append(text))
    monkeypatch.setattr(window.workbench, 'submit_native', lambda *args, **kwargs: (_ for _ in ()).throw(OSError(payload)))
    window.start_run()
    assert len(seen) == 1 and '입력 파일' in seen[0]
    for text in [window.run_status.text(), *seen]:
        assert not any(token in text for token in ('[Errno', '.runs', 'a' * 32, 'b' * 64, 'PID', 'SHA256'))

    win_err = "[WinError 5] 액세스가 거부되었습니다: 'C:\\\\private\\\\secret.inp'"
    env_err = "CalculiX 실행 파일을 찾지 못했습니다 (CAELAB_CCX=C:\\\\secret\\\\ccx.exe)."
    for bad_msg in (win_err, env_err):
        run.error = bad_msg
        window.refresh_runs()
        assert "[WinError" not in window.run_status.text()
        assert "CAELAB_CCX" not in window.run_status.text()
        assert "secret" not in window.run_status.text()
    run.error = env_err
    window.refresh_runs()
    assert "CalculiX 실행 파일 또는 연결 설정을 확인하세요." in window.run_status.text()
