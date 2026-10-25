"""Small native-project file graph; source documents stay owned by their programs."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 2
PROJECT_FILENAME = "project.json"
NODE_STATES = {"needs_input", "ready", "changed", "missing", "stale", "opened", "failed"}


def file_revision(path: str | Path) -> str | None:
    path = Path(path)
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class FileReference:
    path: str
    revision: str | None = None
    units: dict[str, str] = field(default_factory=dict)
    unit_source: str = ""
    stale: bool = False

    @property
    def status(self) -> str:
        current = file_revision(self.path)
        if current is None:
            return "missing"
        if current != self.revision:
            return "changed"
        return "stale" if self.stale else "ready"


@dataclass
class ProgramNode:
    id: str
    kind: str
    program_id: str
    files: dict[str, FileReference] = field(default_factory=dict)
    position: tuple[float, float] = (0.0, 0.0)
    state: str = "needs_input"
    reason: str = ""


@dataclass(frozen=True)
class Connection:
    source_id: str
    target_id: str
    source_role: str = "output"


@dataclass
class NativeProject:
    name: str = "새 프로젝트"
    nodes: dict[str, ProgramNode] = field(default_factory=dict)
    connections: list[Connection] = field(default_factory=list)
    path: Path | None = None
    run_store: Path | None = None
    run_store_expected: bool = False
    issues: list[str] = field(default_factory=list, repr=False, compare=False)
    removed_nodes: dict[str, ProgramNode] = field(default_factory=dict)

    def add_node(self, kind: str, program_id: str, position=(0.0, 0.0)) -> ProgramNode:
        node = ProgramNode(uuid.uuid4().hex, kind, program_id, position=tuple(position))
        self.nodes[node.id] = node
        return node

    def input_file(self, node_id: str) -> FileReference | None:
        node = self.nodes[node_id]
        incoming = next((edge for edge in self.connections if edge.target_id == node_id), None)
        if incoming is not None:
            if incoming.source_role == "input":
                return self.input_file(incoming.source_id)
            return self.nodes[incoming.source_id].files.get(incoming.source_role)
        return node.files.get("input")

    def invalidate(self, node_id: str, reason: str, *, include_self: bool = False) -> None:
        pending = [node_id]
        seen = set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            node = self.nodes[current]
            if current != node_id or include_self:
                node.state, node.reason = "stale", reason
            if "output" in node.files:
                node.files["output"].stale = True
            pending.extend(edge.target_id for edge in self.connections if edge.source_id == current)

    def set_file(self, node_id: str, path: str | Path, role="input", units=None, unit_source="user") -> FileReference:
        if role not in {"input", "output"}:
            raise ValueError(f"지원하지 않는 파일 역할: {role}")
        node = self.nodes[node_id]
        resolved = str(Path(path).expanduser().resolve())
        reference = FileReference(resolved, file_revision(resolved), dict(units or {}), unit_source)
        previous = node.files.get(role)
        if previous is None or (previous.path, previous.revision) != (reference.path, reference.revision):
            self.invalidate(node_id, "파일 선택 또는 개정이 변경되었습니다.")
        node.files[role] = reference
        if role == "input":
            # A deliberate local selection replaces a formerly connected input.
            incoming = [edge for edge in self.connections if edge.target_id == node_id]
            if incoming:
                self.connections = [edge for edge in self.connections if edge.target_id != node_id]
                self.invalidate(node_id, "입력 연결 대신 파일을 선택했습니다.")
            node.state, node.reason = reference.status, ""
        self.refresh()
        return reference

    def connect(self, source_id: str, target_id: str, source_role="output") -> Connection:
        if source_id not in self.nodes or target_id not in self.nodes:
            raise ValueError("연결이 존재하지 않는 노드를 참조합니다.")
        if source_role not in {"input", "output"}:
            raise ValueError(f"지원하지 않는 연결 파일 역할: {source_role}")
        edge = Connection(source_id, target_id, source_role)
        candidate = [item for item in self.connections if item.target_id != target_id] + [edge]
        _check_connections(self.nodes, candidate)
        self.connections = candidate
        self.invalidate(target_id, "입력 연결이 변경되었습니다.")
        self.nodes[target_id].state, self.nodes[target_id].reason = "ready", ""
        self.refresh()
        return edge

    def set_file_units(self, node_id: str, units: dict[str, str], role="input") -> FileReference:
        if role not in {"input", "output"}:
            raise ValueError("지원하지 않는 파일 역할입니다.")
        if not isinstance(units, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) for key, value in units.items()
        ):
            raise ValueError("단위는 이름과 값으로 지정하세요.")
        if role == "input" and any(edge.target_id == node_id for edge in self.connections):
            raise ValueError("연결된 입력의 단위는 상위 파일에서 수정하세요.")
        reference = self.nodes[node_id].files.get(role)
        if reference is None:
            raise ValueError("먼저 파일을 지정하세요.")
        root = self.run_store or (self.path.with_suffix(".runs") if self.path else None)
        if root is not None and Path(reference.path).resolve().is_relative_to(root.resolve()):
            raise ValueError("보존된 계산 파일의 단위는 수정할 수 없습니다.")
        if reference.units == units:
            return reference
        # Editing a declaration must not accept a changed file or renew an old output.
        was_stale = reference.stale
        reference.units = dict(units)
        reference.unit_source = "user" if units else "unknown"
        self.invalidate(node_id, "파일의 단위 설정이 변경되었습니다.")
        reference.stale = was_stale
        return reference

    def disconnect_input(self, node_id: str) -> None:
        if node_id not in self.nodes:
            raise ValueError("연결을 해제할 프로그램이 없습니다.")
        if not any(edge.target_id == node_id for edge in self.connections):
            return
        candidate = copy.deepcopy(self)
        candidate._disconnect_input(node_id)
        self._commit_graph_edit(candidate)

    def _disconnect_input(self, node_id: str) -> None:
        node = self.nodes[node_id]
        if not any(edge.target_id == node_id for edge in self.connections):
            return
        self.invalidate(node_id, "입력 연결을 해제했습니다.", include_self=True)
        self.connections = [edge for edge in self.connections if edge.target_id != node_id]
        # An inactive local input must not silently replace the disconnected file.
        node.files.pop("input", None)

    def remove_node(self, node_id: str) -> ProgramNode:
        candidate = copy.deepcopy(self)
        candidate._disconnect_input(node_id)
        targets = [edge.target_id for edge in candidate.connections if edge.source_id == node_id]
        for target_id in targets:
            candidate._disconnect_input(target_id)
        candidate.removed_nodes[node_id] = candidate.nodes.pop(node_id)
        self._commit_graph_edit(candidate)
        return self.removed_nodes[node_id]

    def restore_node(self, node_id: str) -> ProgramNode:
        if node_id in self.nodes:
            raise ValueError("이미 프로젝트에 있는 프로그램입니다.")
        candidate = copy.deepcopy(self)
        candidate.nodes[node_id] = candidate.removed_nodes.pop(node_id)
        self._commit_graph_edit(candidate)
        return self.nodes[node_id]

    def _commit_graph_edit(self, candidate: NativeProject) -> None:
        # File reads can fail. Validate the entire edit before changing live objects.
        candidate.refresh()
        originals = {**self.nodes, **self.removed_nodes}
        for node_id, edited in {**candidate.nodes, **candidate.removed_nodes}.items():
            node = originals[node_id]
            node.files = {role: node.files[role] for role in edited.files}
            for role, reference in edited.files.items():
                node.files[role].stale = reference.stale
            node.state, node.reason = edited.state, edited.reason
        self.nodes = {key: originals[key] for key in candidate.nodes}
        self.removed_nodes = {key: originals[key] for key in candidate.removed_nodes}
        self.connections = candidate.connections
        self.issues = candidate.issues

    def refresh(self) -> list[str]:
        issues = []
        for node in self.nodes.values():
            for role, reference in node.files.items():
                status = reference.status
                if status != "ready":
                    reason = f"{node.id}.files.{role}: {status} — {reference.path}"
                    issues.append(reason)
                    if role == "input" and self.input_file(node.id) is not reference:
                        continue  # a connected input supersedes this retained local selection
                    self.invalidate(node.id, reason)
                    if role == "input" or self.input_file(node.id) is reference:
                        node.state, node.reason = status, reason
        for node in self.nodes.values():
            reference = self.input_file(node.id)
            if reference is None:
                node.state, node.reason = "needs_input", "연결된 입력 파일이 없습니다."
            elif reference.status != "ready":
                if node.state not in {"changed", "missing"}:
                    node.state, node.reason = "stale", "입력 파일이 누락되었거나 개정이 변경되었습니다."
            elif node.state not in {"stale", "failed", "opened"}:
                node.state, node.reason = "ready", ""
        if self.run_store is not None:
            from .native.run_store import RunStore
            issues.extend(RunStore(self.run_store, expected=self.run_store_expected).integrity_issues())
        self.issues = issues
        return issues


def _check_connections(nodes: dict, connections: list[Connection]) -> None:
    incoming = set()
    edges = {node_id: [] for node_id in nodes}
    for edge in connections:
        if edge.source_id not in nodes or edge.target_id not in nodes:
            raise ValueError("connections: 존재하지 않는 노드 참조입니다.")
        if edge.source_role not in {"input", "output"}:
            raise ValueError("connections: 지원하지 않는 파일 역할입니다.")
        if edge.target_id in incoming:
            raise ValueError("connections: 한 노드의 입력 연결이 중복됩니다.")
        incoming.add(edge.target_id)
        edges[edge.source_id].append(edge.target_id)
    visiting, visited = set(), set()

    def visit(node_id):
        if node_id in visiting:
            raise ValueError("connections: 순환 연결은 지원하지 않습니다.")
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in edges[node_id]:
            visit(target)
        visiting.remove(node_id)
        visited.add(node_id)
    for node_id in nodes:
        visit(node_id)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"중복 JSON 키: {key}")
        result[key] = value
    return result


def load_native_project(path: str | Path) -> NativeProject:
    path = Path(path).expanduser().resolve()
    if path.is_dir():
        path /= PROJECT_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or data.get("schema_version") not in (1, SCHEMA_VERSION):
            raise ValueError(f"지원하지 않는 프로젝트 schema_version: {data.get('schema_version') if isinstance(data, dict) else None}")
        if not isinstance(data.get("name"), str) or not isinstance(data.get("nodes"), dict) or not isinstance(data.get("connections"), list):
            raise ValueError("name/nodes/connections 자료 형식이 잘못되었습니다.")
        project = NativeProject(data["name"], path=path)
        stored = data.get("run_store", path.stem + ".runs")
        if not isinstance(stored, str) or not stored.strip():
            raise ValueError("run_store: 저장소 경로가 잘못되었습니다.")
        project.run_store = (path.parent / stored).resolve()
        if type(data.get('run_store_expected', False)) is not bool:
            raise ValueError('run_store_expected: bool 값이 필요합니다.')
        project.run_store_expected = data.get('run_store_expected', False)
        removed = data.get("removed_nodes", {})
        if not isinstance(removed, dict) or set(removed) & set(data["nodes"]):
            raise ValueError("제거한 프로그램 목록이 잘못되었습니다.")
        for key, raw in {**data["nodes"], **removed}.items():
            if not isinstance(raw, dict) or not key.strip() or raw.get("id") != key:
                raise ValueError(f"nodes.{key}: 비어 있거나 일치하지 않는 노드 ID입니다.")
            if any(not isinstance(raw.get(name), str) or not raw[name].strip() for name in ("kind", "program_id")):
                raise ValueError(f"nodes.{key}: kind/program_id가 필요합니다.")
            position = raw.get("position", [0, 0])
            if not isinstance(position, (list, tuple)) or len(position) != 2 or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in position):
                raise ValueError(f"nodes.{key}.position: 유한한 두 좌표가 필요합니다.")
            if raw.get("state", "needs_input") not in NODE_STATES or not isinstance(raw.get("reason", ""), str):
                raise ValueError(f"nodes.{key}: 상태 형식이 잘못되었습니다.")
            node = ProgramNode(key, raw["kind"], raw["program_id"], position=tuple(position), state=raw.get("state", "needs_input"), reason=raw.get("reason", ""))
            if node.state == "opened":
                # Saved launch requests are not evidence of a live process now.
                node.state, node.reason = "needs_input", ""
            if not isinstance(raw.get("files", {}), dict):
                raise ValueError(f"nodes.{key}.files: 파일 사전이 필요합니다.")
            for role, ref in raw.get("files", {}).items():
                if role not in {"input", "output"} or not isinstance(ref, dict) or not isinstance(ref.get("path"), str) or not ref["path"].strip():
                    raise ValueError(f"nodes.{key}.files.{role}: 파일 경로가 잘못되었습니다.")
                revision = ref.get("revision")
                if revision is not None and (not isinstance(revision, str) or len(revision) != 64 or any(c not in "0123456789abcdef" for c in revision)):
                    raise ValueError(f"nodes.{key}.files.{role}.revision: SHA256 개정이 잘못되었습니다.")
                if not isinstance(ref.get("units", {}), dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in ref.get("units", {}).items()) or not isinstance(ref.get("unit_source", ""), str) or not isinstance(ref.get("stale", False), bool):
                    raise ValueError(f"nodes.{key}.files.{role}: 단위·출처·상태 자료가 잘못되었습니다.")
                file_path = Path(ref["path"]).expanduser()
                if not file_path.is_absolute():
                    file_path = path.parent / file_path
                node.files[role] = FileReference(str(file_path.resolve()), revision, dict(ref.get("units", {})), ref.get("unit_source", ""), ref.get("stale", False))
            (project.removed_nodes if key in removed else project.nodes)[key] = node
        project.connections = [Connection(**edge) for edge in data["connections"]]
        _check_connections(project.nodes, project.connections)
        project.refresh()
        return project
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"프로젝트 읽기 실패 ({path}): {exc}") from exc


def save_native_project(project: NativeProject, path: str | Path | None = None) -> Path:
    from .native.run_store import RunStore
    import shutil
    destination = Path(path or project.path or PROJECT_FILENAME).expanduser().resolve()
    if destination.is_dir():
        destination /= PROJECT_FILENAME
    all_nodes = {**project.nodes, **project.removed_nodes}
    if set(project.nodes) & set(project.removed_nodes):
        raise ValueError("프로그램과 제거한 프로그램의 식별자가 중복됩니다.")
    if any(destination == Path(ref.path).resolve() for node in all_nodes.values() for ref in node.files.values()):
        raise ValueError("프로젝트 저장 경로가 원본 파일과 같습니다.")
    _check_connections(project.nodes, project.connections)
    source_store = project.run_store or (project.path.with_suffix(".runs") if project.path else None)
    saving_as = project.path is not None and destination != project.path
    target_store = destination.with_suffix(".runs") if saving_as or source_store is None else source_store
    if project.run_store_expected and (source_store is None or not source_store.is_dir()):
        raise ValueError(f'기대 실행 저장소가 없습니다: {source_store}')
    if source_store and saving_as and (target_store == source_store or source_store in target_store.parents):
        raise ValueError("다른 이름 저장에는 기존 실행 저장소와 분리된 경로가 필요합니다.")
    if saving_as and target_store.exists():
        raise ValueError("다른 이름 저장의 실행 저장소가 이미 있습니다. 기존 기록을 보호하기 위해 다른 이름을 선택하세요.")
    data = {"schema_version": SCHEMA_VERSION, "name": project.name,
            "run_store": (target_store.relative_to(destination.parent).as_posix()
                          if target_store.is_relative_to(destination.parent) else str(target_store)),
            "run_store_expected": True,
            "nodes": {key: asdict(node) for key, node in project.nodes.items()},
            "removed_nodes": {key: asdict(node) for key, node in project.removed_nodes.items()},
            "connections": [asdict(edge) for edge in project.connections]}
    serialized_nodes = {**data["nodes"], **data["removed_nodes"]}
    rebased = []
    if saving_as and source_store:
        for key, node in serialized_nodes.items():
            for role, ref in node["files"].items():
                try:
                    relative = Path(ref["path"]).relative_to(source_store)
                except ValueError:
                    continue
                ref["path"] = str(target_store / relative)
                rebased.append((key, role, ref["path"]))
    for node in serialized_nodes.values():
        for reference in node['files'].values():
            absolute = Path(reference['path']).resolve()
            reference['path'] = (absolute.relative_to(destination.parent).as_posix()
                                 if absolute.is_relative_to(destination.parent) else str(absolute))
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    copied = False
    created_empty_store = False
    try:
        if saving_as and source_store and source_store.exists():
            RunStore(source_store, expected=project.run_store_expected).copy_to(target_store)
            copied = True
        elif not target_store.exists():
            target_store.mkdir(parents=True, exist_ok=False)
            created_empty_store = True
        elif not target_store.is_dir():
            raise ValueError(f'실행 저장소가 폴더가 아닙니다: {target_store}')
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=destination.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        if copied:
            shutil.rmtree(target_store)
        elif created_empty_store:
            try:
                target_store.rmdir()  # never delete files another writer created
            except OSError:
                pass
        raise
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    project.path = destination
    project.run_store = target_store
    project.run_store_expected = True
    for key, role, new_path in rebased:
        all_nodes[key].files[role].path = new_path
    return destination
