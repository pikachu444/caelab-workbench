"""Headless Studio command service using Workbench and native JobManager receipts."""
from __future__ import annotations

import copy
import csv
import json
import math
import os
import re
import shutil
import threading
import uuid
from dataclasses import asdict
from contextlib import ExitStack
from pathlib import Path

import numpy as np
from filelock import FileLock

from caelab.jobs import BatchItem
from caelab.native.input_snapshot import snapshot_input, _includes
from caelab.native.run_store import RunStore, atomic_json
from . import apply_transaction as transaction
from .constraints import ConstraintSpec
from .doe import DOEConfig, generate_design
from .inp_binder import TemplateDeck, scalar_literal_occurrences
from .native_responses import default_selector, validate_selector, extract_native_responses
from .objectives import ObjectiveSpec, evaluate_criteria
from .study_manager import StudyManager, DesignPointRecord
from .variables import (VariableSpace, ContinuousVariable, DiscreteVariable,
                        CategoricalVariable, LinkedVariable)


def _decode_definition(definition):
    classes = {t.__name__:t for t in (ContinuousVariable,DiscreteVariable,CategoricalVariable,LinkedVariable)}
    variables = []
    for raw in definition["variables"]:
        raw = dict(raw)
        variables.append(classes[raw.pop("type")](**raw))
    return dict(space=VariableSpace(variables=tuple(variables)),
                objectives=tuple(ObjectiveSpec(**o) for o in definition["objectives"]),
                constraints=tuple(ConstraintSpec(**c) for c in definition["constraints"]),
                response_units=copy.deepcopy(definition["response_units"]),
                response_selectors=copy.deepcopy(definition["response_selectors"]))


def _resource_policy(timeout_seconds,max_batch_calls):
    if isinstance(timeout_seconds,bool) or not isinstance(timeout_seconds,(int,float)) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Timeout must be finite positive seconds")
    if type(max_batch_calls) is not int or max_batch_calls < 1:
        raise ValueError("Actual calls per batch must be a positive integer")
    return dict(timeout_seconds=float(timeout_seconds),max_batch_calls=max_batch_calls)


def _validate_saved(data):
    def require(value,kind,location):
        if type(value) is not kind:
            raise ValueError(f"{location}: expected {kind.__name__}")
    def text(value,location):
        if not isinstance(value,str) or not value or "\x00" in value:
            raise ValueError(f"{location}: nonempty text required")
    def scalar(value,location):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError(f"{location}: finite scalar required")
    def mapping(value,location,*,numeric=False):
        require(value,dict,location)
        for key,item in value.items():
            text(key,location)
            if numeric:
                scalar(item,location+"."+key)
    def hashes(value,location):
        mapping(value,location)
        if any(not isinstance(h,str) or not re.fullmatch("[0-9a-f]{64}",h) for h in value.values()):
            raise ValueError(f"{location}: SHA256 values required")
    def evidence(value,location):
        mapping(value,location)
        for key in ("run_id","workdir","revision","status","parse_state","mode"):
            text(value.get(key),location+"."+key)
        for key in ("error","log_path"):
            require(value.get(key),str,location+"."+key)
        mapping(value.get("values"),location+".values")
        mapping(value.get("responses"),location+".responses",numeric=True)
        hashes(value.get("files"),location+".files")
    mapping(data,"study")
    for key in ("study_id","name","revision","input_path","run_root","data_root"):
        text(data.get(key),key)
    if not re.fullmatch("[0-9a-f]{32}",data["study_id"]) or not data["revision"].isdigit():
        raise ValueError("Invalid study identity/revision")
    for key in ("input_path",):
        if not Path(data[key]).is_absolute():
            raise ValueError(f"{key}: absolute persisted path required")
    for key in ("originals","templates","definition","revisions","point_sources"):
        mapping(data.get(key),key)
    hashes(data.get("current_hashes"),"current_hashes")
    if set(data["originals"]) != set(data["templates"]) or set(data["templates"]) != set(data["current_hashes"]) or data["input_path"] not in data["templates"]:
        raise ValueError("Saved native closure keys disagree")
    for key in ("originals","templates"):
        for path,raw in data[key].items():
            if not Path(path).is_absolute() or not isinstance(raw,str):
                raise ValueError("Native closure requires absolute file paths and encoded bytes")
            bytes.fromhex(raw).decode("utf-8")
    for definition in (data["definition"],*data["revisions"].values()):
        mapping(definition,"definition")
        for key in ("variables","objectives","constraints"):
            require(definition.get(key),list,"definition."+key)
        mapping(definition.get("response_units"),"definition.response_units")
        mapping(definition.get("response_selectors"),"definition.response_selectors")
        for name,unit in definition["response_units"].items():
            text(unit,"response unit")
            validate_selector(definition["response_selectors"].get(name,{}),unit)
    classes = {t.__name__:t for t in (ContinuousVariable,DiscreteVariable,CategoricalVariable,LinkedVariable)}
    spaces = {}
    for revision,definition in data["revisions"].items():
        if not isinstance(revision,str) or not revision.isdigit():
            raise ValueError("Saved study revisions require numeric identities")
        variables = []
        for raw in definition["variables"]:
            mapping(raw,"variable")
            raw = dict(raw)
            kind = raw.pop("type",None)
            if kind not in classes:
                raise ValueError("Unknown persisted variable domain")
            variables.append(classes[kind](**raw))
        spaces[revision] = VariableSpace(variables=tuple(variables))
        for obj in definition["objectives"]:
            ObjectiveSpec(**obj)
        for con in definition["constraints"]:
            ConstraintSpec(**con)
    if data["revision"] != "0" and data["revisions"].get(data["revision"]) != data["definition"]:
        raise ValueError("Current definition differs from its immutable saved revision")
    require(data.get("records"),list,"records")
    point_ids = set()
    states = {"PENDING","QUEUED","RUNNING","COMPLETED","FAILED","CANCELLED","INTERRUPTED","STOPPED"}
    for row in data["records"]:
        mapping(row,"record")
        for key in ("point_id","name","revision","study_id"):
            text(row.get(key),"record."+key)
        if row["point_id"] in point_ids or row["study_id"] != data["study_id"] or row["revision"] not in data["revisions"]:
            raise ValueError("Invalid or duplicate saved point identity/revision")
        point_ids.add(row["point_id"])
        if row.get("status") not in states or row.get("mode") not in ("ACTUAL","SURROGATE") or row.get("feasibility") not in (None,"NOT_ASSESSED","FEASIBLE","MARGINAL","INFEASIBLE"):
            raise ValueError("Invalid saved point evaluation state")
        require(row.get("index"),int,"record.index")
        require(row.get("is_pareto"),bool,"record.is_pareto")
        require(row.get("error_message"),str,"record.error_message")
        for key in ("duration_sec","total_violation"):
            scalar(row.get(key),"record."+key)
            if row[key] < 0:
                raise ValueError("Saved duration/violation must be nonnegative")
        for key in ("observations","objectives"):
            mapping(row.get(key),"record."+key,numeric=True)
        mapping(row.get("physical_values"),"record.physical_values")
        for value in row["physical_values"].values():
            if not isinstance(value,str):
                scalar(value,"physical value")
        spaces[row["revision"]]._physical(row["physical_values"])
        require(row.get("attempts"),list,"record.attempts")
        for attempt in row["attempts"]:
            evidence(attempt,"attempt")
        for key in ("run_id","source_point_id","batch_id","model_id"):
            if row.get(key) is not None:
                text(row[key],"record."+key)
    for point_id,value in data["point_sources"].items():
        if point_id not in point_ids:
            raise ValueError("Native evidence refers to an unknown point")
        evidence(value,"point_sources."+point_id)
    for key in ("current_point_id","current_rerun_id"):
        if data.get(key) is not None and data[key] not in point_ids:
            raise ValueError("Current design refers to an unknown point")
    for key in ("apply_receipts","undo_stack","optimization_history"):
        require(data.get(key,[]),list,key)
    require(data.get("parameter_bindings",[]),list,"parameter_bindings")
    for binding in data.get("parameter_bindings",[]):
        mapping(binding,"parameter binding")
        for key in ("token","source_path","expected_line","old_literal"):
            text(binding.get(key),"parameter binding."+key)
        for key in ("line_number","occurrence"):
            require(binding.get(key),int,"parameter binding."+key)
            if binding[key] < 1:
                raise ValueError("Parameter binding line/occurrence must be positive")
    if data.get("surrogate_id") is not None and not re.fullmatch("[0-9a-f]{32}",str(data["surrogate_id"])):
        raise ValueError("Invalid surrogate artifact identity")
    require(data.get("view_state",{}),dict,"view_state")
    if len(json.dumps(data.get("view_state",{}))) > 120000:
        raise ValueError("Saved view state is too large")
    if data.get("training_report") is not None:
        mapping(data["training_report"],"training_report")
    require(data.get("sensitivity_reports",[]),list,"sensitivity_reports")
    for entry in data.get("sensitivity_reports",[]):
        mapping(entry,"sensitivity report index")
        if not re.fullmatch("[0-9a-f]{32}",str(entry.get("id"))):
            raise ValueError("Invalid saved sensitivity identity")
        hashes({k:entry.get(k) for k in ("report_sha256","experiment_sha256")},"sensitivity report hashes")
    policy = data.get("resource_policy",dict(timeout_seconds=300,max_batch_calls=1000))
    mapping(policy,"resource_policy")
    _resource_policy(**policy)


