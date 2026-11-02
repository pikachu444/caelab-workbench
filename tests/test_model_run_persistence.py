"""Auxiliary model storage keeps missing history distinct from a new project."""

import json

import pytest

from caelab.model import store
from caelab.native.run_store import RunStore
from conftest import cantilever_model


def _existing_project(folder):
    model = cantilever_model()
    (folder / 'runs').mkdir()
    path = folder / 'model.json'
    path.write_text(json.dumps({**model.to_dict(), 'run_store_expected': True}), encoding='utf-8')
    return model, path


def test_explicit_save_declares_existing_run_store_without_changing_model_schema(tmp_path):
    model = cantilever_model()
    path = store.save_project(model, tmp_path)
    assert json.loads(path.read_text(encoding='utf-8'))['run_store_expected'] is True
    assert (tmp_path / 'runs').is_dir()
    assert 'run_store_expected' not in model.to_dict()
    assert store.load_project(tmp_path).to_dict() == model.to_dict()


def test_legacy_open_does_not_create_history_or_marker(tmp_path):
    model = cantilever_model()
    path = tmp_path / 'model.json'
    path.write_text(json.dumps(model.to_dict()), encoding='utf-8')
    before = path.read_bytes()
    assert store.load_project(tmp_path).to_dict() == model.to_dict()
    assert path.read_bytes() == before and not (tmp_path / 'runs').exists()
    store.save_project(model, tmp_path)
    assert json.loads(path.read_text(encoding='utf-8'))['run_store_expected'] is True


def test_deleted_expected_store_is_not_recreated_by_open_or_save(tmp_path):
    model, path = _existing_project(tmp_path)
    before = path.read_bytes()
    (tmp_path / 'runs/controller.lock').unlink(missing_ok=True)
    (tmp_path / 'runs').rmdir()
    for operation in (lambda: store.load_project(tmp_path), lambda: store.save_project(model, tmp_path)):
        with pytest.raises(ValueError, match='runs'):
            operation()
        assert path.read_bytes() == before and not (tmp_path / 'runs').exists()


@pytest.mark.parametrize('marker', [None, 1, 'true', [], {}])
def test_malformed_marker_does_not_allow_new_history_or_overwrite(tmp_path, marker):
    model = cantilever_model()
    path = tmp_path / 'model.json'
    path.write_text(json.dumps({**model.to_dict(), 'run_store_expected': marker}), encoding='utf-8')
    before = path.read_bytes()
    for operation in (lambda: store.load_project(tmp_path), lambda: store.save_project(model, tmp_path)):
        with pytest.raises(ValueError, match='run_store_expected'):
            operation()
        assert path.read_bytes() == before and not (tmp_path / 'runs').exists()


def test_atomic_save_failure_keeps_previous_model_and_history(tmp_path, monkeypatch):
    model, path = _existing_project(tmp_path)
    previous = path.read_bytes()
    history = tmp_path / 'runs/retained.txt'
    history.write_bytes(b'previous history')
    model.name = 'changed'
    def fail(*args, **kwargs):
        raise OSError('atomic commit failed')
    monkeypatch.setattr(store, 'atomic_json', fail, raising=False)
    with pytest.raises(OSError, match='atomic commit failed'):
        store.save_project(model, tmp_path)
    assert path.read_bytes() == previous and history.read_bytes() == b'previous history'


def test_active_run_lease_refuses_another_model_save(tmp_path):
    model, path = _existing_project(tmp_path)
    previous = path.read_bytes()
    lease = RunStore(tmp_path / 'runs', expected=True).lease()
    try:
        model.name = 'other window'
        with pytest.raises(ValueError):
            store.save_project(model, tmp_path)
        assert path.read_bytes() == previous
    finally:
        lease.release()


def test_conflicting_dof_aliases_are_not_silently_saved_as_a_valid_boundary(tmp_path):
    model, path = _existing_project(tmp_path)
    before = path.read_bytes()
    model.bcs[0].values = {1: 1.0, '1': 0.0}
    with pytest.raises(ValueError, match=r'bcs\[0\]\.values'):
        store.save_project(model, tmp_path)
    assert path.read_bytes() == before
    assert model.bcs[0].values == {1: 1.0, '1': 0.0}


def test_equal_dof_aliases_have_one_deterministic_persisted_key(tmp_path):
    model = cantilever_model()
    model.bcs[0].values = {1: 0.0, '1': 0.0, '2': 0.0}
    store.save_project(model, tmp_path)
    assert store.load_project(tmp_path).bcs[0].values == {'1': 0.0, '2': 0.0}
    assert model.bcs[0].values == {1: 0.0, '1': 0.0, '2': 0.0}


def test_revision_is_rechecked_after_obtaining_the_store_lease(tmp_path, monkeypatch):
    model, path = _existing_project(tmp_path)
    original_revision = store.model_revision(model)
    other = cantilever_model()
    other.name = 'changed before lease acquisition'
    lease = RunStore.lease
    def intervening_save(owner):
        store.atomic_json(path, {**other.to_dict(), 'run_store_expected': True})
        return lease(owner)
    monkeypatch.setattr(RunStore, 'lease', intervening_save)
    with pytest.raises(ValueError, match='다른 창'):
        store.save_project(model, tmp_path, expected_revision=original_revision)
    assert store.load_project(tmp_path).name == other.name
