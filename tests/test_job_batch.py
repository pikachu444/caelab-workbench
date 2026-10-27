"""Unit and integration tests for JobManager asynchronous batch execution.

Covers:
- Sequential native batch execution and state lifecycle
- Real-time heartbeat progress emissions and ETA calculation
- Cancellation with Windows Kernel Job Object process tree termination
- In-flight scalar extraction via on_item_finished hook
- Scratch storage cleanup policy for non-optimal points
- Store isolation under project.run_store / mdo_runs / <batch_id>
"""

import json
import os
from pathlib import Path
import sys
import time
import types
import pytest

from caelab.connections import LaunchRequest, ProgramStatus, ProgramSpec
from caelab.jobs import JobManager, BatchItem, BatchProgress, BatchRun
from caelab.native.run_store import RunStore
from caelab.workbench import Workbench
from caelab.mdo.responses import ResponseSpec, extract_all_responses


@pytest.fixture
def dummy_status():
    spec = ProgramSpec("calculix", "CalculiX", "solver", "CAELAB_CALCULIX", ("ccx",), "CalculiX solver")
    return ProgramStatus(spec, sys.executable, source="batch probe")


@pytest.fixture
def setup_probe(monkeypatch):
    """Configures calculix runner to execute python snippet probes."""
    import caelab.connections as connections

    script_holder = ["print('probe run', flush=True)"]

    def fake_request(status, workdir):
        return LaunchRequest(
            [sys.executable, "-c", script_holder[0]],
            workdir,
            dict(os.environ),
            "calculix",
        )

    monkeypatch.setattr(connections, "calculix_run_request", fake_request)

    def fake_collect(path, check_cancelled=lambda: None):
        check_cancelled()
        return {"status": "readable", "frames": [], "exchange_files": [], "warnings": [], "summary": {}}

    monkeypatch.setitem(sys.modules, "caelab.native.results", types.SimpleNamespace(collect_results=fake_collect))
    return script_holder


def create_deck(tmp_path: Path, name: str = "deck.inp") -> Path:
    p = tmp_path / name
    p.write_text("*HEADING\nBatch Deck\n*NODE\n1, 0., 0., 0.\n", encoding="utf-8")
    return p


def test_stop_scheduling_finishes_current_without_cancelling_owned_process(tmp_path,dummy_status,setup_probe):
    setup_probe[0] = "import time; print('owned current',flush=True); time.sleep(.3)"
    manager = JobManager()
    deck = create_deck(tmp_path)
    batch = manager.submit_batch(RunStore(tmp_path/"runs"),[
        BatchItem(item_id=str(i),input_path=deck,status=dummy_status) for i in range(3)])
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        snapshot = manager.batch_snapshot(batch.id)
        if snapshot.items[0].state == "running":
            break
        time.sleep(.01)
    assert manager.stop_batch(batch.id)
    while time.monotonic() < deadline:
        snapshot = manager.batch_snapshot(batch.id)
        if snapshot.state == "stopped":
            break
        time.sleep(.02)
    assert snapshot.state == "stopped"
    assert [i.state for i in snapshot.items] == ["completed","stopped","stopped"]
    assert snapshot.items[0].run_id and snapshot.items[1].run_id is None
    assert snapshot.cancelled_count == 0
    assert manager.close()


def test_batch_submit_and_complete_sequential(tmp_path, dummy_status, setup_probe):
    setup_probe[0] = (
        "import time, sys; from pathlib import Path; "
        "time.sleep(0.05); "
        "Path('model.dat').write_text('MAX VON MISES STRESS: 150.0\\n', encoding='utf-8'); "
        "print('done item', flush=True)"
    )

    deck = create_deck(tmp_path)
    store = RunStore(tmp_path / "runs")
    jm = JobManager()

    items = [
        BatchItem(item_id=f"DP_{i:04d}", input_path=deck, status=dummy_status, parameters={"t": float(i)})
        for i in range(1, 4)
    ]

    progress_events: list[BatchProgress] = []
    finished_items: list[str] = []

    batch = jm.submit_batch(
        store,
        items,
        name="Sequential 3-Point Batch",
        on_progress=lambda p: progress_events.append(p),
        on_item_finished=lambda it, run: finished_items.append(it.item_id),
    )

    assert batch.state == "queued"
    assert batch.total_count == 3

    # Wait for completion
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        snap = jm.batch_snapshot(batch.id)
        if snap and snap.state in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)
    else:
        pytest.fail("Batch did not finish within deadline")

    final_batch = jm.batch_snapshot(batch.id)
    assert final_batch is not None
    assert final_batch.state == "completed"
    assert final_batch.completed_count == 3
    assert final_batch.failed_count == 0
    assert final_batch.cancelled_count == 0

    assert len(finished_items) == 3
    assert finished_items == ["DP_0001", "DP_0002", "DP_0003"]

    # Verify each item state and duration
    for it in final_batch.items:
        assert it.state == "completed"
        assert it.run_id is not None
        assert it.duration_sec >= 0.04
        assert not it.error

    # Verify progress events and heartbeat format
    assert len(progress_events) >= 3
    last_prog = progress_events[-1]
    assert last_prog.completed_count == 3
    assert last_prog.total_count == 3
    assert "DONE" in last_prog.heartbeat_message or "3 /   3 Points" in last_prog.heartbeat_message

    assert jm.close()


