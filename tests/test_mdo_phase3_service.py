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


def test_recovery_retains_target_ownership_through_corrected_metadata(service,monkeypatch):
    from filelock import FileLock,Timeout
    (row,) = measured(service,(2.,))
    expected = service.source_revisions
    transaction.replace_closure(service._root,service._render(row.physical_values),expected,
                                dict(point_id=row.point_id,previous_point_id=None,operation="apply"))
    persist = service._persist
    observed = []
    def check_ownership():
        with pytest.raises(Timeout):
            FileLock(str(service.input_path)+".caelab-apply.lock",timeout=0).acquire()
        observed.append(True)
        persist()
    monkeypatch.setattr(service,"_persist",check_ownership)
    assert service.recover_apply()["state"] == "rolled_back"
    assert observed and service.current_point_id is None


def test_failed_apply_keeps_prior_separate_current_rerun_openable(service,monkeypatch):
    first,second = measured(service)
    service.apply(service.prepare_apply(first.point_id))
    service.rerun_current()
    prior = service.current_rerun_id
    original = service.input_path.read_bytes()
    finish = transaction.finish
    def fault(root,receipt):
        if receipt["state"] == "committed":
            raise OSError("new receipt fault")
        finish(root,receipt)
    monkeypatch.setattr(transaction,"finish",fault)
    with pytest.raises(OSError):
        service.apply(service.prepare_apply(second.point_id))
    assert service.current_point_id == first.point_id and service.current_rerun_id == prior
    assert service.input_path.read_bytes() == original and service.result_file(current=True).is_file()


def test_interrupted_apply_recovers_durable_prior_current_rerun_on_reopen(service,monkeypatch):
    first,second = measured(service)
    service.apply(service.prepare_apply(first.point_id))
    service.rerun_current()
    prior = service.current_rerun_id
    original = service.input_path.read_bytes()
    class AbruptExit(BaseException):
        pass
    finish = transaction.finish
    recover = transaction.recover
    def stop_at_receipt(root,receipt):
        raise AbruptExit()
    def unavailable_recovery(*args,**kwargs):
        raise AbruptExit()
    monkeypatch.setattr(transaction,"finish",stop_at_receipt)
    monkeypatch.setattr(transaction,"recover",unavailable_recovery)
    with pytest.raises(AbruptExit):
        service.apply(service.prepare_apply(second.point_id))
    journal = json.loads((service._root/"apply-pending.json").read_text(encoding="utf-8"))
    assert journal["previous_current_rerun_id"] == prior
    saved = service._root/"study.json"
    service.close()
    monkeypatch.setattr(transaction,"finish",finish)
    monkeypatch.setattr(transaction,"recover",recover)
    reopened = MDOStudyService(service.workbench).open(saved)
    assert reopened.current_point_id == first.point_id and reopened.current_rerun_id == prior
    assert reopened.input_path.read_bytes() == original and reopened.result_file(current=True).is_file()
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


def test_save_as_detaches_copy_and_preserves_parent_and_original_study(service,tmp_path):
    wb = Workbench(); wb.jobs = service.workbench.jobs; wb.programs = service.workbench.programs
    project = wb.new_native_project()
    node = project.add_node("analysis","calculix")
    project.set_file(node.id,service.input_path)
    wb.save_native_project(project,tmp_path/"associated.json")
    owner = MDOStudyService(wb,project=project,node_id=node.id)
    owner.open_input(service.input_path)
    owner.configure(service.study.space,service.study.objectives,service.study.constraints,service.response_units)
    measured(owner,(2.,))
    original = owner.save()
    parent_bytes,study_bytes = project.path.read_bytes(),original.read_bytes()
    original_reference = project.nodes[node.id].files["mdo_study"].path
    copied = owner.save_as(tmp_path/"detached.json")
    assert owner.project is None and owner.node_id is None
    assert project.path.read_bytes() == parent_bytes and original.read_bytes() == study_bytes
    assert project.nodes[node.id].files["mdo_study"].path == original_reference
    owner.close()
    reopened_parent = wb.open_native_project(project.path)
    reopened_original = wb.create_mdo_study(project=reopened_parent,node_id=node.id)
    assert reopened_original.is_verified("DP_0001")
    reopened_original.close()
    independent = wb.open_mdo_study(copied)
    fresh = independent.clone_point("DP_0001")
    independent.submit([fresh.point_id])
    assert independent.is_verified(fresh.point_id)
    assert Path(independent.point_evidence(fresh.point_id)["workdir"]).parent == copied.with_suffix(".mdo-data")/"runs"
    assert project.path.read_bytes() == parent_bytes
    independent.close()


