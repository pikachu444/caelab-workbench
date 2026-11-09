"""Selected native run/frame drafts in the independent expert window (no LLM)."""
import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDockWidget

from apps.workbench_qt.expert_window import ExpertWindow
from apps.workbench_qt.native_analysis import NativeAnalysisWindow
from caelab.native.run_store import NativeRun
from caelab.workbench import Workbench


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def analysis(app, tmp_path, monkeypatch):
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui.ini"))
    monkeypatch.setenv("CAELAB_EXPERT_SETTINGS_PATH", str(tmp_path / "expert.ini"))
    wb = Workbench()
    project = wb.new_native_project("전문가 연결 검사")
    node = wb.add_program_node(project, "analysis", "calculix")
    deck = tmp_path / "입력.inp"
    deck.write_text("*HEADING\nBridge\n", encoding="utf-8")
    wb.set_node_file(project, node.id, deck, units={"length": "mm", "force": "N"})
    wb.save_native_project(project, tmp_path / "프로젝트.json")
    state = SimpleNamespace(runs=[], selected=None, closed=[])
    monkeypatch.setattr(wb, "native_runs", lambda *args: state.runs)
    monkeypatch.setattr(wb, "selected_native_run", lambda *args: state.selected)
    def select(_project, _node, run_id):
        state.selected = next(run for run in state.runs if run.id == run_id)
    monkeypatch.setattr(wb, "select_native_run", select)
    def forbidden(*args, **kwargs):
        pytest.fail("Opening a draft must not submit a calculation, attach evidence or call a model")
    for method in ("submit_native", "capture_expert_run", "ask_expert_discussion"):
        monkeypatch.setattr(wb, method, forbidden)
    monkeypatch.setattr(wb, "close", lambda: state.closed.append(True) or True)
    window = NativeAnalysisWindow(project.path, node.id, wb)
    window.timer.stop()
    yield window, state
    window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    wb.jobs.close()


def display_run(window, state, tmp_path, name="run-a", units=None, run_state="completed", **kwargs):
    workdir = tmp_path / name
    workdir.mkdir(exist_ok=True)
    log = workdir / "solver.log"
    log.write_text("begin\nWARNING: contact cutback\nERROR: not convergent\nend\n", encoding="utf-8")
    run = NativeRun(
        id=name, node_id=window.node_id, workdir=workdir, log_path=str(log),
        state=run_state, parse_state=kwargs.pop("parse_state", "readable"),
        returncode=kwargs.pop("returncode", 0),
        created_at="2026-11-09T01:00:00+00:00", finished_at="2026-11-09T01:00:02.500000+00:00",
        input_path=str(tmp_path / f"{name}.inp"),
        units={"length": "mm", "force": "N"} if units is None else units,
        result={"summary": {"frame_index": 1}, "frames": [
            {"index": i, "step": 1, "increment": i + 1, "value": i + 1,
             "summary": {"max_displacement": value}, "fields": []}
            for i, value in enumerate((0.125, 9.75))]}, **kwargs)
    state.runs.append(run)
    state.selected = run
    window.refresh_runs()
    return run


@pytest.mark.parametrize("entry", ["menu", "toolbar", "button", "shortcut"])
def test_all_entries_open_independent_window_with_selected_run(analysis, tmp_path, app, entry):
    window, state = analysis
    run = display_run(window, state, tmp_path)
    assert window._expert_window is None
    assert window.expert_action.shortcut().toString() == "Ctrl+E"
    window.show()
    if entry == "menu":
        window.expert_action.trigger()
    elif entry == "toolbar":
        window.expert_toolbar_action.trigger()
    elif entry == "button":
        window.btn_expert_debate.click()
    else:
        window.activateWindow()
        app.processEvents()
        QTest.keyClick(window, Qt.Key_E, Qt.ControlModifier)
    expert = window._expert_window
    assert isinstance(expert, ExpertWindow)
    assert expert.isVisible() and expert.isWindow() and expert.parent() is None
    assert not window.findChildren(QDockWidget)
    assert expert.workbench is window.workbench
    assert (expert.project_path, expert.node_id, expert.run_id) == (window.project.path, window.node_id, run.id)
    question = expert.question.toPlainText()
    for expected in ("전문가 연결 검사", "calculix", "run-a.inp", "프로세스 정상 종료", "판독 완료",
                     "2.50초", "mm · N (MPa)", "9.75 mm", "증분 2", "WARNING: contact cutback", "ERROR: not convergent"):
        assert expected in question
    assert "0.125 mm" not in question
    assert expert.document == {} and expert._operation is None
    assert state.closed == []