def test_batch_cancel_terminates_active_process_and_marks_remaining(tmp_path, dummy_status, setup_probe):
    # Solver that sleeps for 60s
    setup_probe[0] = "import time; print('sleeping probe', flush=True); time.sleep(60)"

    deck = create_deck(tmp_path)
    store = RunStore(tmp_path / "runs")
    jm = JobManager()

    items = [
        BatchItem(item_id=f"DP_{i:04d}", input_path=deck, status=dummy_status)
        for i in range(1, 4)
    ]

    batch = jm.submit_batch(store, items, name="Cancel Test Batch")

    # Wait until first item is RUNNING
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snap = jm.batch_snapshot(batch.id)
        if snap and snap.items[0].state == "running":
            break
        time.sleep(0.05)
    else:
        pytest.fail("First item never transitioned to running")

    # Cancel the batch
    t0 = time.monotonic()
    assert jm.cancel_batch(batch.id)

    # Wait for batch to transition to cancelled
    while time.monotonic() < deadline:
        snap = jm.batch_snapshot(batch.id)
        if snap and snap.state == "cancelled":
            break
        time.sleep(0.05)
    else:
        pytest.fail("Batch did not enter cancelled state in time")

    cancel_duration = time.monotonic() - t0
    # Kernel process kill + cancellation should occur rapidly (< 3.0s)
    assert cancel_duration < 3.0

    snap = jm.batch_snapshot(batch.id)
    assert snap.state == "cancelled"
    # Item 0 was running, cancelled mid-flight
    assert snap.items[0].state == "cancelled"
    # Items 1 and 2 were never dispatched, marked cancelled
    assert snap.items[1].state == "cancelled"
    assert snap.items[2].state == "cancelled"
    assert snap.cancelled_count == 3

    assert jm.close()


def test_batch_cleanup_policy_deletes_scratch_fields_for_non_optimal_points(tmp_path, dummy_status, setup_probe):
    setup_probe[0] = (
        "from pathlib import Path; "
        "Path('model.dat').write_text('DISPLACEMENT: 0.12\\n', encoding='utf-8'); "
        "Path('model.frd').write_text('LARGE FRD FIELD DATA ' * 1000, encoding='utf-8'); "
        "print('done', flush=True)"
    )

    deck = create_deck(tmp_path)
    store = RunStore(tmp_path / "runs")
    jm = JobManager()

    # Point 2 is our "knee/best point", points 1 and 3 are intermediate non-optimal points
    knee_point_id = "DP_0002"

    def cleanup_scratch_files(item: BatchItem, run):
        if item.item_id != knee_point_id:
            frd_file = run.workdir / "model.frd"
            if frd_file.exists():
                frd_file.unlink()
                run.snapshot.get("outputs", {}).pop("model.frd", None)
                run.snapshot.get("artifacts", {}).pop("model.frd", None)
                store.write(run)

    items = [
        BatchItem(item_id=f"DP_{i:04d}", input_path=deck, status=dummy_status)
        for i in range(1, 4)
    ]

    batch = jm.submit_batch(
        store,
        items,
        name="Cleanup Test",
        cleanup_policy=cleanup_scratch_files,
    )

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        snap = jm.batch_snapshot(batch.id)
        if snap and snap.state == "completed":
            break
        time.sleep(0.05)

    snap = jm.batch_snapshot(batch.id)
    assert snap.state == "completed"

    run_dirs = {it.item_id: store.root / it.run_id for it in snap.items}

    # DP_0001: model.dat preserved, model.frd cleaned up
    assert (run_dirs["DP_0001"] / "model.dat").exists()
    assert not (run_dirs["DP_0001"] / "model.frd").exists()

    # DP_0002 (Knee): both model.dat AND model.frd preserved!
    assert (run_dirs["DP_0002"] / "model.dat").exists()
    assert (run_dirs["DP_0002"] / "model.frd").exists()

    # DP_0003: model.dat preserved, model.frd cleaned up
    assert (run_dirs["DP_0003"] / "model.dat").exists()
    assert not (run_dirs["DP_0003"] / "model.frd").exists()

    assert jm.close()