class MDOStudyService:
    """Owns study revisions, native attempts, applicability and persisted evidence.

    Notification arguments are defensive copies. Callers marshal notifications to
    their UI thread; the service never imports Qt or a front-end module.
    """

    def __init__(self, workbench, study=None, *, project=None, node_id=None):
        if node_id is not None and (project is None or node_id not in project.nodes):
            raise ValueError("Study association needs an existing project node")
        self.workbench, self.project, self.node_id = workbench, project, node_id
        self.study = study or StudyManager()
        self.study_id = uuid.uuid4().hex
        self.revision = "0"
        self.input_path = None
        self.template = None
        self.response_units = {}
        self.response_selectors = {}
        self.study_file = None
        self.current_point_id = None
        self.current_rerun_id = None
        self.read_only = False
        self.active_batch_id = None
        self.surrogate = None
        self.optimization_result = None
        self.optimization_history = []
        self.view_state = {}
        self.origin_association = None
        self.parameter_bindings = []
        self.training_report = None
        self.sensitivity_reports = []
        self.last_sensitivity_report = None
        self.resource_policy = _resource_policy(300,1000)
        self._root = None
        self._run_root = None
        self._originals = {}
        self._templates = {}
        self._current_hashes = {}
        self._point_sources = {}
        self._revisions = {}
        self._plans = {}
        self._apply_receipts = []
        self._undo_stack = []
        self._dispatch_locks = None
        self._cancel_requested = False
        self._stop_requested = False
        self._lease = None
        self._busy = None
        self._lock = threading.RLock()
        self._project_revision = (transaction.digest(project.path.read_bytes())
                                  if project is not None and project.path and project.path.is_file() else None)

    @property
    def current_deck_path(self):
        return self.input_path

    @property
    def template_deck(self):
        return self.template

    @property
    def point_sources(self):
        return copy.deepcopy(self._point_sources)

    @property
    def source_revisions(self):
        return dict(self._current_hashes)

    def _writable(self, *, idle=True):
        if self.read_only:
            raise PermissionError("This study was opened read-only")
        if idle and (self.active_batch_id or self._busy):
            raise ValueError("Finish or cancel the active study operation first")

    def _acquire(self):
        if self._lease is None:
            self._root.mkdir(parents=True, exist_ok=True)
            self._lease = FileLock(self._root / "study.lock", timeout=0, thread_local=False)
            self._lease.acquire()

    def _changed_sources(self):
        return [p for p, h in self._current_hashes.items()
                if not Path(p).is_file() or transaction.digest(Path(p).read_bytes()) != h]

    def _require_sources(self):
        if not self._current_hashes or self._changed_sources():
            raise ValueError("Native root/INCLUDE changed or is missing; reopen and reconcile the source")

    def open_input(self, path):
        self._writable()
        if self.input_path is not None:
            raise ValueError("Create a new study before opening a different native input")
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() != ".inp":
            raise ValueError("A native CalculiX .inp input is required")
        self.input_path = path
        if self.project is not None:
            if self.project.run_store is None:
                raise ValueError("Save the native project before creating its study")
            self._run_root = Path(self.project.run_store).resolve()
            self._root = self._run_root / "mdo" / self.study_id
        else:
            self._root = path.parent / ".caelab_mdo" / self.study_id
            self._run_root = self._root / "runs"
        try:
            self._acquire()
            manifest = snapshot_input(path, self._root / "template-source")
            self._originals = {f["source_path"]: (self._root / "template-source" / f["source_copy"]).read_bytes()
                               for f in manifest["files"]}
            self._templates = dict(self._originals)
            self._current_hashes = {p: transaction.digest(b) for p, b in self._originals.items()}
            self._refresh_template()
            self._persist()
        except BaseException:
            if self._lease:
                self._lease.release(); self._lease = None
            self.input_path,self._root,self._run_root,self.template = None,None,None,None
            self._originals,self._templates,self._current_hashes = {},{},{}
            raise
        return self.template

    def _refresh_template(self):
        root = TemplateDeck.from_text(self._templates[str(self.input_path)].decode("utf-8"), self.input_path)
        tokens = set(root.token_names)
        for content in self._templates.values():
            text = content.decode("utf-8")
            tokens.update(TemplateDeck.from_text(text).token_names)
            card = ""
            prohibited = {"*NODE","*ELEMENT","*NGEN","*NFILL","*ELGEN","*ELCOPY","*NCOPY","*SYSTEM"}
            for line in text.splitlines():
                line = line.strip().lstrip("\ufeff")
                if line.startswith("**"):
                    continue
                if line.startswith("*"):
                    card = line.split(",")[0].upper()
                if card in prohibited and TemplateDeck.from_text(line).token_names:
                    raise ValueError("Geometry/topology tokens require a native CAD/mesh regeneration adapter")
        if len({t.lower() for t in tokens}) != len(tokens):
            raise ValueError("Case-ambiguous native parameter tokens are not allowed")
        self.template = TemplateDeck(original_text=root.original_text, token_names=tuple(sorted(tokens)), file_path=self.input_path)

    def source_text(self, path=None):
        return self._originals[str(Path(path or self.input_path).resolve())].decode("utf-8")

    def input_sources(self):
        return {p:b.decode("utf-8") for p,b in self._originals.items()}

    def predict_values(self, values):
        from .surrogate import predict
        if self.surrogate is None:
            raise ValueError("Train a surrogate first")
        points = [values] if isinstance(values,dict) else values
        return predict(self.surrogate,np.array([self.study.space.encode(p).vector for p in points]))

    def parameterize(self, bindings):
        self._writable()
        self._require_sources()
        if self.study.records:
            raise ValueError("Parameterize before generating points; use a new study for a changed template")
        combined = copy.deepcopy(self.parameter_bindings)
        for binding in bindings:
            if binding not in combined:
                combined.append(copy.deepcopy(binding))
        templates = dict(self._originals)
        grouped = {}
        allowed = {"*ELASTIC", "*PLASTIC", "*DENSITY", "*CLOAD", "*DLOAD", "*BOUNDARY",
                   "*BEAM SECTION", "*SHELL SECTION", "*SOLID SECTION", "*SPRING", "*DASHPOT",
                   "*CONDUCTIVITY", "*SPECIFIC HEAT", "*EXPANSION", "*AMPLITUDE"}
        for binding in combined:
            p = str(Path(binding["source_path"]).resolve())
            if p not in templates:
                raise ValueError("Parameterization target must belong to the collected input closure")
            token = binding["token"]
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", token):
                raise ValueError("Token needs a scalar parameter name")
            lines = self._originals[p].decode("utf-8").splitlines(keepends=True)
            index = binding["line_number"]-1
            if index < 0 or index >= len(lines) or lines[index].rstrip("\r\n") != binding["expected_line"].rstrip("\r\n"):
                raise ValueError("Parameterization line differs from the preview")
            card = ""
            for line in lines[:index+1]:
                body = line.strip().lstrip("\ufeff")
                if body.startswith("*") and not body.startswith("**"):
                    card = body.split(",")[0].upper()
            if card not in allowed or lines[index].lstrip().startswith("*"):
                raise ValueError("Only scalar material/property/load data can be parameterized; geometry and topology require native regeneration")
            literal = binding["old_literal"]
            matches = scalar_literal_occurrences(lines[index],literal)
            occurrence = binding["occurrence"]-1
            if occurrence < 0 or occurrence >= len(matches):
                raise ValueError("Scalar occurrence is missing from selected line")
            start,end = matches[occurrence]
            grouped.setdefault((p,index), []).append((start,end,"<"+token+">"))
        for (p,index), changes in grouped.items():
            lines = templates[p].decode("utf-8").splitlines(keepends=True)
            intervals = sorted(changes)
            if any(a[1] > b[0] for a,b in zip(intervals, intervals[1:])):
                raise ValueError("Parameter bindings overlap")
            for start,end,token in reversed(intervals):
                lines[index] = lines[index][:start]+token+lines[index][end:]
            templates[p] = "".join(lines).encode("utf-8")
        previous = self._templates
        self._templates = templates
        try:
            self._refresh_template()
        except BaseException:
            self._templates = previous
            self._refresh_template()
            raise
        self.parameter_bindings = combined
        self._persist()
        return self.template.token_names

    def configure(self, space, objectives, constraints, response_units, response_selectors=None):
        self._writable()
        self._require_sources()
        if not objectives or not space.variables:
            raise ValueError("Declare variables and at least one objective")
        ids = {v.id.lower() for v in space.variables}
        if len(ids) != len(space.variables) or not {t.lower() for t in self.template.token_names} <= ids:
            raise ValueError("Variables and native closure tokens must match without case ambiguity")
        required = {o.response_id for o in objectives}
        for c in constraints:
            required.update(c.dependencies if c.expression else (c.response_id,))
        if not required <= response_units.keys():
            raise ValueError("Every criterion response needs a declared unit")
        selectors = {name: validate_selector((response_selectors or {}).get(name) or default_selector(name, unit), unit)
                     for name, unit in response_units.items()}
        self.study.space = space
        self.study.objectives, self.study.constraints = list(objectives), list(constraints)
        self.response_units, self.response_selectors = dict(response_units), selectors
        self.revision = str(int(self.revision)+1)
        self.surrogate = None
        self._revisions[self.revision] = self._definition()
        self._persist()

    def _definition(self):
        return dict(variables=[dict(type=type(v).__name__, **asdict(v)) for v in self.study.space.variables],
                    objectives=[asdict(o) for o in self.study.objectives], constraints=[asdict(c) for c in self.study.constraints],
                    response_units=self.response_units, response_selectors=self.response_selectors)

    def revision_definition(self,revision=None):
        revision = self.revision if revision is None else revision
        if revision == "0" and self.revision == "0":
            definition = self._definition()
        elif revision in self._revisions:
            definition = self._revisions[revision]
        else:
            raise ValueError("Unknown immutable study revision")
        return dict(revision=revision,**_decode_definition(definition))

    def revision_ids(self):
        return tuple(sorted(set(self._revisions)|{self.revision},key=int))

    def set_resource_policy(self,timeout_seconds=300,max_batch_calls=1000):
        self._writable()
        self.resource_policy = _resource_policy(timeout_seconds,max_batch_calls)
        self._persist()
        return dict(self.resource_policy)

    def _add_point(self, values, *, mode="ACTUAL", source_point_id=None):
        values = self.study.space._physical(values)
        rec = self.study.add_point(values)
        rec.mode, rec.revision, rec.study_id = mode, self.revision, self.study_id
        rec.source_point_id = source_point_id
        return rec

    def clone_point(self, point_id, values=None, name=None):
        self._writable()
        source = self._record(point_id)
        row = self._add_point(values if values is not None else source.physical_values,source_point_id=point_id)
        if name:
            row.name = str(name)
        self._persist()
        return copy.deepcopy(row)

    def add_point(self, values, name=None):
        self._writable()
        if self.revision == "0":
            raise ValueError("Configure the study before adding candidates")
        row = self._add_point(values)
        if name:
            row.name = str(name)
        self._persist()
        return copy.deepcopy(row)

    def clone(self, point_id):
        return self.clone_point(point_id)

    def generate_doe(self, n_samples, seed, method="lhs"):
        self._writable()
        if self.revision == "0":
            raise ValueError("Configure the study first")
        if not {t.lower() for t in self.template.token_names} <= {v.id.lower() for v in self.study.space.variables}:
            raise ValueError("Configure the newly parameterized variables before generating points")
        batch = generate_design(self.study.space, DOEConfig(method=method, n_samples=n_samples, seed=seed))
        rows = [self._add_point(p.values) for p in batch.points]
        self._persist()
        return copy.deepcopy(rows)

    def _record(self, point_id):
        try:
            return self.study._record_map[point_id]
        except KeyError:
            raise ValueError(f"Unknown design point: {point_id}") from None

    def _render(self, values):
        result = {}
        for p, data in self._templates.items():
            deck = TemplateDeck.from_text(data.decode("utf-8"))
            rendered = deck.render(values)
            if TemplateDeck.from_text(rendered).token_names:
                raise ValueError("Unresolved native token")
            result[p] = rendered.encode("utf-8")
        return result

    def _execution_contents(self, values):
        rendered = self._render(values)
        layout = {p: ("model.inp" if p == str(self.input_path) else f"includes/{i:06d}.inp")
                  for i,p in enumerate(rendered)}
        answer = {}
        for p, data in rendered.items():
            for start,end,name,_ in reversed(list(_includes(data, Path(p)))):
                source = Path(name)
                target = str((self.input_path.parent / source).resolve() if not source.is_absolute() else source.resolve())
                if target not in layout:
                    raise ValueError("Rendered INCLUDE changed outside immutable closure")
                data = data[:start]+layout[target].encode("ascii")+data[end:]
            answer[layout[p]] = data
        return answer

    def _execution_files(self, values, folder):
        for relative,data in self._execution_contents(values).items():
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        return folder / "model.inp"

    def submit(self, point_ids=None, *, fresh=False, on_progress=None, on_item=None, on_finished=None):
        self._writable()
        if self._root is None:
            raise ValueError("Open a native input before running")
        self.recover_apply()
        self._require_sources()
        if self.revision == "0":
            raise ValueError("Configure the study before running")
        rows = [self._record(p) for p in point_ids] if point_ids is not None else [r for r in self.study.records if r.status == "PENDING" and r.revision == self.revision and r.mode == "ACTUAL"]
        if not rows or any(r.mode != "ACTUAL" or r.revision != self.revision for r in rows):
            raise ValueError("Select current-revision actual candidates; confirm predictions separately")
        if len({r.point_id for r in rows}) != len(rows):
            raise ValueError("Duplicate selected point")
        if len(rows) > self.resource_policy["max_batch_calls"]:
            raise ValueError(f"Batch needs {len(rows)} actual calls; allowance is {self.resource_policy['max_batch_calls']}. Split the selection or change the batch allowance.")
        solver = next((s for s in self.workbench.programs() if s.spec.id == "calculix"), None)
        if solver is None or not solver.available:
            raise ValueError("Configure an available CalculiX executable")
        batch_id = uuid.uuid4().hex
        revision = self.revision
        items = []
        for row in rows:
            folder = self._root / "inputs" / batch_id / row.point_id
            input_path = self._execution_files(row.physical_values, folder)
            from .native_endpoint import expected_static_endpoint
            expected_static_endpoint(input_path)
            row.status, row.batch_id = "QUEUED", batch_id
            items.append(BatchItem(item_id=row.point_id, input_path=input_path, status=solver,
                                   node_id=f"mdo:{self.study_id}:{row.point_id}", units=dict(self.response_units),
                                   unit_source="MDO declared native deck units", timeout=self.resource_policy["timeout_seconds"],
                                   parameters=dict(row.physical_values), metadata=dict(study_id=self.study_id,revision=revision,fresh=fresh)))
        self._dispatch_locks = ExitStack()
        try:
            for path in sorted(self._current_hashes):
                self._dispatch_locks.enter_context(FileLock(path+".caelab-apply.lock",timeout=0,thread_local=False))
            self._require_sources()
        except BaseException:
            self._dispatch_locks.close(); self._dispatch_locks = None
            raise
        self.active_batch_id = batch_id
        self._cancel_requested,self._stop_requested = False,False
        self._persist()

        def progress(prog):
            prog = copy.deepcopy(prog)
            prog.study_id, prog.revision = self.study_id, revision
            if prog.current_item_id in self.study._record_map and self._record(prog.current_item_id).status == "QUEUED":
                self._record(prog.current_item_id).status = "RUNNING"
            if on_progress:
                on_progress(prog)

        def item_finished(item, run):
            with self._lock:
                row = self._record(item.item_id)
                self._ingest_run(row,run,state=item.state,duration=item.duration_sec,error=item.error,batch_id=batch_id)
                self.study.update_pareto_front()
                self._persist()
                snapshot = copy.deepcopy(row)
            if on_item:
                on_item(row.point_id, snapshot, copy.deepcopy(run))

        def finished(batch):
            with self._lock:
                for item in batch.items:
                    row = self._record(item.item_id)
                    if row.status in ("QUEUED", "RUNNING"):
                        row.status = item.state.upper() if item.state in ("failed", "cancelled", "stopped") else "INTERRUPTED"
                        row.error_message = item.error or "No terminal native response receipt"
                        row.feasibility = "NOT_ASSESSED"
                self.active_batch_id = None
                self._persist()
                if self._dispatch_locks:
                    self._dispatch_locks.close(); self._dispatch_locks = None
            if on_finished:
                on_finished(batch_id, batch.state, batch.completed_count, batch.failed_count)
        try:
            self.workbench.jobs.submit_batch(RunStore(self._run_root), items, batch_id=batch_id,
                                             name=self.study.name, on_progress=progress,
                                             on_item_finished=item_finished, on_batch_finished=finished)
            if self.active_batch_id == batch_id:
                if self._cancel_requested:
                    self.workbench.cancel_batch(batch_id)
                elif self._stop_requested:
                    self.workbench.jobs.stop_batch(batch_id)
        except BaseException:
            self.active_batch_id = None
            if self._dispatch_locks:
                self._dispatch_locks.close(); self._dispatch_locks = None
            for row in rows:
                row.status = "FAILED"; row.error_message = "Native batch submission failed"
            self._persist()
            raise
        return batch_id

    def _ingest_run(self,row,run,*,state=None,duration=0.,error="",batch_id=None):
        definition = self._revisions[row.revision]
        objectives = [ObjectiveSpec(**o) for o in definition["objectives"]]
        constraints = [ConstraintSpec(**c) for c in definition["constraints"]]
        row.run_id,row.duration_sec = run.id,duration
        row.error_message = error or run.error
        row.observations,row.objectives = {},{}
        row.feasibility,row.total_violation = "NOT_ASSESSED",0.
        row.status = (state or run.state).upper()
        try:
            if run.state != "completed" or run.returncode != 0:
                raise ValueError(row.error_message or f"Native run ended {run.state}")
            row.observations = extract_native_responses(run.workdir,definition["response_selectors"])
            assessment = evaluate_criteria(row.observations,objectives,constraints,units=definition["response_units"])
            if not assessment.assessed:
                raise ValueError("Required native responses could not be assessed")
            row.feasibility = assessment.constraints.feasibility.value
            row.total_violation = assessment.constraints.total_violation or 0.
            row.objectives = {o.id:float(v) for o,v in zip(objectives,assessment.objectives)}
            row.status = "COMPLETED"
        except (ValueError,OSError,KeyError,OverflowError) as exc:
            if row.status not in ("CANCELLED","INTERRUPTED"):
                row.status = "FAILED"
            row.error_message = str(exc)
        evidence = dict(run_id=run.id,workdir=str(run.workdir),values=dict(row.physical_values),revision=row.revision,
                        responses=dict(row.observations),files=self._run_hashes(run),batch_id=batch_id or row.batch_id,
                        status=row.status,parse_state=run.parse_state,error=row.error_message,log_path=run.log_path,mode="ACTUAL")
        evidence.update(feasibility=row.feasibility,total_violation=row.total_violation,objectives=dict(row.objectives),
                        duration_sec=row.duration_sec,finished_at=run.finished_at,sequence=len(row.attempts)+1)
        row.attempts.append(copy.deepcopy(evidence))
        self._point_sources[row.point_id] = evidence

    @staticmethod
    def _run_hashes(run):
        expected = {}
        for item in run.snapshot.get("files", []):
            for path_key, hash_key in (("source_copy","original_sha256"),("execution_path","execution_sha256")):
                expected[item[path_key]] = item[hash_key]
        for key in ("outputs", "artifacts"):
            expected.update(run.snapshot.get(key, {}))
        if Path(run.log_path).is_file():
            expected["job.log"] = transaction.digest(Path(run.log_path).read_bytes())
        if (run.workdir/"model.sta").is_file():
            expected["model.sta"] = transaction.digest((run.workdir/"model.sta").read_bytes())
        return expected

    def point_evidence(self, point_id):
        return copy.deepcopy(self._point_sources.get(point_id, {}))

    def point_details(self, point_id):
        return copy.deepcopy(self._record(point_id))

    def record_snapshot(self, point_id):
        return self.point_details(point_id)

    def _verified_run(self, point_id, *, require_feasible=False, applicable=True):
        row = self._record(point_id)
        if row.mode != "ACTUAL" or row.status != "COMPLETED" or not row.run_id:
            raise ValueError("A completed actual native attempt is required")
        if require_feasible and row.feasibility != "FEASIBLE":
            raise ValueError("Point is not feasible")
        if applicable and row.revision != self.revision:
            raise ValueError("Point belongs to an older study revision")
        if applicable:
            self._require_sources()
        evidence = self._point_sources[point_id]
        if evidence["values"] != row.physical_values or evidence["responses"] != row.observations:
            raise ValueError("Point differs from its native receipt")
        workdir = Path(evidence["workdir"]).resolve()
        run = RunStore(workdir.parent).read(workdir / "job.json")
        if run.id != row.run_id or run.state != "completed" or run.returncode != 0:
            raise ValueError("Native attempt receipt is not completed")
        expected = self._run_hashes(run)
        if not expected or any(evidence["files"].get(p) != h for p,h in expected.items()):
            raise ValueError("Native receipt changed since response extraction")
        for relative,h in evidence["files"].items():
            path = (workdir / relative).resolve()
            if not path.is_relative_to(workdir) or not path.is_file() or transaction.digest(path.read_bytes()) != h:
                raise ValueError("Native input/output/log artifact is missing or damaged")
        if applicable:
            execution = self._execution_contents(row.physical_values)
            source_root = Path(run.input_path).parent
            source_entries = run.snapshot.get("files",[])
            if len(source_entries) != len(execution):
                raise ValueError("Native attempt closure differs from the immutable study template")
            for entry in source_entries:
                try:
                    relative = Path(entry["source_path"]).relative_to(source_root).as_posix()
                except ValueError:
                    raise ValueError("Native attempt source escapes its prepared point directory") from None
                if relative not in execution or (workdir/entry["source_copy"]).read_bytes() != execution[relative]:
                    raise ValueError("Native attempt parameters differ from the selected study point")
        selectors = self._revisions[row.revision]["response_selectors"]
        if extract_native_responses(workdir, selectors) != row.observations:
            raise ValueError("Native responses differ from recorded values")
        return run

    def is_verified(self, point_id, *, require_feasible=True):
        if self.active_batch_id:
            return False
        try:
            self._verified_run(point_id, require_feasible=require_feasible)
            return True
        except (ValueError, OSError, KeyError, TypeError):
            return False

    def stop_scheduling(self):
        self._writable(idle=False)
        if not self.active_batch_id:
            return False
        self._stop_requested = True
        self.workbench.jobs.stop_batch(self.active_batch_id)
        return True

    def cancel(self):
        self._writable(idle=False)
        if not self.active_batch_id:
            return False
        self._cancel_requested = True
        self.workbench.cancel_batch(self.active_batch_id)
        return True

    def confirm(self, point_ids, **callbacks):
        self._writable()
        if len(point_ids) > self.resource_policy["max_batch_calls"]:
            raise ValueError("Fresh confirmation exceeds the actual calls per batch allowance")
        clones = [self._add_point(self._record(p).physical_values, source_point_id=p) for p in point_ids]
        return self.submit([r.point_id for r in clones], fresh=True, **callbacks)

    def prepare_apply(self, point_id):
        self._writable()
        self._verified_run(point_id)
        row = self._record(point_id)
        rendered = self._render(row.physical_values)
        plan = dict(id=uuid.uuid4().hex, point_id=point_id, run_id=row.run_id, revision=self.revision,
                    values=dict(row.physical_values), feasibility=row.feasibility,
                    violations=[] if row.feasibility == "FEASIBLE" else [f"{row.feasibility}; total normalized violation {row.total_violation:g}"],
                    targets=[dict(path=p,before_sha256=self._current_hashes[p],after_sha256=transaction.digest(b)) for p,b in rendered.items()])
        self._plans[plan["id"]] = (copy.deepcopy(plan),rendered)
        return copy.deepcopy(plan)

    def _commit_current(self, replacements, expected, metadata):
        self._ensure_project_unchanged()
        target_locks = ExitStack()
        try:
            for path in sorted(expected):
                target_locks.enter_context(FileLock(path+".caelab-apply.lock",timeout=0))
            store_lease = RunStore(self._run_root).lease()
        except BaseException:
            target_locks.close()
            raise
        old_hashes = dict(self._current_hashes)
        old_point = self.current_point_id
        old_rerun = self.current_rerun_id
        old_stack = list(self._undo_stack)
        old_project = copy.deepcopy(self.project.__dict__) if self.project is not None else None
        project_saved = False
        try:
            metadata = dict(metadata,previous_current_rerun_id=old_rerun)
            receipt = transaction.replace_closure(self._root, replacements, expected, metadata,target_locks_owned=True)
            self._current_hashes = {p: transaction.digest(Path(p).read_bytes()) for p in expected}
            self.current_point_id = metadata.get("point_id")
            self.current_rerun_id = None
            self._apply_receipts.append(receipt)
            if metadata["operation"] == "apply":
                self._undo_stack.append(receipt["id"])
            elif metadata["operation"] == "undo":
                self._undo_stack.pop()
            try:
                self._persist()
                if self.project is not None and self.node_id:
                    self.project.invalidate(self.node_id,"MDO native deck parameters changed; separate current rerun required",include_self=True)
                    reference = self.project.input_file(self.node_id)
                    if reference and Path(reference.path).resolve() == self.input_path:
                        reference.revision = self._current_hashes[str(self.input_path)]
                    self._associate()
                    project_saved = True
                transaction.finish(self._root, receipt)
            except BaseException:
                rollback = transaction.recover(self._root,finalize=False)
                self._current_hashes, self.current_point_id = old_hashes, old_point
                self.current_rerun_id = old_rerun
                self._apply_receipts.pop()
                self._undo_stack = old_stack
                self._persist()
                if old_project is not None:
                    self.project.__dict__.clear(); self.project.__dict__.update(old_project)
                    if project_saved:
                        self.workbench.save_native_project(self.project,expected_revision=self._project_revision)
                        self._project_revision = transaction.digest(self.project.path.read_bytes())
                transaction.finish(self._root,rollback)
                raise
            return copy.deepcopy(receipt)
        finally:
            store_lease.release()
            target_locks.close()

    def apply(self, plan, *, allow_infeasible=False):
        self._writable()
        stored, rendered = self._plans.get(plan.get("id"), (None,None))
        if stored is None or stored != plan or plan["revision"] != self.revision:
            raise ValueError("Apply preview is stale or differs from the prepared plan")
        self._verified_run(plan["point_id"])
        if plan["feasibility"] != "FEASIBLE" and not allow_infeasible:
            raise ValueError("Explicit acceptance of displayed violated criteria is required")
        self._busy = "apply"
        try:
            return self._commit_current(rendered, {t["path"]:t["before_sha256"] for t in plan["targets"]},
                                        dict(point_id=plan["point_id"],run_id=plan["run_id"],revision=self.revision,
                                             previous_point_id=self.current_point_id,operation="apply"))
        finally:
            self._busy = None
            self._plans.clear()

    def undo(self):
        self._writable()
        if not self._undo_stack:
            raise ValueError("No current-design transaction to undo")
        previous = next(r for r in self._apply_receipts if r["id"] == self._undo_stack[-1])
        replacements = {f["path"]:Path(f["backup"]).read_bytes() for f in previous["files"]}
        if any(transaction.digest(replacements[f["path"]]) != f["before_sha256"] for f in previous["files"]):
            raise ValueError("Undo backup is damaged")
        self._busy = "apply"
        try:
            return self._commit_current(replacements, dict(self._current_hashes),
                                        dict(point_id=previous.get("previous_point_id"),run_id=previous.get("run_id"),
                                             revision=self.revision, previous_point_id=self.current_point_id,
                                             undo_receipt_id=previous["id"],operation="undo"))
        finally:
            self._busy = None

    def recover_apply(self):
        self._writable()
        if self._root is None:
            raise ValueError("Open a native input before recovering an apply")
        with ExitStack() as locks:
            if (self._root / "apply-pending.json").exists():
                for path in sorted(self._current_hashes):
                    locks.enter_context(FileLock(path+".caelab-apply.lock",timeout=0))
                store_lease = RunStore(self._run_root).lease()
                locks.callback(store_lease.release)
            result = transaction.recover(self._root,finalize=False)
            if result:
                self._current_hashes = {p:transaction.digest(Path(p).read_bytes()) for p in self._current_hashes}
                self.current_point_id = result.get("previous_point_id")
                self.current_rerun_id = result.get("previous_current_rerun_id")
                self._apply_receipts.append(result)
                self._undo_stack = [i for i in self._undo_stack if i != result["id"]]
                if result.get("operation") == "undo" and result.get("undo_receipt_id") not in self._undo_stack:
                    self._undo_stack.append(result["undo_receipt_id"])
                self._persist()
                transaction.finish(self._root,result)
            return result

    def rerun_current(self, **callbacks):
        self._writable()
        if self.current_point_id is None:
            raise ValueError("Apply an actual point before the separate current rerun")
        row = self._add_point(self._record(self.current_point_id).physical_values, source_point_id=self.current_point_id)
        self.current_rerun_id = row.point_id
        return self.submit([row.point_id], fresh=True, **callbacks)

    def result_file(self, point_id=None, *, current=False):
        from caelab.native.evidence import native_results_file
        point_id = self.current_rerun_id if current else point_id
        if not point_id:
            raise ValueError("Select an actual historical run or perform the separate current rerun")
        return native_results_file(self._verified_run(point_id, applicable=current))

    def open_result(self, point_id=None, *, current=False):
        self._writable(idle=False)
        from caelab.connections import launch_request
        document = self.result_file(point_id, current=current)
        status = next(s for s in self.workbench.programs() if s.spec.id == "paraview")
        return self.workbench.jobs.launch(launch_request(status, document))

    def train(self, response_ids=None, config=None, *, holdout_fraction=.2,validation_seed=None,chronological=False):
        from .surrogate import SurrogateDataset, fit_surrogate, save_surrogate, grouped_holdout
        self._writable()
        seed = (config.seed if config else 0) if validation_seed is None else validation_seed
        if type(seed) is not int or seed < 0 or type(chronological) is not bool:
            raise ValueError("Validation needs a nonnegative integer seed and explicit chronological policy")
        self._busy = "train"
        try:
            response_ids = (response_ids,) if isinstance(response_ids,str) else tuple(response_ids or self.response_units)
            rows = [r for r in self.study.records if r.mode == "ACTUAL" and r.revision == self.revision and self.is_verified(r.point_id,require_feasible=False)]
            if not rows:
                raise ValueError("Verified actual native rows are required for training")
            if chronological:
                if any(not r.attempts or not r.attempts[-1].get("finished_at") for r in rows):
                    raise ValueError("Chronological holdout requires native completion timestamps")
                rows.sort(key=lambda r:(r.attempts[-1]["finished_at"],r.index))
            encoded = [self.study.space.encode(r.physical_values) for r in rows]
            dataset = SurrogateDataset(x=np.array([p.vector for p in encoded]),
                                       y=np.array([[r.observations[k] for k in response_ids] for r in rows]),
                                       point_ids=tuple(r.point_id for r in rows),run_ids=tuple(r.run_id for r in rows),
                                       response_ids=response_ids,units=tuple(self.response_units[k] for k in response_ids),
                                       feature_order=encoded[0].feature_order, training_revision=self.revision,
                                       continuous_only=all(isinstance(v,ContinuousVariable) for v in self.study.space.independent_variables))
            split = grouped_holdout(dataset,fraction=holdout_fraction,seed=seed,chronological=chronological)
            model = fit_surrogate(dataset,config,split,model_id=uuid.uuid4().hex)
            save_surrogate(model,self._root / "models" / model.id)
            report = dict(model_id=model.id,revision=self.revision,
                          split_policy=dict(method=split.method,fraction=holdout_fraction,seed=seed,chronological=chronological),
                          ordering="native attempt completion time" if chronological else "study record order",
                          train_point_ids=[dataset.point_ids[i] for i in split.train_indices],
                          validation_point_ids=[dataset.point_ids[i] for i in split.validation_indices],
                          train_run_ids=[dataset.run_ids[i] for i in split.train_indices],
                          validation_run_ids=[dataset.run_ids[i] for i in split.validation_indices],
                          response_ids=list(dataset.response_ids),units=list(dataset.units),
                          qualification_limits=list(model.config.qualification_rmse),metrics=[asdict(m) for m in model.metrics],
                          qualified=model.qualified,warnings=list(model.warnings))
            atomic_json(self._root/"models"/model.id/"validation.json",report)
            self.surrogate = model
            self.training_report = report
            self._persist()
            return model
        finally:
            self._busy = None

    def analyze_sensitivity(self,response_id,*,method="spearman",point_ids=None,config=None):
        """Analyze existing actual ranks or a frozen predictor's complete paired design."""
        from .sensitivity import (spearman,SensitivityConfig,generate_sensitivity_design,
                                  SensitivityObservation,analyze_sensitivity)
        if self.active_batch_id or self._busy:
            raise ValueError("Finish the active study operation before sensitivity analysis")
        if response_id not in self.response_units or method not in ("spearman","sobol","morris"):
            raise ValueError("Choose a declared response and Spearman, Sobol or Morris")
        self._busy = "sensitivity"
        try:
            experiment_id = uuid.uuid4().hex
            report = dict(experiment_id=experiment_id,method=method,response_id=response_id,unit=self.response_units[response_id],
                          revision=self.revision,model_id=None,qualified=None,qualification_limits=[],seed=None,
                          native_calls=0,point_ids=[],excluded_rows=[],paired_rows=[],metadata={},warnings=[])
            experiment = dict(id=experiment_id,method=method,revision=self.revision,response_id=response_id)
            if method == "spearman":
                if config is not None:
                    raise ValueError("Spearman uses existing actual pairs and has no stochastic configuration")
                rows = [self._record(p) for p in point_ids] if point_ids is not None else [
                    r for r in self.study.records if r.mode == "ACTUAL" and r.revision == self.revision]
                if len({r.point_id for r in rows}) != len(rows) or any(r.mode != "ACTUAL" or r.revision != self.revision for r in rows):
                    raise ValueError("Spearman selection must contain distinct ACTUAL points from the current revision")
                variables = [v for v in self.study.space.variables if not isinstance(v,CategoricalVariable)]
                if not rows or not variables:
                    raise ValueError("Spearman needs actual rows and ordered numeric variables; categories are not numeric distances")
                x = np.array([[r.physical_values[v.id] for v in variables] for r in rows],dtype=float)
                y = np.full(len(rows),np.nan)
                included = []
                replicates = {}
                for i,row in enumerate(rows):
                    try:
                        self._verified_run(row.point_id,require_feasible=False)
                        value = row.observations[response_id]
                        if not math.isfinite(value):
                            raise ValueError("Requested response is not finite")
                        y[i] = value; included.append(row.point_id)
                        key = json.dumps(row.physical_values,sort_keys=True,allow_nan=False)
                        replicates.setdefault(key,[]).append(row.point_id)
                    except (ValueError,OSError,KeyError,TypeError) as error:
                        report["excluded_rows"].append(dict(point_id=row.point_id,reason=str(error)))
                pairs = spearman(x,y,variable_ids=[v.id for v in variables],response_ids=[response_id])
                pair_details = []
                for pair in pairs:
                    detail = asdict(pair)
                    detail.update(point_ids=[rows[i].point_id for i in pair.row_indices],run_ids=[rows[i].run_id for i in pair.row_indices])
                    pair_details.append(detail)
                groups = [ids for ids in replicates.values() if len(ids)>1]
                report.update(source_mode="ACTUAL",variable_ids=[v.id for v in variables],sample_count=len(included),
                              point_ids=[r.point_id for r in rows],
                              estimates={k:[getattr(pair,k) for pair in pairs] for k in ("rho","pvalue","sample_count","missing_count")},
                              metadata=dict(pairs=pair_details,requested_count=len(rows),included_point_ids=included,
                                            response_unit=self.response_units[response_id],metric_unit="1",interval_method=None,
                                            excluded_variables=[v.id for v in self.study.space.variables if isinstance(v,CategoricalVariable)],
                                            replicated_point_groups=groups,replicate_count=sum(len(g)-1 for g in groups)))
                if groups:
                    report["warnings"].append("Replicate rows are retained; rank p-values assume independent observations and are not confidence intervals")
                experiment.update(point_ids=report["point_ids"],rows=[dict(point_id=r.point_id,run_id=r.run_id,values=r.physical_values,
                                                                          response=None if not np.isfinite(y[i]) else float(y[i])) for i,r in enumerate(rows)])
            else:
                if point_ids is not None:
                    raise ValueError("Paired Sobol/Morris uses a complete generated design, unaffected by table selection or filtering")
                model = self.surrogate
                if model is None or model.dataset.training_revision != self.revision or not model.metrics:
                    raise ValueError("Train and validate a surrogate at the current revision before paired sensitivity")
                if response_id not in model.dataset.response_ids:
                    raise ValueError("The captured surrogate does not contain the requested response")
                config = config or SensitivityConfig(method=method)
                if config.method != method:
                    raise ValueError("Sensitivity configuration and selected method disagree")
                design = generate_sensitivity_design(self.study.space,config)
                physical = [self.study.space._physical(dict(zip(design.variable_ids,row))) for row in design.values]
                encoded = [self.study.space.encode(values,purpose="surrogate") for values in physical]
                if any(point.feature_order != model.dataset.feature_order for point in encoded):
                    raise ValueError("Captured surrogate feature order differs from the sensitivity design")
                from .surrogate import predict
                prediction = predict(model,np.array([point.vector for point in encoded]))
                if prediction.model_id != model.id or prediction.training_revision != self.revision or prediction.response_ids != model.dataset.response_ids or prediction.units != model.dataset.units:
                    raise ValueError("Prediction provenance differs from the captured validated surrogate")
                column = prediction.response_ids.index(response_id)
                observations = {row_id:SensitivityObservation(value=float(prediction.mean[i,column]),mode="SURROGATE",source_id=model.id)
                                for i,row_id in enumerate(design.row_ids)}
                result = analyze_sensitivity(design,observations,output_unit=self.response_units[response_id])
                report.update(source_mode="SURROGATE",variable_ids=list(result.variable_ids),
                              estimates={k:v.tolist() for k,v in result.estimates.items()},sample_count=result.sample_count,
                              model_id=model.id,qualified=model.qualified,qualification_limits=list(model.config.qualification_rmse),
                              model_response_ids=list(model.dataset.response_ids),model_response_units=list(model.dataset.units),
                              qualification_limit=model.config.qualification_rmse[column] if model.config.qualification_rmse else None,
                              seed=result.seed,analyzer_seed=result.analyzer_seed,output_variance=result.output_variance,
                              metadata=dict(result.metadata),warnings=list(result.warnings),
                              paired_rows=[dict(row_id=row_id,physical_values=physical[i],prediction=float(prediction.mean[i,column]),
                                                model_id=model.id,mode="SURROGATE",domain_warning=bool(prediction.domain_warning[i]))
                                           for i,row_id in enumerate(design.row_ids)])
                report["metadata"].update(interval_scope="sampling/bootstrap confidence interval conditional on this surrogate; excludes surrogate/model error",
                                          outside_training_count=int(np.count_nonzero(prediction.domain_warning)),
                                          training_revision=model.dataset.training_revision,current_revision=self.revision,
                                          validation_metrics=[asdict(metric) for metric in model.metrics])
                report["warnings"].append("Sensitivity uses deterministic surrogate means; bootstrap intervals exclude surrogate/model error")
                if not model.qualified:
                    report["warnings"].append("Surrogate is unqualified; this sensitivity is conditional exploratory prediction")
                if np.any(prediction.domain_warning):
                    report["warnings"].append("Some paired predictions are outside the observed training range")
                experiment.update(config=asdict(config),variable_ids=list(design.variable_ids),bounds=design.bounds.tolist(),
                                  values=design.values.tolist(),row_ids=list(design.row_ids),sampler_version=design.sampler_version,
                                  analyzer_seed=design.analyzer_seed,distribution=design.distribution,model_id=model.id)
            if not self.read_only:
                folder = self._root/"sensitivity"/experiment_id
                atomic_json(folder/"experiment.json",experiment)
                atomic_json(folder/"report.json",report)
                self.sensitivity_reports.append(dict(id=experiment_id,method=method,response_id=response_id,revision=self.revision,
                                                     source_mode=report["source_mode"],model_id=report["model_id"],
                                                     report_sha256=transaction.digest((folder/"report.json").read_bytes()),
                                                     experiment_sha256=transaction.digest((folder/"experiment.json").read_bytes())))
                self._persist()
            self.last_sensitivity_report = copy.deepcopy(report)
            return copy.deepcopy(report)
        finally:
            self._busy = None

    def sensitivity_report(self,experiment_id=None):
        experiment_id = experiment_id or (self.sensitivity_reports[-1]["id"] if self.sensitivity_reports else None)
        entry = next((item for item in self.sensitivity_reports if item["id"] == experiment_id),None)
        if entry is None or not re.fullmatch("[0-9a-f]{32}",experiment_id):
            raise ValueError("Unknown saved sensitivity experiment")
        folder = self._root/"sensitivity"/experiment_id
        for name,key in (("report.json","report_sha256"),("experiment.json","experiment_sha256")):
            if transaction.digest((folder/name).read_bytes()) != entry[key]:
                raise ValueError("Saved sensitivity experiment/report integrity failed")
        return json.loads((folder/"report.json").read_text(encoding="utf-8"))

    def optimize(self, config=None):
        from .optimize import OptimizationProblem, EvaluationObservation, optimize
        from .surrogate import predict
        self._writable()
        if self.surrogate is None or self.surrogate.dataset.training_revision != self.revision:
            raise ValueError("Train a surrogate at the current study revision")
        model = self.surrogate
        required = set(self.response_units)
        if not required <= set(model.dataset.response_ids):
            raise ValueError("Train all objective and constraint responses before constrained search")
        service = self
        class Predictor:
            def evaluate_batch(self, requests):
                result = predict(model,np.array([service.study.space.encode(r.values).vector for r in requests]))
                return {r.id:EvaluationObservation(request_id=r.id,point_id=r.point_id,mode="SURROGATE",source_id=model.id,
                                                   responses=dict(zip(result.response_ids,result.mean[i]))) for i,r in enumerate(requests)}
        self._busy = "optimize"
        try:
            problem = OptimizationProblem(space=self.study.space,objectives=tuple(self.study.objectives),constraints=tuple(self.study.constraints),
                                          mode="SURROGATE",revision=self.revision,criteria_revision=self.revision,response_units=self.response_units)
            result = optimize(problem,Predictor(),config)
            for point in result.predicted_archive:
                row = self._add_point(point.values,mode="SURROGATE")
                row.observations, row.objectives = dict(point.responses),dict(zip((o.id for o in self.study.objectives),map(float,point.objectives)))
                row.status, row.feasibility, row.total_violation = "COMPLETED",point.feasibility,point.total_violation
                row.model_id = model.id
            self.optimization_result = result
            self.optimization_history.append(dict(model_id=model.id,revision=self.revision,algorithm=result.algorithm,
                                                   config=asdict(result.config),trace=result.trace,
                                                   budget_usage=asdict(result.budget_usage),termination_reason=result.termination_reason,
                                                   predicted_point_ids=[r.point_id for r in self.study.records if r.model_id == model.id]))
            self.study.update_pareto_front()
            self._persist()
            return result
        finally:
            self._busy = None

    def _document(self, destination=None):
        destination = Path(destination or self._root/"study.json")
        records = [asdict(r) for r in self.study.records]
        sources = copy.deepcopy(self._point_sources)
        for evidence in [*sources.values(),*(a for r in records for a in r["attempts"])]:
            evidence["workdir"] = evidence["run_id"]
            evidence["log_path"] = "job.log" if evidence.get("log_path") else ""
        return dict(format="caelab-mdo-study",version=2,study_id=self.study_id,name=self.study.name,revision=self.revision,
                    input_path=str(self.input_path),templates={p:b.hex() for p,b in self._templates.items()},
                    originals={p:b.hex() for p,b in self._originals.items()},current_hashes=self._current_hashes,
                    definition=self._definition(),revisions=self._revisions,records=records,
                    point_sources=sources,current_point_id=self.current_point_id,current_rerun_id=self.current_rerun_id,
                    apply_receipts=self._apply_receipts,undo_stack=self._undo_stack,
                    run_root=os.path.relpath(self._run_root,destination.parent),data_root=os.path.relpath(self._root,destination.parent),
                    run_references="relative-to-run-root",
                    surrogate_id=self.surrogate.id if self.surrogate else None,view_state=self.view_state,
                    optimization_history=self.optimization_history,
                    origin_association=self.origin_association,
                    parameter_bindings=self.parameter_bindings,
                    training_report=self.training_report,sensitivity_reports=self.sensitivity_reports,
                    resource_policy=self.resource_policy,
                    project_path=str(self.project.path) if self.project and self.project.path else None,node_id=self.node_id)

    def _persist(self):
        if self.read_only or self._root is None:
            return
        atomic_json(self._root / "study.json",self._document())
        if self.study_file and self.study_file != self._root / "study.json":
            atomic_json(self.study_file,self._document(self.study_file))

    def _associate(self):
        if self.project is not None and self.node_id:
            self._ensure_project_unchanged()
            from caelab.project import FileReference
            path = self.study_file or self._root / "study.json"
            self.project.nodes[self.node_id].files["mdo_study"] = FileReference(str(path),transaction.digest(path.read_bytes()))
            if self.project.path:
                self.workbench.save_native_project(self.project,expected_revision=self._project_revision)
                self._project_revision = transaction.digest(self.project.path.read_bytes())

    def _ensure_project_unchanged(self):
        if self.project is not None and self.project.path and self._project_revision is not None:
            if not self.project.path.is_file() or transaction.digest(self.project.path.read_bytes()) != self._project_revision:
                raise ValueError("Parent project changed in another window; reopen before updating its study relation")

    def save(self, path=None):
        self._writable()
        if path is not None and Path(path).resolve() != self.study_file:
            return self.save_as(path)
        if self._root is None:
            raise ValueError("Open a native input before saving")
        self.study_file = self.study_file or self._root / "study.json"
        self._persist(); self._associate()
        return self.study_file

    def save_as(self, path):
        self._writable()
        if self._root is None:
            raise ValueError("Open a native input before saving")
        if (self._root / "apply-pending.json").exists():
            raise ValueError("Recover the interrupted current-design transaction before Save As")
        destination = Path(path).expanduser().resolve()
        if destination == self.study_file:
            return self.save()
        target = destination.with_suffix(".mdo-data")
        if destination.exists() or target.exists() or target.is_relative_to(self._root):
            raise ValueError("Save As requires a new independent file and sidecar directory")
        destination.parent.mkdir(parents=True,exist_ok=True)
        old_root, old_runs, old_file = self._root,self._run_root,self.study_file
        old_project,old_node,old_project_revision,old_origin = self.project,self.node_id,self._project_revision,self.origin_association
        old_sources = copy.deepcopy(self._point_sources)
        old_attempts = {r.point_id:copy.deepcopy(r.attempts) for r in self.study.records}
        lease = RunStore(old_runs).lease()
        target_lease = None
        try:
            shutil.copytree(old_root,target,ignore=shutil.ignore_patterns("*.lock","runs","inputs"))
            run_target = target / "runs"
            run_target.mkdir()
            owned = {a["run_id"] for r in self.study.records for a in r.attempts if a.get("run_id")}
            for run_id in owned:
                source = old_runs / run_id
                shutil.copytree(source,run_target / run_id)
                run = RunStore(run_target).read(run_target / run_id / "job.json")
                RunStore(run_target).write(run)
            def rebase(evidence):
                evidence["workdir"] = str(run_target / evidence["run_id"])
                evidence["log_path"] = str(run_target / evidence["run_id"] / "job.log")
            for evidence in self._point_sources.values():
                rebase(evidence)
            for row in self.study.records:
                for evidence in row.attempts:
                    rebase(evidence)
            self._root,self._run_root,self.study_file = target,run_target,destination
            if old_project is not None:
                self.origin_association = dict(project_path=str(old_project.path),node_id=old_node,study_file=str(old_file or old_root/"study.json"))
                self.project,self.node_id,self._project_revision = None,None,None
            target_lease = FileLock(target / "study.lock",timeout=0,thread_local=False)
            target_lease.acquire()
            self._persist()
            if self._lease:
                self._lease.release()
            self._lease = target_lease
            return destination
        except BaseException:
            if target_lease is not None:
                target_lease.release()
            self._root,self._run_root,self.study_file = old_root,old_runs,old_file
            self.project,self.node_id,self._project_revision,self.origin_association = old_project,old_node,old_project_revision,old_origin
            self._point_sources = old_sources
            for row in self.study.records:
                row.attempts = old_attempts[row.point_id]
            if target.exists():
                shutil.rmtree(target)
            destination.unlink(missing_ok=True)
            raise
        finally:
            lease.release()

    def open(self, path, *, read_only=False):
        if self._root is not None:
            raise ValueError("Open a saved study in a new service")
        path = Path(path).expanduser().resolve()
        data = json.loads(path.read_text(encoding="utf-8"),parse_constant=lambda x: (_ for _ in ()).throw(ValueError("Nonfinite study data")))
        if not isinstance(data,dict) or data.get("format") != "caelab-mdo-study" or data.get("version") != 2:
            raise ValueError("Unsupported study format; open the original deck to create a current service study")
        _validate_saved(data)
        self.study_id,self.revision,self.read_only = data["study_id"],data["revision"],read_only
        self.input_path,self.study_file = Path(data["input_path"]),path
        self._root,self._run_root = (path.parent/Path(data["data_root"])).resolve(),(path.parent/Path(data["run_root"])).resolve()
        if not self._root.is_dir():
            raise ValueError("Study sidecar directory is missing")
        self._originals = {p:bytes.fromhex(b) for p,b in data["originals"].items()}
        self._templates = {p:bytes.fromhex(b) for p,b in data["templates"].items()}
        self._current_hashes,self._revisions = data["current_hashes"],data["revisions"]
        self._refresh_template()
        definition = data["definition"]
        decoded = _decode_definition(definition)
        self.study = StudyManager(data["name"],space=decoded["space"],objectives=decoded["objectives"],constraints=decoded["constraints"])
        self.response_units,self.response_selectors = decoded["response_units"],decoded["response_selectors"]
        for raw in data["records"]:
            row = DesignPointRecord(**raw)
            if row.point_id in self.study._record_map:
                raise ValueError("Duplicate saved point IDs")
            self.study.records.append(row); self.study._record_map[row.point_id] = row
        self._point_sources = data["point_sources"]
        if data.get("run_references") == "relative-to-run-root":
            for evidence in [*self._point_sources.values(),*(a for r in self.study.records for a in r.attempts)]:
                evidence["workdir"] = str(self._run_root/evidence["run_id"])
                evidence["log_path"] = str(self._run_root/evidence["run_id"]/"job.log") if evidence.get("log_path") else ""
        self.current_point_id,self.current_rerun_id = data["current_point_id"],data["current_rerun_id"]
        self._apply_receipts = data["apply_receipts"]
        self._undo_stack = data.get("undo_stack",[])
        self.view_state = data.get("view_state",{})
        if not isinstance(self.view_state,dict) or len(json.dumps(self.view_state)) > 120000:
            raise ValueError("Saved view state must be a bounded JSON object")
        self.optimization_history = data.get("optimization_history",[])
        self.origin_association = data.get("origin_association")
        self.parameter_bindings = data.get("parameter_bindings",[])
        self.training_report = data.get("training_report")
        self.sensitivity_reports = data.get("sensitivity_reports",[])
        self.resource_policy = _resource_policy(**data.get("resource_policy",dict(timeout_seconds=300,max_batch_calls=1000)))
        if data.get("surrogate_id"):
            from .surrogate import load_surrogate
            self.surrogate = load_surrogate(self._root / "models" / data["surrogate_id"])
        if not read_only:
            self._acquire()
            self.recover_apply()
        self.reconcile()
        return self

    def reconcile(self):
        if self.active_batch_id:
            return copy.deepcopy(self.study.records)
        # Native receipts may have committed just before the controller died.
        # A read-only observer reads them without recovering another owner's jobs.
        store = RunStore(self._run_root,expected=any(r.attempts for r in self.study.records))
        if not self.read_only:
            store.recover()
        try:
            native_runs = store._records()
        except (OSError,ValueError,TypeError):
            native_runs = []
        for row in self.study.records:
            known = {a["run_id"] for a in row.attempts}
            for run in native_runs:
                if run.node_id == f"mdo:{self.study_id}:{row.point_id}" and run.id not in known and run.state not in {"queued","preparing","running"} and run.parse_state != "reading":
                    self._ingest_run(row,run)
            if row.status in ("QUEUED","RUNNING"):
                if not self.read_only:
                    row.status,row.feasibility,row.error_message = "INTERRUPTED","NOT_ASSESSED","Study controller stopped before a terminal response receipt"
            elif row.mode == "ACTUAL" and row.status == "COMPLETED":
                try:
                    self._verified_run(row.point_id,applicable=False)
                except (OSError,ValueError,KeyError,TypeError) as error:
                    row.status,row.feasibility,row.error_message = "FAILED","NOT_ASSESSED",str(error)
        if not self.read_only:
            self._persist()
        return copy.deepcopy(self.study.records)

    def export_csv(self, path, point_ids=None, *, revision=None):
        selected = [self._record(p) for p in point_ids] if point_ids is not None else self.study.records
        revisions = {r.revision for r in selected}
        if len(revisions)>1 or (revision is not None and revisions and revisions != {revision}):
            raise ValueError("Export one frozen study revision at a time to preserve units and definitions")
        revision = revision or (next(iter(revisions)) if revisions else self.revision)
        definition = self.revision_definition(revision)
        def safe(value):
            text = str(value)
            return "'"+text if text.lstrip().startswith(("=","+","-","@")) and not isinstance(value,(int,float)) else value
        path = Path(path)
        with path.open("w",encoding="utf-8-sig",newline="") as stream:
            writer = csv.writer(stream)
            variables = list(definition["space"].variables)
            responses = list(definition["response_units"])
            writer.writerow(["Point","Mode","Status","Feasibility","Revision","Run","Attempts",*[safe(f"{v.id} [{getattr(v,'unit','')}]") for v in variables],*[safe(f"{k} [{definition['response_units'][k]}]") for k in responses]])
            for row in selected:
                writer.writerow([safe(v) for v in [row.point_id,row.mode,row.status,row.feasibility,row.revision,row.run_id,len(row.attempts),*[row.physical_values.get(v.id,"") for v in variables],*[row.observations.get(k,"") for k in responses]]])
        return path

    def close(self):
        if self.read_only:
            return True
        if self.active_batch_id:
            self.cancel()
            return False
        if self._lease:
            self._lease.release(); self._lease = None
        return True
