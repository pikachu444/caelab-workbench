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


def _input_blocks(lines):
    """Lexical boundaries only: no interpretation of conditions or set membership."""
    start = 0
    for index, line in enumerate(lines):
        stripped = line.lstrip(' \t')
        if stripped.startswith('*') and not stripped.startswith('**'):
            if index > start:
                yield start, lines[start:index]
            start = index
    if start < len(lines):
        yield start, lines[start:]


def _line_excerpt(start, lines):
    return f'[원문 L{start + 1}-L{start + len(lines)}]\n' + ''.join(lines) + '\n'


def _header_end(lines):
    """Retain comma-adjacent raw lines; this does not validate solver syntax."""
    end = 1
    pending = lines[0].rstrip().endswith(',')
    while pending and end < len(lines):
        line = lines[end].lstrip(' \t')
        if not line.strip() or line.startswith('**'):
            end += 1
            continue
        if line.startswith('*'):
            break
        end += 1
        pending = line.rstrip().endswith(',')
    return end


def _input_block_views(start, lines, *, compact_lists=True):
    header = lines[0].lstrip(' \t')
    keyword = ''.join(header.split(',', 1)[0].split()).upper() if header.startswith('*') else ''
    header_end = _header_end(lines) if header.startswith('*') and not header.startswith('**') else 1
    full = _line_excerpt(start, lines)
    # Exact names matter: NODE FILE and SURFACE INTERACTION are not large lists.
    data_size = sum(len(line) for line in lines[header_end:] if not line.lstrip(' \t').startswith('**'))
    if compact_lists and keyword in {'*NODE', '*ELEMENT', '*NSET', '*ELSET', '*SURFACE'} and data_size > 2048:
        pieces = [_line_excerpt(start, lines[:header_end])]
        omitted = None
        for offset, line in enumerate(lines[header_end:], header_end):
            if line.lstrip(' \t').startswith('**'):
                if omitted is not None:
                    pieces.append(f'[대량 목록 미제공: 원문 L{start + omitted + 1}-L{start + offset}]\n')
                    omitted = None
                pieces.append(_line_excerpt(start + offset, [line]))
            elif omitted is None:
                omitted = offset
        if omitted is not None:
            pieces.append(f'[대량 목록 미제공: 원문 L{start + omitted + 1}-L{start + len(lines)}]\n')
        pieces.append('[목록 생략: 절점/요소 membership·연결·면 side/normal·적용 위치는 이 발췌로 확인 불가]\n')
        full = ''.join(pieces)
    if header.startswith('*') and not header.startswith('**'):
        minimal = _line_excerpt(start, lines[:header_end])
        if len(lines) > header_end:
            minimal += f'[예산 제한: 원문 L{start + header_end + 1}-L{start + len(lines)} 본문 미제공]\n'
    else:
        minimal = f'[예산 제한: 원문 L{start + 1}-L{start + len(lines)} 미제공]\n'
    return full, minimal, (start + 1, start + len(lines))


def _fit_input_blocks(views, budget):
    full = ''.join(view[0] for view in views)
    if len(full) <= budget:
        return full
    # Keep the ordered header outline before spending remaining space on bodies.
    minimal = ''.join(view[1] for view in views)
    if len(minimal) <= budget:
        remaining = budget - len(minimal)
        parts = []
        for complete, short, _ in views:
            if len(complete) - len(short) <= remaining:
                parts.append(complete)
                remaining -= len(complete) - len(short)
            else:
                parts.append(short)
        return ''.join(parts)
    # Even the outline may exceed the budget. Never cut through raw data/markers.
    parts = []
    used = 0
    for _, short, bounds in views:
        marker = f'[예산 제한: 원문 L{bounds[0]}-L{views[-1][2][1]} 블록 전체 미제공; 보존 파일 참조]\n'
        next_marker = f'[예산 제한: 원문 L{bounds[1] + 1}-L{views[-1][2][1]} 블록 전체 미제공; 보존 파일 참조]\n'
        if used + len(short) + len(next_marker) > budget:
            break
        parts.append(short)
        used += len(short)
    else:
        return ''.join(parts)
    return ''.join(parts) + marker if len(marker) <= budget else ''


