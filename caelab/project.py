"""Small native-project file graph; source documents stay owned by their programs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCHEMA_VERSION = 1
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

    def refresh(self) -> list[str]:
        issues = []
        for node in self.nodes.values():
            for role, reference in node.files.items():
                status = reference.status
                if status != "ready":
                    reason = f"{node.id}.files.{role}: {status} — {reference.path}"
                    issues.append(reason)
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
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(f"지원하지 않는 프로젝트 schema_version: {data.get('schema_version') if isinstance(data, dict) else None}")
        if not isinstance(data.get("name"), str) or not isinstance(data.get("nodes"), dict) or not isinstance(data.get("connections"), list):
            raise ValueError("name/nodes/connections 자료 형식이 잘못되었습니다.")
        project = NativeProject(data["name"], path=path)
        for key, raw in data["nodes"].items():
            if not isinstance(raw, dict) or not key or raw.get("id") != key:
                raise ValueError(f"nodes.{key}: 비어 있거나 일치하지 않는 노드 ID입니다.")
            if any(not isinstance(raw.get(name), str) or not raw[name].strip() for name in ("kind", "program_id")):
                raise ValueError(f"nodes.{key}: kind/program_id가 필요합니다.")
            position = raw.get("position", [0, 0])
            if not isinstance(position, (list, tuple)) or len(position) != 2 or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in position):
                raise ValueError(f"nodes.{key}.position: 유한한 두 좌표가 필요합니다.")
            if raw.get("state", "needs_input") not in NODE_STATES or not isinstance(raw.get("reason", ""), str):
                raise ValueError(f"nodes.{key}: 상태 형식이 잘못되었습니다.")
            node = ProgramNode(key, raw["kind"], raw["program_id"], position=tuple(position), state=raw.get("state", "needs_input"), reason=raw.get("reason", ""))
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
            project.nodes[key] = node
        project.connections = [Connection(**edge) for edge in data["connections"]]
        _check_connections(project.nodes, project.connections)
        project.refresh()
        return project
    except (ValueError, TypeError, KeyError) as exc:
        raise ValueError(f"프로젝트 읽기 실패 ({path}): {exc}") from exc


def save_native_project(project: NativeProject, path: str | Path | None = None) -> Path:
    destination = Path(path or project.path or PROJECT_FILENAME).expanduser().resolve()
    if destination.is_dir():
        destination /= PROJECT_FILENAME
    if any(destination == Path(ref.path).resolve() for node in project.nodes.values() for ref in node.files.values()):
        raise ValueError("프로젝트 저장 경로가 원본 파일과 같습니다.")
    _check_connections(project.nodes, project.connections)
    data = {"schema_version": SCHEMA_VERSION, "name": project.name,
            "nodes": {key: asdict(node) for key, node in project.nodes.items()},
            "connections": [asdict(edge) for edge in project.connections]}
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=destination.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    project.path = destination
    return destination
