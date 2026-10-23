"""Selected-run delivery uses real files, never a blank result application."""

import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest

from caelab.connections import PROGRAMS, ProgramStatus
from caelab.native.evidence import native_results_file
from caelab.native.run_store import RunStore
from caelab.project import file_revision
from caelab.workbench import Workbench
from test_native_evidence import make_run


def selected_project(tmp_path):
    run = make_run(tmp_path)
    wb = Workbench()
    project = wb.new_native_project()
    analysis = wb.add_program_node(project, 'analysis', 'calculix')
    results = wb.add_program_node(project, 'results', 'paraview')
    expert = wb.add_program_node(project, 'discussion', 'expert')
    wb.set_node_file(project, analysis.id, run.input_path, units=run.units, unit_source=run.unit_source)
    wb.connect_nodes(project, analysis.id, results.id)
    wb.connect_nodes(project, results.id, expert.id, source_role='input')
    wb.save_native_project(project, tmp_path / 'project.json')
    run.node_id = analysis.id
    store = RunStore(project.run_store)
    store.write(run)
    store.select(analysis.id, run.id)
    return wb, project, analysis, results, expert, run


def test_same_selected_run_reaches_paraview_and_expert_through_real_connections(tmp_path, monkeypatch):
    wb, project, analysis, results, expert, run = selected_project(tmp_path)
    spec = next(spec for spec in PROGRAMS if spec.id == 'paraview')
    monkeypatch.setattr(wb, 'programs', lambda: [ProgramStatus(spec, sys.executable)])
    requests = []
    monkeypatch.setattr(wb.jobs, 'launch', lambda request: requests.append(request) or SimpleNamespace(state='opened'))
    assert wb.resolve_evidence_run(project, results.id).id == run.id
    assert wb.resolve_evidence_run(project, expert.id).id == run.id
    wb.open_native_results(project, results.id)
    evidence = wb.capture_native_evidence(project, expert.id, project.run_store / 'discussions/assets', frame_index=0)
    assert evidence['run_id'] == run.id and evidence['frame_index'] == 0
    assert requests[0].argv == [sys.executable, '--data=' + str(run.workdir / 'results/results.pvd')]
    assert requests[0].cwd == run.workdir / 'results'
    # Opening all frames must not relabel the expert's explicit frame-zero summary.
    assert '"max_displacement": 1.0' in evidence['summary_text']
    assert '"max_displacement": 5.0' not in evidence['summary_text']
    assert not wb.jobs.jobs and not wb.jobs._native


@pytest.mark.parametrize('failure', ['missing', 'tamper', 'escape', 'other_run', 'unrecorded', 'empty'])
def test_invalid_exchange_never_launches_blank_or_wrong_result(tmp_path, monkeypatch, failure):
    wb, project, _, results, _, run = selected_project(tmp_path)
    pvd = run.workdir / 'results/results.pvd'
    vtu = run.workdir / 'results/frame-000001.vtu'
    if failure == 'missing':
        vtu.unlink()
    elif failure == 'tamper':
        vtu.write_bytes(b'changed')
    elif failure in {'escape', 'other_run'}:
        text = pvd.read_text()
        text = text.replace('frame-000001.vtu', '../../other-run/results/frame-000001.vtu')
        pvd.write_text(text)
        run.snapshot['artifacts']['results/results.pvd'] = file_revision(pvd)
    elif failure == 'unrecorded':
        run.snapshot.pop('artifacts')
    else:
        run.result['exchange_files'] = []
    RunStore(project.run_store).write(run)
    calls = []
    monkeypatch.setattr(wb.jobs, 'launch', lambda request: calls.append(request))
    with pytest.raises(ValueError):
        wb.open_native_results(project, results.id)
    assert not calls


def test_result_selection_switch_does_not_deliver_prior_run(tmp_path):
    wb, project, analysis, results, expert, first = selected_project(tmp_path)
    second_path = tmp_path / 'second'
    second_path.mkdir()
    second = make_run(second_path)
    second.node_id = analysis.id
    # Different saved input is deliberately selected as a historical run.
    RunStore(project.run_store).write(second)
    # Move its full owned run folder into this project's actual run store.
    import shutil
    new_dir = project.run_store / second.id
    shutil.copytree(second.workdir, new_dir)
    second.workdir, second.log_path = new_dir, str(new_dir / 'job.log')
    RunStore(project.run_store).write(second)
    wb.select_native_run(project, analysis.id, second.id)
    evidence = wb.capture_native_evidence(project, expert.id, tmp_path / 'assets')
    assert evidence['run_id'] == second.id != first.id
    assert wb.resolve_evidence_run(project, results.id).id == second.id
    assert wb.resolve_evidence_run(project, results.id).stale


