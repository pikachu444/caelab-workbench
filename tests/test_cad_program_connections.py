"""External CAD document commands and explicit file handoff, without processes."""

from types import SimpleNamespace

import pytest

from caelab.connections import PROGRAMS, ProgramStatus, find_program, launch_request
from caelab.workbench import Workbench


@pytest.fixture
def cad_statuses(tmp_path, monkeypatch):
    monkeypatch.setattr("caelab.connections.host_platform", lambda: "windows")
    folder = tmp_path / "도구 공백"
    folder.mkdir()
    statuses = {}
    for program_id in ("solidcraft", "cadcraft", "freecad", "prepomax"):
        spec = next(spec for spec in PROGRAMS if spec.id == program_id)
        executable = folder / (program_id + ".exe")
        executable.write_bytes(b"not an executable; command construction only")
        monkeypatch.setenv(spec.env_var, str(executable))
        statuses[program_id] = ProgramStatus(spec, str(executable))
    return statuses


def test_additional_cad_registry_keeps_existing_program_roles():
    specs = {spec.id: spec for spec in PROGRAMS}
    assert specs["solidcraft"].role == specs["cadcraft"].role == "cad"
    assert specs["solidcraft"].env_var == "CAELAB_SOLIDCRAFT"
    assert specs["cadcraft"].env_var == "CAELAB_CADCRAFT"
    assert specs["freecad"].role == "cad"
    assert next(spec.id for spec in PROGRAMS if spec.role == "cad") == "freecad"
    assert specs["prepomax"].role == "preprocessor"


@pytest.mark.parametrize("program_id", ["solidcraft", "cadcraft"])
def test_explicit_cad_executable_is_discovered(cad_statuses, tmp_path, program_id):
    registry = tmp_path / "registry.json"
    registry.write_text("{}", encoding="utf-8")
    status = cad_statuses[program_id]
    assert find_program(status.spec, registry_path=registry) == status.path


@pytest.mark.parametrize("program_id", ["solidcraft", "cadcraft"])
def test_explicit_missing_cad_does_not_use_path_fallback(tmp_path, monkeypatch, program_id):
    spec = next(spec for spec in PROGRAMS if spec.id == program_id)
    monkeypatch.setenv(spec.env_var, str(tmp_path / "missing.exe"))
    monkeypatch.setattr("caelab.connections.host_platform", lambda: "windows")
    monkeypatch.setattr("caelab.connections.shutil.which", lambda name: pytest.fail("Explicit path must win"))
    registry = tmp_path / "registry.json"
    registry.write_text("{}", encoding="utf-8")
    assert find_program(spec, registry_path=registry) is None


@pytest.mark.parametrize("program_id,suffix", [
    ("solidcraft", ".sc"), ("solidcraft", ".solidcraft"),
    ("cadcraft", ".dxf"), ("cadcraft", ".dwg"), ("cadcraft", ".dwt"),
])
def test_cad_native_document_is_one_literal_argument(cad_statuses, tmp_path, monkeypatch, program_id, suffix):
    document = tmp_path / ("한글 설계 공백" + suffix)
    document.write_text("source document", encoding="utf-8")
    status = cad_statuses[program_id]
    status.runtime_env = {"CAD_TEST": "registered", "CAD_ONLY": "preserved"}
    monkeypatch.setattr("caelab.connections.external_environment", lambda: {"CAD_TEST": "caller"})
    request = launch_request(status, document)
    assert request.argv == [status.path, str(document.resolve())]
    assert request.cwd == document.parent
    assert request.program_id == program_id
    assert request.env == {"CAD_TEST": "caller", "CAD_ONLY": "preserved"}
    assert document.read_text(encoding="utf-8") == "source document"


@pytest.mark.parametrize("program_id,suffix", [("solidcraft", ".step"), ("cadcraft", ".step"), ("cadcraft", ".svg")])
def test_exchange_files_are_not_silently_opened_as_native_documents(cad_statuses, tmp_path, program_id, suffix):
    document = tmp_path / ("exchange" + suffix)
    document.write_text("exported data", encoding="utf-8")
    with pytest.raises(ValueError):
        launch_request(cad_statuses[program_id], document)


@pytest.mark.parametrize("program_id,suffix", [("solidcraft", ".sc"), ("freecad", ".FCStd")])
def test_cad_step_prepomax_inp_bindings_survive_reopen(cad_statuses, tmp_path, monkeypatch, program_id, suffix):
    files = {}
    for role, filename in (("cad", "source" + suffix), ("step", "export.step"),
                           ("pmx", "analysis.pmx"), ("inp", "export.inp")):
        files[role] = tmp_path / filename
        files[role].write_text(role, encoding="utf-8")
    units = {"length": "mm", "force": "N"}
    wb = Workbench()
    try:
        project = wb.new_native_project("CAD handoff")
        cad = wb.add_program_node(project, "geometry", program_id)
        pre = wb.add_program_node(project, "preprocessor", "prepomax")
        solver = wb.add_program_node(project, "analysis", "calculix")
        wb.set_node_file(project, cad.id, files["cad"], "document")
        wb.set_node_file(project, cad.id, files["step"], "output", units)
        wb.set_node_file(project, pre.id, files["pmx"], "document")
        wb.set_node_file(project, pre.id, files["inp"], "output", units)
        wb.connect_nodes(project, cad.id, pre.id)
        wb.connect_nodes(project, pre.id, solver.id)
        saved = wb.save_native_project(project, tmp_path / "project.json")
    finally:
        assert wb.close()

    reopened = Workbench()
    try:
        project = reopened.open_native_project(saved)
        assert reopened.input_file(project, pre.id).path == str(files["step"])
        assert reopened.input_file(project, solver.id).path == str(files["inp"])
        assert project.nodes[cad.id].files["document"].path == str(files["cad"])
        assert project.nodes[pre.id].files["output"].units == units
        launches = []
        monkeypatch.setattr(reopened, "programs", lambda: list(cad_statuses.values()))
        monkeypatch.setattr(reopened.jobs, "launch", lambda request: (launches.append(request) or SimpleNamespace(state="opened")))
        reopened.open_program(project, cad.id)
        reopened.open_program(project, pre.id)
        reopened.open_program(project, pre.id, use_input=True)
        assert launches[0].argv == [cad_statuses[program_id].path, str(files["cad"])]
        assert launches[1].argv == [cad_statuses["prepomax"].path, "-f", str(files["pmx"])]
        assert launches[2].argv == [cad_statuses["prepomax"].path, "-f", str(files["step"])]
        assert project.nodes[cad.id].state == "opened"
        assert all(path.read_text(encoding="utf-8") == role for role, path in files.items())
    finally:
        assert reopened.close()