def test_relative_owned_paths_survive_sidecar_relocation(service,tmp_path):
    import shutil
    measured(service,(2.,))
    saved = service.save_as(tmp_path/"portable.json")
    service.close()
    relocated = tmp_path/"relocated"; relocated.mkdir()
    shutil.move(str(saved),str(relocated/saved.name))
    shutil.move(str(saved.with_suffix(".mdo-data")),str(relocated/saved.with_suffix(".mdo-data").name))
    reopened = MDOStudyService(service.workbench).open(relocated/saved.name)
    assert reopened.is_verified("DP_0001")
    assert reopened.result_file("DP_0001").is_relative_to(relocated)
    reopened.close()


def test_parameterization_accumulates_original_coordinates_after_reopen(service,tmp_path):
    source = tmp_path/"ordinary-scalars.inp"
    source.write_bytes(b'*BEAM SECTION,SECTION=RECT\r\n10,5\r\n*ELASTIC\r\n200000,.3\r\n*DENSITY\r\n7.85e-9\r\n')
    owner = MDOStudyService(service.workbench)
    owner.open_input(source)
    def binding(token,line,expected,literal):
        return dict(token=token,source_path=str(source),line_number=line,expected_line=expected,old_literal=literal,occurrence=1)
    owner.parameterize([binding("WIDTH",2,"10,5","10")])
    assert owner.parameterize([binding("E",4,"200000,.3","200000")]) == ("E","WIDTH")
    saved = owner.save()
    owner.close()
    reopened = MDOStudyService(service.workbench).open(saved)
    assert reopened.parameterize([binding("RHO",6,"7.85e-9","7.85e-9")]) == ("E","RHO","WIDTH")
    before = reopened.template.original_text
    with pytest.raises(ValueError,match="overlap"):
        reopened.parameterize([binding("OTHER",2,"10,5","10")])
    assert reopened.template.original_text == before
    assert source.read_bytes().startswith(b'*BEAM SECTION,SECTION=RECT\r\n10,5\r\n')
    reopened.close()


def test_parameterized_include_closure_backs_up_and_undoes_exact_bytes(service):
    include = next(Path(p) for p in service.input_sources() if p != str(service.input_path))
    originals = {p:Path(p).read_bytes() for p in service.input_sources()}
    service.parameterize([dict(token="E",source_path=str(include),line_number=2,
                               expected_line="200000, .3",old_literal="200000",occurrence=1)])
    space = VariableSpace(variables=(*service.study.space.variables,
                                    ContinuousVariable(id="e",name="Elastic modulus",unit="MPa",lower=100000,upper=300000,initial=200000)))
    service.configure(space,service.study.objectives,service.study.constraints,service.response_units)
    first = service.add_point(dict(width=2.,e=120000.))
    second = service.add_point(dict(width=3.,e=180000.))
    service.submit([first.point_id,second.point_id])
    receipt = service.apply(service.prepare_apply(first.point_id))
    assert {f["path"] for f in receipt["files"]} == set(originals)
    assert include.read_bytes() == b'*ELASTIC\r\n120000, .3\r\n'
    for item in receipt["files"]:
        assert Path(item["backup"]).read_bytes() == originals[item["path"]]
    service.apply(service.prepare_apply(second.point_id))
    assert include.read_bytes() == b'*ELASTIC\r\n180000, .3\r\n'
    service.undo()
    assert include.read_bytes() == b'*ELASTIC\r\n120000, .3\r\n'
    service.undo()
    assert all(Path(p).read_bytes() == content for p,content in originals.items())


def test_train_exposes_and_persists_explicit_grouped_validation_policy(service,tmp_path):
    measured(service,np.linspace(1,3.2,12))
    config = SurrogateConfig(method="prs",seed=4,qualification_rmse=(1e-6,1e-6,1e-6))
    model = service.train(config=config,holdout_fraction=.25,validation_seed=9,chronological=True)
    report = service.training_report
    assert model.split.seed == 9 and model.split.method == "chronological_grouped_holdout"
    assert report["split_policy"] == dict(method=model.split.method,fraction=.25,seed=9,chronological=True)
    assert report["validation_point_ids"] == [r.point_id for r in service.study.records[-3:]]
    assert not set(report["train_point_ids"]) & set(report["validation_point_ids"])
    assert report["response_ids"] == ["disp","mass_eall","stress"]
    assert report["units"] == ["mm","kg","MPa"]
    assert report["qualification_limits"] == [1e-6]*3 and report["qualified"]
    saved = service.save_as(tmp_path/"validated.json")
    service.close()
    reopened = MDOStudyService(service.workbench).open(saved)
    assert reopened.training_report == report and reopened.surrogate.split == model.split
    reopened.close()


