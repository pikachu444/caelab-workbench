"""Connection registry and discussion-summary contracts."""

import os
from pathlib import Path

import pytest

from caelab.connections import (
    PROGRAMS,
    ProgramSpec,
    find_program,
    launch_command,
    program_statuses,
)
from caelab.result_summary import format_summary_ko, summarize_result
from caelab.solvers.base import ResultSet
from caelab.workbench import Workbench

CCX = os.environ.get("CAELAB_CCX")


def test_registry_covers_the_connected_programs():
    ids = {spec.id for spec in PROGRAMS}
    assert {"freecad", "salome", "gmsh", "calculix", "paraview",
            "codeaster", "opencourant"} <= ids
    roles = {spec.id: spec.role for spec in PROGRAMS}
    assert roles["freecad"] == "cad"
    assert roles["prepomax"] == "preprocessor"
    assert roles["paraview"] == "postprocessor"
    solvers = {spec.id for spec in PROGRAMS if spec.role == "solver"}
    assert solvers == {"calculix", "codeaster", "opencourant", "fenicsx"}


def test_env_override_wins_and_missing_program_is_not_an_error(tmp_path):
    spec = ProgramSpec("dummy", "Dummy", "solver", "CAELAB_DUMMY_XYZ", ("dummy-xyz",))
    assert find_program(spec) is None
    target = tmp_path / ("dummy-xyz.exe" if os.name == "nt" else "dummy-xyz")
    target.write_text("")
    target.chmod(0o755)
    os.environ["CAELAB_DUMMY_XYZ"] = str(target)
    try:
        assert find_program(spec) == str(target)
    finally:
        del os.environ["CAELAB_DUMMY_XYZ"]


def test_statuses_report_all_programs():
    statuses = program_statuses()
    assert len(statuses) == len(PROGRAMS)
    by_id = {s.spec.id: s for s in statuses}
    if CCX:
        assert by_id["calculix"].available


def test_launch_command_requires_availability():
    spec = ProgramSpec("dummy", "Dummy", "solver", "CAELAB_DUMMY_XYZ", ("dummy-xyz",))
    from caelab.connections import ProgramStatus

    assert launch_command(ProgramStatus(spec, None), "a.inp") is None
    assert launch_command(ProgramStatus(spec, "/bin/dummy"), "a.inp") == ["/bin/dummy", "a.inp"]


@pytest.mark.skipif(not CCX, reason="CAELAB_CCX not set")
def test_summary_matches_end_to_end_numbers(tmp_path):
    from conftest import cantilever_model

    wb = Workbench()
    job = wb.run(cantilever_model(), "calculix", tmp_path)
    assert job.result is not None
    summary = summarize_result(job.result)
    assert summary["success"]
    # equilibrium: the summary must carry the same numbers as the run
    assert summary["reaction_totals"]["fy"] == pytest.approx(1000.0, abs=0.5)
    assert summary["max_displacement_mm"] == pytest.approx(0.133, abs=0.01)
    text = format_summary_ko(summary)
    assert " m" in text
    assert "1,000.0 N" in text


@pytest.mark.parametrize("length,value,metres", [("m", 0.002, 0.002),
                                                ("mm", 2.0, 0.002),
                                                ("in", 2.0, None)])
def test_summary_preserves_actual_solver_and_declared_units(length, value, metres):
    result = ResultSet(solver_id="code_aster", success=True,
                       max_displacement=value, reaction_totals={"fy": 12.0})
    result.units = {"length": length, "force": "kN"}
    summary = summarize_result(result, solver="calculix")
    assert summary["solver"] == summary["solver_id"] == "code_aster"
    assert summary["max_displacement"] == value
    assert summary["units"] == result.units
    assert summary["units"] is not result.units
    assert summary["max_displacement_m"] == metres
    assert summary["max_displacement_mm"] == (metres * 1000 if metres is not None else None)
    text = format_summary_ko(summary)
    assert f"{value:.6g} {length}" in text
    assert "12.0 kN" in text
    assert "calculix" not in text


