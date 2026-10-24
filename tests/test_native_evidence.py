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
    assert '*INCLUDE,INPUT=loads.inc' in evidence['model_text']
    assert '*INCLUDE,INPUT=includes/000001.inp' in evidence['model_text']
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


def test_dat_only_displacement_summary_keeps_finite_values_units_and_set_frame(tmp_path):
    dat = tmp_path / 'displacement.dat'
    dat.write_text(' displacements (vx,vy,vz) for set TIP and time 1.0\n\n 101 3.0 4.0 0.0\n\n', encoding='ascii')
    evidence = import_file_evidence(tmp_path / 'assets', files={'dat': dat}, solver_id='calculix',
                                    units={'length': 'mm'}, unit_source='user declaration')
    assert evidence['parse_state'] == 'readable' and evidence['frame_index'] is None
    summary = evidence['summary_text']
    assert 'DAT 변위 요약=' in summary and '"max_displacement": 5.0' in summary
    assert '"displacement_node_count": 1' in summary and '"mesh_node_count": null' in summary
    assert '"length": "mm"' in summary and 'user declaration' in summary
    assert 'DAT 집합 TIP' in summary and '"value": 1.0' in summary
    assert '"minimum": 3.0, "maximum": 3.0, "finite_count": 1' in summary
    assert '"minimum": 4.0, "maximum": 4.0, "finite_count": 1' in summary
    assert '누락 범위는 미확인' in summary


def test_dat_displacement_summary_stays_separate_from_selected_frd_frame(tmp_path):
    run = make_run(tmp_path)
    dat = run.workdir / 'model.dat'
    dat.write_text(' displacements (vx,vy,vz) for set TIP and time 9.0\n\n 101 6.0 8.0 0.0\n\n', encoding='ascii')
    run.snapshot['artifacts']['model.dat'] = file_revision(dat)
    evidence = capture_run_evidence(run, tmp_path / 'assets', frame_index=0)
    summary = evidence['summary_text']
    assert 'FRD 프레임 요약={"max_displacement": 1.0' in summary
    assert 'DAT 변위 요약={"max_displacement": 10.0' in summary
    assert 'DAT 집합 TIP' in summary and '"value": 9.0' in summary
    assert evidence['frame_index'] == 0 and 'FRD와 합치지 않은 별도 파일' in summary


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


def large_input():
    return ('** original heading\r\n*NODE,NSET=ALL\r\n'
            + ''.join(f'{node}, {node}.0, 0, 0\r\n' for node in range(1, 4001))
            + '** mesh comment retained\r\n*ELEMENT,TYPE=S20,ELSET=BODY\r\n'
              '1,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,\r\n'
              ',16,17,18,19,20\r\n'
              '*NSET,NSET=SHORT,GENERATE\r\n1,4000,2\r\n'
              '*ELSET,ELSET=BODY\r\nOTHER_SET\r\n'
              '*SURFACE,NAME=ShortSide,TYPE=ELEMENT\r\nBODY,SNEG\r\n'
              '*MATERIAL,NAME=Steel\r\n*ELASTIC\r\n210000,0.3\r\n'
              '*PLASTIC\r\n240,0\r\n270,0.025\r\n'
              '*SURFACE INTERACTION,NAME=Contact\r\n*SURFACE BEHAVIOR\r\n1000000\r\n'
              '*STEP,NLGEOM\r\n*STATIC\r\n0.1,1\r\n'
              '*BOUNDARY,OP=NEW\r\nSHORT,1,3\r\n*CLOAD,AMPLITUDE=Ramp\r\nSHORT,3,9\r\n'
              '*NODE FILE\r\nU\r\n*ELEMENT OUTPUT\r\nS\r\n*END STEP\r\n'
              '*STEP\r\n*STATIC\r\n1,1\r\n*CLOAD,OP=MOD\r\nSHORT,3,-9\r\n*END STEP\r\n')