def test_actual_spearman_retains_infeasible_rows_and_reports_missing_pairs(service):
    rows = measured(service,(1.,2.,4.))
    assert rows[-1].feasibility == "INFEASIBLE"
    pending = service.add_point(dict(width=3.))
    before = service.workbench.jobs.count
    report = service.analyze_sensitivity("disp")
    assert report["source_mode"] == "ACTUAL" and report["native_calls"] == 0
    assert report["estimates"]["rho"] == [1.] and report["estimates"]["sample_count"] == [3]
    assert report["estimates"]["missing_count"] == [1]
    assert report["metadata"]["included_point_ids"] == [r.point_id for r in rows]
    assert report["excluded_rows"][0]["point_id"] == pending.point_id
    assert service.sensitivity_report()["experiment_id"] == report["experiment_id"]
    assert service.workbench.jobs.count == before


@pytest.mark.parametrize("method",["sobol","morris"])
def test_paired_sensitivity_uses_captured_surrogate_complete_design_and_persists(service,tmp_path,method):
    from caelab.mdo.sensitivity import SensitivityConfig
    measured(service,np.linspace(1,3.2,12))
    model = service.train(config=SurrogateConfig(method="prs",qualification_rmse=(1e-6,1e-6,1e-6)))
    count = service.workbench.jobs.count
    config = SensitivityConfig(method=method,base_samples=16,trajectories=4,resamples=32,seed=7)
    report = service.analyze_sensitivity("disp",method=method,config=config)
    expected = 48 if method == "sobol" else 8
    assert report["sample_count"] == expected and len(report["paired_rows"]) == expected
    assert report["source_mode"] == "SURROGATE" and report["model_id"] == model.id
    assert report["metadata"]["distribution"] == "independent physical uniform"
    assert "excludes surrogate/model error" in report["metadata"]["interval_scope"]
    assert report["metadata"]["outside_training_count"] > 0
    assert report["native_calls"] == 0 and service.workbench.jobs.count == count
    assert {row["mode"] for row in report["paired_rows"]} == {"SURROGATE"}
    assert set(report["estimates"]) == ({"S1","ST","S1_conf","ST_conf"} if method == "sobol" else {"mu","mu_star","sigma","mu_star_conf"})
    with pytest.raises(ValueError,match="unaffected by table"):
        service.analyze_sensitivity("disp",method=method,point_ids=["DP_0001"],config=config)
    saved = service.save_as(tmp_path/(method+".json")); service.close()
    reopened = MDOStudyService(service.workbench).open(saved)
    assert reopened.sensitivity_report()["paired_rows"] == report["paired_rows"]
    reopened.close()


def test_readonly_sensitivity_is_ephemeral_and_model_revision_is_frozen(service,tmp_path):
    from caelab.mdo.sensitivity import SensitivityConfig
    measured(service,np.linspace(1,3.2,12))
    service.train(config=SurrogateConfig(method="prs"))
    saved = service.save_as(tmp_path/"read-analysis.json"); service.close()
    observer = MDOStudyService(service.workbench).open(saved,read_only=True)
    before = {str(p):p.read_bytes() for p in saved.parent.rglob("*") if p.is_file() and not p.name.endswith(".lock")}
    observer.analyze_sensitivity("disp",method="morris",config=SensitivityConfig(method="morris",trajectories=4,resamples=16))
    assert not observer.sensitivity_reports
    assert before == {str(p):p.read_bytes() for p in saved.parent.rglob("*") if p.is_file() and not p.name.endswith(".lock")}
    observer.close()
    writer = MDOStudyService(service.workbench).open(saved)
    writer.configure(writer.study.space,writer.study.objectives,writer.study.constraints,writer.response_units)
    with pytest.raises(ValueError,match="current revision"):
        writer.analyze_sensitivity("disp",method="sobol")
    writer.close()