def test_reuse_preserves_draft_and_separates_run_and_frame_contexts(analysis, tmp_path):
    window, state = analysis
    first = display_run(window, state, tmp_path)
    window.request_expert_debate()
    old = window._expert_window
    old.question.setPlainText("사용자가 작성한 질문")
    window.request_expert_debate()
    assert window._expert_window is old
    assert old.question.toPlainText() == "사용자가 작성한 질문"
    window.frame_selector.setCurrentIndex(0)
    window.request_expert_debate()
    frame_window = window._expert_window
    assert frame_window is not old
    assert "0.125 mm" in frame_window.question.toPlainText()
    assert "9.75 mm" not in frame_window.question.toPlainText()
    second = display_run(window, state, tmp_path, name="run-b")
    window.request_expert_debate()
    assert window._expert_window is not old
    assert window._expert_window.run_id == second.id
    assert old.run_id == first.id
    assert old.question.toPlainText() == "사용자가 작성한 질문"
    window.run_selector.setCurrentIndex(window.run_selector.findData(first.id))
    window.frame_selector.setCurrentIndex(window.frame_selector.findData(1))
    window.request_expert_debate()
    assert window._expert_window is old
    old.question.clear()
    window.request_expert_debate()
    assert "9.75 mm" in old.question.toPlainText()


@pytest.mark.parametrize("units, expected", [({"length": "m", "force": "N"}, "9.75 m"), ({}, "9.75 (단위 미확인)")])
def test_run_units_are_not_replaced_by_input_units(analysis, tmp_path, units, expected):
    window, state = analysis
    display_run(window, state, tmp_path, units=units)
    window.request_expert_debate()
    question = window._expert_window.question.toPlainText()
    assert expected in question
    assert "9.75 mm" not in question


@pytest.mark.parametrize("status, code", [("failed", 7), ("cancelled", None), ("running", None)])
def test_failed_or_unfinished_run_has_no_invented_success(analysis, tmp_path, status, code):
    window, state = analysis
    run = display_run(window, state, tmp_path, run_state=status, returncode=code, parse_state="missing")
    run.result = {}
    run.finished_at = None
    run.error = "수렴하지 않았습니다"
    run.stale, run.stale_reason = True, "입력 변경"
    window.refresh_runs()
    window.request_expert_debate()
    question = window._expert_window.question.toPlainText()
    assert "프로세스 정상 종료" not in question
    assert "9.75" not in question and "경과 시간" not in question
    assert "오래된 입력의 결과" in question and "수렴하지 않았습니다" in question
    if code is not None:
        assert "프로세스 종료 코드: 7" in question
    else:
        assert "프로세스 종료 코드" not in question


def test_no_run_and_closed_window_reopens_with_active_timer(analysis, app):
    window, state = analysis
    window.request_expert_debate()
    expert = window._expert_window
    assert expert.run_id is None
    assert "아직 선택한 실행이 없습니다" in expert.question.toPlainText()
    assert "mm" not in expert.question.toPlainText()
    assert expert.close()
    assert state.closed == []
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert window._expert_window is None and window._expert_windows == {}
    window.request_expert_debate()
    assert window._expert_window is not expert
    assert window._expert_window.timer.isActive()


def test_log_diagnostics_are_limited_and_plain_log_falls_back(analysis, tmp_path):
    window, state = analysis
    display_run(window, state, tmp_path)
    window.log.setPlainText("\n".join(f"WARNING: item {i}" for i in range(9)))
    window.request_expert_debate()
    question = window._expert_window.question.toPlainText()
    assert question.count("WARNING: item") == 5
    assert "WARNING: item 3" not in question
    assert "WARNING: item 8" in question
    window._expert_window.question.clear()
    window.log.setPlainText("begin\nfinished normally")
    window.request_expert_debate()
    assert "[최근 로그]\n  * begin\n  * finished normally" in window._expert_window.question.toPlainText()


def test_parent_close_respects_expert_veto_then_closes_shared_workbench(analysis, monkeypatch):
    window, state = analysis
    window.request_expert_debate()
    expert = window._expert_window
    with monkeypatch.context() as veto:
        veto.setattr(expert, "close", lambda: False)
        assert not window.close()
        assert state.closed == []
    assert window.close()
    assert not expert.isVisible()
    assert state.closed == [True]


def test_standalone_expert_still_closes_its_owned_workbench(app, tmp_path, monkeypatch):
    monkeypatch.setenv("CAELAB_EXPERT_SETTINGS_PATH", str(tmp_path / "expert.ini"))
    expert = ExpertWindow()
    closed = []
    monkeypatch.setattr(expert.workbench, "close", lambda: closed.append(True))
    assert expert.close()
    assert closed == [True]
    expert.workbench.jobs.close()
