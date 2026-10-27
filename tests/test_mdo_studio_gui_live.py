"""Offscreen GUI integration with an explicitly mocked subprocess, not native FEA."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from apps.workbench_qt.mdo_studio.window import MDOStudioWindow, extract_calculix_responses
from apps.workbench_qt.mdo_studio.study_config import StudyConfigDialog
from caelab.connections import ProgramSpec, ProgramStatus, LaunchRequest
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import ConstraintSpec
from test_native_results import _geometry, _field, DISPLACEMENT

STRESS = [(name, 4, row, col, 0) for name, row, col in (("SXX", 1, 1), ("SYY", 2, 2), ("SZZ", 3, 3), ("SXY", 1, 2), ("SYZ", 2, 3), ("SZX", 3, 1))]


def result_text(disp=.01, stress=25., *, step=1, increment=1, value=1.):
    ids = (40, 10, 90, 25)
    return (_field([(n, (disp, 0., 0.)) for n in ids], step=step, increment=increment, value=value)
            + _field([(n, (stress, 0., 0., 0., 0., 0.)) for n in ids], name="STRESS", components=STRESS,
                     step=step, increment=increment, value=value))


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def messages(monkeypatch, qapp):
    seen = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a: seen.append((title, text)))
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: seen.append((title, text)))
    yield seen
    qapp.processEvents()


@pytest.fixture
def mock_solver_probe(monkeypatch):
    import caelab.connections as connections
    from caelab.workbench import Workbench
    frd = _geometry() + result_text() + " 9999\n"
    dat = " total mass for set EALL and time  0.1000000E+01\n\n        3.140000E-05\n"
    script = [f"from pathlib import Path; Path('model.frd').write_text({frd!r}); Path('model.dat').write_text({dat!r}); print('MOCK subprocess fixture completed')"]
    spec = ProgramSpec("calculix", "Mock CalculiX subprocess", "solver", "CAELAB_CCX", ("ccx",), "test fixture")
    monkeypatch.setattr(Workbench, "programs", lambda self: [ProgramStatus(spec, sys.executable, source="test")])
    monkeypatch.setattr(connections, "calculix_run_request", lambda status, workdir: LaunchRequest([sys.executable, "-c", script[0]], workdir, dict(os.environ), "calculix"))
    return script


@pytest.fixture
def window(qapp, tmp_path):
    win = MDOStudioWindow()
    deck = tmp_path / "plate.inp"
    deck.write_text("*HEADING\nExplicit mock-pipeline input\n*NODE\n1, 0, 0, 0\n*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL\n<T>\n", encoding="utf-8")
    win._on_load_deck(deck)
    space = VariableSpace(variables=(ContinuousVariable(id="t", name="T", lower=1, upper=3, initial=2, unit="mm"),))
    win._configure_study(space, [ObjectiveSpec(id="mass", response_id="mass_eall"), ObjectiveSpec(id="displacement", response_id="disp")],
                         [ConstraintSpec(id="stress_limit", response_id="stress", kind="UPPER", limit=40, unit="MPa")],
                         {"mass_eall": "N*s**2/mm", "disp": "mm", "stress": "MPa"})
    win._on_generate_doe(2, 42)
    yield win
    win.close()
    qapp.processEvents()


def wait_batch(win, qapp):
    deadline = time.monotonic() + 15
    while win._active_batch_id and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.02)
    assert win._active_batch_id is None


def test_mdo_studio_default_empty_and_explicit_pending_spec(qapp):
    win = MDOStudioWindow()
    try:
        assert not win.study.records
        win._on_load_demo()
        assert "Specification (Synthetic Demo)" in win.study.name
        assert len(win.study.records) == 25
        assert all(r.status == "PENDING" and not r.observations and r.duration_sec == 0 for r in win.study.records)
        assert not win.setup_widget.btn_apply_current.isEnabled()
        assert not win.tornado_view.ax.patches
        win._run_all_points()
        assert win._active_batch_id is None
    finally:
        win.close()


def test_mdo_studio_live_batch_dispatch_and_progress(window, qapp, mock_solver_probe):
    window._run_all_points()
    assert window._active_batch_id
    assert not window.act_run.isEnabled()
    assert window.act_stop.isEnabled()
    assert not window.act_generate.isEnabled()
    wait_batch(window, qapp)
    assert window.act_run.isEnabled()
    assert window.progress_bar.isHidden()
    for rec in window.study.records:
        assert rec.status == "COMPLETED"
        assert rec.observations == pytest.approx({"mass_eall": 3.14e-5, "stress": 25., "disp": .01})
        assert rec.objectives["mass"] == pytest.approx(3.14e-5)
        assert rec.duration_sec > 0
        assert window._point_is_verified(rec)
    assert "배치 완료" in window.status_bar.currentMessage()


def test_mdo_studio_stop_scheduling_cancels_cleanly(window, qapp, mock_solver_probe):
    mock_solver_probe[0] = "import time; print('MOCK slow subprocess', flush=True); time.sleep(30)"
    window._run_all_points()
    original_study = window.study
    window._on_new_study()
    assert window.study is original_study
    window._stop_evaluations()
    wait_batch(window, qapp)
    assert all(r.status in ("CANCELLED", "FAILED") for r in window.study.records)
    assert all(r.feasibility == "NOT_ASSESSED" and not r.observations for r in window.study.records)


def test_apply_preserves_backups_and_saved_study_reopens(window, qapp, mock_solver_probe, tmp_path):
    original = window.current_deck_path.read_bytes()
    window._run_all_points(); wait_batch(window, qapp)
    rec = window.study.records[0]
    window._on_apply_as_current(rec.point_id)
    assert window.current_deck_path.with_name("plate.inp.bak").read_bytes() == original
    assert b"<T>" not in window.current_deck_path.read_bytes()
    window._on_apply_as_current(rec.point_id)
    assert window.current_deck_path.with_name("plate.inp.bak").read_bytes() == original
    assert len(list(tmp_path.glob("plate.inp.*.bak"))) == 1
    saved = tmp_path / "study.json"
    window._on_save_study(saved)
    other = MDOStudioWindow()
    try:
        other._on_open_study(saved)
        assert len(other.study.records) == 3
        assert other._active_batch_id is None
        assert other._point_is_verified(other.study.records[0])
        other._on_generate_doe(1, 42)
        other._run_all_points(); wait_batch(other, qapp)
        assert all(r.status == "COMPLETED" for r in other.study.records)
    finally:
        other.close()


def test_stale_source_or_result_blocks_apply(window, qapp, mock_solver_probe, messages):
    window._run_all_points(); wait_batch(window, qapp)
    rec = window.study.records[0]
    run = Path(window._point_sources[rec.point_id]["workdir"])
    (run / "model.frd").write_text("changed output")
    original = window.current_deck_path.read_bytes()
    window._on_apply_as_current(rec.point_id)
    assert window.current_deck_path.read_bytes() == original
    assert not window.current_deck_path.with_name("plate.inp.bak").exists()
    assert messages[-1][0] == "적용할 수 없음"


def test_demo_transition_clears_native_binding(window):
    original = window.current_deck_path
    before = original.read_bytes()
    window._on_load_demo()
    assert window.current_deck_path is None and window.template_deck is None
    window._on_apply_as_current(window.study.records[0].point_id)
    assert original.read_bytes() == before


def test_missing_responses_are_failed_not_feasible(window, qapp, mock_solver_probe):
    mock_solver_probe[0] = "print('MOCK completed without outputs')"
    window._run_all_points(); wait_batch(window, qapp)
    assert all(r.status == "FAILED" and r.feasibility == "NOT_ASSESSED" and not r.is_pareto for r in window.study.records)
    assert not window.setup_widget.btn_apply_current.isEnabled()


def test_objective_response_binding_and_max_direction(window, tmp_path):
    window.study.objectives = [ObjectiveSpec(id="maximize_motion", response_id="disp", sense="MAX")]
    window.study.constraints = []
    window._active_batch_id = "batch"
    try:
        for index, rec in enumerate(window.study.records):
            item = SimpleNamespace(batch_id="batch", scalar_responses={"disp": float(index)}, duration_sec=.1, state="completed", error="")
            run = SimpleNamespace(id=str(index), workdir=tmp_path, result={})
            window._on_item_finished(rec.point_id, item, run)
        assert window.study.records[-1].is_pareto
        assert not window.study.records[0].is_pareto
        assert window.study.records[-1].objectives == {"maximize_motion": -2.0}
    finally:
        window._active_batch_id = None


def test_parser_uses_final_frame_and_retains_zero(tmp_path):
    (tmp_path / "model.frd").write_text(_geometry() + result_text(10., 100., increment=1, value=.5) + result_text(0., 0., increment=2, value=1.) + " 9999\n")
    assert extract_calculix_responses(tmp_path, None) == {"disp": 0., "stress": 0.}
    (tmp_path / "model.frd").write_text(_geometry() + result_text() + _field([(n, (0., 0., 0.)) for n in (40, 10, 90, 25)], increment=2, value=2.) + " 9999\n")
    assert extract_calculix_responses(tmp_path, None) == {"disp": 0.}


def test_configuration_requires_units_and_token_collisions_are_rejected(window, tmp_path, messages):
    dialog = StudyConfigDialog(("T",), window.study, window.response_units, window)
    dialog.variables.item(0, 4).setText("")
    with pytest.raises(ValueError, match="단위"):
        dialog.configuration()
    dialog.close()
    bad = tmp_path / "collision.inp"
    bad.write_text("*NODE\n1,<T>,<t>,0\n")
    old = window.current_deck_path
    window._on_load_deck(bad)
    assert window.current_deck_path == old
    assert "대소문자" in messages[-1][1]


def test_nested_include_snapshot_and_stale_include(window, qapp, mock_solver_probe, tmp_path):
    sub = tmp_path / "nested"; sub.mkdir()
    include = sub / "mesh.inp"; include.write_text("*NODE\n1,0,0,0\n")
    path = tmp_path / "with_include.inp"
    path.write_text("*INCLUDE, INPUT=nested/mesh.inp\n*SOLID SECTION,ELSET=EALL,MATERIAL=M\n<T>\n")
    space, objectives, constraints, units = window.study.space, window.study.objectives, window.study.constraints, window.response_units
    window._on_load_deck(path)
    window._configure_study(space, objectives, constraints, units)
    window._on_generate_doe(1, 42)
    window._run_all_points(); wait_batch(window, qapp)
    rec = window.study.records[0]
    assert window._point_is_verified(rec)
    include.write_text("*NODE\n1,1,0,0\n")
    assert not window._point_is_verified(rec)
    window._run_all_points()
    assert window._active_batch_id is None


def test_apply_atomic_failure_does_not_report_success(window, qapp, mock_solver_probe, monkeypatch, messages):
    import apps.workbench_qt.mdo_studio.window as module
    window._run_all_points(); wait_batch(window, qapp)
    original = window.current_deck_path.read_bytes()
    monkeypatch.setattr(module.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("write denied")))
    window._on_apply_as_current(window.study.records[0].point_id)
    assert window.current_deck_path.read_bytes() == original
    assert messages[-1][0] == "적용 실패"
    assert "Apply failed" in window.status_bar.currentMessage()


def test_reopened_template_tamper_and_stale_outputs_are_not_plotted(window, qapp, mock_solver_probe, tmp_path):
    import json
    window._run_all_points(); wait_batch(window, qapp)
    saved = tmp_path / "saved.json"
    window._on_save_study(saved)
    original_data = json.loads(saved.read_text())
    data = dict(original_data)
    data["template"] = data["template"].replace("*NODE", "*CLOAD\n1,1,9999\n*NODE")
    saved.write_text(json.dumps(data), encoding="utf-8")
    other = MDOStudioWindow()
    try:
        other._on_open_study(saved)
        assert all(r.status == "STALE" for r in other.study.records)
        assert not other.setup_widget.btn_apply_current.isEnabled()
        assert not other.surface_view.ax.collections
        assert not other.tornado_view.ax.patches
        other._run_all_points()
        assert other._active_batch_id is None
        saved.write_text(json.dumps(original_data), encoding="utf-8")
        record = window.study.records[0]
        (Path(window._point_sources[record.point_id]["workdir"]) / "model.frd").write_text("tampered")
        other._on_open_study(saved)
        assert other.study.records[0].status == "STALE"
        assert all(r.status != "COMPLETED" or other._point_is_verified(r) for r in other.study.records)
    finally:
        other.close()
