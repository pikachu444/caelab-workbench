"""Native file relationships and persistence; no FE model or external service."""

import json
from pathlib import Path

import pytest

from caelab.project import FileReference, file_revision
from caelab.workbench import Workbench


def graph(tmp_path):
    wb = Workbench()
    project = wb.new_native_project("한글 프로젝트")
    first = wb.add_program_node(project, "geometry", "freecad", (10, 20))
    second = wb.add_program_node(project, "geometry", "freecad", (30, 40))
    result = wb.add_program_node(project, "results", "paraview")
    path = tmp_path / "외부 원본 공백" / "사용자 모델.FCStd"
    path.parent.mkdir()
    path.write_bytes(b"original native file")
    wb.set_node_file(project, first.id, path, units={"length": "mm"}, unit_source="user declaration")
    return wb, project, first, second, result, path


def test_save_reopen_save_as_preserves_external_original_and_multiple_nodes(tmp_path):
    wb, project, first, second, result, source = graph(tmp_path)
    other = source.with_name("다른 파일.FCStd")
    other.write_bytes(b"other")
    wb.set_node_file(project, second.id, other)
    exported = source.with_suffix(".vtk")
    exported.write_bytes(b"exported result")
    wb.set_node_file(project, first.id, exported, role="output")
    wb.connect_nodes(project, first.id, result.id)
    initial = wb.save_native_project(project, tmp_path / "처음 프로젝트" / "project.json")
    loaded = wb.open_native_project(initial)
    assert len(loaded.nodes) == 3
    assert loaded.nodes[first.id].kind == loaded.nodes[second.id].kind == "geometry"
    assert loaded.nodes[first.id].files["input"].path == str(source.resolve())
    assert loaded.nodes[second.id].files["input"].path == str(other.resolve())
    reference = loaded.nodes[first.id].files["input"]
    assert reference.revision == file_revision(source)
    assert reference.units == {"length": "mm"}
    assert reference.unit_source == "user declaration"
    assert loaded.nodes[first.id].position == (10, 20)
    assert wb.input_file(loaded, result.id).path == str(exported.resolve())
    copied = wb.save_native_project(loaded, tmp_path / "다른 위치" / "project.json")
    assert copied != initial and initial.exists()
    assert wb.open_native_project(copied).nodes[first.id].files["input"].path == str(source.resolve())
    assert source.read_bytes() == b"original native file"


def test_connections_select_input_and_reselection_invalidates_old_results(tmp_path):
    wb, project, first, second, result, source = graph(tmp_path)
    old = source.with_suffix(".vtk")
    old.write_bytes(b"old result")
    wb.set_node_file(project, first.id, old, role="output")
    wb.set_node_file(project, second.id, source)
    wb.set_node_file(project, second.id, old, role="output")
    wb.connect_nodes(project, first.id, second.id)
    wb.connect_nodes(project, second.id, result.id)
    assert wb.input_file(project, second.id).path == str(old.resolve())
    replacement = source.with_name("replacement.FCStd")
    replacement.write_bytes(b"new input")
    wb.set_node_file(project, second.id, replacement)
    assert wb.input_file(project, second.id).path == str(replacement.resolve())
    assert not any(edge.target_id == second.id for edge in project.connections)
    assert project.nodes[result.id].state == "stale"
    assert second.files["output"].stale
    assert old.read_bytes() == b"old result"


def test_refresh_retains_recorded_revision_and_propagates_change_missing_and_failure(tmp_path):
    wb, project, first, second, result, source = graph(tmp_path)
    wb.connect_nodes(project, first.id, second.id, source_role="input")
    wb.connect_nodes(project, second.id, result.id, source_role="input")
    revision = first.files["input"].revision
    source.write_bytes(b"edited externally")
    assert wb.refresh_native_project(project)
    assert first.files["input"].revision == revision
    assert first.state == "changed"
    assert second.state == "stale"
    wb.set_node_file(project, first.id, source)
    assert first.files["input"].revision != revision
    assert second.state == "stale"
    source.unlink()
    wb.refresh_native_project(project)
    assert first.state == "missing"
    assert second.state == "stale"
    wb.mark_node_failed(project, first.id, "launch failed")
    assert first.state == "failed"
    assert second.state == "stale"