def test_save_as_copies_discussion_assets_without_replacing_external_source(tmp_path):
    wb, project, _, _, expert, run = selected_project(tmp_path)
    original_store = project.run_store
    assets = original_store / 'discussions/assets'
    evidence = wb.capture_native_evidence(project, expert.id, assets)
    (original_store / 'discussions/document.json').write_text(json.dumps({'evidence': evidence}), encoding='utf-8')
    wb.save_native_project(project, tmp_path / 'copy.json')
    assert project.run_store != original_store
    copied = project.run_store / 'discussions/assets'
    from caelab.native.evidence import verify_evidence
    assert verify_evidence(evidence, copied) == []
    assert (project.run_store / 'discussions/document.json').is_file()
    assert Path(run.input_path).is_file()
    item = evidence['files'][0]
    (copied / item['stored_path']).write_bytes(b'new project asset changed')
    assert verify_evidence(evidence, assets) == []


def test_manual_facade_needs_no_project_and_opens_no_solver(tmp_path, monkeypatch):
    wb = Workbench()
    source = tmp_path / 'failure.log'
    source.write_bytes(b'failed to converge')
    monkeypatch.setattr(wb.jobs, 'launch', lambda *_: pytest.fail('automatic external process'))
    evidence = wb.import_expert_files(tmp_path / 'assets', files={'log': source}, solver_id='calculix')
    assert evidence['run_id'] is None and 'failed to converge' in evidence['log_text']
    assert not wb.jobs.jobs and not wb.jobs._native


def test_actual_probe_receives_data_argument_cwd_and_environment(tmp_path, monkeypatch):
    """Actual small executable probe, not an installed ParaView GUI."""
    if os.name == 'nt':
        pytest.skip('POSIX executable probe; Windows argument construction is tested separately')
    wb, project, _, results, _, run = selected_project(tmp_path)
    receipt = tmp_path / 'launch.json'
    probe = tmp_path / 'paraview probe'
    probe.write_text(f'#!{sys.executable}\nimport json,os,sys\nfrom pathlib import Path\n'
                     f'Path({str(receipt)!r}).write_text(json.dumps([sys.argv[1:],os.getcwd(),os.environ["DELIVERY_PROBE"]]))\n')
    probe.chmod(0o755)
    spec = next(spec for spec in PROGRAMS if spec.id == 'paraview')
    monkeypatch.setattr(wb, 'programs', lambda: [ProgramStatus(spec, str(probe), runtime_env={'DELIVERY_PROBE': 'registered'})])
    monkeypatch.setenv('DELIVERY_PROBE', 'explicit')
    record = wb.open_native_results(project, results.id)
    assert record.state == 'opened'
    assert record.process.wait(timeout=5) == 0
    assert json.loads(receipt.read_text()) == [['--data=' + str(run.workdir / 'results/results.pvd')],
                                             str(run.workdir / 'results'), 'explicit']


def test_actual_ccx_records_final_hashes_and_delivers_the_same_run(tmp_path, monkeypatch):
    """Actual CalculiX, with no ParaView/LLM invocation or auxiliary FE conversion."""
    import shutil
    from test_native_results import _affine_deck
    executable = os.environ.get('CAELAB_CCX') or shutil.which('ccx')
    if not executable:
        pytest.skip('Actual CalculiX executable not configured/installed')
    monkeypatch.setenv('CAELAB_CCX', executable)
    wb = Workbench()
    project = wb.new_native_project()
    node = wb.add_program_node(project, 'analysis', 'calculix')
    source = tmp_path / 'native model.inp'
    deck, _ = _affine_deck('C3D4', False, True)
    source.write_text(deck, encoding='ascii')
    wb.set_node_file(project, node.id, source, units={'length': 'user unit'}, unit_source='user declaration')
    wb.save_native_project(project, tmp_path / 'project.json')
    submitted = wb.submit_native(project, node.id, timeout=30)
    deadline = time.monotonic() + 35
    try:
        while time.monotonic() < deadline:
            run = wb.selected_native_run(project, node.id)
            if run.finished_at:
                break
            time.sleep(.02)
        assert run.finished_at and run.state == 'completed', run.error
        assert run.parse_state == 'readable'
        artifacts = run.snapshot['artifacts']
        assert {'model.frd', 'model.dat', 'job.log', 'results/results.pvd',
                'results/frame-000000.vtu', 'results/frame-000001.vtu'} <= artifacts.keys()
        assert all(file_revision(run.workdir / name) == value for name, value in artifacts.items())
        assert native_results_file(run) == run.workdir / 'results/results.pvd'
        evidence = wb.capture_native_evidence(project, node.id, tmp_path / 'evidence', frame_index=1)
        assert evidence['run_id'] == submitted.id and evidence['frame_index'] == 1
        assert evidence['units'] == {'length': 'user unit'}
        assert all(item['verification'] == 'recorded_match' for item in evidence['files'])
    finally:
        assert wb.close()
