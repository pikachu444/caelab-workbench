"""Frozen auxiliary models and results carried by the existing native run owner."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

from .model.schema import StructuralModel
from .model.store import model_revision
from .native.adapter import get_solver_adapter
from .native.input_snapshot import snapshot_input as snapshot_native_input
from .native.run_store import NativeRun, atomic_json
from .project import file_revision
from .result_summary import summarize_result
from .solvers.base import ResultSet, SolverConverter
from .validate.checker import validate_model


MODEL_NODE_ID = 'structural-model'


@dataclass(frozen=True)
class ModelRunResult:
    run: NativeRun
    model: StructuralModel
    result: ResultSet | None


class ModelRunAdapter:
    def __init__(self, model: StructuralModel, converter: SolverConverter):
        self.model = copy.deepcopy(model)
        self.converter = converter
        self.native = get_solver_adapter(converter.id)
        self._input_snapshot = None

    def persist_input(self, workdir: Path) -> dict:
        path = Path(workdir) / 'model-snapshot.json'
        atomic_json(path, self.model.to_dict())
        return {'auxiliary_model': {'path': path.name, 'revision': model_revision(self.model),
                                    'sha256': file_revision(path)}}

    def snapshot_input(self, input_path, workdir, check_cancelled) -> dict:
        check_cancelled()
        issues = validate_model(self.model) + self.converter.validate(self.model)
        errors = [str(issue) for issue in issues if issue.severity == 'error']
        if errors:
            raise ValueError('\n'.join(errors))
        check_cancelled()
        converted = Path(workdir) / '.converted'
        converted.mkdir(exist_ok=True)
        deck = self.converter.write_input(self.model, converted)
        if not Path(deck).resolve().is_relative_to(converted.resolve()):
            raise ValueError('변환 입력이 소유 실행 폴더 밖을 가리킵니다.')
        self._input_snapshot = snapshot_native_input(deck, Path(workdir), check_cancelled)
        return self._input_snapshot

    def build_run_request(self, status, workdir, input_path):
        if status.spec.id != self.converter.id:
            raise ValueError('보조 변환기와 선택한 솔버가 다릅니다.')
        return self.native.build_run_request(status, workdir, Path(workdir) / 'model.inp')

    def collect_results(self, workdir, *, check_cancelled=None) -> dict:
        check = check_cancelled or (lambda: None)
        check()
        workdir = Path(workdir)
        if self._input_snapshot is None:
            raise ValueError('보조 모델의 실행 입력 사본이 없습니다.')
        _verify_input_files(workdir, self._input_snapshot, check)
        result = self.converter.read_result(self.model, workdir / 'model.inp', workdir,
                                            check_cancelled=check)
        check()
        path = workdir / 'model-result.json'
        payload = {**asdict(result), 'log_path': 'job.log'}
        atomic_json(path, payload)
        return {'status': 'readable' if result.success else 'failed', 'frames': [],
                'exchange_files': [], 'warnings': list(result.validation_issues),
                'summary': summarize_result(result),
                'auxiliary_result': {'path': path.name, 'sha256': file_revision(path)}}

    def expected_output_names(self) -> tuple[str, ...]:
        return ('model.frd', 'model.dat', 'job.log', 'model-snapshot.json', 'model-result.json')

    def artifact_paths(self, workdir, snapshot) -> list[Path]:
        root = Path(workdir).resolve()
        paths = []
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.name not in {'job.json', '.start-solver'}:
                if not path.resolve().is_relative_to(root):
                    raise ValueError(f'실행 파일이 소유 폴더 밖을 가리킵니다: {path}')
                paths.append(path)
        return paths


def _owned_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip() or '\0' in relative:
        raise ValueError('보존 JSON의 소유 상대 경로가 필요합니다.')
    path = Path(relative)
    root = root.resolve()
    target = (root / path).resolve()
    if path.is_absolute() or '..' in path.parts or not target.is_relative_to(root):
        raise ValueError(f'보존 JSON이 소유 상대 경로 밖을 가리킵니다: {relative}')
    return target


def _verify_file(root: Path, relative, digest) -> None:
    target = _owned_path(root, relative)
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError(f'보존 파일 해시 기록이 온전하지 않습니다: {relative}')
    if file_revision(target) != digest:
        raise ValueError(f'보존 파일이 없거나 해시가 다릅니다: {relative}')


def _verify_input_files(root, snapshot, check=lambda: None):
    files = snapshot.get('files', [])
    if not isinstance(files, list):
        raise ValueError('보존 입력 파일 목록이 필요합니다.')
    for item in files:
        check()
        if not isinstance(item, dict):
            raise ValueError('보존 입력 파일 기록이 필요합니다.')
        for path_key, hash_key in (('source_copy', 'original_sha256'),
                                   ('execution_path', 'execution_sha256')):
            _verify_file(root, item.get(path_key), item.get(hash_key))


def _read_owned_json(run: NativeRun, metadata) -> dict:
    if not isinstance(metadata, dict):
        raise ValueError('보존 JSON의 경로와 해시가 필요합니다.')
    relative, digest = metadata.get('path'), metadata.get('sha256')
    target = _owned_path(Path(run.workdir), relative)
    artifacts = run.snapshot.get('artifacts', {})
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)
            or not isinstance(artifacts, dict) or artifacts.get(relative) != digest):
        raise ValueError(f'보존 JSON 해시 기록이 온전하지 않습니다: {relative}')
    try:
        raw = target.read_bytes()
    except OSError as error:
        raise ValueError(f'보존 JSON을 읽을 수 없습니다: {relative}: {error}') from error
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f'보존 JSON 해시가 다릅니다: {relative}')
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f'보존 JSON 객체가 필요합니다: {relative}')
    return data


def read_model_result(run: NativeRun) -> ModelRunResult:
    if run.node_id != MODEL_NODE_ID or not isinstance(run.snapshot, dict):
        raise ValueError('보조 모델 실행 기록이 필요합니다.')
    if not run.finished_at:
        raise ValueError('실행 정리를 마친 뒤 보존 모델을 여세요.')
    artifacts = run.snapshot.get('artifacts')
    if not isinstance(artifacts, dict):
        raise ValueError('보존 실행 파일 해시 기록이 필요합니다.')
    for relative, digest in artifacts.items():
        _verify_file(Path(run.workdir), relative, digest)
    _verify_input_files(Path(run.workdir), run.snapshot)
    metadata = run.snapshot.get('auxiliary_model')
    model = StructuralModel.from_dict(_read_owned_json(run, metadata))
    if metadata.get('revision') != model_revision(model):
        raise ValueError('보존 모델의 내용 또는 단위 선언 해시가 다릅니다.')
    if not isinstance(run.result, dict):
        raise ValueError('보조 모델 결과 기록 객체가 필요합니다.')
    result_metadata = run.result.get('auxiliary_result')
    if result_metadata is None:
        if run.parse_state in {'readable', 'partial'}:
            raise ValueError('판독된 보조 모델 결과 JSON이 없습니다.')
        return ModelRunResult(run, model, None)
    payload = _read_owned_json(run, result_metadata)
    for field in ('displacements', 'reactions', 'nodal_forces'):
        values = payload.get(field, {})
        if not isinstance(values, dict):
            raise ValueError(f'{field}: 절점 결과 객체가 필요합니다.')
        converted = {}
        for node, value in values.items():
            try:
                number = int(node)
            except (TypeError, ValueError) as error:
                raise ValueError(f'{field}: 절점 ID가 잘못되었습니다.') from error
            if number <= 0 or str(number) != node:
                raise ValueError(f'{field}: 절점 ID가 잘못되었습니다.')
            converted[number] = value
        payload[field] = converted
    result = ResultSet(**payload)
    if result.solver_id != run.solver_id or type(result.success) is not bool:
        raise ValueError('보존 결과의 솔버 또는 성공 상태가 잘못되었습니다.')
    result.log_path = str(Path(run.workdir) / 'job.log')
    return ModelRunResult(run, model, result)
