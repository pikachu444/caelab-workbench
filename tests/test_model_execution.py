"""Auxiliary model transport through the native owner, without executing a solver."""

import copy
import hashlib
import importlib
import json
from pathlib import Path

import pytest

from caelab.native.run_store import NativeRun
from caelab.project import file_revision
from caelab.solvers.base import ResultSet
from caelab.solvers.calculix import CalculiXConverter
from conftest import cantilever_model


def implementation():
    return importlib.import_module('caelab.model_execution')


def completed(tmp_path, monkeypatch, outcome=None):
    module = implementation()
    model = cantilever_model()
    converter = CalculiXConverter()
    calls = []
    def read(snapshot, deck, workdir, *, check_cancelled=None):
        assert deck == workdir / 'model.inp'
        calls.append((snapshot, deck))
        if check_cancelled:
            check_cancelled()
        return outcome if outcome is not None else ResultSet('calculix', True, displacements={1: [0, -.002, 0]},
                         reactions={1: [0, 10, 0]}, nodal_forces={1: [0, 10, 0]},
                         max_displacement=.002, reaction_totals={'fy': 10},
                         units={'length': 'm', 'force': 'N'}, log_tail='saved log')
    monkeypatch.setattr(converter, 'read_result', read)
    monkeypatch.setattr(converter, 'run', lambda *a: pytest.fail('adapter executed converter'))
    adapter = module.ModelRunAdapter(model, converter)
    workdir = tmp_path / 'run'
    workdir.mkdir()
    initial = adapter.persist_input(workdir)
    snapshot = adapter.snapshot_input('model.json', workdir, lambda: None)
    (workdir / 'job.log').write_bytes(b'raw log')
    (workdir / 'model.frd').write_bytes(b'raw output')
    result = adapter.collect_results(workdir, check_cancelled=lambda: None)
    artifacts = {path.relative_to(workdir).as_posix(): file_revision(path)
                 for path in adapter.artifact_paths(workdir, snapshot)}
    run = NativeRun('a' * 32, module.MODEL_NODE_ID, workdir, state='completed',
                    parse_state=result['status'], returncode=0, finished_at='finished',
                    log_path=str(workdir / 'job.log'), result=result,
                    snapshot={**snapshot, **initial, 'artifacts': artifacts})
    return module, adapter, run, model, calls


def test_model_revision_includes_raw_unit_declaration():
    module = implementation()
    model = cantilever_model()
    initial = module.model_revision(model)
    model.unit_system = 'mm-tonne-s'
    assert module.model_revision(model) != initial
    model.unit_system = None
    assert module.model_revision(model) != initial


def test_model_revision_and_saved_snapshot_accept_mixed_distinct_dof_keys(tmp_path):
    module = implementation()
    model = cantilever_model()
    model.bcs[0].values = {1: 0.0, '2': 0.0}
    revision = module.model_revision(model)
    module.ModelRunAdapter(model, CalculiXConverter()).persist_input(tmp_path)
    saved = json.loads((tmp_path / 'model-snapshot.json').read_text(encoding='utf-8'))
    restored = type(model).from_dict(saved)
    assert module.model_revision(restored) == revision
    assert not CalculiXConverter().validate(restored)


@pytest.mark.parametrize('name', ['model.inp', 'model.frd', 'job.log'])
@pytest.mark.parametrize('change', ['mutate', 'delete'])
def test_changed_execution_artifacts_refuse_successful_saved_result(tmp_path, monkeypatch, name, change):
    module, _, run, _, _ = completed(tmp_path, monkeypatch)
    path = run.workdir / name
    if change == 'mutate':
        path.write_bytes(path.read_bytes() + b'\n')
    else:
        path.unlink()
    with pytest.raises((OSError, ValueError), match=name.replace('.', r'\.')):
        module.read_model_result(run)


def test_adapter_detaches_nested_model_before_worker_persistence(tmp_path):
    module = implementation()
    model = cantilever_model()
    expected = copy.deepcopy(model)
    adapter = module.ModelRunAdapter(model, CalculiXConverter())
    model.unit_system = 'other'
    next(iter(model.mesh.nodes.values()))[0] = 98765
    initial = adapter.persist_input(tmp_path)
    raw = json.loads((tmp_path / 'model-snapshot.json').read_text(encoding='utf-8'))
    assert raw['unit_system'] == 'si'
    assert initial['auxiliary_model']['revision'] == module.model_revision(expected)
    assert initial['auxiliary_model']['sha256'] == file_revision(tmp_path / 'model-snapshot.json')
    assert 98765 not in next(iter(raw['mesh']['nodes'].values()))


def test_result_transport_uses_execution_basename_and_restores_integer_keys(tmp_path, monkeypatch):
    module, _, run, model, calls = completed(tmp_path, monkeypatch)
    restored = module.read_model_result(run)
    assert len(calls) == 1 and calls[0][1].name == 'model.inp'
    assert run.result['status'] == 'readable' and run.result['frames'] == []
    assert restored.model.to_dict() == model.to_dict()
    assert restored.result.displacements == {1: [0, -.002, 0]}
    assert restored.result.reactions == {1: [0, 10, 0]}
    assert restored.result.nodal_forces == {1: [0, 10, 0]}
    assert restored.result.units == {'length': 'm', 'force': 'N'}
    assert restored.result.log_path == str(run.workdir / 'job.log')
    raw = json.loads((run.workdir / 'model-result.json').read_text(encoding='utf-8'))
    assert raw['log_path'] == 'job.log'


