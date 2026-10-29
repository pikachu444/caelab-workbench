"""CAD exchange and editable FE projects stay distinct across restart and edits."""
import json
import sys
import time

import pytest

from caelab.connections import PROGRAMS, ProgramStatus, _status, launch_request
from caelab.workbench import Workbench


def chain(tmp_path):
    wb = Workbench()
    project = wb.new_native_project()
    cad = project.add_node("geometry", "freecad")
    pre = project.add_node("preprocessor", "prepomax")
    solver = project.add_node("analysis", "calculix")
    paths = {}
    for suffix in ("FCStd", "step", "pmx", "inp"):
        paths[suffix] = tmp_path / ("사용자 부품." + suffix)
        paths[suffix].write_text("original " + suffix)
    project.set_file(cad.id, paths["FCStd"])
    project.set_file(cad.id, paths["step"], "output")
    project.connect(cad.id, pre.id)
    project.set_file(pre.id, paths["pmx"], "document")
    project.set_file(pre.id, paths["inp"], "output")
    project.connect(pre.id, solver.id)
    return wb, project, cad, pre, solver, paths


def test_exchange_document_and_deck_survive_save_copy_remove_restore(tmp_path):
    wb, project, cad, pre, solver, paths = chain(tmp_path)
    saved = wb.save_native_project(project, tmp_path / "project.json")
    loaded = wb.open_native_project(saved)
    assert loaded.input_file(pre.id).path == str(paths["step"])
    assert loaded.input_file(solver.id).path == str(paths["inp"])
    assert loaded.nodes[pre.id].files["document"].path == str(paths["pmx"])
    wb.remove_program_node(loaded, pre.id)
    copied = wb.save_native_project(loaded, tmp_path / "copy/project.json")
    resumed = wb.open_native_project(copied)
    wb.restore_program_node(resumed, pre.id)
    assert resumed.nodes[pre.id].files["document"].path == str(paths["pmx"])
    assert paths["pmx"].read_text() == "original pmx"
    assert json.loads(copied.read_text())["schema_version"] == 3
    with pytest.raises(ValueError, match="역할"):
        resumed.connect(pre.id, solver.id, "document")
    wb.close()


def test_changed_document_invalidates_deck_and_reregister_preserves_cad_edge(tmp_path):
    wb, project, cad, pre, solver, paths = chain(tmp_path)
    paths["pmx"].write_text("edited contact and multiple steps")
    project.refresh()
    assert project.nodes[pre.id].files["output"].stale
    assert project.nodes[solver.id].state == "stale"
    project.set_file(pre.id, paths["pmx"], "document")
    assert project.input_file(pre.id).path == str(paths["step"])
    assert project.nodes[pre.id].files["output"].stale  # PMX save is not INP export.
    project.set_file(pre.id, paths["inp"], "output")
    assert not project.nodes[pre.id].files["output"].stale
    wb.close()


def test_document_change_is_checked_before_process_start_even_without_refresh(tmp_path, monkeypatch):
    wb, project, cad, pre, solver, paths = chain(tmp_path)
    spec = next(s for s in PROGRAMS if s.id == "calculix")
    monkeypatch.setattr(wb, "programs", lambda: [ProgramStatus(spec, sys.executable)])
    wb.save_native_project(project, tmp_path / "project.json")
    paths["pmx"].write_text("changed while the project was closed")
    submitted = wb.submit_native(project, solver.id)
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        run = next(r for r in wb.native_runs(project) if r.id == submitted.id)
        if run.finished_at:
            break
        time.sleep(.01)
    assert run.state == "failed" and "상위 파일" in run.error
    assert run.pid is None
    wb.close()


