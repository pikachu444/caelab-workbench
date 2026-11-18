"""D32 program connections, launch arguments, snapshot/receipts, stale invalidation and MDO service boundaries.

CAD, PMX, INP, FRD and DAT fixtures are synthetic. The job boundary records
launch requests and provides fixture output for the real receipt readers.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from caelab.artifact_refs import NumericResultV1
from caelab.result_readers import ResultError
from caelab.assist import experts
from caelab.connections import PROGRAMS, ProgramStatus, _status
from caelab.jobs import BatchRun, LaunchRecord
from caelab import mdo
from caelab.mdo import apply_transaction as transaction
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.optimize import OptimizationConfig
from caelab.mdo.surrogate import SurrogateConfig
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.native.adapter import get_solver_adapter
from caelab.native.input_snapshot import fingerprint
from caelab.native.results import collect_results
from caelab.native.run_store import NativeRun, RunStore, utc_now
from caelab.workbench import Workbench
from test_native_results import _field, _geometry


_TETRA_NODES = (1, 2, 3, 4)
_TETRA_XYZ = ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1))


def _tetra_geometry():
    return _geometry(ids=_TETRA_NODES, connectivity=_TETRA_NODES, coordinates=_TETRA_XYZ, element_id=1)


class _E2EBoundaryJobs:
    """Use real snapshots and result collection with synthetic files and recorded launch arguments."""

    def __init__(self, real_jobs):
        self._real_jobs = real_jobs
        self.launches: list = []
        self.native_count = 0
        self.batch_count = 0
        self._runs: dict[str, NativeRun] = {}

    def launch(self, request):
        self.launches.append(request)
        return LaunchRecord(
            id=len(self.launches),
            request=request,
            state="opened",
        )

    def submit_native(self, store, node_id, reference, status, timeout, prerequisites=()):
        self.native_count += 1
        run_id = uuid.uuid4().hex
        folder = store.root / run_id
        manifest = get_solver_adapter("calculix").snapshot_input(Path(reference.path), folder)
        manifest["prerequisites"] = [
            {
                "source_path": item.path,
                "revision": item.revision,
                "stat": fingerprint(item.path),
            }
            for item in prerequisites
        ]
        x = 2.0
        components = [(k, 4, i // 3 + 1, i % 3 + 1, 0) for i, k in enumerate(("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX"))]
        text = _tetra_geometry()
        for inc_idx, factor in ((1, 0.5), (2, 1.0)):
            displacement = [(n, [x * factor * (1.23456 if n == 3 else 1.0), 0, 0]) for n in _TETRA_NODES]
            text += _field(displacement, value=factor, increment=inc_idx)
            text += _field(
                [(n, [10 * x * factor, 0, 0, 0, 0, 0]) for n in _TETRA_NODES],
                name="STRESS",
                components=components,
                value=factor,
                increment=inc_idx,
            )
        (folder / "model.frd").write_text(text + " 9999\n", encoding="ascii")
        (folder / "model.dat").write_text("total mass for set EALL and time 1.0\n\n 8.0\n", encoding="ascii")
        (folder / "model.sta").write_text(
            "STEP INC ATT ITRS TOT TIME STEP TIME INC TIME\n1 1 1 1 1.000000E+00 1.000000E+00 1.000000E+00\n",
            encoding="ascii",
        )
        (folder / "job.log").write_text("CalculiX synthetic E2E run complete\n", encoding="ascii")
        result = collect_results(folder)
        manifest["outputs"] = {
            p.name: transaction.digest(p.read_bytes()) for p in (folder / "model.frd", folder / "model.dat")
        }
        manifest["artifacts"] = {
            **{name: transaction.digest((folder / name).read_bytes()) for name in result["exchange_files"]},
            "model.frd": manifest["outputs"]["model.frd"],
        }
        run = NativeRun(
            run_id,
            node_id,
            folder,
            state="completed",
            parse_state=result["status"],
            input_path=reference.path,
            input_revision=manifest["input_revision"],
            units=dict(reference.units),
            unit_source=reference.unit_source,
            returncode=0,
            finished_at=utc_now(),
            snapshot=manifest,
            result=result,
            log_path=str(folder / "job.log"),
        )
        store.write(run)
        store.select(node_id, run_id)
        self._runs[run_id] = run
        return run

    def submit_batch(self, store, items, **kwargs):
        batch = BatchRun(
            kwargs["batch_id"],
            "synthetic PrePoMax CalculiX batch",
            store.root,
            items,
            total_count=len(items),
            state="running",
        )
        for item in items:
            self.batch_count += 1
            run_id = uuid.uuid4().hex
            folder = store.root / run_id
            manifest = get_solver_adapter("calculix").snapshot_input(item.input_path, folder)
            x = float(item.parameters["WIDTH"])
            components = [(k, 4, i // 3 + 1, i % 3 + 1, 0) for i, k in enumerate(("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX"))]
            text = _tetra_geometry() + _field([(n, [x, 0, 0]) for n in _TETRA_NODES], value=1.0, increment=1)
            text += _field(
                [(n, [10 * x, 0, 0, 0, 0, 0]) for n in _TETRA_NODES],
                name="STRESS",
                components=components,
                value=1.0,
                increment=1,
            )
            (folder / "model.frd").write_text(text + " 9999\n", encoding="ascii")
            (folder / "model.dat").write_text(
                f"total mass for set EALL and time 1.0\n\n {10.0 - x}\n",
                encoding="ascii",
            )
            (folder / "model.sta").write_text(
                "STEP INC ATT ITRS TOT TIME STEP TIME INC TIME\n1 1 1 1 1.000000E+00 1.000000E+00 1.000000E+00\n",
                encoding="ascii",
            )
            (folder / "job.log").write_text("MDO batch item complete\n", encoding="ascii")
            result = collect_results(folder)
            manifest["outputs"] = {
                p.name: transaction.digest(p.read_bytes()) for p in (folder / "model.frd", folder / "model.dat")
            }
            manifest["artifacts"] = {
                name: transaction.digest((folder / name).read_bytes()) for name in result["exchange_files"]
            }
            run = NativeRun(
                run_id,
                item.node_id,
                folder,
                state="completed",
                parse_state=result["status"],
                input_path=str(item.input_path),
                returncode=0,
                finished_at=utc_now(),
                snapshot=manifest,
                result=result,
                log_path=str(folder / "job.log"),
            )
            store.write(run)
            self._runs[run_id] = run
            item.state, item.run_id, item.batch_id = "completed", run_id, batch.id
            batch.completed_count += 1
            kwargs["on_item_finished"](item, run)
        batch.state = "completed"
        kwargs["on_batch_finished"](batch)
        return batch

    def native_snapshot(self, run_id: str):
        return self._runs.get(run_id)

    def cancel_native(self, run_id: str) -> bool:
        return False

    def close(self) -> bool:
        return self._real_jobs.close()


class _BridgeProvider:
    id = "stub_bridge"
    name = "스텁 브리지 공급자"
    model = "stub-cae"

    def availability(self):
        return True, ""

    def discuss(self, prompt: str) -> str:
        return "해석 결과 요약을 확인했습니다."

    def cancel(self):
        return None


def _build_freecad_prepomax_calculix_project(tmp_path: Path):
    wb = Workbench()
    jobs = _E2EBoundaryJobs(wb.jobs)
    wb.jobs = jobs
    wb.programs = lambda: [ProgramStatus(spec, sys.executable) for spec in PROGRAMS]

    project = wb.new_native_project("브래킷 CAD-FE 일관 흐름")
    cad = wb.add_program_node(project, "geometry", "freecad")
    pre = wb.add_program_node(project, "preprocessor", "prepomax")
    solver = wb.add_program_node(project, "analysis", "calculix")
    pv = wb.add_program_node(project, "results", "paraview")
    pre_res = wb.add_program_node(project, "results", "prepomax")

    model_dir = tmp_path / "cad_fe"
    model_dir.mkdir(parents=True, exist_ok=True)
    fcstd = model_dir / "bracket.FCStd"
    step = model_dir / "bracket.step"
    pmx = model_dir / "bracket.pmx"
    inc = model_dir / "material.inc"
    inp = model_dir / "bracket.inp"

    fcstd.write_text("FreeCAD document placeholder", encoding="utf-8")
    step.write_text("ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;\n", encoding="utf-8")
    pmx.write_text("PrePoMax model v2.6", encoding="utf-8")
    inc.write_text("*ELASTIC\n210000.0, 0.3\n", encoding="utf-8")
    inp.write_text(
        "** Exported from PrePoMax\n"
        "*INCLUDE,INPUT=material.inc\n"
        "*BEAM SECTION,SECTION=RECT\n"
        "2.0, 2.0\n"
        "*NODE\n"
        + "".join(f"{node},{xyz[0]},{xyz[1]},{xyz[2]}\n" for node, xyz in zip(_TETRA_NODES, _TETRA_XYZ))
        + "*ELEMENT,TYPE=C3D4\n1,1,2,3,4\n"
        "*STEP\n*STATIC\n*END STEP\n",
        encoding="utf-8",
    )

    units = {"length": "mm", "force": "N", "stress": "MPa"}
    wb.set_node_file(project, cad.id, fcstd, "document")
    wb.set_node_file(project, cad.id, step, "output")
    wb.connect_nodes(project, cad.id, pre.id)

    wb.set_node_file(project, pre.id, pmx, "document", units=units)
    wb.set_node_file(project, pre.id, inp, "output", units=units)
    wb.connect_nodes(project, pre.id, solver.id)
    wb.connect_nodes(project, solver.id, pv.id)
    wb.connect_nodes(project, solver.id, pre_res.id)
    return wb, jobs, project, cad, pre, solver, pv, pre_res, dict(fcstd=fcstd, step=step, pmx=pmx, inc=inc, inp=inp)


def _verify_precise_calculix_results(manager, store, run):
    from caelab.native.operations import (
        TERMINAL_OPERATIONS, load_completed_operation, operation_snapshot, plan_conversion, submit_conversion,
    )
    from caelab.native.result_adapters import make_result_ref, read_result
    from caelab.result_readers.vtk_fields import read_vtu

    snapshot_path = run.workdir / "input" / "snapshot.json"
    snapshot = json.loads(snapshot_path.read_bytes())
    assert snapshot["input_revision"] == run.input_revision
    assert snapshot["files"] == run.snapshot["files"]
    frames = [frame["vtu_file"] for frame in run.result["frames"]]
    plan = plan_conversion(store, run.id, "VTU-1", frame_ids=frames)
    queued = submit_conversion(manager, store, plan)
    deadline = time.monotonic() + 10
    operation = operation_snapshot(manager, store, run.id, queued["id"])
    while operation["state"] not in TERMINAL_OPERATIONS and time.monotonic() < deadline:
        time.sleep(0.02)
        operation = operation_snapshot(manager, store, run.id, queued["id"])
    assert operation["state"] == "completed", operation.get("error")
    folder, receipt, dataset, stale = load_completed_operation(store, run.id, queued["id"])
    assert not stale and receipt["execution"] == []
    assert (folder / "operation.json").is_file() and (folder / "dataset.json").is_file()
    fixed = json.loads((folder / receipt["source_receipt_ref"]["path"]).read_bytes())
    assert fixed["snapshot"]["input_snapshot"] == run.snapshot["input_snapshot"]
    assert len(dataset.frames) == 2
    refs = []
    for frame_id, coordinate, expected in zip(frames, (0.5, 1.0), (1.23456, 2.46912)):
        ref = make_result_ref(store, run.id, queued["id"], frame_id, "DISP",
                              component="D1", selection={"ids": [3]})
        refs.append(ref)
        numeric = read_result(store, ref)
        assert isinstance(numeric, NumericResultV1) and numeric.status == "available"
        assert numeric.values == pytest.approx((expected,))
        assert numeric.ref.units["symbol"] == "mm"
        assert numeric.operator_provenance["native_value"] == pytest.approx(coordinate)
        assert numeric.operator_provenance["coordinate_kind"] == "static"
        assert numeric.operator_provenance["frame_time"] is None
        grid = read_vtu(run.workdir / frame_id)
        assert tuple(grid.node_ids) == _TETRA_NODES
        assert np.array_equal(grid.points, _TETRA_XYZ)
        assert tuple(grid.element_ids) == (1,)
        assert tuple(grid.node_ids[i] for i in grid.cells[0]) == _TETRA_NODES

    raw = snapshot_path.read_bytes()
    try:
        snapshot_path.write_bytes(raw + b" ")
        with pytest.raises(ResultError, match="immutable input snapshot changed"):
            read_result(store, refs[0])
    finally:
        snapshot_path.write_bytes(raw)


def test_d32_freecad_prepomax_calculix_paraview_and_expert_e2e(tmp_path: Path, monkeypatch):
    """Check file connections, launch arguments and a synthetic CalculiX receipt through the Expert handoff."""
    wb, jobs, project, cad, pre, solver, pv, pre_res, files = _build_freecad_prepomax_calculix_project(tmp_path)
    monkeypatch.setattr(experts, "get_provider", lambda _pid=None: _BridgeProvider())
    try:
        # Platform verification: PrePoMax is Windows-only per connections.py
        pre_spec = next(s for s in PROGRAMS if s.id == "prepomax")
        linux_status = _status(pre_spec, {}, "", "linux")
        assert not linux_status.available and "windows" in linux_status.reason.lower()

        # PrePoMax opens .pmx by default and .step on use_input=True
        wb.open_program(project, cad.id)
        wb.open_program(project, pre.id)
        wb.open_program(project, pre.id, use_input=True)
        assert jobs.launches[0].argv[-1] == str(files["fcstd"].resolve())
        assert jobs.launches[1].argv[-2:] == ["-f", str(files["pmx"].resolve())]
        assert jobs.launches[2].argv[-2:] == ["-f", str(files["step"].resolve())]

        wb.save_native_project(project, tmp_path / "project.json")
        run = wb.submit_native(project, solver.id)
        assert run.state == "completed"
        assert run.parse_state in {"readable", "partial"}
        assert Path(run.workdir, "results", "results.pvd").is_file()
        assert list(Path(run.workdir, "results").glob("*.vtu"))
        _verify_precise_calculix_results(jobs._real_jobs, RunStore(project.run_store), run)

        # Results launchers hand off .pvd to ParaView and .frd to PrePoMax
        wb.open_native_results(project, pv.id)
        wb.open_native_results(project, pre_res.id)
        assert jobs.launches[3].argv[-1] == "--data=" + str((run.workdir / "results" / "results.pvd").resolve())
        assert jobs.launches[4].argv[-2:] == ["-f", str((run.workdir / "model.frd").resolve())]

        # Expert bridge attaches immutable CalculiX evidence derived upstream from PrePoMax
        expert = wb.add_program_node(project, "expert", "expert")
        wb.connect_nodes(project, solver.id, expert.id)
        wb.save_native_project(project)
        wb.open_native_expert(project, expert.id)
        disc_path = (
            project.run_store
            / "discussions"
            / hashlib.sha256(expert.id.encode("utf-8")).hexdigest()
            / "discussion.json"
        )
        doc = wb.open_expert_discussion(disc_path, create=True)
        resolved_run = wb.expert_evidence_run(project, expert.id, run.id)
        evidence = wb.capture_expert_run(resolved_run, disc_path.parent / "assets", frame_index=0)
        doc = wb.attach_expert_evidence(disc_path, evidence, expected_revision=doc["revision"])
        updated = wb.ask_expert_discussion(
            disc_path,
            "CalculiX 응력 결과를 요약해 주세요.",
            evidence_id=evidence["id"],
            template_id="interpretation",
            expected_revision=doc["revision"],
        )
        assert updated["turns"][0]["question_scope"] == "case_analysis"
        assert updated["turns"][0]["evidence_id"] == evidence["id"]
        assert updated["turns"][0]["run_id"] == run.id
    finally:
        wb.close()


def test_d32_step_and_pmx_stale_invalidation_and_reexport(tmp_path: Path, monkeypatch):
    """Invalidate old exports and receipts on STEP, PMX and INP edits, then reopen a fresh export/run."""
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        app = None
    else:
        from apps.workbench_qt.native_analysis import NativeAnalysisWindow
        monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "native.ini"))
        app = QApplication.instance() or QApplication([])

    wb, _jobs, project, cad, pre, solver, pv, _pre_res, files = _build_freecad_prepomax_calculix_project(tmp_path)
    auxiliary = None
    try:
        saved = wb.save_native_project(project, tmp_path / "stale_proj.json")
        initial_run = wb.submit_native(project, solver.id)
        initial_bytes = files["inp"].read_bytes()
        files["step"].write_text("UPDATED STEP GEOMETRY\n", encoding="utf-8")
        project.refresh()
        assert project.nodes[pre.id].files["output"].stale
        assert project.nodes[solver.id].state == "stale"

        # Re-registering updated STEP + PMX clears upstream CAD change but keeps .inp stale until .inp is exported
        wb.set_node_file(project, cad.id, files["step"], "output")
        files["pmx"].write_text("PrePoMax regenerated mesh", encoding="utf-8")
        wb.set_node_file(project, pre.id, files["pmx"], "document")
        assert project.nodes[pre.id].files["output"].stale
        wb.set_node_file(project, pre.id, files["inp"], "output", units=initial_run.units)
        assert not project.nodes[pre.id].files["output"].stale

        middle_run = wb.submit_native(project, solver.id)
        assert not wb.selected_native_run(project, solver.id).stale
        if app is not None:
            auxiliary = NativeAnalysisWindow(saved, solver.id, Workbench())
            auxiliary.timer.stop()
            assert auxiliary.run_selector.currentData() == middle_run.id
        updated_bytes = initial_bytes.replace(b"2.0, 2.0", b"2.5, 2.0")
        assert updated_bytes != initial_bytes
        files["inp"].write_bytes(updated_bytes)
        project.refresh()
        assert project.input_file(solver.id).status == "changed"
        assert wb.selected_native_run(project, solver.id).stale
        assert project.nodes[solver.id].files["output"].stale
        assert project.nodes[pv.id].state == "stale"
        assert (middle_run.workdir / middle_run.snapshot["files"][0]["source_copy"]).read_bytes() == initial_bytes

        export = wb.set_node_file(project, pre.id, files["inp"], "output", units=middle_run.units)
        assert export.status == "ready"
        refreshed = wb.submit_native(project, solver.id)
        assert refreshed.id != middle_run.id
        assert refreshed.input_revision != middle_run.input_revision
        assert (refreshed.workdir / refreshed.snapshot["files"][0]["source_copy"]).read_bytes() == updated_bytes
        assert (initial_run.workdir / initial_run.snapshot["files"][0]["source_copy"]).read_bytes() == initial_bytes
        assert wb.open_native_results(project, pv.id).request.argv[-1] == "--data=" + str(
            (refreshed.workdir / "results" / "results.pvd").resolve()
        )
        wb.save_native_project(project)
        if auxiliary is not None:
            auxiliary.refresh_runs()
            assert auxiliary.run_selector.currentData() == refreshed.id
            assert auxiliary.run_selector.findData(middle_run.id) >= 0
            assert auxiliary.close()
            auxiliary = NativeAnalysisWindow(saved, solver.id, Workbench())
            auxiliary.timer.stop()
            assert auxiliary.run_selector.currentData() == refreshed.id
            assert {auxiliary.run_selector.itemData(i) for i in range(auxiliary.run_selector.count())} == {
                initial_run.id, middle_run.id, refreshed.id,
            }

        reopened_wb = Workbench()
        try:
            reopened = reopened_wb.open_native_project(saved)
            assert reopened.input_file(solver.id).revision == export.revision
            assert reopened.input_file(solver.id).path == str(files["inp"].resolve())
            assert reopened.input_file(solver.id).status == "ready"
            selected = reopened_wb.selected_native_run(reopened, solver.id)
            assert selected.id == refreshed.id and not selected.stale
            assert reopened.nodes[solver.id].files["output"].path == str(refreshed.workdir / "model.frd")
            old = {run.id: run for run in reopened_wb.native_runs(reopened, solver.id)}
            assert old[middle_run.id].stale and old[initial_run.id].stale
        finally:
            reopened_wb.close()
    finally:
        if auxiliary is not None:
            auxiliary.close()
        if app is not None:
            app.processEvents()
        wb.close()


def test_d32_prepomax_inp_to_mdo_study_full_cycle_and_save_as(tmp_path: Path, monkeypatch):
    """Exercise scalar CalculiX MDO using synthetic receipts, plus independent study and project copies."""
    app = None
    wb, jobs, project, _cad, _pre, solver, _pv, _pre_res, files = _build_freecad_prepomax_calculix_project(tmp_path)
    studio = None
    try:
        wb.save_native_project(project, tmp_path / "mdo_proj.json")
        service = wb.create_mdo_study(project=project, node_id=solver.id)
        tokens = service.parameterize(
            [
                {
                    "token": "WIDTH",
                    "source_path": str(files["inp"].resolve()),
                    "line_number": 4,
                    "expected_line": "2.0, 2.0",
                    "old_literal": "2.0",
                    "occurrence": 1,
                }
            ]
        )
        assert tokens == ("WIDTH",)

        service.configure(
            VariableSpace(variables=(ContinuousVariable(id="WIDTH", name="Width", unit="mm", lower=1.0, upper=3.5, initial=2.0),)),
            [ObjectiveSpec(id="disp", response_id="disp"), ObjectiveSpec(id="mass", response_id="mass_eall")],
            [ConstraintSpec(id="stress_cap", response_id="stress", kind="UPPER", limit=35.0, unit="MPa")],
            {"disp": "mm", "mass_eall": "kg", "stress": "MPa"},
        )
        rows = [service._add_point({"WIDTH": val}) for val in np.linspace(1.2, 3.2, 10)]
        # A literal connectivity change in the root deck is rejected by evaluation before dispatch.
        original_deck = files["inp"].read_bytes()
        launched = jobs.batch_count
        files["inp"].write_bytes(original_deck.replace(b"1,1,2,3,4", b"1,1,2,4,3"))
        assert files["inp"].read_bytes() != original_deck
        try:
            with pytest.raises(ValueError, match="Native root/INCLUDE changed"):
                service.submit([rows[0].point_id])
            assert jobs.batch_count == launched and rows[0].status == "PENDING"
        finally:
            files["inp"].write_bytes(original_deck)
        service.submit([r.point_id for r in rows])
        assert all(r.status == "COMPLETED" for r in rows)

        model = service.train(config=SurrogateConfig(method="prs", qualification_rmse=(1e-4, 1e-4, 1e-3)))
        assert model.qualified
        opt = service.optimize(OptimizationConfig(algorithm="nsga2", max_evaluations=16, population_size=8, max_iterations=2, seed=7))
        assert opt.predicted_archive
        predicted = [r for r in service.study.records if r.mode == "SURROGATE"]
        service.confirm([predicted[0].point_id])
        actual_clone = service.study.records[-1]
        assert actual_clone.mode == "ACTUAL" and service.is_verified(actual_clone.point_id)

        feasible_row = next(r for r in rows if r.feasibility == "FEASIBLE")
        plan = service.prepare_apply(feasible_row.point_id)
        service.apply(plan)
        assert f"{feasible_row.physical_values['WIDTH']}" in files["inp"].read_text(encoding="utf-8")
        service.undo()
        assert "2.0, 2.0" in files["inp"].read_text(encoding="utf-8")
        service.apply(service.prepare_apply(feasible_row.point_id))
        service.rerun_current()
        assert service.result_file(current=True).suffix in (".pvd", ".vtu")
        original_study_path = service.save()
        original_study_bytes = original_study_path.read_bytes()
        detached_path = service.save_as(tmp_path / "independent_study" / "study.json")
        assert service.project is None
        assert service.origin_association["study_file"] == str(original_study_path)
        assert original_study_path.read_bytes() == original_study_bytes
        assert project.nodes[solver.id].files["mdo_study"].path == str(original_study_path)
        service.close()
        detached = wb.open_mdo_study(detached_path)
        assert detached.is_verified(actual_clone.point_id)
        assert detached.study_file == detached_path
        assert detached.result_file(actual_clone.point_id).is_relative_to(detached_path.with_suffix(".mdo-data"))
        detached.close()

        service = wb.open_mdo_study(original_study_path, project=project, node_id=solver.id)
        # Project SaveAs refuses while study lock is open, then succeeds cleanly once service is closed
        save_as_path = tmp_path / "copied_project" / "project.json"
        with pytest.raises(ValueError, match="MDO Studio"):
            wb.save_native_project(project, save_as_path)
        service.close()
        wb.save_native_project(project, save_as_path)
        reopened_proj = wb.open_native_project(save_as_path)
        reopened_study_ref = reopened_proj.nodes[solver.id].files["mdo_study"]
        assert str((tmp_path / "copied_project").resolve()) in reopened_study_ref.path
        reopened_mdo = wb.open_mdo_study(reopened_study_ref.path, project=reopened_proj, node_id=solver.id)
        assert reopened_mdo.is_verified(actual_clone.point_id)
        try:
            from PySide6.QtWidgets import QApplication, QMessageBox
        except ImportError:
            reopened_mdo.close()
            return
        from apps.workbench_qt.mdo_studio.window import MDOStudioWindow

        monkeypatch.setenv("CAELAB_MDO_SETTINGS_PATH", str(tmp_path / "mdo.ini"))
        app = QApplication.instance() or QApplication([])
        warnings = []
        monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args[-1]))
        studio = MDOStudioWindow(service=reopened_mdo)
        previous = studio.service.current_rerun_id
        historical_runs = [attempt["run_id"] for attempt in studio.service.point_details(actual_clone.point_id).attempts]
        assert historical_runs and previous
        studio.select_point(actual_clone.point_id)
        studio._rerun_current()
        deadline = time.monotonic() + 10
        while studio._busy and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert not studio._busy and not warnings
        latest = studio.service.current_rerun_id
        assert latest != previous
        result = studio.service.result_file(current=True)
        assert result.is_file()
        assert [attempt["run_id"] for attempt in studio.service.point_details(actual_clone.point_id).attempts] == historical_runs
        studio.service.view_state = studio._view_state()
        studio.service.save()
        assert studio.close()
        studio = MDOStudioWindow(study_path=reopened_study_ref.path)
        assert studio.service.current_rerun_id == latest
        assert studio.service.result_file(current=True) == result
        assert actual_clone.point_id in studio._selected_ids
        assert [attempt["run_id"] for attempt in studio.service.point_details(actual_clone.point_id).attempts] == historical_runs
    finally:
        if studio is not None:
            studio.close()
        if app is not None:
            app.processEvents()
        wb.close()


def test_d32_contract_boundaries_and_discipline_graph_gap(tmp_path: Path):
    """Check the scalar MDO service boundary and the absence of multidisciplinary topology support."""
    wb = Workbench()
    try:
        # 1. MDOStudyService accepts only .inp, rejecting .pmx or .step directly
        step_file = tmp_path / "part.step"
        step_file.write_text("STEP", encoding="utf-8")
        svc = wb.create_mdo_study()
        with pytest.raises(ValueError, match=r"\.inp"):
            svc.open_input(step_file)
        svc.close()

        # 2. Topology tokens under *NODE/*ELEMENT are rejected without CAD/mesh regeneration adapter
        topo_inp = tmp_path / "mesh_param.inp"
        topo_inp.write_text("*NODE\n1, <X_COORD>, 0.0, 0.0\n*STEP\n*STATIC\n*END STEP\n", encoding="utf-8")
        svc2 = wb.create_mdo_study()
        with pytest.raises(ValueError, match="Geometry/topology tokens"):
            svc2.open_input(topo_inp)
        svc2.close()

        # 3. Project graph enforces acyclic connections
        proj = wb.new_native_project("DAG 검증")
        cad = wb.add_program_node(proj, "geometry", "freecad")
        pre = wb.add_program_node(proj, "preprocessor", "prepomax")
        wb.connect_nodes(proj, cad.id, pre.id)
        with pytest.raises(ValueError, match="순환"):
            wb.connect_nodes(proj, pre.id, cad.id)

        # An acyclic program-connection graph is separate from a coupled MDO discipline graph.
        assert not hasattr(mdo, "DisciplineGraph")
        assert not hasattr(svc2, "discipline_graph")
        assert importlib.util.find_spec("caelab.mdo.disciplines") is None
    finally:
        wb.close()



@pytest.mark.parametrize("element_header", ["*ELEMENT,TYPE=C3D4", "*ELEMENT ,TYPE=C3D4", "* ELEMENT,TYPE=C3D4", "*\tELE MENT,TYPE=C3D4"])
@pytest.mark.parametrize("comment", ["", "* \t* included connectivity\n"])
def test_d32_include_card_state_and_rendered_connectivity_guard(tmp_path, element_header, comment):
    root = tmp_path / "part.inp"
    include = tmp_path / "rows.inc"
    include.write_text("1,1,2,3,<LAST_NODE>\n", encoding="utf-8")
    root.write_text(
        "*NODE\n1,0,0,0\n2,1,0,0\n3,0,1,0\n4,0,0,1\n5,1,1,1\n"
        f"{element_header}\n{comment}*INCLUDE,INPUT=rows.inc\n"
        "*STEP\n*STATIC\n*CLOAD\n1,1,<LOAD>\n*END STEP\n",
        encoding="utf-8",
    )
    wb = Workbench()
    wb.programs = lambda: [ProgramStatus(spec, sys.executable) for spec in PROGRAMS]
    jobs = _E2EBoundaryJobs(wb.jobs)
    wb.jobs = jobs
    try:
        study = wb.create_mdo_study()
        with pytest.raises(ValueError, match="Geometry/topology tokens"):
            study.open_input(root)
        study.close()

        literal = b"1,1,2,3,4\n"
        include.write_bytes(literal)
        study = wb.create_mdo_study()
        study.open_input(root)
        with pytest.raises(ValueError, match="geometry and topology"):
            study.parameterize([{"source_path": str(include), "line_number": 1,
                                 "expected_line": "1,1,2,3,4", "old_literal": "4",
                                 "occurrence": 1, "token": "LAST_NODE"}])

        study.configure(
            VariableSpace(variables=(ContinuousVariable(id="LOAD", name="Load", unit="N", lower=1, upper=10, initial=2),)),
            [ObjectiveSpec(id="disp", response_id="disp")], [], {"disp": "mm"},
        )
        point = study.add_point({"LOAD": 2})
        # Simulate an altered saved template while leaving the live input untouched.
        study._templates[str(include.resolve())] = b"1,1,2,3,5\n"
        assert include.read_bytes() == literal
        with pytest.raises(ValueError, match="Rendered native mesh topology changed"):
            study.submit([point.point_id])
        assert jobs.batch_count == 0
        study.close()
    finally:
        wb.close()
