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


@pytest.mark.parametrize("stale", [False, True])
def test_unit_edit_preserves_changed_file_revision_connections_and_stale(tmp_path, stale):
    wb, project, first, second, result, source = graph(tmp_path)
    wb.connect_nodes(project, first.id, second.id, "input")
    wb.connect_nodes(project, second.id, result.id, "input")
    reference = first.files["input"]
    reference.stale = stale
    revision, connections = reference.revision, list(project.connections)
    source.write_bytes(b"changed externally; not accepted")
    wb.set_node_file_units(project, first.id, {"length": "m"})
    assert reference.revision == revision
    assert reference.path == str(source.resolve())
    assert reference.stale is stale
    assert reference.status == "changed"
    assert reference.units == {"length": "m"} and reference.unit_source == "user"
    assert project.connections == connections
    assert second.state == result.state == "stale"
    assert source.read_bytes() == b"changed externally; not accepted"


@pytest.mark.parametrize("stale", [False, True])
def test_output_unit_edit_invalidates_dependents_without_renewing_output(tmp_path, stale):
    wb, project, first, second, result, source = graph(tmp_path)
    output = source.with_suffix(".inp")
    output.write_bytes(b"native deck")
    reference = wb.set_node_file(project, first.id, output, "output", {"length": "mm"})
    wb.connect_nodes(project, first.id, second.id)
    wb.connect_nodes(project, second.id, result.id, "input")
    reference.stale = stale
    revision = reference.revision
    wb.set_node_file_units(project, first.id, {}, role="output")
    assert reference.stale is stale and reference.revision == revision
    assert reference.units == {} and reference.unit_source == "unknown"
    assert second.state == result.state == "stale"
    assert wb.input_file(project, result.id) is reference
    assert output.read_bytes() == b"native deck"


def test_unchanged_units_preserve_declaration_source_and_entire_graph(tmp_path):
    from copy import deepcopy
    wb, project, first, second, _, _ = graph(tmp_path)
    wb.connect_nodes(project, first.id, second.id, "input")
    before = deepcopy(project)
    wb.set_node_file_units(project, first.id, {"length": "mm"})
    assert project == before
    assert first.files["input"].unit_source == "user declaration"


@pytest.mark.parametrize("case", ["connected", "absent", "role", "invalid", "stored", "default_store"])
def test_rejected_unit_edits_leave_entire_graph_unchanged(tmp_path, case):
    from copy import deepcopy
    wb, project, first, second, _, source = graph(tmp_path)
    node_id, role, units = first.id, "input", {"length": "m"}
    if case == "connected":
        wb.connect_nodes(project, first.id, second.id, "input")
        node_id = second.id
    elif case == "absent":
        node_id = second.id
    elif case == "role":
        role = "unknown"
    elif case == "invalid":
        units = {"length": 123}
    else:
        project.path = tmp_path / "project.json"
        root = tmp_path / "custom-runs" if case == "stored" else project.path.with_suffix(".runs")
        if case == "stored":
            project.run_store = root
        root.mkdir()
        preserved = root / "result.frd"
        preserved.write_bytes(b"preserved calculation")
        wb.set_node_file(project, first.id, preserved, "output")
        role = "output"
    before = deepcopy(project)
    with pytest.raises(ValueError):
        wb.set_node_file_units(project, node_id, units, role)
    assert project == before


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


def test_missing_inactive_local_file_does_not_invalidate_effective_connected_input(tmp_path):
    wb, project, first, second, result, source = graph(tmp_path)
    active = source.with_suffix(".msh")
    active.write_bytes(b"actual connected input")
    wb.set_node_file(project, first.id, active, "output")
    inactive = source.with_name("unused local.geo")
    inactive.write_bytes(b"old local input")
    wb.set_node_file(project, second.id, inactive)
    wb.connect_nodes(project, first.id, second.id)
    wb.connect_nodes(project, second.id, result.id, "input")
    assert second.state == result.state == "ready"
    inactive.unlink()
    wb.refresh_native_project(project)
    assert wb.input_file(project, second.id).path == str(active.resolve())
    assert second.state == result.state == "ready"


