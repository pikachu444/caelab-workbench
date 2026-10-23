"""Fixed bytes and honest result provenance, without solvers or providers."""

import copy
import json
from pathlib import Path
import uuid

import pytest

from caelab.native.evidence import capture_run_evidence, import_file_evidence, verify_evidence
from caelab.native.input_snapshot import snapshot_input
from caelab.native.results import collect_results
from caelab.native.run_store import NativeRun, utc_now
from caelab.project import file_revision
from caelab.result_summary import finite_summary
from test_native_results import _geometry, _field


def make_run(tmp_path):
    identifier = uuid.uuid4().hex
    workdir = tmp_path / 'project.runs' / identifier
    workdir.mkdir(parents=True)
    source = tmp_path / '외부 원본 공백'
    source.mkdir()
    (source / 'loads.inc').write_bytes(b'** INPUT SECRET\n*CONTACT PAIR,INTERACTION=FRICTION\nA,B\n')
    inp = source / 'model.inp'
    inp.write_bytes(b'** native original\r\n*INCLUDE,INPUT=loads.inc\r\n')
    snapshot = snapshot_input(inp, workdir)
    ids = (40, 10, 90, 25)
    frd = (_geometry() + _field([(node, [1, 0, 0]) for node in ids], value=.5)
           + _field([(node, [3, 4, 0]) for node in ids], value=2, increment=3, counter=8) + ' 9999\n')
    (workdir / 'model.frd').write_text(frd, encoding='ascii')
    (workdir / 'model.dat').write_text(' forces (fx,fy,fz) for set OTHER and time 9.0\n\n 40 1.0 2.0 3.0\n\n', encoding='ascii')
    (workdir / 'job.log').write_bytes(b'LOG SECRET\nCalculiX exited\n')
    result = collect_results(workdir)
    snapshot['outputs'] = {name: file_revision(workdir / name) for name in ('model.frd', 'model.dat')}
    snapshot['artifacts'] = {name: file_revision(workdir / name) for name in
                             ('model.frd', 'model.dat', 'job.log', *result['exchange_files'])}
    return NativeRun(identifier, 'analysis-node', workdir, state='completed', parse_state=result['status'],
                     input_path=str(inp), input_revision=snapshot['input_revision'], snapshot=snapshot,
                     units={'length': 'mm', 'force': 'N'}, unit_source='declared by user',
                     result=result, log_path=str(workdir / 'job.log'), finished_at=utc_now(), returncode=0)


def test_capture_preserves_saved_source_and_exchange_bytes_and_selected_frame(tmp_path):
    run = make_run(tmp_path)
    assets = tmp_path / '토론 자료'
    evidence = capture_run_evidence(run, assets, frame_index=0)
    assert verify_evidence(evidence, assets) == []
    assert evidence['run_id'] == run.id and evidence['frame_index'] == 0
    assert evidence['input_manifest'] == run.snapshot
    assert evidence['units'] == run.units and evidence['unit_source'] == run.unit_source
    assert evidence['process_state'] == 'completed' and evidence['parse_state'] == 'readable'
    assert 'LOG SECRET' not in evidence['summary_text'] and 'INPUT SECRET' not in evidence['summary_text']
    assert 'LOG SECRET' in evidence['log_text'] and 'INPUT SECRET' in evidence['model_text']
    assert '"max_displacement": 1.0' in evidence['summary_text']
    assert '"analysis_type"' in evidence['summary_text'] and '"kode"' in evidence['summary_text']
    assert 'coverage=' in evidence['summary_text'] and '유한값 범위' in evidence['summary_text']
    assert 'DAT는 FRD와 합치지 않은 별도' in evidence['summary_text']
    assert '"value": 9.0' in evidence['summary_text']
    for item in evidence['files']:
        assert item['verification'] == 'recorded_match'
        assert not Path(item['stored_path']).is_absolute()
        assert (assets / item['stored_path']).stat().st_size == item['size']
        if item['role'] in {'frd', 'dat', 'log', 'exchange'}:
            assert (assets / item['stored_path']).read_bytes() == Path(item['source_path']).read_bytes()
    json.dumps(evidence, ensure_ascii=False, allow_nan=False)


def test_manual_imports_are_separate_and_reopen_reads_fixed_assets_not_originals(tmp_path):
    run = make_run(tmp_path)
    assets = tmp_path / 'assets'
    files = {'input': Path(run.input_path), 'frd': run.workdir / 'model.frd', 'dat': run.workdir / 'model.dat', 'log': Path(run.log_path)}
    first = import_file_evidence(assets, files=files, solver_id='calculix')
    assert first['source_kind'] == 'manual' and first['run_id'] is None and first['node_id'] is None
    assert first['process_state'] == 'unconfirmed' and first['units'] == {}
    assert '사용자 선택 자료, 동일 실행 대응 미확인' in first['summary_text']
    frozen = json.loads(json.dumps(first))
    Path(run.input_path).write_bytes(b'** another input B')
    (run.workdir / 'model.frd').unlink()
    second = import_file_evidence(assets, files={'input': Path(run.input_path)}, solver_id='calculix')
    assert first == frozen and second['id'] != first['id']
    assert second['input_revision'] != first['input_revision']
    assert second['parse_state'] == 'missing'
    assert verify_evidence(frozen, assets) == []
    assert '"max_displacement": 5.0' in first['summary_text']
    assert '"max_displacement": 5.0' not in second['summary_text']