def _model_text(asset_root, items, limit=40000):
    """Bounded source excerpts, not a reconstructed executable CalculiX deck."""
    groups = {}
    for index, item in enumerate(items):
        if item['role'] not in {'input', 'include', 'execution_input', 'execution_include'}:
            continue
        # All copies remain assets. Identical bytes need only one displayed body.
        groups.setdefault(item['sha256'], []).append((index, item))
    if not groups:
        return ''
    sections = []
    for aliases in groups.values():
        item = aliases[0][1]
        with _inside(asset_root, item['stored_path']).open('r', encoding='utf-8', errors='replace', newline='') as stream:
            lines = stream.readlines()
        header = '\n--- 별도 입력 파일 / 동일 SHA256 자료 ---\n' + '\n'.join(
            f"files[{index}] {alias['role']}: {alias['source_path']}" for index, alias in aliases)
        header += f"\nSHA256={item['sha256']}\n"
        if len(aliases) > 1:
            header += '[동일 bytes: 위 출처/역할의 본문을 한 번만 제공; INCLUDE 호출 순서는 각 파일 원문 참조]\n'
        header += f'[파일 원문 범위: L1-L{len(lines)}]\n' if lines else '[빈 파일]\n'
        compact = Path(item['source_path']).suffix.lower() in {'.inp', '.inc'}
        views = [_input_block_views(start, block, compact_lists=compact) for start, block in _input_blocks(lines)]
        raw = _line_excerpt(0, lines) if lines else ''
        sections.append((header, raw, views))
    notice = ('[입력 자료 발췌: 각 파일은 독립 자료이며 이어 붙인 실행 덱이 아닙니다. '
              'L은 해당 파일 원문 행입니다. 대괄호 설명은 발췌 메모입니다. '
              '원문 보존과 실제 제공 범위를 구분하세요. 미제공 범위의 조건/개수/총량은 추정하지 마세요.]\n')
    footer = '[끝: 생략된 본문/파일은 보존 자산과 manifest 참조; 이 발췌로 모델 완전성을 판정하지 마세요.]\n'
    omission = '[미제공 파일: 위에 files[n] 출처 섹션이 없는 모든 입력/include/execution 항목은 경로·본문 전체 미제공; 보존 files 목록 참조]\n'
    available = max(0, limit - len(notice) - len(footer) - len(omission))
    sizes = [len(header) + len(raw) for header, raw, _ in sections]
    # Water-fill the file budgets so original/execution duplicates and many small
    # INCLUDEs cannot consume everything before a later file gets a share.
    budgets = [0] * len(sections)
    pending = set(range(len(sections)))
    while pending:
        share = available // len(pending)
        small = {index for index in pending if sizes[index] <= share}
        if not small:
            for index in pending:
                budgets[index] = share
            break
        for index in small:
            budgets[index] = sizes[index]
            available -= sizes[index]
        pending -= small
    parts = [notice]
    missing = []
    for (header, raw, views), budget, aliases in zip(sections, budgets, groups.values()):
        body_budget = budget - len(header)
        body = ''
        if len(raw) <= body_budget:
            body = raw
        elif body_budget >= 0:
            body = _fit_input_blocks(views, body_budget)
        if not body and views:
            missing.extend(index for index, _ in aliases)
        elif len(header) <= budget:
            parts.append(header + body)
        else:
            missing.extend(index for index, _ in aliases)
    if missing:
        # A bounded marker still identifies omissions if even all paths won't fit.
        parts.append(omission)
    return ''.join(parts) + footer


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
    input_items = [item for item in evidence['files'] if item['role'] in
                   {'input', 'include', 'execution_input', 'execution_include'}]
    evidence['model_text'] = (_model_text(asset_root, evidence['files']) if evidence['solver_id'] == 'calculix'
                              else _text(asset_root, input_items, 40000))
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


def native_results_file(run, *, native_format=None) -> Path:
    """Only open recorded, intact exchange files from this selected run."""
    _terminal(run)
    files = run.result.get('exchange_files', [])
    chosen = next((name for name in files if name.lower().endswith('.pvd')), None)
    chosen = chosen or next((name for name in files if name.lower().endswith('.vtu')), None)
    if native_format is not None:
        if native_format != 'frd' or run.solver_id != 'calculix':
            raise ValueError('선택한 솔버 결과를 이 후처리 형식으로 열 수 없습니다.')
        chosen = 'model.frd'
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
