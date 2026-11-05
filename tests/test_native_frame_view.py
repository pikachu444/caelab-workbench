"""Selected native frame bytes retain the recorded run's own geometry and fields."""
import hashlib

import pytest

from caelab.native.evidence import native_results_file
from test_native_evidence import make_run


def test_selected_frame_uses_that_frame_instead_of_pvd(tmp_path):
    run = make_run(tmp_path)
    assert native_results_file(run).suffix == '.pvd'
    assert native_results_file(run, frame_index=0) == run.workdir / 'results/frame-000000.vtu'
    assert native_results_file(run, frame_index=1) == run.workdir / 'results/frame-000001.vtu'


@pytest.mark.parametrize('index', [-1, 2, True, '0', 0.0])
def test_invalid_frame_never_falls_back_to_another_frame(tmp_path, index):
    run = make_run(tmp_path)
    with pytest.raises(ValueError):
        native_results_file(run, frame_index=index)


@pytest.mark.parametrize('damage', ['missing', 'tamper', 'unrecorded', 'escape', 'duplicate'])
def test_selected_frame_integrity_fails_closed(tmp_path, damage):
    run = make_run(tmp_path)
    relative = run.result['frames'][0]['vtu_file']
    if damage == 'missing':
        (run.workdir / relative).unlink()
    elif damage == 'tamper':
        (run.workdir / relative).write_bytes(b'changed result')
    elif damage == 'unrecorded':
        run.result['exchange_files'].remove(relative)
    elif damage == 'escape':
        run.result['frames'][0]['vtu_file'] = '../../other-run/frame.vtu'
    else:
        run.result['frames'].append(dict(run.result['frames'][0]))
    with pytest.raises(ValueError):
        native_results_file(run, frame_index=0)


def test_read_frame_keeps_metadata_and_consumed_bytes_in_one_snapshot(tmp_path):
    from caelab.native.frame_view import read_native_frame
    run = make_run(tmp_path)
    run.stale, run.stale_reason = True, 'current input differs'
    data = read_native_frame(run, 0)
    assert data.run_id == run.id and data.frame['index'] == 0
    assert data.payload == (run.workdir / 'results/frame-000000.vtu').read_bytes()
    assert hashlib.sha256(data.payload).hexdigest() == data.sha256
    assert data.units == run.units and data.unit_source == run.unit_source
    assert data.stale and data.stale_reason == 'current input differs'
    run.units['length'] = 'm'
    run.result['frames'][0]['fields'].clear()
    assert data.units['length'] == 'mm' and data.frame['fields']


def test_bytes_changed_between_path_check_and_consumption_are_rejected(tmp_path, monkeypatch):
    from caelab.native import frame_view
    run = make_run(tmp_path)
    original = frame_view.native_results_file
    def changed_after_check(*args, **kwargs):
        path = original(*args, **kwargs)
        path.write_bytes(b'same path, different bytes')
        return path
    monkeypatch.setattr(frame_view, 'native_results_file', changed_after_check)
    with pytest.raises(ValueError, match='해시'):
        frame_view.read_native_frame(run, 0)
