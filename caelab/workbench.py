"""The single command path.

GUI, CLI and MCP front-ends all go through this facade: open/save a
project, validate it, and run it through one converter. Opening a
project has no solver or LLM side effects.
"""

from __future__ import annotations

from pathlib import Path

from .jobs import Job, JobManager, LaunchRecord
from .project import NativeProject, FileReference, load_native_project, save_native_project
from .model.schema import StructuralModel
from .model.store import load_project, save_project
from .solvers import get_converter
from .validate.checker import Issue, validate_model
from .native.run_store import NativeRun, RunStore
from .native.input_snapshot import fingerprint


def _source_metadata_changed(item: dict) -> bool:
    current = fingerprint(item["source_path"])
    if current is None:
        return True
    # WSL remounts can assign a different device number to the same file.
    # Keep full device identity for collection races, but not saved-run freshness.
    return any(current[key] != item["stat"].get(key) for key in
               ("st_size", "st_mtime_ns", "st_ctime_ns", "st_ino"))


class Workbench:
    def __init__(self, registry_path: str | Path | None = None) -> None:
        self.jobs = JobManager()
        self.registry_path = registry_path
        self._run_stores = {}

    # -- project ---------------------------------------------------------
    def open_project(self, folder: str | Path) -> StructuralModel:
        return load_project(folder)

    def save_project(self, model: StructuralModel, folder: str | Path) -> Path:
        return save_project(model, folder)

    # -- native program/file projects -----------------------------------
    def new_native_project(self, name: str = "새 프로젝트") -> NativeProject:
        return NativeProject(name)

    def open_native_project(self, path: str | Path) -> NativeProject:
        project = load_native_project(path)
        self._run_store(project).recover()
        return project

    def save_native_project(self, project: NativeProject, path: str | Path | None = None) -> Path:
        return save_native_project(project, path)

    def add_program_node(self, project: NativeProject, kind: str, program_id: str, position=(0.0, 0.0)):
        return project.add_node(kind, program_id, position)

    def set_node_file(self, project: NativeProject, node_id: str, path: str | Path,
                      role="input", units=None, unit_source="user") -> FileReference:
        return project.set_file(node_id, path, role, units, unit_source)

    def connect_nodes(self, project: NativeProject, source_id: str, target_id: str, source_role="output"):
        return project.connect(source_id, target_id, source_role)

    def disconnect_node_input(self, project: NativeProject, node_id: str) -> None:
        project.disconnect_input(node_id)

    def remove_program_node(self, project: NativeProject, node_id: str):
        if node_id not in project.nodes:
            raise ValueError("제거할 프로그램이 없습니다.")
        if project.path is None:
            return project.remove_node(node_id)
        store = self._run_store(project)
        # The controller holds this lease through result reading, in every window.
        lease = store.lease()
        try:
            if any(run.state in {"queued", "preparing", "running"} or run.parse_state == "reading"
                   for run in store.runs()):
                raise ValueError("계산 또는 결과 판독을 끝낸 뒤 프로그램을 제거하세요.")
            return project.remove_node(node_id)
        finally:
            lease.release()

    def restore_program_node(self, project: NativeProject, node_id: str):
        if node_id not in project.removed_nodes:
            raise ValueError("복원할 프로그램이 없습니다.")
        return project.restore_node(node_id)

    def set_node_file_units(self, project: NativeProject, node_id: str,
                            units: dict[str, str], role="input") -> FileReference:
        return project.set_file_units(node_id, units, role)

    def input_file(self, project: NativeProject, node_id: str) -> FileReference | None:
        return project.input_file(node_id)

    def refresh_native_project(self, project: NativeProject) -> list[str]:
        issues = project.refresh()
        for node in project.nodes.values():
            from .native.adapter import supported_solver_ids
            if (node.kind == "analysis" or node.program_id in supported_solver_ids()) and project.path:
                try:
                    self.selected_native_run(project, node.id)
                except (OSError, ValueError) as error:
                    node.state, node.reason = 'failed', str(error)
                    issues.append(str(error))
        return issues

    def _run_store(self, project: NativeProject) -> RunStore:
        if project.path is None:
            raise ValueError("계산 전에 프로젝트를 저장하세요.")
        root = (project.run_store or project.path.with_suffix(".runs")).resolve()
        if root not in self._run_stores:
            self._run_stores[root] = RunStore(root, expected=project.run_store_expected)
        else:
            self._run_stores[root].expected = project.run_store_expected
        return self._run_stores[root]

    def submit_native(self, project: NativeProject, node_id: str, timeout=3600) -> NativeRun:
        import copy
        node = project.nodes[node_id]
        from .native.adapter import supported_solver_ids
        if node.kind != "analysis" and node.program_id not in supported_solver_ids():
            raise ValueError("네이티브 계산은 해석 노드에서 실행하세요.")
        reference = project.input_file(node_id)
        if reference is None:
            raise ValueError(f"선택하거나 연결한 {node.program_id} 입력 파일이 없습니다.")
        status = next((status for status in self.programs() if status.spec.id == node.program_id), None)
        if status is None:
            raise ValueError(f"{node.program_id} 솔버 프로그램을 찾을 수 없습니다.")
        prerequisites = []
        pending, seen = [node_id], set()
        while pending:
            current = pending.pop()
            for edge in project.connections:
                if edge.target_id == current and edge.source_id not in seen:
                    seen.add(edge.source_id)
                    source = project.input_file(edge.source_id)
                    if source is not None and source is not reference:
                        prerequisites.append(copy.deepcopy(source))
                    pending.append(edge.source_id)
        run = self.jobs.submit_native(self._run_store(project), node_id, copy.deepcopy(reference), status, timeout, prerequisites)
        self._apply_run_selection(project, node_id, run)
        return run

    def native_runs(self, project: NativeProject, node_id: str | None = None) -> list[NativeRun]:
        store = self._run_store(project)
        store.recover()
        runs = store.runs(node_id)
        for index, stored in enumerate(runs):
            owned = self.jobs.native_snapshot(stored.id)
            run = owned if owned and owned.workdir == stored.workdir else stored
            reference = project.input_file(run.node_id) if run.node_id in project.nodes else None
            reason = ""
            if reference is None or reference.path != run.input_path:
                reason = "현재 선택 입력이 이 실행의 입력과 다릅니다."
            elif reference.stale:
                reason = "상위 변경으로 현재 입력 덱이 오래되었습니다."
            elif reference.units != run.units or reference.unit_source != run.unit_source:
                reason = "입력 단위 선언/출처가 변경되었습니다."
            elif any(_source_metadata_changed(item) for item in
                     run.snapshot.get("files", []) + run.snapshot.get("prerequisites", [])):
                reason = "실행 원본 또는 INCLUDE 파일이 변경/누락되었습니다."
            run.stale, run.stale_reason = bool(reason), reason
            runs[index] = run
        return runs

    def _apply_run_selection(self, project, node_id, run):
        node = project.nodes[node_id]
        previous = node.files.get("output")
        outputs = run.snapshot.get("outputs", {})
        from .native.adapter import get_solver_adapter
        try:
            expected = get_solver_adapter(run.solver_id).expected_output_names()
        except ValueError:
            expected = ("model.frd", "model.dat")
        name = next((name for name in expected if outputs.get(name)), None)
        if run.state == "completed" and run.parse_state in {"readable", "partial"} and name:
            reference = FileReference(str(run.workdir / name), outputs[name], dict(run.units), run.unit_source, run.stale)
            if previous != reference:
                project.invalidate(node_id, "선택 실행 또는 결과 출처가 변경되었습니다.")
                node.files["output"] = reference
        elif previous is not None:
            project.invalidate(node_id, "선택 실행에 사용할 수 있는 최신 결과가 없습니다.")
            node.files.pop("output", None)
        if run.state in {"failed", "cancelled", "interrupted"}:
            node.state, node.reason = "failed", run.error
        elif run.stale:
            node.state, node.reason = "stale", run.stale_reason
        elif run.state == "completed":
            node.state, node.reason = "ready", "솔버가 종료되었습니다. 판독 상태와 공학적 타당성은 별도로 확인하세요."

    def select_native_run(self, project: NativeProject, node_id: str, run_id: str) -> None:
        store = self._run_store(project)
        store.select(node_id, run_id)
        self.selected_native_run(project, node_id)

    def selected_native_run(self, project: NativeProject, node_id: str) -> NativeRun | None:
        run_id = self._run_store(project).selected_id(node_id)
        run = next((run for run in self.native_runs(project, node_id) if run.id == run_id), None)
        if run is not None:
            self._apply_run_selection(project, node_id, run)
        return run

    def cancel_native_run(self, run_id: str) -> bool:
        return self.jobs.cancel_native(run_id)

    def submit_batch(self, project: NativeProject, items: Sequence[Any], **kwargs) -> Any:
        import uuid
        batch_id = kwargs.get("batch_id") or uuid.uuid4().hex
        store = RunStore(project.run_store / "mdo_runs" / batch_id)
        return self.jobs.submit_batch(store, items, batch_id=batch_id, **kwargs)

    def cancel_batch(self, batch_id: str) -> bool:
        return self.jobs.cancel_batch(batch_id)

    def batch_snapshot(self, batch_id: str) -> Any | None:
        return self.jobs.batch_snapshot(batch_id)

    def close(self) -> bool:
        return self.jobs.close()

    def resolve_evidence_run(self, project: NativeProject, node_id: str) -> NativeRun | None:
        """Follow the selected node's real incoming path to one selected analysis."""
        seen = set()
        while node_id not in seen:
            seen.add(node_id)
            node = project.nodes[node_id]
            from .native.adapter import supported_solver_ids
            if node.kind == 'analysis' or node.program_id in supported_solver_ids():
                return self.selected_native_run(project, node_id)
            edge = next((edge for edge in project.connections if edge.target_id == node_id), None)
            if edge is None:
                return None
            node_id = edge.source_id
        raise ValueError('실행 근거 연결이 순환합니다.')

    def open_native_results(self, project: NativeProject, node_id: str) -> LaunchRecord:
        from .native.evidence import native_results_file
        from .connections import launch_request

        run = self.resolve_evidence_run(project, node_id)
        if run is None:
            raise ValueError('연결된 해석 노드에서 실행을 선택하세요.')
        document = native_results_file(run)
        status = next(status for status in self.programs() if status.spec.id == 'paraview')
        return self.jobs.launch(launch_request(status, document))

    def capture_native_evidence(self, project: NativeProject, node_id: str, asset_root: Path, *, frame_index=None) -> dict:
        from .native.evidence import capture_run_evidence

        run = self.resolve_evidence_run(project, node_id)
        if run is None:
            raise ValueError('연결된 해석 노드에서 실행을 선택하세요.')
        return capture_run_evidence(run, asset_root, frame_index=frame_index)

    def import_expert_files(self, asset_root: Path, **kwargs) -> dict:
        from .native.evidence import import_file_evidence
        return import_file_evidence(asset_root, **kwargs)

    # Independent expert front ends use this facade, without importing a solver UI.
    def expert_templates(self):
        from .assist.experts import TEMPLATES
        return TEMPLATES

    def expert_provider_status(self, provider_id=None):
        from .assist.experts import get_provider
        provider = get_provider(provider_id)
        available, reason = provider.availability()
        return {'id': provider.id, 'name': provider.name, 'available': available,
                'reason': reason or '설정됨 — 실제 호출은 질문을 보낼 때 확인합니다.'}

    def expert_provider_configuration(self):
        import os
        return {key: os.environ.get('CAELAB_EXPERT_' + suffix, '') for key, suffix in
                [('provider_id', 'PROVIDER'), ('endpoint', 'ENDPOINT'), ('model', 'MODEL'), ('api_key', 'API_KEY')]}

    def configure_expert_provider(self, *, endpoint, model, api_key):
        import os
        for key, value in [('ENDPOINT', endpoint), ('MODEL', model), ('API_KEY', api_key)]:
            os.environ['CAELAB_EXPERT_' + key] = value

    def open_expert_discussion(self, path, *, create=False):
        from .assist.discussions import create_discussion, load_discussion
        return create_discussion(path) if create and not Path(path).exists() else load_discussion(path)

    def save_expert_discussion(self, path, document, *, expected_revision):
        from .assist.discussions import save_discussion
        return save_discussion(path, document, expected_revision=expected_revision)

    def attach_expert_evidence(self, path, evidence, *, expected_revision):
        from .assist.discussions import attach_evidence
        return attach_evidence(path, evidence, expected_revision=expected_revision)

    def ask_expert_discussion(self, path, question, **kwargs):
        from .assist.discussions import ask_discussion
        return ask_discussion(path, question, **kwargs)

    def review_expert_proposal(self, path, proposal_id, **kwargs):
        from .assist.discussions import review_proposal
        return review_proposal(path, proposal_id, **kwargs)

    def expert_discussion_issues(self, path, document=None):
        from .assist.discussions import discussion_issues
        return discussion_issues(path, document)

    def expert_evidence_run(self, project, node_id, run_id=None):
        if not run_id:
            return self.resolve_evidence_run(project, node_id)
        ancestors, pending = set(), [node_id]
        while pending:
            current = pending.pop()
            if current in ancestors:
                continue
            ancestors.add(current)
            pending.extend(edge.source_id for edge in project.connections if edge.target_id == current)
        run = next((run for run in self.native_runs(project) if run.id == run_id), None)
        if run is None or run.node_id not in ancestors:
            raise ValueError('전달받은 실행이 연결된 해석 기록에 없습니다.')
        return run

    def capture_expert_run(self, run, asset_root, *, frame_index=None):
        from .native.evidence import capture_run_evidence
        return capture_run_evidence(run, asset_root, frame_index=frame_index)

    def open_native_expert(self, project, node_id):
        import hashlib
        from .connections import expert_launch_request
        if project.path is None or node_id not in project.nodes or project.nodes[node_id].kind != 'expert':
            raise ValueError('프로젝트를 저장하고 전문가 상자를 선택하세요.')
        run = self.resolve_evidence_run(project, node_id)
        folder = hashlib.sha256(node_id.encode('utf-8')).hexdigest()
        path = self._run_store(project).root / 'discussions' / folder / 'discussion.json'
        request = expert_launch_request(path, project.path, node_id, run.id if run else None)
        if self.registry_path is not None:
            request.env['CAELAB_INSTALL_REGISTRY'] = str(Path(self.registry_path).expanduser().resolve())
        return self.jobs.launch(request)

    def mark_node_failed(self, project: NativeProject, node_id: str, reason: str) -> None:
        node = project.nodes[node_id]
        node.state, node.reason = "failed", reason
        project.invalidate(node_id, reason)

    def open_program(self, project: NativeProject, node_id: str) -> LaunchRecord:
        from .connections import launch_request

        node = project.nodes[node_id]
        project.refresh()
        try:
            reference = project.input_file(node_id)
            if reference is None or reference.status == "missing":
                raise ValueError("선택하거나 연결한 입력 파일이 없습니다. 파일 경로를 확인하세요.")
            status = next((status for status in self.programs() if status.spec.id == node.program_id), None)
            if status is None:
                raise ValueError(f"{node.program_id} 독립 프로그램 연결은 구현되지 않았습니다.")
            request = launch_request(status, reference.path)
        except ValueError as exc:
            self.mark_node_failed(project, node_id, str(exc))
            raise
        record = self.jobs.launch(request)
        if record.state == "failed":
            self.mark_node_failed(project, node_id, record.error)
        elif node.state in {"changed", "stale"}:
            node.reason += " 프로그램 열기 요청을 전달했습니다. 변경/오래된 출처 상태는 유지합니다."
        else:
            node.state, node.reason = "opened", "프로그램 열기 요청을 전달했습니다. 계산 완료를 뜻하지 않습니다."
        return record

    def open_native_analysis(self, project: NativeProject, node_id: str) -> LaunchRecord:
        from .connections import analysis_launch_request

        if node_id not in project.nodes:
            raise ValueError("입력 확인 창을 열 노드가 없습니다.")
        if project.path is None:
            raise ValueError("입력 확인 창을 열기 전에 프로젝트를 저장하세요.")
        request = analysis_launch_request(project.path, node_id)
        if self.registry_path is not None:
            request.env["CAELAB_INSTALL_REGISTRY"] = str(Path(self.registry_path).expanduser().resolve())
        record = self.jobs.launch(request)
        if record.state == "failed":
            self.mark_node_failed(project, node_id, record.error)
        return record

    # -- checking ---------------------------------------------------------
    def programs(self):
        """Availability of the connected external programs."""
        from .connections import program_statuses

        return program_statuses(self.registry_path)

    def check(self, model: StructuralModel, solver_id: str = "calculix") -> list[Issue]:
        issues = validate_model(model)
        issues.extend(get_converter(solver_id).validate(model))
        return issues

    # -- running ------------------------------------------------------------
    def run(
        self,
        model: StructuralModel,
        solver_id: str,
        workdir: str | Path,
    ) -> Job:
        converter = get_converter(solver_id)
        return self.jobs.submit(converter, model, workdir)
