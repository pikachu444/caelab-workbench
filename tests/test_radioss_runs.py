"""Independent W3 run-boundary checks.

Snapshot checks use authored native bytes. Lifecycle probes explicitly simulate
solver processes. The opt-in checks run the installed official solver through
Workbench; they do not claim engineering validation of the two small fixtures.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from caelab.workbench import Workbench
from caelab.connections import PROGRAMS, LaunchRequest, ProgramStatus


FIXTURES = Path(__file__).parent / "fixtures" / "radioss_runs"


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _input_pair(directory, newline=b"\n"):
    directory.mkdir(parents=True, exist_ok=True)
    starter = directory / "independent_case_0000.rad"
    starter.write_bytes(newline.join([
        b"#RADIOSS STARTER", b"/BEGIN", b"independent_case", b"      2024",
        b"                  Mg                  mm                   s",
        b"                  Mg                  mm                   s",
        b"# user trailing spaces  ", b"#include nested/part.inc", b"/END", b"",
    ]))
    starter.with_name("independent_case_0001.rad").write_bytes(newline.join([
        b"#RADIOSS ENGINE", b"/RUN/independent_case/1", b"1e-6", b"/ANIM/DT", b"0 5e-7", b"",
    ]))
    (directory / "nested").mkdir()
    (directory / "nested" / "part.inc").write_bytes(newline.join([
        b"# nested include uses solver cwd", b"#include material.inc", b"#enddata",
        b"#include absent_after_enddata.inc", b"",
    ]))
    (directory / "material.inc").write_bytes(newline.join([
        b"/MAT/LAW1/413", b"Independent material", b"             7.8e-09",
        b"              210000                 0.3", b"",
    ]))
    # This is deliberately not the target of the nested #include.
    (directory / "nested" / "material.inc").write_bytes(b"WRONG parent-relative target\n")
    return starter


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["LF", "CRLF"])
def test_rad_snapshot_preserves_names_bytes_root_cwd_and_enddata(tmp_path, newline):
    from caelab.native.input_snapshot import snapshot_radioss_input

    source = _input_pair(tmp_path / "source with spaces", newline)
    run_dir = tmp_path / "owned run"
    snapshot = snapshot_radioss_input(source, run_dir)
    expected = {source.name, "independent_case_0001.rad", "nested/part.inc", "material.inc"}
    assert snapshot["execution_input"] == source.name
    assert snapshot["engine_input"] == "independent_case_0001.rad"
    assert snapshot["run_name"] == "independent_case"
    assert {item["execution_path"] for item in snapshot["files"]} == expected
    for item in snapshot["files"]:
        original = Path(item["source_path"])
        assert (run_dir / item["execution_path"]).read_bytes() == original.read_bytes()
        assert (run_dir / item["source_copy"]).read_bytes() == original.read_bytes()
        assert item["original_sha256"] == item["execution_sha256"] == _sha(original)
    original_snapshot = (run_dir / "material.inc").read_bytes()
    (source.parent / "material.inc").write_bytes(b"changed AFTER snapshot\n")
    assert (run_dir / "material.inc").read_bytes() == original_snapshot


@pytest.mark.parametrize("invalid", ["missing_engine", "missing_include", "cycle", "parent_escape"])
def test_rad_snapshot_rejects_unowned_or_incomplete_inputs(tmp_path, invalid):
    from caelab.native.input_snapshot import snapshot_radioss_input

    source = _input_pair(tmp_path / "source")
    if invalid == "missing_engine":
        source.with_name("independent_case_0001.rad").unlink()
    elif invalid == "missing_include":
        (source.parent / "material.inc").unlink()
    elif invalid == "cycle":
        (source.parent / "material.inc").write_bytes(b"#include nested/part.inc\n")
    else:
        (tmp_path / "outside.inc").write_bytes(b"external input\n")
        (source.parent / "material.inc").write_bytes(b"#include ../outside.inc\n")
    with pytest.raises(ValueError):
        snapshot_radioss_input(source, tmp_path / "run")


@pytest.mark.parametrize("relative", ["STARTER.LOG", "JOB.JSON", ".SOURCE/raw.inc", "Results/mesh.inc"])
def test_rad_snapshot_rejects_casefold_reserved_paths_for_portable_runs(tmp_path, relative):
    from caelab.native.input_snapshot import snapshot_radioss_input

    source = _input_pair(tmp_path / "source")
    target = source.parent / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"# valid include bytes, reserved execution destination\n")
    (source.parent / "material.inc").write_bytes(b"#include " + relative.encode() + b"\n")
    with pytest.raises(ValueError):
        snapshot_radioss_input(source, tmp_path / "run")


@pytest.mark.parametrize("explicit_starter", [False, True], ids=["installed-home", "explicit-starter"])
def test_radioss_discovery_resolves_two_executables_and_rejects_missing_engine(tmp_path, monkeypatch, explicit_starter):
    from caelab.connections import program_statuses, radioss_run_requests

    home = tmp_path / "installation with spaces"
    executable_dir = home / "exec"
    executable_dir.mkdir(parents=True)
    suffix = "win64.exe" if os.name == "nt" else "linux64_gf"
    starter, engine = [executable_dir / f"{stage}_{suffix}" for stage in ("starter", "engine")]
    # Discovery-only placeholders are never launched as solvers.
    for executable in (starter, engine):
        executable.write_bytes(b"discovery placeholder")
        executable.chmod(0o755)
    monkeypatch.delenv("RAD_CFG_PATH", raising=False)
    monkeypatch.setenv("CAELAB_OPENCOURANT", str(starter if explicit_starter else home))
    status = next(item for item in program_statuses() if item.spec.id == "opencourant")
    assert status.available and Path(status.path) == starter
    requests = radioss_run_requests(status, tmp_path / "run", Path("arbitrary_0000.rad"))
    assert [name for name, _ in requests] == ["starter", "engine"]
    assert [Path(request.argv[0]) for _, request in requests] == [starter, engine]
    assert "arbitrary_0000.rad" in requests[0][1].argv and "arbitrary_0001.rad" in requests[1][1].argv
    assert Path(requests[0][1].env["RAD_CFG_PATH"]) == home / "hm_cfg_files"
    engine.unlink()
    status = next(item for item in program_statuses() if item.spec.id == "opencourant")
    assert not status.available and "engine_" in status.reason


def _project(tmp_path, source):
    wb = Workbench()
    project = wb.new_native_project("Independent Radioss run")
    node = wb.add_program_node(project, "analysis", "opencourant")
    wb.set_node_file(project, node.id, source,
                     units={"mass": "Mg", "length": "mm", "time": "s"}, unit_source="explicit input")
    wb.save_native_project(project, tmp_path / "project.json")
    return wb, project, node


def _wait(wb, project, identifier, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = next(item for item in wb.native_runs(project) if item.id == identifier)
        if run.finished_at:
            return run
        time.sleep(.02)
    raise AssertionError(f"Run {identifier} did not terminate within {timeout} seconds")


def _assert_output_reference(project, node, run):
    reference = project.nodes[node.id].files.get("output")
    assert reference is not None, "A completed RAD run must expose its preserved native result reference"
    relative = Path(reference.path).relative_to(run.workdir).as_posix()
    assert relative in run.snapshot["outputs"]
    assert reference.revision == run.snapshot["outputs"][relative] == _sha(reference.path)
    assert Path(reference.path).name != "job.log"


@pytest.fixture
def radioss_probe(monkeypatch):
    """Real Python subprocesses simulate solver behavior; no real solver claim."""
    import caelab.connections as connections

    spec = next(item for item in PROGRAMS if item.id == "opencourant")
    status = ProgramStatus(spec, sys.executable, source="simulated solver process")
    monkeypatch.setattr(Workbench, "programs", lambda self: [status])
    control = {"mode": "normal"}
    code = r'''
from pathlib import Path
import sys, time
stage, mode, prefix = sys.argv[1:]
with Path('probe-events.txt').open('a') as stream:
    stream.write(stage + '\n')
print(stage + ' process started', flush=True)
if mode == stage + '_wait':
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with Path(stage + '-heartbeat').open('ab') as heartbeat:
            heartbeat.write(b'.')
        time.sleep(.02)
if mode == stage + '_fail':
    print(stage + ' deliberately failed', flush=True)
    raise SystemExit(9)
listing = Path(prefix + ('_0000.out' if stage == 'starter' else '_0001.out'))
listing.write_text('FATAL ERROR\n' if mode == stage + '_bad_listing' else
                   'TERMINATION WITH WARNING\n0 ERROR(S)\n1 WARNING(S)\n' if stage == 'starter' and mode == 'starter_warning' else
                   'NORMAL TERMINATION\n0 ERROR(S)\n0 WARNING(S)\n')
if stage == 'starter' and mode != 'starter_missing_restart':
    Path(prefix + '_0000_0001.rst').write_bytes(b'opaque restart probe')
if stage == 'engine':
    Path(prefix + 'A001').write_bytes(b'\x00\x01Opaque native animation probe\xff')
    if mode == 'engine_mutates_input':
        Path('material.inc').write_bytes(b'# overwritten by a normally exiting external process\n')
print(stage + ' process ended', flush=True)
'''

    def requests(found, workdir, input_path):
        prefix = Path(input_path).name[:-9]
        return [(stage, LaunchRequest([sys.executable, "-c", code, stage, control["mode"], prefix],
                                      Path(workdir), dict(os.environ), "opencourant"))
                for stage in ("starter", "engine")]
    monkeypatch.setattr(connections, "radioss_run_requests", requests)
    return control


def _await_stage(wb, project, identifier, name):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        run = next(item for item in wb.native_runs(project) if item.id == identifier)
        if any(stage["name"] == name and stage["state"] == "running" for stage in run.snapshot.get("stages", [])):
            return run
        assert not run.finished_at, run.error
        time.sleep(.01)
    raise AssertionError(f"{name} never started")


@pytest.mark.parametrize("mode,expected_stages", [
    ("starter_fail", ["starter"]),
    ("starter_bad_listing", ["starter"]),
    ("starter_missing_restart", ["starter"]),
    ("engine_fail", ["starter", "engine"]),
    ("engine_bad_listing", ["starter", "engine"]),
])
def test_radioss_stage_failure_stops_pipeline_and_keeps_receipts(tmp_path, radioss_probe, mode, expected_stages):
    source = _input_pair(tmp_path / "source")
    wb, project, node = _project(tmp_path, source)
    radioss_probe["mode"] = mode
    try:
        submitted = wb.submit_native(project, node.id, timeout=10)
        run = _wait(wb, project, submitted.id)
        assert run.state == "failed" and run.error
        assert (run.workdir / "probe-events.txt").read_text().splitlines() == expected_stages
        stages = [stage for stage in run.snapshot["stages"] if stage["started_at"]]
        assert [stage["name"] for stage in stages] == expected_stages
        assert stages[-1]["state"] == "failed" and stages[-1]["error"]
        assert all(stage["started_at"] and stage["finished_at"] for stage in stages)
        assert all((run.workdir / stage["log_path"]).is_file() for stage in stages)
        for stage in run.snapshot["stages"]:
            if not stage["started_at"]:
                assert stage["state"] == "not_started" and stage["error"]
                assert stage["pid"] is None and stage["returncode"] is None
                assert stage["finished_at"]
        assert "output" not in project.nodes[node.id].files
    finally:
        wb.close()


@pytest.mark.parametrize("stage,text", [
    ("starter", "ERROR TERMINATION\nTERMINATION WITH WARNING\n0 ERROR(S)\n1 WARNING(S)\n"),
    ("starter", "TERMINATION WITH WARNING\n0 ERROR(S)\n1 WARNING(S)\n1 ERROR(S) reported earlier\n"),
    ("starter", "TERMINATION WITH WARNING\n1 WARNING(S)\n"),
    ("engine", "TERMINATION WITH WARNING\n0 ERROR(S)\n1 WARNING(S)\n"),
], ids=["error-marker", "positive-errors", "missing-error-count", "engine-warning"])
def test_warning_marker_never_overrides_stage_failure(tmp_path, stage, text):
    from caelab.native.adapter import OpenRadiossAdapter

    sequence = "0000" if stage == "starter" else "0001"
    (tmp_path / f"case_{sequence}.out").write_text(text)
    (tmp_path / "case_0000_0001.rst").write_bytes(b"restart")
    with pytest.raises(ValueError):
        OpenRadiossAdapter().validate_stage(stage, tmp_path, {"run_name": "case"})


def test_radioss_rejects_existing_stage_outputs_before_any_process(tmp_path, radioss_probe):
    from caelab.native.adapter import OpenRadiossAdapter

    status = ProgramStatus(next(spec for spec in PROGRAMS if spec.id == "opencourant"), sys.executable,
                           source="simulated solver process")
    snapshot = {"run_name": "case", "execution_input": "case_0000.rad"}
    for name in ("case_0000.out", "case_0001.out", "case_0000_0001.rst"):
        folder = tmp_path / name
        folder.mkdir()
        existing = folder / name
        existing.write_bytes(b"must not delete or overwrite")
        with pytest.raises(ValueError, match="기존.*출력"):
            OpenRadiossAdapter().build_run_stages(status, folder, snapshot)
        assert existing.read_bytes() == b"must not delete or overwrite"
        assert not (folder / "probe-events.txt").exists()


def test_starter_warning_continues_engine_and_survives_reopen(tmp_path, radioss_probe):
    wb, project, node = _project(tmp_path, _input_pair(tmp_path / "source"))
    radioss_probe["mode"] = "starter_warning"
    try:
        submitted = wb.submit_native(project, node.id, timeout=10)
        run = _wait(wb, project, submitted.id)
        assert run.state == "completed", run.error
        assert run.parse_state == "partial"
        assert [(stage["name"], stage["returncode"]) for stage in run.snapshot["stages"]] == [("starter", 0), ("engine", 0)]
        diagnostic = run.snapshot["stages"][0]["diagnostics"]
        assert diagnostic["termination"] == "warning"
        assert diagnostic["error_count"] == 0 and diagnostic["warning_count"] == 1
        assert diagnostic["listing"] == "independent_case_0000.out"
        assert len(diagnostic["messages"]) == 1 and "모델 검토 필요" in diagnostic["messages"][0]
        assert diagnostic["messages"][0] in run.result["warnings"]
        assert any(message not in diagnostic["messages"] for message in run.result["warnings"])
        assert (run.workdir / "probe-events.txt").read_text().splitlines() == ["starter", "engine"]
        saved = wb.save_native_project(project, project.path)
        assert wb.close()
        reopened = Workbench()
        try:
            restored = reopened.selected_native_run(reopened.open_native_project(saved), node.id)
            assert restored.snapshot["stages"][0]["diagnostics"] == diagnostic
            assert restored.result["warnings"] == run.result["warnings"] and restored.parse_state == "partial"
        finally:
            assert reopened.close()
    finally:
        assert wb.close()


@pytest.mark.parametrize("stage_name", ["starter", "engine"])
def test_radioss_cancel_owns_each_stage_and_does_not_run_next(tmp_path, radioss_probe, stage_name):
    wb, project, node = _project(tmp_path, _input_pair(tmp_path / "source"))
    radioss_probe["mode"] = stage_name + "_wait"
    try:
        submitted = wb.submit_native(project, node.id, timeout=20)
        _await_stage(wb, project, submitted.id, stage_name)
        assert wb.cancel_native_run(submitted.id)
        run = _wait(wb, project, submitted.id)
        assert run.state == "cancelled"
        stages = [stage for stage in run.snapshot["stages"] if stage["started_at"]]
        assert stages[-1]["name"] == stage_name and stages[-1]["state"] == "cancelled"
        assert stages[-1]["finished_at"]
        assert len(stages) == (1 if stage_name == "starter" else 2)
        for stage in run.snapshot["stages"]:
            if not stage["started_at"]:
                assert stage["state"] == "not_started" and stage["error"]
                assert stage["pid"] is None and stage["returncode"] is None
        assert "output" not in project.nodes[node.id].files
    finally:
        wb.close()


@pytest.mark.parametrize("stage_name", ["starter", "engine"])
def test_radioss_timeout_is_terminal_and_releases_run_for_retry(tmp_path, radioss_probe, monkeypatch, stage_name):
    import caelab.jobs as jobs

    # Target a running stage without charging OS/process startup to this deadline.
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(jobs, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=time.sleep))
    wb, project, node = _project(tmp_path, _input_pair(tmp_path / "source"))
    radioss_probe["mode"] = stage_name + "_wait"
    try:
        submitted = wb.submit_native(project, node.id, timeout=.5)
        running = _await_stage(wb, project, submitted.id, stage_name)
        heartbeat = running.workdir / (stage_name + "-heartbeat")
        ready_deadline = time.monotonic() + 10
        while not heartbeat.is_file() or not heartbeat.read_bytes():
            assert time.monotonic() < ready_deadline, "Solver probe did not start"
            time.sleep(.01)
        with wb.jobs._native_lock:
            owned_process = wb.jobs._controls[submitted.id]["process"]
            assert owned_process is not None and owned_process.poll() is None
        clock.now = 1.0
        failed = _wait(wb, project, submitted.id)
        assert failed.state == "failed" and "TimeoutError" in failed.error
        stages = failed.snapshot["stages"]
        timed_out = next(stage for stage in stages if stage["name"] == stage_name)
        assert timed_out["state"] == "failed" and "TimeoutError" in timed_out["error"]
        assert timed_out["started_at"] and timed_out["finished_at"] and timed_out["pid"]
        expected_stages = ["starter"] if stage_name == "starter" else ["starter", "engine"]
        assert (failed.workdir / "probe-events.txt").read_text().splitlines() == expected_stages
        if stage_name == "starter":
            assert stages[1]["state"] == "not_started" and stages[1]["pid"] is None
        else:
            assert stages[0]["state"] == "completed"
        assert owned_process.poll() is not None
        last_heartbeat = heartbeat.read_bytes()
        time.sleep(.15)
        assert heartbeat.read_bytes() == last_heartbeat
        assert "output" not in project.nodes[node.id].files
        old_files = {name: (failed.workdir / name).read_bytes() for name in ("job.json", "job.log")}
        radioss_probe["mode"] = "normal"
        retried = wb.submit_native(project, node.id, timeout=10)
        completed = _wait(wb, project, retried.id)
        assert completed.state == "completed" and completed.parse_state == "partial"
        assert wb.selected_native_run(project, node.id).id == completed.id
        _assert_output_reference(project, node, completed)
        assert completed.workdir != failed.workdir
        assert len(wb.native_runs(project, node.id)) == 2
        assert wb.selected_native_run(project, node.id).id == completed.id
        assert wb.jobs.native_snapshot(failed.id).state == "failed"
        assert wb.jobs.native_snapshot(failed.id).snapshot == failed.snapshot
        for name, original in old_files.items():
            assert (failed.workdir / name).read_bytes() == original
    finally:
        assert wb.close()


def test_radioss_rerun_keeps_old_snapshot_and_detects_include_and_engine_changes(tmp_path, radioss_probe):
    source = _input_pair(tmp_path / "source")
    wb, project, node = _project(tmp_path, source)
    try:
        first = _wait(wb, project, wb.submit_native(project, node.id, timeout=10).id)
        assert first.state == "completed" and first.parse_state == "partial"
        saved_include = (first.workdir / "material.inc").read_bytes()
        saved_engine = (first.workdir / "independent_case_0001.rad").read_bytes()
        for path in (source.parent / "material.inc", source.with_name("independent_case_0001.rad")):
            path.write_bytes(path.read_bytes() + b"# changed after completed run\n")
            assert wb.selected_native_run(project, node.id).stale
        assert (first.workdir / "material.inc").read_bytes() == saved_include
        assert (first.workdir / "independent_case_0001.rad").read_bytes() == saved_engine
        second = _wait(wb, project, wb.submit_native(project, node.id, timeout=10).id)
        assert second.state == "completed" and not second.stale
        assert second.id != first.id and second.workdir != first.workdir
        assert (second.workdir / "material.inc").read_bytes() != saved_include
        assert len(wb.native_runs(project, node.id)) == 2
    finally:
        wb.close()


def test_registered_starter_change_is_rejected_before_launch_until_reregistered(tmp_path, radioss_probe):
    source = _input_pair(tmp_path / "source")
    wb, project, node = _project(tmp_path, source)
    try:
        source.write_bytes(source.read_bytes() + b"# changed since Workbench registered this source\n")
        failed = _wait(wb, project, wb.submit_native(project, node.id, timeout=10).id)
        assert failed.state == "failed" and failed.error
        assert not (failed.workdir / "probe-events.txt").exists()
        assert not any(stage["started_at"] for stage in failed.snapshot.get("stages", []))
        wb.set_node_file(project, node.id, source,
                         units={"mass": "Mg", "length": "mm", "time": "s"}, unit_source="explicit input")
        completed = _wait(wb, project, wb.submit_native(project, node.id, timeout=10).id)
        assert completed.state == "completed" and completed.input_revision == _sha(source)
    finally:
        wb.close()


def test_normal_exit_cannot_validate_a_run_that_modified_its_execution_input(tmp_path, radioss_probe):
    source = _input_pair(tmp_path / "source")
    source_files = [source, source.with_name("independent_case_0001.rad"),
                    source.parent / "material.inc", source.parent / "nested" / "part.inc"]
    originals = {path: path.read_bytes() for path in source_files}
    wb, project, node = _project(tmp_path, source)
    radioss_probe["mode"] = "engine_mutates_input"
    try:
        run = _wait(wb, project, wb.submit_native(project, node.id, timeout=10).id)
        assert [(stage["name"], stage["returncode"]) for stage in run.snapshot["stages"]] == [
            ("starter", 0), ("engine", 0)]
        assert run.state == "failed" and "material.inc" in run.error
        assert run.parse_state == "unparsed" and not run.result
        assert "output" not in project.nodes[node.id].files
        material = next(item for item in run.snapshot["files"] if item["execution_path"] == "material.inc")
        assert _sha(run.workdir / "material.inc") != material["execution_sha256"]
        assert _sha(run.workdir / material["source_copy"]) == material["original_sha256"]
        for path, original in originals.items():
            assert path.read_bytes() == original
        for item in run.snapshot["files"]:
            assert (run.workdir / item["source_copy"]).read_bytes() == originals[Path(item["source_path"])]
    finally:
        wb.close()


def _actual_bundles():
    configured = os.environ.get("CAELAB_OPENCOURANT_TEST_BUNDLES")
    if configured:
        values = json.loads(configured)
        if not isinstance(values, list) or not values or not all(isinstance(v, str) for v in values):
            raise ValueError("CAELAB_OPENCOURANT_TEST_BUNDLES must be a nonempty JSON list of Starter paths")
        return [Path(value) for value in values]
    return [FIXTURES / "tetra" / "tetcase_0000.rad", FIXTURES / "shell_include" / "conditions_0000.rad"]


@pytest.mark.skipif(not os.environ.get("CAELAB_OPENCOURANT"),
                    reason="Set CAELAB_OPENCOURANT explicitly to run the real Workbench Starter/Engine path.")
@pytest.mark.parametrize("source", _actual_bundles(), ids=lambda path: path.stem)
def test_actual_radioss_workbench_two_stage_inputs_outputs_and_history(tmp_path, source):
    """Real solver boundary check, not stress/contact accuracy or animation decoding."""
    source = source.resolve()
    wb, project, node = _project(tmp_path, source)
    try:
        submitted = wb.submit_native(project, node.id, timeout=60)
        run = _wait(wb, project, submitted.id, timeout=65)
        assert run.state == "completed", run.error + "\n" + Path(run.log_path).read_text(errors="replace")
        assert [(stage["name"], stage["state"], stage["returncode"])
                for stage in run.snapshot["stages"]] == [("starter", "completed", 0), ("engine", "completed", 0)]
        prefix = source.name[:-9]
        assert (run.workdir / f"{prefix}_0000_0001.rst").is_file()
        assert "NORMAL TERMINATION" in (run.workdir / f"{prefix}_0000.out").read_text(errors="replace")
        assert "NORMAL TERMINATION" in (run.workdir / f"{prefix}_0001.out").read_text(errors="replace")
        assert run.parse_state == "partial"  # Full native animation decoding is outside W3.
        assert run.result["warnings"]
        raw_outputs = [path for path in run.workdir.iterdir()
                       if path.name.startswith(prefix) and path.suffix not in {".rad", ".inc"}]
        assert any(path.name.endswith("A001") for path in raw_outputs)
        for path in raw_outputs:
            assert run.snapshot["artifacts"][path.name] == _sha(path)
        for item in run.snapshot["files"]:
            assert _sha(item["source_path"]) == item["original_sha256"]
            assert _sha(run.workdir / item["execution_path"]) == item["execution_sha256"]
        selected = wb.selected_native_run(project, node.id)
        assert selected.id == run.id and not selected.stale
        _assert_output_reference(project, node, selected)
        saved = wb.save_native_project(project, tmp_path / "project.json")
        assert wb.close()
        wb = Workbench()
        reopened = wb.open_native_project(saved)
        restored = wb.selected_native_run(reopened, node.id)
        assert restored.id == run.id and restored.snapshot == run.snapshot
        assert restored.state == "completed" and restored.parse_state == "partial"
    finally:
        wb.close()