@pytest.mark.parametrize("current_state", ["ready", "changed", "missing", "stale"])
def test_reopen_recomputes_opened_status_without_restoring_a_live_program(tmp_path, current_state):
    wb, project, first, _, _, source = graph(tmp_path)
    first.state = "opened"
    first.reason = "과거 프로그램 열기 요청을 전달했습니다."
    if current_state == "stale":
        first.files["input"].stale = True
    path = wb.save_native_project(project, tmp_path / "project.json")
    if current_state == "changed":
        source.write_bytes(b"changed after the program was opened")
    elif current_state == "missing":
        source.unlink()
    reopened_workbench = Workbench()
    loaded = reopened_workbench.open_native_project(path)
    assert reopened_workbench.jobs.launches == {}
    assert loaded.nodes[first.id].state == current_state
    assert "과거 프로그램 열기" not in loaded.nodes[first.id].reason


def test_disconnect_clears_hidden_input_and_invalidates_downstream_without_touching_files(tmp_path):
    wb, project, first, second, result, source = graph(tmp_path)
    old = source.with_suffix('.vtk')
    old.write_bytes(b'previous output')
    wb.set_node_file(project, second.id, source)
    wb.set_node_file(project, second.id, old, 'output')
    wb.connect_nodes(project, first.id, second.id, 'input')
    wb.connect_nodes(project, second.id, result.id)
    wb.disconnect_node_input(project, second.id)
    assert wb.input_file(project, second.id) is None
    assert second.state == 'needs_input'
    assert second.files['output'].stale and result.state == 'stale'
    assert source.read_bytes() == b'original native file'
    assert old.read_bytes() == b'previous output'
    path = wb.save_native_project(project, tmp_path / 'project.json')
    loaded = wb.open_native_project(path)
    assert loaded.input_file(second.id) is None
    # Calling disconnect on a direct input is a no-op, not a file removal.
    wb.disconnect_node_input(loaded, first.id)
    assert loaded.input_file(first.id).path == str(source.resolve())


def test_remove_restore_keeps_history_after_save_as_and_does_not_revive_hidden_input(tmp_path):
    from test_project_resume import research
    from caelab.native.run_store import RunStore
    wb, project, analysis, run, discussion, document, _ = research(tmp_path)
    geometry = wb.add_program_node(project, 'geometry', 'freecad')
    upstream = tmp_path / 'replacement.inp'
    upstream.write_bytes(b'new input')
    wb.set_node_file(project, geometry.id, upstream, 'output')
    wb.connect_nodes(project, geometry.id, analysis.id)
    original_input = Path(run.input_path).read_bytes()
    run_bytes = (run.workdir / 'job.json').read_bytes()
    discussion_bytes = discussion.read_bytes()
    old_root = project.run_store
    output_relative = Path(analysis.files['output'].path).relative_to(old_root)
    wb.remove_program_node(project, analysis.id)
    assert analysis.id not in project.nodes
    assert 'input' not in project.removed_nodes[analysis.id].files
    copied = wb.save_native_project(project, tmp_path / 'copy' / 'project.json')
    loaded = wb.open_native_project(copied)
    assert loaded.removed_nodes[analysis.id].files['output'].path == str(loaded.run_store / output_relative)
    restored = wb.restore_program_node(loaded, analysis.id)
    assert restored.id == analysis.id and loaded.input_file(analysis.id) is None
    assert not loaded.connections
    assert wb.selected_native_run(loaded, analysis.id).id == run.id
    assert RunStore(loaded.run_store).selected_id(analysis.id) == run.id
    assert (loaded.run_store / discussion.relative_to(old_root)).read_bytes() == discussion_bytes
    assert (run.workdir / 'job.json').read_bytes() == run_bytes
    assert discussion.read_bytes() == discussion_bytes
    assert Path(run.input_path).read_bytes() == original_input
    assert upstream.read_bytes() == b'new input'