def test_shared_scalar_matcher_keeps_preview_and_original_binding_coordinates_equal(service,tmp_path):
    from caelab.mdo.inp_binder import scalar_literal_occurrences
    assert scalar_literal_occurrences("10,1,1","1") == ((3,4),(5,6))
    assert scalar_literal_occurrences("-1,1.0,1e2,11,1","1") == ((1,2),(14,15))
    with pytest.raises(ValueError):
        scalar_literal_occurrences("1","nan")
    path = tmp_path/"scalars.inp"; path.write_text("*BEAM SECTION,SECTION=RECT\n10,1,1\n",encoding="utf-8")
    owner = MDOStudyService(service.workbench); owner.open_input(path)
    owner.parameterize([dict(token="W",source_path=str(path),line_number=2,expected_line="10,1,1",old_literal="1",occurrence=2)])
    assert "10,1,<W>" in owner.template.original_text
    owner.close()


def test_frozen_revision_export_and_actual_batch_allowance(service,tmp_path):
    first = service.add_point(dict(width=2.))
    old = service.revision
    service.configure(service.study.space,service.study.objectives,service.study.constraints,
                      dict(disp="m",mass_eall="kg",stress="MPa"))
    current = service.add_point(dict(width=3.))
    assert service.revision_ids() == (old,service.revision)
    definition = service.revision_definition(old)
    definition["response_units"]["disp"] = "corrupted"
    assert service.revision_definition(old)["response_units"]["disp"] == "mm"
    export = service.export_csv(tmp_path/"old.csv",[first.point_id],revision=old)
    assert "disp [mm]" in export.read_text(encoding="utf-8-sig")
    with pytest.raises(ValueError,match="one frozen"):
        service.export_csv(tmp_path/"mixed.csv",[first.point_id,current.point_id])
    service.set_resource_policy(timeout_seconds=7.5,max_batch_calls=1)
    another = service.add_point(dict(width=2.5))
    with pytest.raises(ValueError,match="allowance"):
        service.submit([current.point_id,another.point_id])
    with pytest.raises(ValueError,match="allowance"):
        service.confirm([current.point_id,another.point_id])
    assert service.workbench.jobs.count == 0
    captured = []
    submit = service.workbench.jobs.submit_batch
    def capture(store,items,**kwargs):
        captured.extend(item.timeout for item in items)
        return submit(store,items,**kwargs)
    service.workbench.jobs.submit_batch = capture
    service.submit([current.point_id])
    assert captured == [7.5]
    saved = service.save_as(tmp_path/"limited.json"); service.close()
    reopened = MDOStudyService(service.workbench).open(saved)
    assert reopened.resource_policy == dict(timeout_seconds=7.5,max_batch_calls=1)
    reopened.close()


def test_historical_full_objective_front_survives_new_objective_ids_and_reopen(service,tmp_path):
    first,second = measured(service,(1.,3.))
    old = service.revision
    assert first.is_pareto and second.is_pareto
    service.configure(service.study.space,[ObjectiveSpec(id="new_mass_only",response_id="mass_eall")],[],service.response_units)
    current1,current2 = measured(service,(2.,3.))
    assert first.revision == old and second.revision == old
    assert first.is_pareto and second.is_pareto
    assert not current1.is_pareto and current2.is_pareto
    assert service.result_file(first.point_id).is_file()
    saved = service.save_as(tmp_path/"historical-fronts.json"); service.close()
    reopened = MDOStudyService(service.workbench).open(saved)
    assert reopened.point_details(first.point_id).is_pareto and reopened.point_details(second.point_id).is_pareto
    assert not reopened.point_details(current1.point_id).is_pareto and reopened.point_details(current2.point_id).is_pareto
    # A missing frozen coordinate cannot be projected onto the remaining one.
    reopened._record(first.point_id).objectives.pop("d")
    reopened._update_pareto_front()
    assert not reopened.point_details(first.point_id).is_pareto
    assert not reopened.is_verified(first.point_id,require_feasible=False)
    reopened.close()


@pytest.mark.parametrize("field",["feasibility","objectives","total_violation"])
def test_native_verification_rejects_corrupted_frozen_assessment(service,field):
    (row,) = measured(service,(4.,))
    assert row.feasibility == "INFEASIBLE"
    if field == "feasibility":
        row.feasibility = "FEASIBLE"
    elif field == "objectives":
        row.objectives["d"] = 0.
    else:
        row.total_violation = 0.
    assert not service.is_verified(row.point_id,require_feasible=False)
    with pytest.raises(ValueError,match="Stored assessment"):
        service.prepare_apply(row.point_id)


import numpy as np
