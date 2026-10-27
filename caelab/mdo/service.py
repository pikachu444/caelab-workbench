"""Headless Studio command service using Workbench and native JobManager receipts."""
from __future__ import annotations

import copy
import csv
import json
import math
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
from .inp_binder import TemplateDeck
from .native_responses import default_selector, validate_selector, extract_native_responses
from .objectives import ObjectiveSpec, evaluate_criteria
from .study_manager import StudyManager, DesignPointRecord
from .variables import (VariableSpace, ContinuousVariable, DiscreteVariable,
                        CategoricalVariable, LinkedVariable)


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
        self._acquire()
        manifest = snapshot_input(path, self._root / "template-source")
        self._originals = {f["source_path"]: (self._root / "template-source" / f["source_copy"]).read_bytes()
                           for f in manifest["files"]}
        self._templates = dict(self._originals)
        self._current_hashes = {p: transaction.digest(b) for p, b in self._originals.items()}
        self._refresh_template()
        self._persist()
        return self.template

    def _refresh_template(self):
        root = TemplateDeck.from_text(self._templates[str(self.input_path)].decode("utf-8"), self.input_path)
        tokens = set(root.token_names)
        for content in self._templates.values():
            tokens.update(TemplateDeck.from_text(content.decode("utf-8")).token_names)
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
        templates = dict(self._originals)
        grouped = {}
        allowed = {"*ELASTIC", "*PLASTIC", "*DENSITY", "*CLOAD", "*DLOAD", "*BOUNDARY",
                   "*BEAM SECTION", "*SHELL SECTION", "*SOLID SECTION", "*SPRING", "*DASHPOT",
                   "*CONDUCTIVITY", "*SPECIFIC HEAT", "*EXPANSION", "*AMPLITUDE"}
        for binding in bindings:
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
            if not literal or not re.fullmatch(r"[+\-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+\-]?\d+)?", literal):
                raise ValueError("Select an exact numeric scalar literal")
            matches = list(re.finditer(r"(?<![\w.])"+re.escape(literal)+r"(?![\w.])", lines[index]))
            occurrence = binding["occurrence"]-1
            if occurrence < 0 or occurrence >= len(matches):
                raise ValueError("Scalar occurrence is missing from selected line")
            match = matches[occurrence]
            grouped.setdefault((p,index), []).append((match.start(),match.end(),"<"+token+">"))
        for (p,index), changes in grouped.items():
            lines = templates[p].decode("utf-8").splitlines(keepends=True)
            intervals = sorted(changes)
            if any(a[1] > b[0] for a,b in zip(intervals, intervals[1:])):
                raise ValueError("Parameter bindings overlap")
            for start,end,token in reversed(intervals):
                lines[index] = lines[index][:start]+token+lines[index][end:]
            templates[p] = "".join(lines).encode("utf-8")
        self._templates = templates
        self._refresh_template()
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
        self.recover_apply()
        self._require_sources()
        if self.revision == "0":
            raise ValueError("Configure the study before running")
        rows = [self._record(p) for p in point_ids] if point_ids is not None else [r for r in self.study.records if r.status == "PENDING" and r.revision == self.revision and r.mode == "ACTUAL"]
        if not rows or any(r.mode != "ACTUAL" or r.revision != self.revision for r in rows):
            raise ValueError("Select current-revision actual candidates; confirm predictions separately")
        if len({r.point_id for r in rows}) != len(rows):
            raise ValueError("Duplicate selected point")
        solver = next((s for s in self.workbench.programs() if s.spec.id == "calculix"), None)
        if solver is None or not solver.available:
            raise ValueError("Configure an available CalculiX executable")
        batch_id = uuid.uuid4().hex
        revision = self.revision
        items = []
        for row in rows:
            folder = self._root / "inputs" / batch_id / row.point_id
            input_path = self._execution_files(row.physical_values, folder)
            row.status, row.batch_id = "QUEUED", batch_id
            items.append(BatchItem(item_id=row.point_id, input_path=input_path, status=solver,
                                   node_id=f"mdo:{self.study_id}:{row.point_id}", units=dict(self.response_units),
                                   unit_source="MDO declared native deck units", timeout=300,
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
                row.run_id, row.duration_sec = run.id, item.duration_sec
                row.error_message = item.error or run.error
                row.observations, row.objectives = {}, {}
                row.feasibility, row.total_violation = "NOT_ASSESSED", 0.
                row.status = item.state.upper()
                try:
                    if run.state != "completed" or run.returncode != 0:
                        raise ValueError(row.error_message or f"Native run ended {run.state}")
                    row.observations = extract_native_responses(run.workdir, self.response_selectors)
                    assessment = evaluate_criteria(row.observations, self.study.objectives, self.study.constraints, units=self.response_units)
                    if not assessment.assessed:
                        raise ValueError("Required native responses could not be assessed")
                    row.feasibility = assessment.constraints.feasibility.value
                    row.total_violation = assessment.constraints.total_violation or 0.
                    row.objectives = {o.id: float(v) for o,v in zip(self.study.objectives, assessment.objectives)}
                    row.status = "COMPLETED"
                except (ValueError, OSError, KeyError, OverflowError) as exc:
                    if row.status not in ("CANCELLED", "INTERRUPTED"):
                        row.status = "FAILED"
                    row.error_message = str(exc)
                evidence = dict(run_id=run.id, workdir=str(run.workdir), values=dict(row.physical_values), revision=revision,
                                responses=dict(row.observations), files=self._run_hashes(run), batch_id=batch_id,
                                status=row.status, parse_state=run.parse_state, error=row.error_message,
                                log_path=run.log_path, mode="ACTUAL")
                row.attempts.append(copy.deepcopy(evidence))
                self._point_sources[row.point_id] = evidence
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
        except BaseException:
            self.active_batch_id = None
            if self._dispatch_locks:
                self._dispatch_locks.close(); self._dispatch_locks = None
            for row in rows:
                row.status = "FAILED"; row.error_message = "Native batch submission failed"
            self._persist()
            raise
        return batch_id

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
        return expected

    def point_evidence(self, point_id):
        return copy.deepcopy(self._point_sources.get(point_id, {}))

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
        return bool(self.active_batch_id and self.workbench.jobs.stop_batch(self.active_batch_id))

    def cancel(self):
        self._writable(idle=False)
        return bool(self.active_batch_id and self.workbench.cancel_batch(self.active_batch_id))

    def confirm(self, point_ids, **callbacks):
        self._writable()
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
        store_lease = RunStore(self._run_root).lease()
        old_hashes = dict(self._current_hashes)
        old_point = self.current_point_id
        old_stack = list(self._undo_stack)
        try:
            receipt = transaction.replace_closure(self._root, replacements, expected, metadata)
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
                transaction.finish(self._root, receipt)
            except BaseException:
                transaction.recover(self._root)
                self._current_hashes, self.current_point_id = old_hashes, old_point
                self._apply_receipts.pop()
                self._undo_stack = old_stack
                raise
            if self.project is not None and self.node_id:
                self.project.invalidate(self.node_id, "MDO native deck parameters changed; separate current rerun required", include_self=True)
                reference = self.project.input_file(self.node_id)
                if reference and Path(reference.path).resolve() == self.input_path:
                    reference.revision = self._current_hashes[str(self.input_path)]
                self._associate()
            return copy.deepcopy(receipt)
        finally:
            store_lease.release()

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
                                             revision=self.revision, previous_point_id=self.current_point_id,operation="undo"))
        finally:
            self._busy = None

    def recover_apply(self):
        self._writable()
        with ExitStack() as locks:
            if (self._root / "apply-pending.json").exists():
                for path in sorted(self._current_hashes):
                    locks.enter_context(FileLock(path+".caelab-apply.lock",timeout=0))
            result = transaction.recover(self._root)
        if result:
            self._current_hashes = {p:transaction.digest(Path(p).read_bytes()) for p in self._current_hashes}
            self.current_point_id = result.get("previous_point_id")
            self.current_rerun_id = None
            self._apply_receipts.append(result)
            self._undo_stack = [i for i in self._undo_stack if i != result["id"]]
            self._persist()
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
        from caelab.connections import launch_request
        document = self.result_file(point_id, current=current)
        status = next(s for s in self.workbench.programs() if s.spec.id == "paraview")
        return self.workbench.jobs.launch(launch_request(status, document))

    def train(self, response_ids=None, config=None):
        from .surrogate import SurrogateDataset, fit_surrogate, save_surrogate, grouped_holdout
        self._writable()
        self._busy = "train"
        try:
            response_ids = (response_ids,) if isinstance(response_ids,str) else tuple(response_ids or self.response_units)
            rows = [r for r in self.study.records if r.mode == "ACTUAL" and r.revision == self.revision and self.is_verified(r.point_id,require_feasible=False)]
            if not rows:
                raise ValueError("Verified actual native rows are required for training")
            encoded = [self.study.space.encode(r.physical_values) for r in rows]
            dataset = SurrogateDataset(x=np.array([p.vector for p in encoded]),
                                       y=np.array([[r.observations[k] for k in response_ids] for r in rows]),
                                       point_ids=tuple(r.point_id for r in rows),run_ids=tuple(r.run_id for r in rows),
                                       response_ids=response_ids,units=tuple(self.response_units[k] for k in response_ids),
                                       feature_order=encoded[0].feature_order, training_revision=self.revision,
                                       continuous_only=all(isinstance(v,ContinuousVariable) for v in self.study.space.independent_variables))
            model = fit_surrogate(dataset,config,grouped_holdout(dataset,seed=config.seed if config else 0),model_id=uuid.uuid4().hex)
            save_surrogate(model,self._root / "models" / model.id)
            self.surrogate = model
            self._persist()
            return model
        finally:
            self._busy = None

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

    def _document(self):
        return dict(format="caelab-mdo-study",version=2,study_id=self.study_id,name=self.study.name,revision=self.revision,
                    input_path=str(self.input_path),templates={p:b.hex() for p,b in self._templates.items()},
                    originals={p:b.hex() for p,b in self._originals.items()},current_hashes=self._current_hashes,
                    definition=self._definition(),revisions=self._revisions,records=[asdict(r) for r in self.study.records],
                    point_sources=self._point_sources,current_point_id=self.current_point_id,current_rerun_id=self.current_rerun_id,
                    apply_receipts=self._apply_receipts,undo_stack=self._undo_stack,run_root=str(self._run_root),data_root=str(self._root),
                    surrogate_id=self.surrogate.id if self.surrogate else None,view_state=self.view_state,
                    optimization_history=self.optimization_history,
                    project_path=str(self.project.path) if self.project and self.project.path else None,node_id=self.node_id)

    def _persist(self):
        if self.read_only or self._root is None:
            return
        data = self._document()
        atomic_json(self._root / "study.json",data)
        if self.study_file and self.study_file != self._root / "study.json":
            atomic_json(self.study_file,data)

    def _associate(self):
        if self.project is not None and self.node_id:
            self._ensure_project_unchanged()
            from caelab.project import FileReference
            path = self.study_file or self._root / "study.json"
            self.project.nodes[self.node_id].files["mdo_study"] = FileReference(str(path),transaction.digest(path.read_bytes()))
            if self.project.path:
                self.workbench.save_native_project(self.project)
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
        destination = Path(path).expanduser().resolve()
        if destination == self.study_file:
            return self.save()
        target = destination.with_suffix(".mdo-data")
        if destination.exists() or target.exists() or target.is_relative_to(self._root):
            raise ValueError("Save As requires a new independent file and sidecar directory")
        destination.parent.mkdir(parents=True,exist_ok=True)
        old_root, old_runs, old_file = self._root,self._run_root,self.study_file
        old_sources = copy.deepcopy(self._point_sources)
        old_attempts = {r.point_id:copy.deepcopy(r.attempts) for r in self.study.records}
        lease = RunStore(old_runs).lease()
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
            self._persist()
            if self._lease:
                self._lease.release(); self._lease = None
            self._acquire()
            self._associate()
            return destination
        except BaseException:
            self._root,self._run_root,self.study_file = old_root,old_runs,old_file
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
        if data.get("format") != "caelab-mdo-study" or data.get("version") != 2:
            raise ValueError("Unsupported study format; open the original deck to create a current service study")
        self.study_id,self.revision,self.read_only = data["study_id"],data["revision"],read_only
        self.input_path,self.study_file = Path(data["input_path"]),path
        self._root,self._run_root = Path(data["data_root"]),Path(data["run_root"])
        if not self._root.is_dir():
            raise ValueError("Study sidecar directory is missing")
        self._originals = {p:bytes.fromhex(b) for p,b in data["originals"].items()}
        self._templates = {p:bytes.fromhex(b) for p,b in data["templates"].items()}
        self._current_hashes,self._revisions = data["current_hashes"],data["revisions"]
        self._refresh_template()
        classes = {t.__name__:t for t in (ContinuousVariable,DiscreteVariable,CategoricalVariable,LinkedVariable)}
        definition = data["definition"]
        variables = []
        for raw in definition["variables"]:
            raw = dict(raw)
            variables.append(classes[raw.pop("type")](**raw))
        self.study = StudyManager(data["name"],space=VariableSpace(variables=tuple(variables)),
                                  objectives=[ObjectiveSpec(**o) for o in definition["objectives"]],
                                  constraints=[ConstraintSpec(**c) for c in definition["constraints"]])
        self.response_units,self.response_selectors = definition["response_units"],definition["response_selectors"]
        for raw in data["records"]:
            row = DesignPointRecord(**raw)
            if row.point_id in self.study._record_map:
                raise ValueError("Duplicate saved point IDs")
            self.study.records.append(row); self.study._record_map[row.point_id] = row
        self._point_sources = data["point_sources"]
        self.current_point_id,self.current_rerun_id = data["current_point_id"],data["current_rerun_id"]
        self._apply_receipts = data["apply_receipts"]
        self._undo_stack = data.get("undo_stack",[])
        self.view_state = data.get("view_state",{})
        if not isinstance(self.view_state,dict) or len(json.dumps(self.view_state)) > 120000:
            raise ValueError("Saved view state must be a bounded JSON object")
        self.optimization_history = data.get("optimization_history",[])
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
        for row in self.study.records:
            if row.status in ("QUEUED","RUNNING"):
                row.status,row.feasibility,row.error_message = "INTERRUPTED","NOT_ASSESSED","Study controller stopped before a terminal response receipt"
            elif row.mode == "ACTUAL" and row.status == "COMPLETED":
                try:
                    self._verified_run(row.point_id,applicable=False)
                except (OSError,ValueError,KeyError,TypeError) as error:
                    row.status,row.feasibility,row.error_message = "FAILED","NOT_ASSESSED",str(error)
        if not self.read_only:
            self._persist()
        return copy.deepcopy(self.study.records)

    def export_csv(self, path, point_ids=None):
        selected = [self._record(p) for p in point_ids] if point_ids is not None else self.study.records
        def safe(value):
            text = str(value)
            return "'"+text if text.lstrip().startswith(("=","+","-","@")) and not isinstance(value,(int,float)) else value
        path = Path(path)
        with path.open("w",encoding="utf-8-sig",newline="") as stream:
            writer = csv.writer(stream)
            variables = list(self.study.space.variables)
            responses = list(self.response_units)
            writer.writerow(["Point","Mode","Status","Feasibility","Revision","Run","Attempts",*[safe(f"{v.id} [{getattr(v,'unit','')}]") for v in variables],*[safe(f"{k} [{self.response_units[k]}]") for k in responses]])
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