def test_remove_source_clears_direct_targets_and_restore_preserves_direct_source(tmp_path):
    wb, project, first, second, result, source = graph(tmp_path)
    wb.set_node_file(project, second.id, source)
    wb.connect_nodes(project, first.id, second.id, 'input')
    wb.connect_nodes(project, second.id, result.id, 'input')
    wb.remove_program_node(project, first.id)
    assert project.input_file(second.id) is None
    assert project.input_file(result.id) is None
    assert second.state == result.state == 'needs_input'
    with pytest.raises(ValueError, match='원본 파일'):
        wb.save_native_project(project, source)
    restored = wb.restore_program_node(project, first.id)
    assert restored.files['input'].path == str(source.resolve())
    assert project.input_file(second.id) is None
    assert source.read_bytes() == b'original native file'


@pytest.mark.parametrize('busy', ['lease', 'running', 'reading'])
def test_remove_rejects_active_work_without_mutation_and_releases_own_lock(tmp_path, busy):
    from copy import deepcopy
    from caelab.native.run_store import NativeRun, RunStore
    wb, project, first, *_ = graph(tmp_path)
    wb.save_native_project(project, tmp_path / 'project.json')
    store = RunStore(project.run_store)
    run = NativeRun('a' * 32, first.id, store.root / ('a' * 32),
                    state='running' if busy == 'running' else 'completed',
                    parse_state='reading' if busy == 'reading' else 'unparsed')
    store.write(run)
    lease = store.lease() if busy == 'lease' else None
    before = deepcopy(project)
    try:
        with pytest.raises(ValueError):
            wb.remove_program_node(project, first.id)
        assert project == before
    finally:
        if lease is not None:
            lease.release()
    check = store.lease()
    check.release()


def test_removed_programs_are_validated_like_active_programs(tmp_path):
    wb, project, first, *_ = graph(tmp_path)
    wb.remove_program_node(project, first.id)
    path = wb.save_native_project(project, tmp_path / 'project.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    data['nodes'][first.id] = data['removed_nodes'][first.id]
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='제거한 프로그램'):
        wb.open_native_project(path)


@pytest.mark.parametrize('operation', ['disconnect', 'remove', 'restore'])
def test_graph_edit_file_read_failure_leaves_every_live_object_unchanged(tmp_path, monkeypatch, operation):
    from copy import deepcopy
    import caelab.project as project_module
    wb, project, first, second, result, source = graph(tmp_path)
    wb.set_node_file(project, second.id, source)
    wb.set_node_file(project, second.id, source, 'output')
    wb.set_node_file(project, result.id, source)
    wb.connect_nodes(project, first.id, second.id, 'input')
    wb.connect_nodes(project, first.id, result.id, 'input')
    if operation == 'restore':
        wb.remove_program_node(project, first.id)
    before = deepcopy(project)
    objects = {node.id: (node, deepcopy(node)) for node in (first, second, result)}
    def denied(path):
        raise PermissionError('file temporarily unavailable')
    monkeypatch.setattr(project_module, 'file_revision', denied)
    with pytest.raises(PermissionError):
        if operation == 'disconnect':
            wb.disconnect_node_input(project, second.id)
        elif operation == 'remove':
            wb.remove_program_node(project, first.id)
        else:
            wb.restore_program_node(project, first.id)
    assert project == before
    assert all(original == snapshot for original, snapshot in objects.values())
    assert source.read_bytes() == b'original native file'


def test_current_save_format_preserves_archive_by_rejecting_legacy_writers(tmp_path):
    wb, project, first, *_ = graph(tmp_path)
    wb.remove_program_node(project, first.id)
    path = wb.save_native_project(project, tmp_path / 'project.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    assert data['schema_version'] == 2  # Version-1 loaders explicitly reject this.
    assert first.id in wb.open_native_project(path).removed_nodes
