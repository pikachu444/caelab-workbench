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

    def input_file(self, project: NativeProject, node_id: str) -> FileReference | None:
        return project.input_file(node_id)

    def refresh_native_project(self, project: NativeProject) -> list[str]:
        issues = project.refresh()
        for node in project.nodes.values():
            if node.program_id == "calculix" and project.path:
                self.selected_native_run(project, node.id)
        return issues

    def _run_store(self, project: NativeProject) -> RunStore:
        if project.path is None:
            raise ValueError("계산 전에 프로젝트를 저장하세요.")
        root = (project.run_store or project.path.with_suffix(".runs")).resolve()
        if root not in self._run_stores:
            self._run_stores[root] = RunStore(root)
        return self._run_stores[root]

    def submit_native(self, project: NativeProject, node_id: str, timeout=3600) -> NativeRun:
        import copy
        node = project.nodes[node_id]
        if node.program_id != "calculix":
            raise ValueError("네이티브 계산은 CalculiX 노드에서 실행하세요.")
        reference = project.input_file(node_id)
        if reference is None:
            raise ValueError("선택하거나 연결한 INP 파일이 없습니다.")
        status = next(status for status in self.programs() if status.spec.id == "calculix")
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
            elif any(fingerprint(item["source_path"]) != item["stat"] for item in
                     run.snapshot.get("files", []) + run.snapshot.get("prerequisites", [])):
                reason = "실행 원본 또는 INCLUDE 파일이 변경/누락되었습니다."
            run.stale, run.stale_reason = bool(reason), reason
            runs[index] = run
        return runs

    def _apply_run_selection(self, project, node_id, run):
        node = project.nodes[node_id]
        previous = node.files.get("output")
        outputs = run.snapshot.get("outputs", {})
        name = next((name for name in ("model.frd", "model.dat") if outputs.get(name)), None)
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

    def close(self) -> bool:
        return self.jobs.close()

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
