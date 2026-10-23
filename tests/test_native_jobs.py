"""Owned native execution, immutable inputs and durable selection (no FE model)."""

import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types

import pytest

from caelab.connections import LaunchRequest, ProgramStatus, PROGRAMS
from caelab.native.input_snapshot import snapshot_input
from caelab.native.run_store import NativeRun, RunStore
from caelab.project import file_revision
from caelab.workbench import Workbench


def project_with_input(tmp_path):
    wb = Workbench()
    project = wb.new_native_project("한글 프로젝트")
    node = wb.add_program_node(project, "analysis", "calculix")
    source = tmp_path / "외부 원본 공백" / "원본.inp"
    source.parent.mkdir()
    source.write_bytes(b"** any native physics\n*CONTACT PAIR,INTERACTION=FRICTION\nA,B\n")
    wb.set_node_file(project, node.id, source, units={"length": "mm"}, unit_source="user")
    wb.save_native_project(project, tmp_path / "프로젝트 공백.json")
    return wb, project, node, source


def wait_run(wb, project, run_id, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        run = next(r for r in wb.native_runs(project) if r.id == run_id)
        if run.finished_at:
            return run
        time.sleep(.02)
    raise AssertionError(f"run did not terminate: {run}")


@pytest.fixture
def probe(monkeypatch):
    """Actual Python subprocess; only solver behavior/result interpretation are mocked."""
    import caelab.connections as connections
    spec = next(s for s in PROGRAMS if s.id == "calculix")
    status = ProgramStatus(spec, sys.executable, source="test probe")
    monkeypatch.setattr(Workbench, "programs", lambda self: [status])
    script = ["from pathlib import Path; print('probe output',flush=True); Path('model.frd').write_text('raw result')"]
    def request(found, workdir):
        return LaunchRequest([sys.executable, "-c", script[0]], workdir,
                             {**os.environ, "CAELAB_PROBE": "specific"}, "calculix")
    monkeypatch.setattr(connections, "calculix_run_request", request)
    def collect(path, check_cancelled=lambda: None):
        check_cancelled()
        return {"status": "readable", "frames": [], "exchange_files": [], "warnings": [], "summary": {}}
    monkeypatch.setitem(sys.modules, "caelab.native.results", types.SimpleNamespace(collect_results=collect))
    return script


def test_queued_submit_does_not_hash_on_caller_and_snapshots_are_detached(tmp_path, probe, monkeypatch):
    import caelab.jobs as jobs
    wb, project, node, source = project_with_input(tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = jobs.snapshot_input
    def blocked(*args):
        entered.set()
        assert release.wait(5)
        return original(*args)
    monkeypatch.setattr(jobs, "snapshot_input", blocked)
    start = time.monotonic()
    submitted = wb.submit_native(project, node.id)
    assert time.monotonic() - start < 1
    assert submitted.state == "queued"
    assert entered.wait(2)
    submitted.state = "tampered"
    assert wb.native_runs(project)[0].state == "preparing"
    release.set()
    run = wait_run(wb, project, submitted.id)
    assert run.state == "completed" and run.parse_state == "readable"
    assert (run.workdir / "source/000000.inp").read_bytes() == source.read_bytes()
    assert (run.workdir / "model.inp").read_bytes() == source.read_bytes()
    assert run.units == {"length": "mm"}
    assert run.input_revision == file_revision(source)
    assert run.snapshot["execution"]["cwd"] == str(run.workdir)
    assert "probe output" in Path(run.log_path).read_text()
    assert wb.close()


@pytest.mark.parametrize("mode", ["cancel", "timeout", "close"])
def test_owned_live_probe_terminates_and_leaves_receipt(tmp_path, probe, mode):
    probe[0] = "import time; print('started',flush=True); time.sleep(60)"
    wb, project, node, _ = project_with_input(tmp_path)
    run = wb.submit_native(project, node.id, timeout=.3 if mode == "timeout" else 30)
    end = time.monotonic() + 5
    while wb.native_runs(project)[0].state != "running" and time.monotonic() < end:
        time.sleep(.01)
    if mode == "cancel":
        assert wb.cancel_native_run(run.id)
    elif mode == "close":
        assert wb.close()
    result = wait_run(wb, project, run.id)
    assert result.state == ("failed" if mode == "timeout" else "cancelled")
    assert result.returncode is not None
    assert Path(result.log_path).exists()
    assert json.loads((result.workdir / "job.json").read_text(encoding="utf-8"))["state"] == result.state
    assert wb.close()


def test_project_store_lock_prevents_other_controller_and_recovers_only_unlocked(tmp_path, probe):
    probe[0] = "import time; time.sleep(60)"
    wb, project, node, _ = project_with_input(tmp_path)
    run = wb.submit_native(project, node.id)
    other = Workbench()
    loaded = other.open_native_project(project.path)
    assert other.native_runs(loaded)[0].state != "interrupted"
    with pytest.raises(ValueError, match="다른 창"):
        other.submit_native(loaded, node.id)
    assert not other.cancel_native_run(run.id)
    with pytest.raises(ValueError, match="다른 창"):
        wb.save_native_project(project, tmp_path / "active-saveas.json")
    assert wb.close()
    store = RunStore(project.run_store)
    abandoned = NativeRun("a" * 32, node.id, store.root / ("a" * 32), state="running", pid=123456789)
    store.write(abandoned)
    new = other.open_native_project(project.path)
    recovered = next(r for r in other.native_runs(new) if r.id == abandoned.id)
    assert recovered.state == "interrupted" and "프로세스가 남아" in recovered.error


def test_save_as_copies_history_selection_and_new_runs_are_separate(tmp_path, probe):
    wb, project, node, source = project_with_input(tmp_path)
    first = wb.submit_native(project, node.id)
    wait_run(wb, project, first.id)
    wb.selected_native_run(project, node.id)
    original_path, original_store = project.path, project.run_store
    wb.save_native_project(project, tmp_path / "다른 이름.json")
    assert project.run_store != original_store
    chosen = wb.selected_native_run(project, node.id)
    assert chosen.id == first.id and chosen.workdir.parent == project.run_store
    assert Path(node.files["output"].path).parent == chosen.workdir
    second = wb.submit_native(project, node.id)
    wait_run(wb, project, second.id)
    original = Workbench().open_native_project(original_path)
    assert RunStore(original_store).selected_id(node.id) == first.id
    assert len(wb.native_runs(original)) == 1
    assert len(wb.native_runs(project)) == 2
    assert source.exists()
    assert wb.close()


def test_failed_new_selection_clears_old_output_and_marks_downstream(tmp_path, probe):
    wb, project, node, source = project_with_input(tmp_path)
    downstream = wb.add_program_node(project, "results", "paraview")
    wb.connect_nodes(project, node.id, downstream.id)
    first = wb.submit_native(project, node.id)
    wait_run(wb, project, first.id)
    wb.selected_native_run(project, node.id)
    assert node.files["output"].path.endswith("model.frd")
    probe[0] = "raise SystemExit(8)"
    second = wb.submit_native(project, node.id)
    assert "output" not in node.files
    assert downstream.state == "stale"
    failed = wait_run(wb, project, second.id)
    assert failed.state == "failed" and failed.returncode == 8
    assert wb.selected_native_run(project, node.id).id == second.id
    assert "output" not in node.files
    wb.select_native_run(project, node.id, first.id)
    assert "output" in node.files
    assert node.state == "ready"
    source.write_bytes(b"** deliberate current input")
    assert wb.selected_native_run(project, node.id).stale
    third = wb.submit_native(project, node.id)
    assert wait_run(wb, project, third.id).input_revision == file_revision(source)
    assert wb.close()


def test_upstream_change_refuses_old_deck_without_converter_gate(tmp_path, probe):
    wb, project, node, source = project_with_input(tmp_path)
    cad = wb.add_program_node(project, "geometry", "freecad")
    cadfile = source.with_suffix(".FCStd")
    cadfile.write_bytes(b"geometry")
    wb.set_node_file(project, cad.id, cadfile)
    wb.set_node_file(project, cad.id, source, role="output")
    wb.connect_nodes(project, cad.id, node.id)
    cadfile.write_bytes(b"new geometry")
    submitted = wb.submit_native(project, node.id)
    run = wait_run(wb, project, submitted.id)
    assert run.state == "failed" and "상위 파일" in run.error
    assert run.pid is None
    assert wb.close()


def test_include_snapshot_preserves_tokens_order_and_top_level_cwd(tmp_path):
    source = tmp_path / "external sources"
    source.mkdir()
    (source / "nested").mkdir()
    (source / "nested/a.inc").write_bytes(b"** inside\r\n*INCLUDE,INPUT=b.inc\r\n")
    (source / "b.inc").write_bytes(b"*CONTACT PAIR,INTERACTION=F\nA,B\n")
    root = source / "main.inp"
    # Original absolute paths with spaces are ambiguous to ccx and rejected separately.
    outside = tmp_path / "outside.inc"
    outside.write_bytes(b"** outside")
    root.write_bytes(b'** source\r\n*INCLUDE, INPUT= "nested/a.inc" \r\n*INCLUDE,INPUT=b.inc\n*INCLUDE,INPUT=b.inc\n*INCLUDE,INPUT=../outside.inc\n*RESTART,WRITE,FREQUENCY=0\n')
    run_dir = tmp_path / "run"
    snapshot = snapshot_input(root, run_dir)
    assert len(snapshot["files"]) == 4
    assert len(snapshot["includes"]) == 5
    assert (run_dir / "source/000000.inp").read_bytes() == root.read_bytes()
    rewritten = (run_dir / "model.inp").read_bytes()
    assert b'INPUT= "includes/000001.inp" \r\n' in rewritten
    assert rewritten.count(b"INPUT=includes/000002.inp") == 2
    assert b"*CONTACT PAIR" in (run_dir / "includes/000002.inp").read_bytes()
    def expand(path, cwd):
        result = b""
        for line in path.read_bytes().splitlines(keepends=True):
            if line.startswith(b"*INCLUDE"):
                name = line.split(b"=", 1)[1].strip().strip(b'"').decode()
                result += expand(cwd / name, cwd)
            else:
                result += line
        return result
    assert expand(root, source) == expand(run_dir / "model.inp", run_dir)
    (source / "b.inc").write_bytes(b"changed")
    assert snapshot["files"][2]["original_sha256"] != file_revision(source / "b.inc")


@pytest.mark.parametrize("text,reason", [
    (b'*INCLUDE,INPUT="a b.inc"\n', "공백"),
    (b'*INCLUDE,INPUT=a.inc,b.inc\n', "쉼표"),
    (b'*RESTART,READ\n', "외부/restart"),
    (b'*TEMPERATURE,FILE=other\n', "외부 파일"),
    (b'*INCLUDE,INPUT=missing.inc\n', "없습니다"),
])
def test_ambiguous_or_missing_external_input_refuses_snapshot(tmp_path, text, reason):
    root = tmp_path / "main.inp"
    root.write_bytes(text)
    with pytest.raises(ValueError, match=reason):
        snapshot_input(root, tmp_path / "run")


def test_cycle_depth_limit_and_absolute_include(tmp_path):
    root = tmp_path / "main.inp"
    root.write_text("*INCLUDE,INPUT=main.inp\n")
    with pytest.raises(ValueError, match="순환"):
        snapshot_input(root, tmp_path / "cycle")
    for index in range(11):
        (tmp_path / f"{index}.inc").write_text(f"*INCLUDE,INPUT={index + 1}.inc\n" if index < 10 else "** end")
    with pytest.raises(ValueError, match="9단계"):
        snapshot_input(tmp_path / "0.inc", tmp_path / "deep")
    snapshot_input(tmp_path / "1.inc", tmp_path / "allowed")
    root.write_text(f'*INCLUDE,INPUT="{tmp_path / "10.inc"}"\n')
    snapshot_input(root, tmp_path / "absolute")


def test_change_during_collection_refuses_start(tmp_path, monkeypatch):
    root = tmp_path / "main.inp"
    root.write_bytes(b"** first")
    original = Path.read_bytes
    def changed(path):
        data = original(path)
        if path == root:
            path.write_bytes(b"** edited while collecting")
        return data
    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(ValueError, match="변경"):
        snapshot_input(root, tmp_path / "run")


def test_child_receives_explicit_install_registry(tmp_path, monkeypatch):
    wb, project, node, _ = project_with_input(tmp_path)
    registry = tmp_path / "settings/registry.json"
    wb.registry_path = registry
    requests = []
    monkeypatch.setattr(wb.jobs, "launch", lambda request: requests.append(request) or types.SimpleNamespace(state="opened"))
    wb.open_native_analysis(project, node.id)
    assert requests[0].env["CAELAB_INSTALL_REGISTRY"] == str(registry.resolve())


def test_completed_run_tracks_upstream_and_include_changes_without_refresh(tmp_path, probe):
    wb, project, node, source = project_with_input(tmp_path)
    include = source.with_name("mesh.inc")
    include.write_bytes(b"** include")
    source.write_bytes(b"*INCLUDE,INPUT=mesh.inc\n")
    wb.set_node_file(project, node.id, source)
    cad = wb.add_program_node(project, "geometry", "freecad")
    geometry = source.with_suffix(".FCStd")
    geometry.write_bytes(b"original geometry")
    wb.set_node_file(project, cad.id, geometry)
    wb.set_node_file(project, cad.id, source, role="output")
    wb.connect_nodes(project, cad.id, node.id)
    submitted = wb.submit_native(project, node.id)
    assert not wait_run(wb, project, submitted.id).stale
    geometry.write_bytes(b"new geometry")
    assert wb.native_runs(project)[0].stale
    geometry.unlink()
    assert wb.native_runs(project)[0].stale
    assert wb.close()


def test_cancel_ends_owned_descendant_and_preserves_unrelated_process(tmp_path, probe):
    child = "from pathlib import Path\nimport time\nwhile True:\n Path('heartbeat').write_text(str(time.monotonic()))\n time.sleep(.02)\n"
    probe[0] = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    wb, project, node, _ = project_with_input(tmp_path)
    try:
        run = wb.submit_native(project, node.id)
        heartbeat = run.workdir / "heartbeat"
        deadline = time.monotonic() + 5
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert heartbeat.exists()
        assert wb.cancel_native_run(run.id)
        assert wait_run(wb, project, run.id).state == "cancelled"
        before = heartbeat.read_bytes()
        time.sleep(.15)
        assert heartbeat.read_bytes() == before
        assert unrelated.poll() is None
        assert wb.close()
    finally:
        wb.close()
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_parse_cancellation_and_exception_keep_distinct_solver_receipt(tmp_path, probe, monkeypatch):
    entered = threading.Event()
    def reading(path, check_cancelled):
        entered.set()
        while True:
            check_cancelled()
            time.sleep(.01)
    monkeypatch.setitem(sys.modules, "caelab.native.results", types.SimpleNamespace(collect_results=reading))
    wb, project, node, _ = project_with_input(tmp_path)
    submitted = wb.submit_native(project, node.id)
    assert entered.wait(5)
    assert wb.cancel_native_run(submitted.id)
    run = wait_run(wb, project, submitted.id)
    assert run.state == "cancelled" and run.parse_state == "failed" and run.returncode == 0
    def broken(path, check_cancelled):
        raise ValueError("bad result")
    monkeypatch.setitem(sys.modules, "caelab.native.results", types.SimpleNamespace(collect_results=broken))
    submitted = wb.submit_native(project, node.id)
    run = wait_run(wb, project, submitted.id)
    assert run.state == "completed" and run.parse_state == "failed" and run.returncode == 0
    assert "bad result" in run.error
    assert wb.close()


def test_missing_executable_is_terminal_with_log(tmp_path, monkeypatch):
    wb, project, node, _ = project_with_input(tmp_path)
    spec = next(s for s in PROGRAMS if s.id == "calculix")
    status = ProgramStatus(spec, str(tmp_path / "removed.exe"), source="explicit environment")
    monkeypatch.setattr(wb, "programs", lambda: [status])
    run = wait_run(wb, project, wb.submit_native(project, node.id).id)
    assert run.state == "failed" and run.pid is None and Path(run.log_path).is_file()
    assert wb.close()


def test_polling_does_not_hash_source_files(tmp_path, probe, monkeypatch):
    wb, project, node, source = project_with_input(tmp_path)
    submitted = wb.submit_native(project, node.id)
    wait_run(wb, project, submitted.id)
    import caelab.project as storage
    def forbidden(*args):
        raise AssertionError("hash on UI polling path")
    monkeypatch.setattr(storage, "file_revision", forbidden)
    assert wb.selected_native_run(project, node.id).state == "completed"
    source.unlink()
    assert wb.selected_native_run(project, node.id).stale
    assert wb.close()


def test_os_store_lock_is_exclusive_in_another_process(tmp_path):
    store = RunStore(tmp_path / "runs")
    lease = store.lease()
    try:
        script = "from caelab.native.run_store import RunStore; import sys; RunStore(sys.argv[1]).lease()"
        result = subprocess.run([sys.executable, "-c", script, str(store.root)], capture_output=True, timeout=10)
        assert result.returncode != 0 and b"ValueError" in result.stderr
    finally:
        lease.release()


@pytest.mark.parametrize("failure", ["existing", "replace"])
def test_save_as_failure_preserves_source_and_destination_history(tmp_path, probe, monkeypatch, failure):
    wb, project, node, source = project_with_input(tmp_path)
    first = wb.submit_native(project, node.id)
    wait_run(wb, project, first.id)
    old_path, old_store = project.path, project.run_store
    old_bytes = old_path.read_bytes()
    destination = tmp_path / "copy.json"
    destination.write_bytes(b"previous destination document")
    new_store = destination.with_suffix(".runs")
    if failure == "existing":
        new_store.mkdir()
        (new_store / "keep.txt").write_bytes(b"destination history")
        expected = ValueError
    else:
        original = os.replace
        def refuse(source, target):
            if Path(target) == destination:
                raise OSError("injected document replace failure")
            return original(source, target)
        monkeypatch.setattr(os, "replace", refuse)
        expected = OSError
    with pytest.raises(expected):
        wb.save_native_project(project, destination)
    assert project.path == old_path and project.run_store == old_store
    assert old_path.read_bytes() == old_bytes
    assert destination.read_bytes() == b"previous destination document"
    assert (old_store / first.id / "job.json").is_file()
    if failure == "existing":
        assert (new_store / "keep.txt").read_bytes() == b"destination history"
    else:
        assert not new_store.exists()
    assert source.is_file() and wb.close()


def test_native_request_passes_cwd_environment_and_argv(tmp_path, monkeypatch):
    import caelab.connections as connections
    wb, project, node, _ = project_with_input(tmp_path)
    spec = next(s for s in PROGRAMS if s.id == "calculix")
    status = ProgramStatus(spec, sys.executable, source="registry", runtime_env={"CAELAB_TEST_RUNTIME": "from registry"})
    monkeypatch.setenv("CAELAB_TEST_RUNTIME", "explicit wins")
    real = connections.calculix_run_request
    script = "import json,os,sys; from pathlib import Path; print(json.dumps([sys.argv[1:],os.getcwd(),os.environ['CAELAB_TEST_RUNTIME']])); Path('model.frd').write_text('probe')"
    def request(found, workdir):
        native = real(found, workdir)
        return LaunchRequest([sys.executable, "-c", script, *native.argv[1:]], native.cwd, native.env)
    monkeypatch.setattr(wb, "programs", lambda: [status])
    monkeypatch.setattr(connections, "calculix_run_request", request)
    run = wait_run(wb, project, wb.submit_native(project, node.id).id)
    assert run.state == "completed"
    values = json.loads(Path(run.log_path).read_text().strip())
    assert values == [["-i", "model"], str(run.workdir), "explicit wins"]
    assert run.snapshot["execution"]["runtime_env_names"] == ["CAELAB_TEST_RUNTIME"]
    assert "explicit wins" not in json.dumps(run.snapshot)
    assert wb.close()


def test_actual_ccx_direct_and_service_keep_physics_and_outputs(tmp_path, monkeypatch):
    """Actual configured solver, independent raw DAT comparison; no auxiliary model."""
    import shutil
    executable = os.environ.get("CAELAB_CCX") or shutil.which("ccx")
    if not executable:
        pytest.skip("Actual CalculiX executable not configured/installed")
    monkeypatch.setenv("CAELAB_CCX", executable)
    wb, project, node, source = project_with_input(tmp_path)
    mesh = b"*NODE\n1,0,0,0\n2,10,0,0\n3,0,10,0\n4,0,0,10\n*ELEMENT,TYPE=C3D4,ELSET=BODY\n1,1,2,3,4\n"
    (source.parent / "mesh.inc").write_bytes(mesh)
    deck = b"""*HEADING
native execution equivalence
*INCLUDE,INPUT=mesh.inc
*MATERIAL,NAME=MAT
*ELASTIC
210000,0.3
*SOLID SECTION,ELSET=BODY,MATERIAL=MAT
*STEP
*STATIC
*BOUNDARY
1,1,3,0
2,2,3,0
3,3,3,0
*CLOAD
4,3,-100
*NODE PRINT,NSET=NALL
U,RF
*NODE FILE
U,RF
*END STEP
"""
    mesh += b"*NSET,NSET=NALL\n1,2,3,4\n"
    (source.parent / "mesh.inc").write_bytes(mesh)
    source.write_bytes(deck)
    (source.parent / "direct.inp").write_bytes(deck)
    direct = subprocess.run([executable, "-i", "direct"], cwd=source.parent,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
    assert direct.returncode == 0, direct.stdout.decode(errors="replace")
    submitted = wb.submit_native(project, node.id, timeout=30)
    run = wait_run(wb, project, submitted.id, timeout=35)
    assert run.state == "completed", run.error
    assert run.returncode == 0 and run.input_revision == file_revision(source)
    assert (run.workdir / "model.dat").read_bytes() == (source.parent / "direct.dat").read_bytes()
    assert (run.workdir / "model.frd").is_file()
    assert b"CalculiX Version" in Path(run.log_path).read_bytes()
    assert (run.workdir / "source/000000.inp").read_bytes() == deck
    assert b"*INCLUDE,INPUT=includes/000001.inp" in (run.workdir / "model.inp").read_bytes()
    (source.parent / "mesh.inc").write_bytes(mesh + b"** external change\n")
    assert wb.selected_native_run(project, node.id).stale
    assert wb.close()
