"""Fixed read-only evidence assets, independent of auxiliary FE models/providers."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import shutil
import uuid
from xml.etree import ElementTree as ET

from .input_snapshot import fingerprint, snapshot_input
from .results import collect_results
from .run_store import TERMINAL_STATES, utc_now
from ..result_summary import finite_summary, format_native_evidence


def _inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, (str, Path)):
        raise ValueError('파일 경로 자료 형식이 잘못되었습니다.')
    relative_path = Path(relative)
    if relative_path.is_absolute() or '..' in relative_path.parts:
        raise ValueError(f"경로 탈출을 허용하지 않습니다: {relative}")
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"경로가 소유 폴더 밖을 가리킵니다: {relative}")
    return path


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _terminal(run):
    if run.state not in TERMINAL_STATES or run.parse_state == 'reading' or run.finished_at is None:
        raise ValueError("계산 또는 결과 판독이 진행 중입니다. 종료 후 근거를 전달하세요.")


def _copy_asset(evidence, asset_root, source, relative, role, *, source_path=None, expected=None):
    try:
        before = fingerprint(source)
        destination = _inside(asset_root, evidence['id'] + '/data/' + relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with Path(source).open('rb') as incoming, destination.open('xb') as outgoing:
            for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
                outgoing.write(chunk)
                digest.update(chunk)
        if before is None or before != fingerprint(source):
            destination.unlink(missing_ok=True)
            raise ValueError("읽는 동안 파일이 변경되었습니다.")
        actual = digest.hexdigest()
        verification = 'unrecorded' if expected is None else 'recorded_match' if actual == expected else 'mismatch'
        item = {'role': role, 'source_path': str(source_path or source),
                'stored_path': destination.relative_to(asset_root).as_posix(), 'sha256': actual,
                'size': destination.stat().st_size, 'expected_sha256': expected, 'verification': verification}
        evidence['files'].append(item)
        if verification == 'mismatch':
            evidence['warnings'].append(f"{role}: 저장 실행 해시와 불일치합니다: {source}")
        elif verification == 'unrecorded' and evidence['source_kind'] == 'native_run':
            evidence['warnings'].append(f"{role}: 과거 실행에 해시가 기록되지 않아 당시 파일과의 일치를 검증할 수 없습니다: {source}")
        return item
    except (OSError, ValueError) as error:
        evidence['warnings'].append(f"{role}: 파일 보존 실패 ({source}): {error}")
        return None


def _new(asset_root, *, source_kind, solver_id, units, unit_source):
    asset_root = Path(asset_root).expanduser().resolve()
    identifier = uuid.uuid4().hex
    (asset_root / identifier).mkdir(parents=True, exist_ok=False)
    evidence = {'id': identifier, 'source_kind': source_kind, 'created_at': utc_now(), 'solver_id': solver_id,
                'run_id': None, 'node_id': None, 'input_revision': None, 'input_manifest': {},
                'units': dict(units or {}), 'unit_source': unit_source, 'process_state': 'unconfirmed',
                'parse_state': 'missing', 'frame_index': None, 'summary_text': '', 'model_text': '',
                'log_text': '', 'files': [], 'warnings': []}
    return asset_root, evidence


def _text(asset_root, items, limit, *, tail=False):
    parts = []
    for item in items:
        path = _inside(asset_root, item['stored_path'])
        # Only the displayed excerpt is bounded; the complete bytes are owned assets.
        with path.open('rb') as stream:
            if tail and item['size'] > limit:
                stream.seek(-limit, 2)
            content = stream.read(limit + 1)
        cut = item['size'] > limit
        text = content[:limit].decode('utf-8', errors='replace')
        parts.append(f"--- {item['role']}: {item['source_path']} ---\n"
                     + ('[앞부분 잘림: 전체 bytes는 보존 파일 참조]\n' if cut and tail else '') + text
                     + ('\n[뒷부분 잘림: 전체 bytes는 보존 파일 참조]' if cut and not tail else ''))
    combined = '\n'.join(parts)
    if len(combined) > limit:
        return combined[:limit] + '\n[표시 길이 제한으로 잘림: 전체 bytes는 보존 파일 참조]'
    return combined


def _finish(evidence, asset_root, frame_index):
    scratch = asset_root / evidence['id'] / '_parse'
    scratch.mkdir()
    try:
        for role in ('frd', 'dat'):
            item = next((item for item in evidence['files'] if item['role'] == role and item['verification'] != 'mismatch'), None)
            if item:
                shutil.copyfile(_inside(asset_root, item['stored_path']), scratch / ('model.' + role))
        if evidence['solver_id'] == 'calculix':
            result = collect_results(scratch)
        else:
            result = {'status': 'unsupported', 'frames': [], 'warnings': [f"{evidence['solver_id']}: 파일 판독 미지원"], 'summary': {}}
    finally:
        shutil.rmtree(scratch)
    evidence['parse_state'] = result['status']
    evidence['requested_frame_index'] = frame_index
    frames = result.get('frames', [])
    if frame_index is not None and (isinstance(frame_index, bool) or not isinstance(frame_index, int)):
        raise ValueError('frame_index는 정수 또는 None이어야 합니다.')
    if frame_index is not None and not any(frame['index'] == frame_index for frame in frames):
        if frames:
            raise ValueError(f'선택 FRD 프레임이 없습니다: {frame_index}')
        evidence['warnings'].append(f'요청 FRD 프레임 {frame_index}를 판독할 수 없습니다. 다른 프레임으로 대체하지 않습니다.')
    else:
        evidence['frame_index'] = frame_index if frame_index is not None else frames[-1]['index'] if frames else None
    evidence['warnings'].extend(result.get('warnings', []))
    evidence['model_text'] = _text(asset_root, [item for item in evidence['files'] if item['role'] in
                                  {'input', 'include', 'execution_input', 'execution_include'}], 40000)
    evidence['log_text'] = _text(asset_root, [item for item in evidence['files'] if item['role'] == 'log'], 24000, tail=True)
    evidence['summary_text'] = format_native_evidence(evidence, finite_summary(result))
    # Reject accidental non-JSON metadata before handing it to a saved discussion.
    json.dumps(evidence, ensure_ascii=False, allow_nan=False)
    return evidence


def capture_run_evidence(run, asset_root: Path, *, frame_index: int | None = None) -> dict:
    _terminal(run)
    asset_root, evidence = _new(asset_root, source_kind='native_run', solver_id=run.solver_id,
                                units=run.units, unit_source=run.unit_source)
    evidence.update(run_id=run.id, node_id=run.node_id, input_revision=run.input_revision,
                    input_manifest=copy.deepcopy(run.snapshot), process_state=run.state)
    if run.stale:
        evidence['warnings'].append('오래된 실행: ' + run.stale_reason)
    if run.error:
        evidence['warnings'].append('실행 기록: ' + run.error)
    if run.state != 'completed':
        evidence['warnings'].append('솔버 정상 완료가 확인되지 않은 실행입니다. 입력과 로그를 근거로 확인하세요.')
    records = run.snapshot.get('files', [])
    if not records:
        evidence['warnings'].append('저장 입력/INCLUDE manifest가 없어 전체 입력 조건을 확인할 수 없습니다.')
    for index, record in enumerate(records):
        for key, hash_key, role in (('source_copy', 'original_sha256', 'input' if index == 0 else 'include'),
                                     ('execution_path', 'execution_sha256', 'execution_input' if index == 0 else 'execution_include')):
            relative = record.get(key)
            if not relative:
                evidence['warnings'].append(f"입력 manifest에 {key}가 없습니다.")
                continue
            try:
                path = _inside(run.workdir, relative)
            except ValueError as error:
                evidence['warnings'].append(str(error))
                continue
            _copy_asset(evidence, asset_root, path, relative, role, source_path=record.get('source_path') if key == 'source_copy' else path,
                        expected=record.get(hash_key))
    artifacts = {**run.snapshot.get('outputs', {}), **run.snapshot.get('artifacts', {})}
    candidates = [('model.frd', 'frd'), ('model.dat', 'dat'), ('job.log', 'log')]
    candidates.extend((name, 'exchange') for name in run.result.get('exchange_files', []))
    for relative, role in dict.fromkeys(candidates):
        try:
            path = _inside(run.workdir, relative)
        except ValueError as error:
            evidence['warnings'].append(str(error))
            continue
        if role in {'frd', 'dat'} and not path.exists() and relative not in artifacts:
            continue
        _copy_asset(evidence, asset_root, path, relative, role, expected=artifacts.get(relative))
    return _finish(evidence, asset_root, frame_index)


def import_file_evidence(asset_root: Path, *, files: dict[str, Path], solver_id: str,
                         units: dict | None = None, unit_source: str = 'user', frame_index: int | None = None) -> dict:
    asset_root, evidence = _new(asset_root, source_kind='manual', solver_id=solver_id, units=units, unit_source=unit_source)
    evidence['warnings'].append('사용자 선택 자료, 동일 실행 대응 미확인')
    for index, (role, source) in enumerate(files.items()):
        role = 'input' if role == 'inp' else role
        source = Path(source).expanduser().resolve()
        item = _copy_asset(evidence, asset_root, source, f'manual/{index:04d}-{source.name}', role)
        if item and role == 'input':
            evidence['input_revision'] = item['sha256']
            # Collect complete INCLUDE provenance when possible; never reinterpret physics.
            scratch = asset_root / evidence['id'] / '_input'
            try:
                manifest = snapshot_input(source, scratch)
                if manifest['input_revision'] != item['sha256']:
                    raise ValueError('수집 중 입력 원본이 변경되었습니다.')
                evidence['input_manifest'] = manifest
                manifest['purpose'] = 'include_collection_only_not_execution'
                for number, record in enumerate(manifest['files'][1:], 1):
                    _copy_asset(evidence, asset_root, scratch / record['source_copy'],
                                f'manual/includes/{number:06d}.inp', 'include', source_path=record['source_path'],
                                expected=record['original_sha256'])
            except (OSError, ValueError) as error:
                evidence['warnings'].append(f"INP/INCLUDE 전체 조건을 수집하지 못했습니다: {error}")
            finally:
                shutil.rmtree(scratch, ignore_errors=True)
        elif role not in {'frd', 'dat', 'log'}:
            evidence['warnings'].append(f"{role}: 지원하지 않는 자료 형식/역할로 원문만 보존합니다: {source}")
    return _finish(evidence, asset_root, frame_index)


def verify_evidence(evidence: dict, asset_root: Path) -> list[str]:
    """Check owned saved bytes only; never substitute today's external originals."""
    root = Path(asset_root).resolve()
    issues = []
    if not isinstance(evidence, dict) or not isinstance(evidence.get('files'), list):
        return ['근거 파일 목록 자료 형식이 잘못되었습니다.']
    identifier = evidence.get('id', '')
    if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        return ['근거 ID가 잘못되었습니다.']
    for item in evidence.get('files', []):
        if not isinstance(item, dict):
            issues.append('근거 파일 항목 자료 형식이 잘못되었습니다.')
            continue
        try:
            relative = item['stored_path']
            if Path(relative).parts[0] != identifier:
                raise ValueError('다른 근거의 파일을 참조합니다.')
            path = _inside(root, relative)
            if not path.is_file():
                raise ValueError('보존 파일이 없습니다.')
            if path.stat().st_size != item['size'] or _sha(path) != item['sha256']:
                raise ValueError('보존 파일 SHA256/크기가 일치하지 않습니다.')
        except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
            issues.append(f"근거 파일 검증 실패 ({item.get('stored_path')}): {error}")
    return issues


