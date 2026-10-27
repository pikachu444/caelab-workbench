"""Headless service integration with synthetic native-format receipts (no CAE claims)."""
from __future__ import annotations

import copy
import json
import sys
import types
from dataclasses import asdict
from pathlib import Path

import pytest

from caelab.connections import ProgramSpec, ProgramStatus
from caelab.jobs import BatchRun
from caelab.mdo.service import MDOStudyService
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.surrogate import SurrogateConfig
from caelab.mdo.optimize import OptimizationConfig
from caelab.mdo import apply_transaction as transaction
from caelab.native.input_snapshot import snapshot_input
from caelab.native.run_store import NativeRun, RunStore
from caelab.native.run_store import utc_now
from caelab.native.results import collect_results
from caelab.workbench import Workbench
from test_native_results import _geometry, _field


class ReceiptJobs:
    """Deterministic JobManager boundary double producing real parser input formats."""
    def __init__(self):
        self.count = 0

    def submit_batch(self, store, items, **kwargs):
        import uuid
        batch = BatchRun(kwargs["batch_id"],"synthetic native receipts",store.root,items,total_count=len(items),state="running")
        for item in items:
            self.count += 1
            run_id = uuid.uuid4().hex
            folder = store.root / run_id
            manifest = snapshot_input(item.input_path,folder)
            x = item.parameters["width"]
            components = [(k,4,i//3+1,i%3+1,0) for i,k in enumerate(("SXX","SYY","SZZ","SXY","SYZ","SZX"))]
            text = _geometry()+_field([(n,[x,0,0]) for n in (40,10,90,25)],value=1.,increment=1)
            text += _field([(n,[10*x,0,0,0,0,0]) for n in (40,10,90,25)],name="STRESS",components=components,value=1.,increment=1)
            (folder / "model.frd").write_text(text+" 9999\n",encoding="ascii")
            (folder / "model.dat").write_text(f"total mass for set EALL and time 1.0\n\n {10-x}\n",encoding="ascii")
            (folder / "model.sta").write_text("STEP INC ATT ITRS TOT TIME STEP TIME INC TIME\n1 1 1 1 1.000000E+00 1.000000E+00 1.000000E+00\n",encoding="ascii")
            (folder / "job.log").write_text("synthetic format fixture\n",encoding="ascii")
            result = collect_results(folder)
            manifest["outputs"] = {p.name:transaction.digest(p.read_bytes()) for p in (folder/"model.frd",folder/"model.dat")}
            manifest["artifacts"] = {name:transaction.digest((folder/name).read_bytes()) for name in result["exchange_files"]}
            run = NativeRun(run_id,item.node_id,folder,state="completed",parse_state=result["status"],
                            input_path=str(item.input_path),returncode=0,finished_at=utc_now(),snapshot=manifest,result=result,log_path=str(folder/"job.log"))
            store.write(run)
            item.state,item.run_id,item.batch_id = "completed",run_id,batch.id
            batch.completed_count += 1
            kwargs["on_item_finished"](item,run)
        batch.state = "completed"
        kwargs["on_batch_finished"](batch)
        return batch


@pytest.fixture
def service(tmp_path):
    wb = Workbench()
    wb.jobs = ReceiptJobs()
    status = ProgramStatus(ProgramSpec("calculix","CalculiX","solver","CAELAB_CCX",("ccx",),"test"),sys.executable)
    wb.programs = lambda: [status]
    root = tmp_path / "user.inp"
    include = tmp_path / "material.inc"
    root.write_bytes(b'\xef\xbb\xbf** literal comment <IGNORE>\r\n*INCLUDE,INPUT=material.inc\r\n*BEAM SECTION,SECTION=RECT\r\n<WIDTH>, 2\r\n*STEP\r\n*STATIC\r\n*END STEP\r\n')
    include.write_bytes(b'*ELASTIC\r\n200000, .3\r\n')
    result = MDOStudyService(wb)
    result.open_input(root)
    result.configure(VariableSpace(variables=(ContinuousVariable(id="width",name="Width",unit="mm",lower=1,upper=4,initial=2),)),
                     [ObjectiveSpec(id="d",response_id="disp"),ObjectiveSpec(id="m",response_id="mass_eall")],
                     [ConstraintSpec(id="s",response_id="stress",kind="UPPER",limit=35,unit="MPa")],
                     dict(disp="mm",mass_eall="kg",stress="MPa"))
    yield result
    result.close()


def measured(service, values=(2.,3.)):
    rows = [service._add_point(dict(width=x)) for x in values]
    service.submit([r.point_id for r in rows])
    assert all(r.status == "COMPLETED" for r in rows), [r.error_message for r in rows]
    return rows


def test_native_receipt_apply_two_undo_and_result_handoff(service):
    original = service.input_path.read_bytes()
    first,second = measured(service)
    assert service.is_verified(first.point_id)
    plan = service.prepare_apply(first.point_id)
    receipt = service.apply(plan)
    assert b'\r\n2, 2\r\n' in service.input_path.read_bytes()
    assert Path(receipt["files"][0]["backup"]).read_bytes() == original
    assert service.input_path.with_name("user.inp.bak").read_bytes() == original
    service.apply(service.prepare_apply(second.point_id))
    assert b'\r\n3, 2\r\n' in service.input_path.read_bytes()
    assert list(service.input_path.parent.glob("user.inp.*.previous.bak"))
    service.undo()
    assert b'\r\n2, 2\r\n' in service.input_path.read_bytes()
    assert service.current_point_id == first.point_id
    assert service.workbench.jobs.count == 2
    service.rerun_current()
    assert service.workbench.jobs.count == 3
    assert service.result_file(current=True).suffix in (".pvd", ".vtu")
    assert service.result_file(first.point_id).is_file()


def test_plan_detects_include_edit_and_infeasible_requires_explicit_accept(service):
    (row,) = measured(service,(4.,))
    assert row.feasibility == "INFEASIBLE"
    plan = service.prepare_apply(row.point_id)
    with pytest.raises(ValueError,match="Explicit acceptance"):
        service.apply(plan)
    include = next(Path(p) for p in service.input_sources() if p != str(service.input_path))
    include.write_bytes(include.read_bytes()+b'** external edit\r\n')
    with pytest.raises(ValueError,match="changed"):
        service.apply(service.prepare_apply(row.point_id),allow_infeasible=True)
    assert b'<WIDTH>' in service.input_path.read_bytes()


def test_save_as_reopens_independently_and_read_only_writes_nothing(service,tmp_path):
    measured(service)
    target = service.save_as(tmp_path / "independent.json")
    service.close()
    before = {str(p):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    readonly = MDOStudyService(service.workbench).open(target,read_only=True)
    assert readonly.is_verified("DP_0001")
    with pytest.raises(PermissionError):
        readonly.submit()
    with pytest.raises(PermissionError):
        readonly.recover_apply()
    readonly.close()
    assert before == {str(p):p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    reopened = MDOStudyService(service.workbench).open(target)
    assert reopened.is_verified("DP_0001")
    evidence = reopened.point_evidence("DP_0001")
    assert str(tmp_path/"independent.mdo-data"/"runs") in evidence["workdir"]
    (Path(evidence["workdir"])/"model.frd").unlink()
    assert not reopened.is_verified("DP_0001")
    reopened.reconcile()
    assert reopened.study.records[0].status == "FAILED"
    reopened.close()


def test_surrogate_validation_search_and_actual_confirmation_are_separate(service):
    measured(service,np.linspace(1,3.2,12))
    model = service.train(config=SurrogateConfig(method="prs",qualification_rmse=(1e-6,1e-6,1e-6)))
    assert model.metrics and model.qualified
    assert model.dataset.training_revision == service.revision
    count = service.workbench.jobs.count
    result = service.optimize(OptimizationConfig(algorithm="nsga2",max_evaluations=16,population_size=8,max_iterations=2,seed=9))
    predicted = [r for r in service.study.records if r.mode == "SURROGATE"]
    assert result.predicted_archive and predicted
    assert service.workbench.jobs.count == count
    assert all(r.model_id == model.id and not service.is_verified(r.point_id) for r in predicted)
    service.confirm([predicted[0].point_id])
    clone = service.study.records[-1]
    assert clone.mode == "ACTUAL" and clone.source_point_id == predicted[0].point_id
    assert predicted[0].mode == "SURROGATE" and not predicted[0].attempts
    assert clone.attempts and service.workbench.jobs.count == count+1


def test_parameterize_literal_and_immutable_revision_preserve_old_attempts(tmp_path,service):
    rows = measured(service)
    before = copy.deepcopy(rows[0].attempts)
    service.configure(service.study.space,service.study.objectives,service.study.constraints,service.response_units)
    assert rows[0].revision != service.revision
    assert rows[0].attempts == before and not service.is_verified(rows[0].point_id)
    wb = service.workbench
    source = tmp_path/"ordinary.inp"
    source.write_bytes(b'\xef\xbb\xbf*ELASTIC\r\n200000, .3\r\n')
    other = MDOStudyService(wb)
    other.open_input(source)
    assert other.parameterize([dict(token="E",source_path=str(source),line_number=2,expected_line="200000, .3",old_literal="200000",occurrence=1)]) == ("E",)
    assert source.read_bytes() == b'\xef\xbb\xbf*ELASTIC\r\n200000, .3\r\n'
    assert other.template.original_text.endswith('<E>, .3\r\n')
    other.close()


def test_transaction_write_fault_rolls_back_closure_and_recovery_preserves_external_edit(tmp_path,monkeypatch):
    root = tmp_path/"study"; root.mkdir()
    a,b = tmp_path/"a.inp",tmp_path/"b.inc"
    a.write_bytes(b'before a'); b.write_bytes(b'before b')
    before = {str(p):transaction.digest(p.read_bytes()) for p in (a,b)}
    real = transaction.write_staged
    def fault(path,data):
        if path == b and data == b'after b':
            raise OSError("full disk fixture")
        real(path,data)
    monkeypatch.setattr(transaction,"write_staged",fault)
    with pytest.raises(OSError,match="full disk"):
        transaction.replace_closure(root,{str(a):b'after a',str(b):b'after b'},before,dict(point_id="p"))
    assert a.read_bytes() == b'before a' and b.read_bytes() == b'before b'
    monkeypatch.setattr(transaction,"write_staged",real)
    receipt = transaction.replace_closure(root,{str(a):b'after a',str(b):b'after b'},before,dict(point_id="p"))
    a.write_bytes(b'external edit')
    with pytest.raises(ValueError,match="externally edited"):
        transaction.recover(root)
    assert a.read_bytes() == b'external edit' and b.read_bytes() == b'after b'
    a.write_bytes(b'after a')
    assert transaction.recover(root)["state"] == "rolled_back"
    assert a.read_bytes() == b'before a' and b.read_bytes() == b'before b'


def test_readonly_target_aborts_before_target_writes(service):
    import stat
    (row,) = measured(service,(2.,))
    plan = service.prepare_apply(row.point_id)
    original = service.input_path.read_bytes()
    mode = service.input_path.stat().st_mode
    service.input_path.chmod(stat.S_IREAD)
    try:
        with pytest.raises(PermissionError):
            service.apply(plan)
    finally:
        service.input_path.chmod(mode)
    assert service.input_path.read_bytes() == original


def test_finish_fault_restores_durable_metadata_and_retains_target_locks(service,monkeypatch):
    from filelock import FileLock,Timeout
    (row,) = measured(service,(2.,))
    original = service.input_path.read_bytes()
    persist = service._persist
    locked = []
    def observe_persist():
        if service.current_point_id is not None:
            with pytest.raises(Timeout):
                FileLock(str(service.input_path)+".caelab-apply.lock",timeout=0).acquire()
            locked.append(True)
        persist()
    monkeypatch.setattr(service,"_persist",observe_persist)
    finish = transaction.finish
    def fail_commit(root,receipt):
        if receipt["state"] == "committed":
            raise OSError("receipt write fault")
        return finish(root,receipt)
    monkeypatch.setattr(transaction,"finish",fail_commit)
    with pytest.raises(OSError,match="receipt write fault"):
        service.apply(service.prepare_apply(row.point_id))
    assert locked and service.input_path.read_bytes() == original
    saved = service.save()
    assert json.loads(saved.read_text(encoding="utf-8"))["current_point_id"] is None
    service.close()
    reopened = MDOStudyService(service.workbench).open(saved)
    assert reopened.current_point_id is None and not reopened._undo_stack
    assert reopened.is_verified(row.point_id)
    reopened.close()


@pytest.mark.parametrize("damage", ["point_sources","error_message","physical_values"])
def test_saved_schema_rejects_corruption_before_adoption(service,tmp_path,damage):
    measured(service,(2.,))
    saved = service.save()
    data = json.loads(saved.read_text(encoding="utf-8"))
    if damage == "point_sources":
        data[damage] = 1
    elif damage == "error_message":
        data["records"][0][damage] = {"invalid":"object"}
    else:
        data["records"][0][damage] = {"width":True}
    bad = tmp_path/(damage+".json")
    bad.write_text(json.dumps(data),encoding="utf-8")
    other = MDOStudyService(service.workbench)
    with pytest.raises(ValueError):
        other.open(bad,read_only=True)
    assert other.input_path is None and other._root is None


def test_readonly_observer_keeps_live_pending_state_without_writes(service):
    class PendingJobs(ReceiptJobs):
        def submit_batch(self,store,items,**callbacks):
            self.batch = BatchRun(callbacks["batch_id"],"live",store.root,items,state="running",total_count=len(items))
            self.callbacks = callbacks
            return self.batch
        def cancel_batch(self,batch_id):
            for item in self.batch.items:
                item.state = "cancelled"
            self.batch.state = "cancelled"
            self.callbacks["on_batch_finished"](self.batch)
            return True
    jobs = PendingJobs()
    service.workbench.jobs = jobs
    row = service.add_point({"width":2.})
    service.submit([row.point_id])
    path = service._root/"study.json"
    before = {str(p):p.read_bytes() for p in service._root.rglob("*") if p.is_file() and not p.name.endswith(".lock")}
    observer = MDOStudyService(service.workbench).open(path,read_only=True)
    assert observer.study.records[0].status == "QUEUED"
    assert before == {str(p):p.read_bytes() for p in service._root.rglob("*") if p.is_file() and not p.name.endswith(".lock")}
    observer.close()
    jobs.batch.items[0].state = "stopped"; jobs.batch.state = "stopped"
    jobs.callbacks["on_batch_finished"](jobs.batch)


def test_reopen_recovers_completed_receipt_when_callback_metadata_was_lost(service):
    (row,) = measured(service,(2.,))
    saved = service.save()
    data = json.loads(saved.read_text(encoding="utf-8"))
    # Simulate crash after JobManager committed native receipt, before service callback.
    data["records"][0].update(status="RUNNING",run_id=None,attempts=[],observations={},objectives={},feasibility="NOT_ASSESSED")
    data["point_sources"] = {}
    saved.write_text(json.dumps(data),encoding="utf-8")
    service.close()
    reopened = MDOStudyService(service.workbench).open(saved)
    assert reopened.study.records[0].status == "COMPLETED"
    assert len(reopened.study.records[0].attempts) == 1 and reopened.is_verified(row.point_id)
    reopened.close()


def test_two_undo_operations_walk_history_and_include_parameter_bytes(service):
    original = service.input_path.read_bytes()
    first,second = measured(service)
    service.apply(service.prepare_apply(first.point_id))
    service.apply(service.prepare_apply(second.point_id))
    service.undo(); service.undo()
    assert service.current_point_id is None and service.input_path.read_bytes() == original


def test_input_failure_preserves_empty_service_and_rejects_unsafe_tokens(tmp_path,service):
    for name,content in (("bad",b'*NODE\n1,<T>,<t>,0\n'),("geometry",b'*NODE\n1,<X>,0,0\n')):
        path = tmp_path/(name+".inp"); path.write_bytes(content)
        other = MDOStudyService(service.workbench)
        with pytest.raises(ValueError):
            other.open_input(path)
        assert other.input_path is None and other._lease is None


def test_parent_project_concurrent_edit_refuses_association(service,tmp_path):
    wb = Workbench()
    project = wb.new_native_project()
    node = project.add_node("analysis","calculix")
    project.set_file(node.id,service.input_path)
    wb.save_native_project(project,tmp_path/"parent.json")
    owner = MDOStudyService(wb,project=project,node_id=node.id)
    owner.open_input(service.input_path)
    external = wb.open_native_project(project.path)
    external.name = "other window edited name"
    wb.save_native_project(external)
    expected = project.path.read_bytes()
    with pytest.raises(ValueError,match="changed"):
        owner.save()
    assert project.path.read_bytes() == expected
    owner.close()


@pytest.mark.parametrize("sta",[
    "1 1 1 1 0.500000E+00 0.500000E+00 0.500000E+00\n",
    "1 1 1 1 1.000000E+00 1.000000E+00 1.000000E+00\n1 2 2U 1 1.000000E+00 1.000000E+00 0.100000E+00\n",
    "2 1 1 1 1.000000E+00 1.000000E+00 1.000000E+00\n",
])
def test_response_endpoint_rejects_intermediate_failed_or_wrong_step_sta(service,sta):
    from caelab.mdo.native_responses import extract_native_responses
    (row,) = measured(service,(2.,))
    folder = Path(service.point_evidence(row.point_id)["workdir"])
    (folder/"model.sta").write_text(sta,encoding="ascii")
    with pytest.raises(ValueError,match="STA"):
        extract_native_responses(folder,service.response_selectors)
    assert not service.is_verified(row.point_id)


def test_response_endpoint_accepts_recovered_attempt_but_not_terminated_intermediate_frd(service):
    from caelab.mdo.native_responses import extract_native_responses
    (row,) = measured(service,(2.,))
    folder = Path(service.point_evidence(row.point_id)["workdir"])
    (folder/"model.sta").write_text("1 1 1U 1 0.5 0.5 0.5\n1 1 2 1 1.0 1.0 1.0\n",encoding="ascii")
    assert extract_native_responses(folder,service.response_selectors)["disp"] == 2.
    text = _geometry()+_field([(n,[2.,0,0]) for n in (40,10,90,25)],value=.5,increment=1)+" 9999\n"
    (folder/"model.frd").write_text(text,encoding="ascii")
    with pytest.raises(ValueError,match="FRD"):
        extract_native_responses(folder,{"disp":service.response_selectors["disp"]})


def test_response_endpoint_subset_and_stress_semantics_are_explicit(service):
    from caelab.mdo.native_responses import extract_native_responses
    from caelab.mdo.native_endpoint import expected_static_endpoint
    (row,) = measured(service,(2.,))
    folder = Path(service.point_evidence(row.point_id)["workdir"])
    original = (folder/"model.inp").read_bytes()
    (folder/"model.inp").write_bytes(original.replace(b'*STATIC',b'*STATIC\n.1,2.'))
    with pytest.raises(ValueError,match="non-unit"):
        expected_static_endpoint(folder/"model.inp")
    (folder/"model.inp").write_bytes(original+b'*EL FILE,SECTION FORCES\nS\n')
    with pytest.raises(ValueError,match="SECTION FORCES"):
        extract_native_responses(folder,service.response_selectors)


import numpy as np