def test_atomic_save_failure_keeps_previous_document_and_save_as_path(tmp_path, monkeypatch):
    import caelab.project as storage
    wb, project, *_ = graph(tmp_path)
    original = wb.save_native_project(project, tmp_path / "project.json")
    before = original.read_bytes()
    project.name = "unsaved changes"
    def fail(*args):
        raise OSError("replace denied")
    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError, match="replace denied"):
        wb.save_native_project(project)
    assert original.read_bytes() == before
    with pytest.raises(OSError):
        wb.save_native_project(project, tmp_path / "copy.json")
    assert project.path == original
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("mutation,reason", [
    (lambda data: data.update(schema_version=99), "schema_version"),
    (lambda data: data["nodes"].update({"": {"id": ""}}), "ID"),
    (lambda data: next(iter(data["nodes"].values())).update(id="different"), "ID"),
    (lambda data: next(iter(data["nodes"].values())).update(files={"input": {"path": 42}}), "파일 경로"),
    (lambda data: data["connections"].append({"source_id": "ghost", "target_id": "other"}), "노드 참조"),
])
def test_bad_project_has_a_read_error(tmp_path, mutation, reason):
    wb, project, *_ = graph(tmp_path)
    path = wb.save_native_project(project, tmp_path / "project.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    mutation(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match=reason):
        wb.open_native_project(path)


def test_cycles_duplicate_ids_and_original_overwrite_are_rejected(tmp_path):
    wb, project, first, second, _, source = graph(tmp_path)
    wb.connect_nodes(project, first.id, second.id, "input")
    with pytest.raises(ValueError, match="순환"):
        wb.connect_nodes(project, second.id, first.id, "input")
    with pytest.raises(ValueError, match="원본"):
        wb.save_native_project(project, source)
    path = tmp_path / "duplicate.json"
    path.write_text('{"schema_version":1,"name":"a","nodes":{"same":{},"same":{}},"connections":[]}')
    with pytest.raises(ValueError, match="중복"):
        wb.open_native_project(path)


def test_open_project_cannot_spawn_process_or_call_network(tmp_path, monkeypatch):
    import subprocess
    import urllib.request
    wb, project, *_ = graph(tmp_path)
    path = wb.save_native_project(project, tmp_path / "project.json")
    def forbidden(*args, **kwargs):
        pytest.fail("Opening a project must not start a process or network call")
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    assert wb.open_native_project(path).name == project.name


def test_connection_of_an_input_forwards_its_effective_upstream_file(tmp_path):
    wb, project, first, second, result, source = graph(tmp_path)
    output = source.with_suffix(".msh")
    output.write_bytes(b"upstream export")
    wb.set_node_file(project, first.id, output, role="output")
    local_old = source.with_name("old local.FCStd")
    local_old.write_bytes(b"old local input")
    wb.set_node_file(project, second.id, local_old)
    wb.connect_nodes(project, first.id, second.id)
    wb.connect_nodes(project, second.id, result.id, "input")
    assert second.files["input"].path == str(local_old.resolve())
    assert wb.input_file(project, result.id).path == str(output.resolve())


def test_first_input_registration_invalidates_already_registered_output(tmp_path):
    wb = Workbench()
    project = wb.new_native_project()
    source = wb.add_program_node(project, "analysis", "calculix")
    target = wb.add_program_node(project, "results", "paraview")
    output = tmp_path / "old.vtk"
    output.write_bytes(b"old result")
    wb.set_node_file(project, source.id, output, "output")
    wb.connect_nodes(project, source.id, target.id)
    native_input = tmp_path / "new.inp"
    native_input.write_bytes(b"new deck")
    wb.set_node_file(project, source.id, native_input)
    assert source.files["output"].stale
    assert target.state == "stale"