def test_batch_on_item_finished_scalar_response_extraction(tmp_path, dummy_status, setup_probe):
    setup_probe[0] = (
        "from pathlib import Path; "
        "dat = ('TOTAL MASS: 12.45 kg\\n' "
        "       'MAX VON MISES STRESS: 215.80 MPa\\n' "
        "       'TIP DISPLACEMENT: 3.42 mm\\n'); "
        "Path('model.dat').write_text(dat, encoding='utf-8'); "
        "print('extracted probe done', flush=True)"
    )

    deck = create_deck(tmp_path)
    store = RunStore(tmp_path / "runs")
    jm = JobManager()

    response_specs = {
        "mass": ResponseSpec(id="mass", name="Mass", source_file="model.dat", pattern=r"TOTAL MASS:\s*([0-9.]+)"),
        "stress": ResponseSpec(id="stress", name="Stress", source_file="model.dat", pattern=r"MAX VON MISES STRESS:\s*([0-9.]+)"),
        "disp": ResponseSpec(id="disp", name="Displacement", source_file="model.dat", pattern=r"TIP DISPLACEMENT:\s*([0-9.]+)"),
    }

    def on_finished(item: BatchItem, run):
        scalars = extract_all_responses(response_specs, run.workdir)
        item.scalar_responses = scalars

    items = [
        BatchItem(item_id="DP_0001", input_path=deck, status=dummy_status),
        BatchItem(item_id="DP_0002", input_path=deck, status=dummy_status),
    ]

    batch = jm.submit_batch(
        store,
        items,
        name="Scalar Extraction Batch",
        on_item_finished=on_finished,
    )

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        snap = jm.batch_snapshot(batch.id)
        if snap and snap.state == "completed":
            break
        time.sleep(0.05)

    snap = jm.batch_snapshot(batch.id)
    assert snap.state == "completed"

    for it in snap.items:
        assert it.scalar_responses["mass"] == pytest.approx(12.45)
        assert it.scalar_responses["stress"] == pytest.approx(215.80)
        assert it.scalar_responses["disp"] == pytest.approx(3.42)

    assert jm.close()


def test_batch_failure_modes_and_stop_on_failure(tmp_path, dummy_status, setup_probe):
    # Step 1: Probe fails with exit code 7
    setup_probe[0] = "import sys; print('failing probe', flush=True); sys.exit(7)"

    deck = create_deck(tmp_path)
    store = RunStore(tmp_path / "runs")
    jm = JobManager()

    # Case A: stop_on_failure = False -> runs all items even if earlier fail
    items = [
        BatchItem(item_id="DP_FAIL", input_path=deck, status=dummy_status),
        BatchItem(item_id="DP_FAIL2", input_path=deck, status=dummy_status),
    ]

    batch = jm.submit_batch(store, items, stop_on_failure=False)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        snap = jm.batch_snapshot(batch.id)
        if snap and snap.state in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)

    snap = jm.batch_snapshot(batch.id)
    assert snap.state == "failed"
    assert snap.failed_count == 2
    assert snap.items[0].state == "failed"
    assert snap.items[1].state == "failed"

    # Case B: stop_on_failure = True -> stops immediately after first failure
    items_stop = [
        BatchItem(item_id="DP_FAIL1", input_path=deck, status=dummy_status),
        BatchItem(item_id="DP_NEVER_RUN", input_path=deck, status=dummy_status),
    ]
    batch2 = jm.submit_batch(store, items_stop, stop_on_failure=True)

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        snap2 = jm.batch_snapshot(batch2.id)
        if snap2 and snap2.state in {"completed", "failed", "cancelled"}:
            break
        time.sleep(0.05)

    snap2 = jm.batch_snapshot(batch2.id)
    assert snap2.state == "failed"
    assert snap2.items[0].state == "failed"
    # Second item remained queued (never started)
    assert snap2.items[1].state == "queued"

    assert jm.close()


def test_workbench_submit_batch_isolated_store(tmp_path, dummy_status, setup_probe):
    setup_probe[0] = "from pathlib import Path; print('workbench batch probe', flush=True)"

    wb = Workbench()
    project = wb.new_native_project("Batch Test Project")
    node = wb.add_program_node(project, "analysis", "calculix")
    source = tmp_path / "model.inp"
    source.write_text("*HEADING\nWorkbench batch test\n", encoding="utf-8")
    wb.set_node_file(project, node.id, source)
    wb.save_native_project(project, tmp_path / "project.json")

    items = [
        BatchItem(item_id=f"DP_{i:04d}", input_path=source, status=dummy_status, node_id=node.id)
        for i in range(1, 3)
    ]

    batch = wb.submit_batch(project, items, name="Workbench Isolated Batch")

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        snap = wb.batch_snapshot(batch.id)
        if snap and snap.state == "completed":
            break
        time.sleep(0.05)

    snap = wb.batch_snapshot(batch.id)
    assert snap.state == "completed"
    assert snap.completed_count == 2

    # Verify storage isolation:
    # Batch runs must be stored inside project.run_store / mdo_runs / <batch.id>
    expected_mdo_store = project.run_store / "mdo_runs" / batch.id
    assert expected_mdo_store.is_dir()
    for it in snap.items:
        run_json = expected_mdo_store / it.run_id / "job.json"
        assert run_json.is_file()

    # The top-level project.run_store should NOT contain these run folders
    for it in snap.items:
        assert not (project.run_store / it.run_id).exists()

    assert wb.close()
