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
            text = _geometry()+_field([(n,[x,0,0]) for n in (40,10,90,25)])
            text += _field([(n,[10*x,0,0,0,0,0]) for n in (40,10,90,25)],name="STRESS",components=components)
            (folder / "model.frd").write_text(text+" 9999\n",encoding="ascii")
            (folder / "model.dat").write_text(f"total mass for set EALL and time 0.5\n\n {10-x}\n",encoding="ascii")
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
    root.write_bytes(b'\xef\xbb\xbf** literal comment <IGNORE>\r\n*INCLUDE,INPUT=material.inc\r\n*BEAM SECTION,SECTION=RECT\r\n<WIDTH>, 2\r\n')
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
    assert service.input_path.read_bytes().endswith(b'2, 2\r\n')
    assert Path(receipt["files"][0]["backup"]).read_bytes() == original
    assert service.input_path.with_name("user.inp.bak").read_bytes() == original
    service.apply(service.prepare_apply(second.point_id))
    assert service.input_path.read_bytes().endswith(b'3, 2\r\n')
    assert list(service.input_path.parent.glob("user.inp.*.previous.bak"))
    service.undo()
    assert service.input_path.read_bytes().endswith(b'2, 2\r\n')
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


import numpy as np