def test_large_inp_tail_conditions_keep_original_order_lines_comments_and_bytes(tmp_path):
    inp = tmp_path / 'large.inp'
    raw = large_input().encode('ascii')
    inp.write_bytes(raw)
    assets = tmp_path / 'assets'
    value = import_file_evidence(assets, files={'input': inp}, solver_id='calculix')
    text = value['model_text']
    assert len(text) <= 40000 and str(inp) in text
    assert '대량 목록 미제공: 원문 L3-L4002' in text
    assert '4000, 4000.0, 0, 0' not in text
    assert '** mesh comment retained\r\n' in text
    for block in ('*NSET,NSET=SHORT,GENERATE\r\n1,4000,2', '*ELSET,ELSET=BODY\r\nOTHER_SET',
                  ',16,17,18,19,20', '*PLASTIC\r\n240,0\r\n270,0.025',
                  '*SURFACE,NAME=ShortSide,TYPE=ELEMENT\r\nBODY,SNEG',
                  '*SURFACE BEHAVIOR\r\n1000000', '*NODE FILE\r\nU', '*ELEMENT OUTPUT\r\nS'):
        assert block in text
    assert text.index('*MATERIAL') < text.index('*SURFACE INTERACTION') < text.index('*STEP,NLGEOM')
    assert text.index('*CLOAD,AMPLITUDE=Ramp') < text.index('*CLOAD,OP=MOD')
    assert text.count('*END STEP') == 2
    assert 'membership' in text and 'side/normal' in text and '실행 덱이 아닙니다' in text
    assert inp.read_bytes() == raw
    assert (assets / value['files'][0]['stored_path']).read_bytes() == raw
    assert value['files'][0]['sha256'] == file_revision(inp)
    assert verify_evidence(value, assets) == []


@pytest.mark.parametrize('keyword', ['NODE', 'ELEMENT', 'NSET', 'ELSET', 'SURFACE'])
def test_only_large_exact_list_keyword_data_is_omitted(tmp_path, keyword):
    inp = tmp_path / 'lists.inp'
    inp.write_text(f'*{keyword},NAME=Long\n' + '123, S2\n' * 800
                   + '** comment after list\n*SURFACE INTERACTION,NAME=Contact\n'
                     '*SURFACE BEHAVIOR\n1000000\n*NODE PRINT,NSET=ALL\nU\n', encoding='ascii', newline='')
    text = import_file_evidence(tmp_path / 'assets', files={'input': inp}, solver_id='calculix')['model_text']
    assert '123, S2' not in text and f'*{keyword},NAME=Long' in text
    assert '** comment after list' in text
    assert '*SURFACE BEHAVIOR\n1000000' in text and '*NODE PRINT,NSET=ALL\nU' in text


def test_comments_do_not_turn_short_generated_or_referenced_sets_into_large_lists(tmp_path):
    inp = tmp_path / 'comments.inp'
    inp.write_text('*NSET,NSET=SHORT,GENERATE\n1,4000,2\n' + '** original comment\n' * 150
                   + '*ELSET,ELSET=BODY\nOTHER_SET\n', encoding='ascii', newline='')
    text = import_file_evidence(tmp_path / 'assets', files={'input': inp}, solver_id='calculix')['model_text']
    assert '*NSET,NSET=SHORT,GENERATE\n1,4000,2' in text and 'OTHER_SET' in text
    assert text.count('** original comment') == 150 and '대량 목록 미제공' not in text


def test_duplicate_original_execution_bodies_do_not_spend_budget_twice(tmp_path):
    run = make_run(tmp_path)
    raw = large_input().encode('ascii')
    source = Path(run.input_path)
    source.write_bytes(raw)
    run.snapshot.update(snapshot_input(source, run.workdir))
    assets = tmp_path / 'assets'
    value = capture_run_evidence(run, assets)
    text = value['model_text']
    assert text.count('*MATERIAL,NAME=Steel') == 1
    assert 'input:' in text and 'execution_input:' in text and '동일 bytes' in text
    assert len([item for item in value['files'] if item['role'] in {'input', 'execution_input'}]) == 2
    assert value['input_manifest'] == run.snapshot and verify_evidence(value, assets) == []