def test_summary_does_not_guess_units_or_replace_missing_values_with_zero():
    result = ResultSet(solver_id="unknown-solver", success=True,
                       max_displacement=None, reaction_totals={})
    result.units = {}
    summary = summarize_result(result)
    assert summary["max_displacement"] is None
    assert summary["max_displacement_m"] is None
    assert summary["max_displacement_mm"] is None
    assert "최대 변위 미확인" in format_summary_ko(summary)
    result.max_displacement = 0.0
    result.reaction_totals = {"fy": 0.0}
    text = format_summary_ko(summarize_result(result))
    assert "최대 변위 0 단위 미확인" in text
    assert "반력 합 Fy 0.0 단위 미확인" in text
    assert "사용자 선언 필요" in text
    assert " mm" not in text and " N" not in text


def test_failed_result_summary_keeps_failure_and_solver():
    result = ResultSet(solver_id="fenicsx", success=False, max_displacement=None)
    summary = summarize_result(result)
    assert not summary["success"]
    assert "fenicsx 계산이 실패" in format_summary_ko(summary)



def test_explicit_invalid_path_never_falls_back_to_registry_or_path(tmp_path, monkeypatch):
    import json
    import sys
    spec = next(spec for spec in PROGRAMS if spec.id == "gmsh")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"env": {spec.env_var: sys.executable}}))
    monkeypatch.setenv(spec.env_var, str(tmp_path / "missing"))
    monkeypatch.setattr("caelab.connections.shutil.which", lambda name: sys.executable)
    status = next(status for status in program_statuses(registry) if status.spec.id == "gmsh")
    assert not status.available
    assert status.source == "environment"
    assert "실행 파일" in status.reason
    assert find_program(spec, registry) is None


def test_registry_runtime_defaults_and_explicit_environment_precedence(tmp_path, monkeypatch):
    import json
    import sys
    from caelab.connections import launch_request
    monkeypatch.delenv("CAELAB_GMSH", raising=False)
    monkeypatch.setenv("GUI_EXPLICIT", "host wins")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"env": {"CAELAB_GMSH": sys.executable},
                                    "runtime_env": {"gmsh": {"GUI_DEFAULT": "registered", "GUI_EXPLICIT": "registry"}}}))
    document = tmp_path / "한글 입력 공백.geo"
    document.write_text("Point(1) = {0,0,0};")
    status = next(status for status in program_statuses(registry) if status.spec.id == "gmsh")
    request = launch_request(status, document)
    assert status.available and status.source == "registry"
    assert request.argv == [sys.executable, str(document.resolve())]
    assert request.cwd == tmp_path.resolve()
    assert request.env["GUI_DEFAULT"] == "registered"
    assert request.env["GUI_EXPLICIT"] == "host wins"


@pytest.mark.parametrize("program,name", [("freecad", "freecadcmd"), ("paraview", "pvpython")])
def test_console_only_programs_are_not_gui_launchers(tmp_path, monkeypatch, program, name):
    target = tmp_path / (name + (".exe" if os.name == "nt" else ""))
    target.write_text("not executed")
    target.chmod(0o755)
    spec = next(spec for spec in PROGRAMS if spec.id == program)
    monkeypatch.setenv(spec.env_var, str(target))
    status = next(status for status in program_statuses() if status.spec.id == program)
    assert not status.available
    assert "GUI" in status.reason


def test_native_file_open_arguments_and_solver_separation(tmp_path):
    import sys
    from caelab.connections import ProgramStatus, launch_request
    for program, suffix, expected in [("freecad", ".FCStd", None), ("gmsh", ".msh", None), ("paraview", ".vtk", "--data=")]:
        spec = next(spec for spec in PROGRAMS if spec.id == program)
        file = tmp_path / ("한글 공백" + suffix)
        file.write_bytes(b"native input")
        request = launch_request(ProgramStatus(spec, sys.executable), file)
        assert request.argv == [sys.executable, (expected or "") + str(file.resolve())]
        assert request.cwd == tmp_path.resolve()
    solver = next(spec for spec in PROGRAMS if spec.id == "calculix")
    with pytest.raises(ValueError, match="P4"):
        launch_request(ProgramStatus(solver, sys.executable), file)


def test_unimplemented_windows_wsl_environment_is_visible(monkeypatch):
    monkeypatch.setattr("caelab.connections.host_platform", lambda: "windows")
    by_id = {status.spec.id: status for status in program_statuses()}
    assert not by_id["fenicsx"].available
    assert by_id["fenicsx"].execution_environment == "wsl2"
    assert "구현되지" in by_id["fenicsx"].reason
    assert not by_id["codeaster"].available
    assert by_id["codeaster"].execution_environment == "unresolved"


