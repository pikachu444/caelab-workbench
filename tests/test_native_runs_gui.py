"""Recorded-run selection and explicit execution in Qt (mocked job service)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from types import SimpleNamespace

import pytest
pytest.importorskip("PySide6")
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from apps.workbench_qt.native_analysis import NativeAnalysisWindow
from caelab.workbench import Workbench


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
        assert box.status_label == "선택 실행 변경"
        text = window.properties.toPlainText()
        assert "입력 상태: 연결 입력 준비됨" in text
        assert "기존 표시·대화와 다를 수 있습니다" in text
        assert "입력 상태: 오래됨" not in text
        # Selection never certifies an external display/discussion as completed.
        assert window.project.nodes[consumer.id].state == "stale"
        assert not wb.jobs.launches

        reference = wb.input_file(window.project, consumer.id)
        reference.stale = True
        window._reflect_node(consumer.id)
        window._selection_changed()
        assert not box.status_label
        assert "입력 상태: 오래됨" in window.properties.toPlainText()
        reference.stale = False
        raw.write_text("externally changed output")
        window._selection_changed()
        assert "입력 상태: 원본 변경됨" in window.properties.toPlainText()
        raw.unlink()
        window._selection_changed()
        assert "입력 상태: 파일 누락" in window.properties.toPlainText()
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