def test_include_files_keep_call_positions_and_later_conditions_with_many_small_files(tmp_path):
    inp = tmp_path / 'main.inp'
    children = []
    for index in range(35):
        child = tmp_path / f'part{index}.inc'
        child.write_text(f'** include {index}\n*MATERIAL,NAME=M{index}\n*ELASTIC\n{100 + index},0.3\n', encoding='ascii', newline='')
        children.append(child)
    inp.write_text(large_input() + ''.join(f'*INCLUDE,INPUT={child.name}\n' for child in children), encoding='ascii', newline='')
    assets = tmp_path / 'assets'
    value = import_file_evidence(assets, files={'input': inp}, solver_id='calculix')
    text = value['model_text']
    assert len(text) <= 40000
    assert '*INCLUDE,INPUT=part0.inc' in text and '*INCLUDE,INPUT=part34.inc' in text
    assert '*MATERIAL,NAME=Steel' in text and '*MATERIAL,NAME=M34\n' in text
    assert str(children[-1]) in text
    assert len(value['input_manifest']['includes']) == 35
    assert verify_evidence(value, assets) == []


def test_budget_overflow_marks_missing_bodies_and_never_cuts_a_table_as_complete(tmp_path):
    inp = tmp_path / 'tables.inp'
    inp.write_text('*MATERIAL,NAME=LongTable\n*PLASTIC\n' + '100,0.1\n' * 10000
                   + '*STEP\n*STATIC\n0.1,1\n*CLOAD\nTIP,3,1\n*END STEP\n', encoding='ascii', newline='')
    value = import_file_evidence(tmp_path / 'assets', files={'input': inp}, solver_id='calculix')
    text = value['model_text']
    assert len(text) <= 40000 and '*PLASTIC' in text
    assert '원문 L3-L10002 본문 미제공' in text and '100,0.1' not in text
    assert '*CLOAD\nTIP,3,1' in text and '*STEP' in text
    assert '모델 완전성을 판정하지 마세요' in text


def test_too_many_blocks_and_files_report_unprovided_ranges_without_overflow(tmp_path):
    from caelab.native.evidence import _model_text
    inp = tmp_path / 'blocks.inp'
    inp.write_text('*STEP\n*STATIC\n0.1,1\n*END STEP\n' * 3000, encoding='ascii', newline='')
    value = import_file_evidence(tmp_path / 'assets', files={'input': inp}, solver_id='calculix')
    text = value['model_text']
    assert len(text) <= 40000 and '블록 전체 미제공' in text and '-L12000' in text
    # A pathological file index cannot fit all paths. The omission remains explicit.
    items = [dict(value['files'][0], sha256=str(index), source_path='x' * 500) for index in range(200)]
    text = _model_text(tmp_path / 'assets', items)
    assert len(text) <= 40000 and '미제공 파일' in text and 'files[' in text


def test_small_and_non_inp_direct_inputs_are_not_reinterpreted(tmp_path):
    inp = tmp_path / 'small.inp'
    raw = b'** original\r\n*NODE\r\n1,0,0,0\r\n*MATERIAL,NAME=X\r\n*ELASTIC\r\n1,.3\r\n'
    inp.write_bytes(raw)
    value = import_file_evidence(tmp_path / 'a', files={'input': inp}, solver_id='calculix')
    assert '*NODE\r\n1,0,0,0\r\n' in value['model_text'] and '미제공:' not in value['model_text']
    other = tmp_path / 'native.txt'
    other.write_text('*NODE\n' + '1,2,3,4\n' * 500, encoding='ascii', newline='')
    value = import_file_evidence(tmp_path / 'b', files={'input': other}, solver_id='calculix')
    assert value['model_text'].count('1,2,3,4') == 500
    value = import_file_evidence(tmp_path / 'c', files={'input': other}, solver_id='other_solver')
    assert value['model_text'].count('1,2,3,4') == 500