def test_missing_or_unsupported_manual_file_does_not_hide_valid_dat(tmp_path):
    run = make_run(tmp_path)
    unsupported = tmp_path / 'unknown.bin'
    unsupported.write_bytes(b'\x00\x01raw')
    evidence = import_file_evidence(tmp_path / 'assets', files={'frd': tmp_path / 'missing.frd',
                                    'dat': run.workdir / 'model.dat', 'other': unsupported}, solver_id='calculix')
    assert evidence['parse_state'] == 'readable'
    assert any('보존 실패' in warning for warning in evidence['warnings'])
    assert any('미판독' in warning or '원문만 보존' in warning for warning in evidence['warnings'])
    assert any(item['role'] == 'other' for item in evidence['files'])


def test_manual_include_failure_does_not_claim_complete_conditions(tmp_path):
    inp = tmp_path / 'model.inp'
    inp.write_bytes(b'*INCLUDE,INPUT=absent.inc\n')
    evidence = import_file_evidence(tmp_path / 'assets', files={'input': inp}, solver_id='calculix')
    assert evidence['input_manifest'] == {}
    assert any('INP/INCLUDE 전체 조건을 수집하지 못했습니다' in warning for warning in evidence['warnings'])
    assert 'absent.inc' in evidence['model_text']


@pytest.mark.parametrize('change', ['tamper', 'missing', 'escape', 'other_id'])
def test_verification_detects_tamper_missing_and_path_escape(tmp_path, change):
    run = make_run(tmp_path)
    assets = tmp_path / 'assets'
    evidence = capture_run_evidence(run, assets)
    item = evidence['files'][0]
    path = assets / item['stored_path']
    if change == 'tamper':
        path.write_bytes(b'altered asset')
    elif change == 'missing':
        path.unlink()
    elif change == 'escape':
        item['stored_path'] = evidence['id'] + '/../../outside.inp'
    else:
        item['stored_path'] = 'b' * 32 + '/data/model.inp'
    assert verify_evidence(evidence, assets)


def test_native_hash_mismatch_is_preserved_but_not_used_as_verified_result(tmp_path):
    run = make_run(tmp_path)
    (run.workdir / 'model.frd').write_bytes(b'corrupted FRD')
    evidence = capture_run_evidence(run, tmp_path / 'assets', frame_index=0)
    item = next(item for item in evidence['files'] if item['role'] == 'frd')
    assert item['verification'] == 'mismatch'
    assert any('해시와 불일치' in warning for warning in evidence['warnings'])
    assert 'FRD 선택 프레임이 없습니다' in evidence['summary_text']
    assert evidence['frame_index'] is None and evidence['log_text']
    assert evidence['requested_frame_index'] == 0
    assert any('다른 프레임으로 대체하지 않습니다' in warning for warning in evidence['warnings'])


def test_readable_frames_do_not_substitute_another_requested_index(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(ValueError, match='선택 FRD 프레임이 없습니다'):
        capture_run_evidence(run, tmp_path / 'assets', frame_index=99)


def test_old_unrecorded_log_and_exchange_hashes_are_not_promoted(tmp_path):
    run = make_run(tmp_path)
    run.snapshot.pop('artifacts')
    evidence = capture_run_evidence(run, tmp_path / 'assets')
    assert next(item for item in evidence['files'] if item['role'] == 'log')['verification'] == 'unrecorded'
    assert all(item['verification'] == 'unrecorded' for item in evidence['files'] if item['role'] == 'exchange')
    assert any('과거 실행에 해시가 기록되지 않아' in warning for warning in evidence['warnings'])


def test_failed_run_keeps_input_log_without_invented_success_or_values(tmp_path):
    run = make_run(tmp_path)
    run.state, run.parse_state, run.error, run.result = 'failed', 'missing', 'solver error', {}
    (run.workdir / 'model.frd').unlink()
    (run.workdir / 'model.dat').unlink()
    run.snapshot['outputs'] = {}
    run.snapshot['artifacts'] = {'job.log': file_revision(Path(run.log_path))}
    evidence = capture_run_evidence(run, tmp_path / 'assets')
    assert evidence['process_state'] == 'failed' and evidence['parse_state'] == 'missing'
    assert '정상 완료가 확인되지 않은 실행' in evidence['summary_text']
    assert 'LOG SECRET' in evidence['log_text'] and evidence['model_text']
    assert '"max_displacement": 0' not in evidence['summary_text']


@pytest.mark.parametrize('state,parse,finished', [('running', 'unparsed', None), ('completed', 'reading', None), ('completed', 'readable', None)])
def test_incomplete_run_waits_for_terminal_record(tmp_path, state, parse, finished):
    run = make_run(tmp_path)
    run.state, run.parse_state, run.finished_at = state, parse, finished
    with pytest.raises(ValueError, match='종료 후'):
        capture_run_evidence(run, tmp_path / 'assets')


def test_excerpt_truncation_preserves_complete_bytes_and_summaries_are_finite(tmp_path):
    run = make_run(tmp_path)
    Path(run.log_path).write_bytes(b'L' * 30000)
    run.snapshot['artifacts']['job.log'] = file_revision(Path(run.log_path))
    evidence = capture_run_evidence(run, tmp_path / 'assets')
    assert '잘림' in evidence['log_text']
    item = next(item for item in evidence['files'] if item['role'] == 'log')
    assert (tmp_path / 'assets' / item['stored_path']).read_bytes() == b'L' * 30000
    assert finite_summary({'x': float('nan'), 'y': [float('inf'), -float('inf')]}) == {'x': None, 'y': [None, None]}
    assert 'Infinity' not in json.dumps(finite_summary({'x': float('inf')}), allow_nan=False)