def native_results_file(run) -> Path:
    """Only open recorded, intact exchange files from this selected run."""
    _terminal(run)
    files = run.result.get('exchange_files', [])
    chosen = next((name for name in files if name.lower().endswith('.pvd')), None)
    chosen = chosen or next((name for name in files if name.lower().endswith('.vtu')), None)
    if chosen is None:
        raise ValueError('선택 실행에 열 수 있는 PVD/VTU 결과 파일이 없습니다.')
    expected = run.snapshot.get('artifacts', {})
    def verify(relative):
        path = _inside(run.workdir, relative)
        if not path.is_file():
            raise ValueError(f'교환 파일이 없습니다: {relative}')
        if not expected.get(relative):
            raise ValueError('교환 파일 무결성을 확인할 수 없습니다. 새로 계산한 실행을 선택하세요.')
        if _sha(path) != expected[relative]:
            raise ValueError(f'교환 파일 해시가 일치하지 않습니다: {relative}')
        return path
    document = verify(chosen)
    if document.suffix.lower() == '.pvd':
        try:
            datasets = ET.parse(document).getroot().findall('.//DataSet')
        except ET.ParseError as error:
            raise ValueError(f'PVD를 읽을 수 없습니다: {error}') from error
        if not datasets:
            raise ValueError('PVD에 결과 프레임 참조가 없습니다.')
        for dataset in datasets:
            name = dataset.get('file', '')
            path = _inside(document.parent, name)
            if not name or path.suffix.lower() != '.vtu' or not path.is_relative_to(run.workdir.resolve()):
                raise ValueError('PVD가 이 실행의 VTU를 참조하지 않습니다.')
            relative = path.relative_to(run.workdir.resolve()).as_posix()
            if relative not in files:
                raise ValueError('PVD가 선택 실행에 기록되지 않은 파일을 참조합니다.')
            verify(relative)
    return document
