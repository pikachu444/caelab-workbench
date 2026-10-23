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


class Workbench:
    def __init__(self, registry_path: str | Path | None = None) -> None:
        self.jobs = JobManager()
        self.registry_path = registry_path

    # -- project ---------------------------------------------------------
    def open_project(self, folder: str | Path) -> StructuralModel:
        return load_project(folder)

    def save_project(self, model: StructuralModel, folder: str | Path) -> Path:
        return save_project(model, folder)

    # -- native program/file projects -----------------------------------
    def new_native_project(self, name: str = "새 프로젝트") -> NativeProject:
        return NativeProject(name)

    def open_native_project(self, path: str | Path) -> NativeProject:
        return load_native_project(path)

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
        return project.refresh()

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
        record = self.jobs.launch(analysis_launch_request(project.path, node_id))
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