@pytest.mark.parametrize('name', ['model-snapshot.json', 'model-result.json'])
@pytest.mark.parametrize('change', ['mutate', 'delete'])
def test_missing_or_changed_owned_json_never_falls_back_to_current_or_empty(tmp_path, monkeypatch, name, change):
    module, _, run, _, _ = completed(tmp_path, monkeypatch)
    path = run.workdir / name
    if change == 'mutate':
        path.write_bytes(path.read_bytes() + b'\n')
    else:
        path.unlink()
    with pytest.raises((OSError, ValueError), match=name.replace('.', r'\.')):
        module.read_model_result(run)


@pytest.mark.parametrize('absolute', [False, True])
def test_model_snapshot_reference_must_be_owned_relative_path(tmp_path, monkeypatch, absolute):
    module, _, run, _, _ = completed(tmp_path, monkeypatch)
    metadata = run.snapshot['auxiliary_model']
    if absolute:
        metadata['path'] = str(run.workdir / 'model-snapshot.json')
    else:
        outside = tmp_path / 'outside.json'
        outside.write_bytes((run.workdir / 'model-snapshot.json').read_bytes())
        metadata['path'] = '../outside.json'
    run.snapshot['artifacts'][metadata['path']] = metadata['sha256']
    with pytest.raises(ValueError, match='소유|상대|밖'):
        module.read_model_result(run)


def test_cancelled_preparse_run_can_restore_frozen_model_without_inventing_result(tmp_path, monkeypatch):
    module, _, run, model, _ = completed(tmp_path, monkeypatch)
    run.state, run.parse_state, run.returncode = 'cancelled', 'unparsed', None
    run.result = {}
    run.snapshot['artifacts'].pop('model-result.json')
    (run.workdir / 'model-result.json').unlink()
    restored = module.read_model_result(run)
    assert restored.model.to_dict() == model.to_dict() and restored.result is None
    run.parse_state = 'readable'
    with pytest.raises(ValueError):
        module.read_model_result(run)


def test_input_validation_precedes_writer_but_keeps_failed_model_snapshot(tmp_path, monkeypatch):
    module = implementation()
    model = cantilever_model()
    model.sections[0].material = 'MISSING'
    converter = CalculiXConverter()
    monkeypatch.setattr(converter, 'write_input', lambda *a: pytest.fail('invalid model writer'))
    adapter = module.ModelRunAdapter(model, converter)
    initial = adapter.persist_input(tmp_path)
    with pytest.raises(ValueError, match='sections'):
        adapter.snapshot_input('model.json', tmp_path, lambda: None)
    assert initial['auxiliary_model']['sha256'] == file_revision(tmp_path / 'model-snapshot.json')
    assert not (tmp_path / '.converted').exists()


def test_collector_propagates_cancellation_before_publishing_result(tmp_path, monkeypatch):
    module = implementation()
    converter = CalculiXConverter()
    reading = []
    def read(model, deck, folder, *, check_cancelled):
        reading.append(True)
        check_cancelled()
        pytest.fail('cancel callback was swallowed')
    monkeypatch.setattr(converter, 'read_result', read)
    adapter = module.ModelRunAdapter(cantilever_model(), converter)
    adapter.snapshot_input('model.json', tmp_path, lambda: None)
    def cancel():
        if reading:
            raise InterruptedError('reading cancelled')
    with pytest.raises(InterruptedError, match='reading cancelled'):
        adapter.collect_results(tmp_path, check_cancelled=cancel)
    assert not (tmp_path / 'model-result.json').exists()
    assert reading == [True]


def test_failed_reader_unknown_quantities_stay_unknown_after_storage(tmp_path, monkeypatch):
    outcome = ResultSet('calculix', False, validation_issues=['outputs.U: missing'])
    module, _, run, _, _ = completed(tmp_path, monkeypatch, outcome)
    assert run.result['status'] == 'failed'
    restored = module.read_model_result(run).result
    assert restored.success is False and restored.max_displacement is None
    assert restored.reaction_totals == {} and restored.units == {}


def test_non_integer_result_node_key_is_not_silently_coerced(tmp_path, monkeypatch):
    module, _, run, _, _ = completed(tmp_path, monkeypatch)
    path = run.workdir / 'model-result.json'
    raw = json.loads(path.read_text(encoding='utf-8'))
    raw['displacements'] = {'1.5': [0, 0, 0]}
    path.write_text(json.dumps(raw), encoding='utf-8')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    run.result['auxiliary_result']['sha256'] = digest
    run.snapshot['artifacts'][path.name] = digest
    with pytest.raises(ValueError, match='절점'):
        module.read_model_result(run)