def test_standalone_project_and_explicit_geometry_import_use_different_files(tmp_path, monkeypatch):
    wb, project, cad, pre, solver, paths = chain(tmp_path)
    import caelab.connections as connections
    spec = next(s for s in PROGRAMS if s.id == "prepomax")
    executable = tmp_path / "PrePoMax.exe"
    executable.write_bytes(b"GUI probe")
    monkeypatch.setattr(connections, "host_platform", lambda: "windows")
    monkeypatch.setattr(wb, "programs", lambda: [ProgramStatus(spec, str(executable))])
    launched = []
    from types import SimpleNamespace
    monkeypatch.setattr(wb.jobs, "launch", lambda request: (launched.append(request) or SimpleNamespace(state="opened")))
    wb.open_program(project, pre.id)
    wb.open_program(project, pre.id, use_input=True)
    assert launched[0].argv == [str(executable), "-f", str(paths["pmx"])]
    assert launched[1].argv == [str(executable), "-f", str(paths["step"])]
    project.disconnect_input(pre.id)
    project.refresh()
    wb.open_program(project, pre.id)  # no STEP is required to reopen PMX.
    assert launched[-1].argv[-1] == str(paths["pmx"])
    wb.close()


def test_platform_and_program_selection_do_not_claim_unsupported_execution(tmp_path):
    wb, project, cad, pre, solver, paths = chain(tmp_path)
    spec = next(s for s in PROGRAMS if s.id == "prepomax")
    assert not _status(spec, {}, "", "linux").available
    wb.set_node_program(project, pre.id, "prepomax")
    assert not pre.files["output"].stale
    wb.set_node_program(project, pre.id, "salome")
    assert pre.files["document"].stale and pre.files["output"].stale
    with pytest.raises(ValueError, match="역할"):
        wb.set_node_program(project, cad.id, "calculix")
    wb.close()


@pytest.mark.parametrize("version", [1, 2])
def test_existing_projects_still_open(tmp_path, version):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema_version": version, "name": "existing", "nodes": {}, "connections": []}))
    wb = Workbench()
    assert wb.open_native_project(path).name == "existing"
    wb.close()


def test_preprocessor_post_opens_recorded_frd_and_rejects_modified_result(tmp_path, monkeypatch):
    from test_native_evidence import make_run
    import caelab.connections as connections
    from types import SimpleNamespace
    run = make_run(tmp_path)
    wb = Workbench()
    project = wb.new_native_project()
    results = project.add_node("results", "paraview")
    wb.set_node_program(project, results.id, "prepomax")
    executable = tmp_path / "PrePoMax.exe"
    executable.write_bytes(b"launch probe")
    spec = next(s for s in PROGRAMS if s.id == "prepomax")
    monkeypatch.setattr(connections, "host_platform", lambda: "windows")
    monkeypatch.setattr(wb, "programs", lambda: [ProgramStatus(spec, str(executable))])
    monkeypatch.setattr(wb, "resolve_evidence_run", lambda *args: run)
    launches = []
    monkeypatch.setattr(wb.jobs, "launch", lambda request: (launches.append(request) or SimpleNamespace(state="opened")))
    wb.open_native_results(project, results.id)
    assert launches[-1].argv == [str(executable), "-f", str(run.workdir / "model.frd")]
    (run.workdir / "model.frd").write_bytes(b"different run result")
    with pytest.raises(ValueError, match="해시"):
        wb.open_native_results(project, results.id)
    assert len(launches) == 1
    wb.close()


def test_solver_switch_keeps_previous_selected_run_as_old_solver(tmp_path):
    from test_native_evidence import make_run
    run = make_run(tmp_path)
    wb = Workbench()
    project = wb.new_native_project()
    solver = project.add_node("analysis", "calculix")
    project.set_file(solver.id, run.input_path, units=run.units, unit_source=run.unit_source)
    wb.save_native_project(project, tmp_path / "project.json")
    run.node_id = solver.id
    store = wb._run_store(project)
    store.write(run)
    store.select(solver.id, run.id)
    assert not wb.selected_native_run(project, solver.id).stale
    wb.set_node_program(project, solver.id, "codeaster")
    previous = wb.selected_native_run(project, solver.id)
    assert previous.stale and "솔버" in previous.stale_reason
    assert previous.solver_id == "calculix"
    assert project.nodes[solver.id].files["output"].stale
    wb.close()
