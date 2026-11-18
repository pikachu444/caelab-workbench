"""D32 E2E verification: FreeCAD STEP -> PrePoMax .pmx/.inp -> CalculiX -> ParaView / MDO."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import uuid

import numpy as np
import pytest

from caelab.assist import experts
from caelab.connections import PROGRAMS, ProgramStatus, _status
from caelab.jobs import BatchRun, LaunchRecord
from caelab.mdo import apply_transaction as transaction
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.optimize import OptimizationConfig
from caelab.mdo.surrogate import SurrogateConfig
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.native.input_snapshot import fingerprint, snapshot_input
from caelab.native.results import collect_results
from caelab.native.run_store import NativeRun, utc_now
from caelab.workbench import Workbench
from test_native_results import _field, _geometry


class _E2EBoundaryJobs:
    """Job boundary double executing real snapshot_input/collect_results while recording GUI launches."""

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
        manifest = snapshot_input(Path(reference.path), folder)
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
        text = _geometry()
        for inc_idx, factor in ((1, 0.5), (2, 1.0)):
            text += _field([(n, [x * factor, 0, 0]) for n in (40, 10, 90, 25)], value=factor, increment=inc_idx)
            text += _field(
                [(n, [10 * x * factor, 0, 0, 0, 0, 0]) for n in (40, 10, 90, 25)],
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
            manifest = snapshot_input(item.input_path, folder)
            x = float(item.parameters["WIDTH"])
            components = [(k, 4, i // 3 + 1, i % 3 + 1, 0) for i, k in enumerate(("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX"))]
            text = _geometry() + _field([(n, [x, 0, 0]) for n in (40, 10, 90, 25)], value=1.0, increment=1)
            text += _field(
                [(n, [10 * x, 0, 0, 0, 0, 0]) for n in (40, 10, 90, 25)],
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


def test_d32_freecad_prepomax_calculix_paraview_and_expert_e2e(tmp_path: Path, monkeypatch):
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

        project_file = wb.save_native_project(project, tmp_path / "project.json")
        run = wb.submit_native(project, solver.id)
        assert run.state == "completed"
        assert run.parse_state in {"readable", "partial"}
        assert Path(run.workdir, "results", "results.pvd").is_file()
        assert list(Path(run.workdir, "results").glob("*.vtu"))

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


def test_d32_step_and_pmx_stale_invalidation_and_reexport(tmp_path: Path):
    wb, _jobs, project, cad, pre, solver, _pv, _pre_res, files = _build_freecad_prepomax_calculix_project(tmp_path)
    try:
        saved = wb.save_native_project(project, tmp_path / "stale_proj.json")
        files["step"].write_text("UPDATED STEP GEOMETRY\n", encoding="utf-8")
        project.refresh()
        assert project.nodes[pre.id].files["output"].stale
        assert project.nodes[solver.id].state == "stale"

        # Re-registering updated STEP + PMX clears upstream CAD change but keeps .inp stale until .inp is exported
        wb.set_node_file(project, cad.id, files["step"], "output")
        files["pmx"].write_text("PrePoMax regenerated mesh", encoding="utf-8")
        wb.set_node_file(project, pre.id, files["pmx"], "document")
        assert project.nodes[pre.id].files["output"].stale
        wb.set_node_file(project, pre.id, files["inp"], "output")
        assert not project.nodes[pre.id].files["output"].stale

        reopened = wb.open_native_project(saved)
        assert reopened.input_file(solver.id).path == str(files["inp"].resolve())
    finally:
        wb.close()


def test_d32_prepomax_inp_to_mdo_study_full_cycle_and_save_as(tmp_path: Path):
    wb, _jobs, project, _cad, pre, solver, _pv, _pre_res, files = _build_freecad_prepomax_calculix_project(tmp_path)
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
        service.save()

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
        reopened_mdo.close()
    finally:
        wb.close()


def test_d32_contract_boundaries_and_discipline_graph_gap(tmp_path: Path):
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

        # 4. Multi-discipline DisciplineGraph module is future unimplemented spec
        assert importlib.util.find_spec("caelab.mdo.disciplines") is None
    finally:
        wb.close()