def test_changed_and_stale_files_can_open_without_accepting_new_revision(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    wb = Workbench()
    project = wb.new_native_project()
    source = wb.add_program_node(project, "mesh", "gmsh")
    target = wb.add_program_node(project, "results", "paraview")
    file = tmp_path / "source.msh"
    file.write_bytes(b"original")
    ref = wb.set_node_file(project, source.id, file)
    original_revision = ref.revision
    wb.connect_nodes(project, source.id, target.id, "input")
    monkeypatch.setenv("CAELAB_GMSH", sys.executable)
    monkeypatch.setenv("CAELAB_PARAVIEW", sys.executable)
    monkeypatch.setattr(wb.jobs, "launch", lambda req: SimpleNamespace(state="opened"))
    file.write_bytes(b"edited outside")
    assert wb.open_program(project, source.id).state == "opened"
    assert source.state == "changed"
    assert ref.revision == original_revision
    assert wb.open_program(project, target.id).state == "opened"
    assert target.state == "stale"


def test_relative_installer_registry_uses_install_location_after_cwd_changes(tmp_path, monkeypatch):
    import json
    first, other = tmp_path / "cwd A", tmp_path / "cwd B"
    first.mkdir()
    other.mkdir()
    relative_root = Path("한글 설치") / "tools"
    suffix = "gmsh.exe" if os.name == "nt" else "gmsh"
    executable = first / relative_root / "bin" / suffix
    executable.parent.mkdir(parents=True)
    executable.write_text("original installation")
    executable.chmod(0o755)
    decoy = other / relative_root / "bin" / suffix
    decoy.parent.mkdir(parents=True)
    decoy.write_text("wrong cwd executable")
    decoy.chmod(0o755)
    registry = first / relative_root / "caelab-install-registry.json"
    registry.write_text(json.dumps({
        "root": str(relative_root),
        "env": {"CAELAB_GMSH": str(relative_root / "bin" / suffix)},
        "runtime_env": {"gmsh": {
            "LD_LIBRARY_PATH": os.pathsep.join(str(relative_root / name) for name in ("lib", "vendor")),
            "OMP_STACKSIZE": "400m", "CUSTOM_NOTE": str(relative_root / "ordinary text"),
        }},
    }), encoding="utf-8")
    monkeypatch.delenv("CAELAB_GMSH", raising=False)
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    monkeypatch.delenv("OMP_STACKSIZE", raising=False)
    for directory in (first, other):
        monkeypatch.chdir(directory)
        status = next(status for status in program_statuses(registry) if status.spec.id == "gmsh")
        assert status.available, status.reason
        assert status.path == str(executable)
        assert status.runtime_env["LD_LIBRARY_PATH"] == os.pathsep.join(str(registry.parent / name) for name in ("lib", "vendor"))
        assert status.runtime_env["OMP_STACKSIZE"] == "400m"
        assert status.runtime_env["CUSTOM_NOTE"] == str(relative_root / "ordinary text")


@pytest.mark.parametrize("recorded_root", [None, "unrelated-root"])
def test_ambiguous_registry_relative_path_does_not_guess_from_cwd(tmp_path, monkeypatch, recorded_root):
    import json
    import sys
    target = tmp_path / ("gmsh.exe" if os.name == "nt" else "gmsh")
    target.write_text("must not select this cwd file")
    target.chmod(0o755)
    data = {"env": {"CAELAB_GMSH": target.name}}
    if recorded_root:
        data["root"] = recorded_root
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAELAB_GMSH", raising=False)
    monkeypatch.setattr("caelab.connections.shutil.which", lambda name: sys.executable)
    status = next(status for status in program_statuses(registry) if status.spec.id == "gmsh")
    assert not status.available
    assert status.source == "registry"
    assert "상대경로" in status.reason
    monkeypatch.setenv("CAELAB_GMSH", target.name)
    explicit = next(status for status in program_statuses(registry) if status.spec.id == "gmsh")
    assert explicit.available
    assert explicit.source == "environment"
    assert explicit.path == str(target)
