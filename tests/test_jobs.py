import pytest

from caelab.jobs import JobManager
from caelab.model.schema import Interaction
from caelab.solvers.base import ResultSet
from caelab.solvers.calculix import CalculiXConverter
from conftest import cantilever_model


@pytest.mark.parametrize('invalid', ['reference', 'contact', 'modal'])
def test_preflight_never_writes_or_runs_invalid_model(tmp_path, monkeypatch, invalid):
    model = cantilever_model()
    if invalid == 'reference':
        model.steps[0].loads.append('MISSING')
    elif invalid == 'contact':
        model.interactions.append(Interaction('CONTACT', 'contact', ['FIX', 'TIP']))
    else:
        model.steps[0].kind = 'modal'
    converter = CalculiXConverter()
    monkeypatch.setattr(converter, 'write_input', lambda *args: pytest.fail('deck write called'))
    monkeypatch.setattr(converter, 'run', lambda *args: pytest.fail('process called'))
    job = JobManager().submit(converter, model, tmp_path)
    assert job.state == 'failed'
    assert 'running' not in job.history
    assert job.result.validation_issues
    assert '[' in job.result.validation_issues[0]
    assert job.result.max_displacement is None
    assert (tmp_path / 'job.log').read_text(encoding='utf-8')


@pytest.mark.parametrize('stage', ['validate', 'write_input', 'run'])
def test_any_converter_exception_terminates_and_persists_reason(tmp_path, monkeypatch, stage):
    converter = CalculiXConverter()
    def fail(*args):
        raise RuntimeError('failure in ' + stage)
    monkeypatch.setattr(converter, stage, fail)
    job = JobManager().submit(converter, cantilever_model(), tmp_path)
    assert job.state == 'failed'
    assert 'RuntimeError: failure in ' + stage in job.result.log_tail
    assert 'Traceback' in (tmp_path / 'job.log').read_text(encoding='utf-8')


def test_nonexistent_executable_fails_job_and_persists_log(tmp_path, monkeypatch):
    monkeypatch.setenv('CAELAB_CCX', str(tmp_path / 'missing-ccx'))
    job = JobManager().submit(CalculiXConverter(), cantilever_model(), tmp_path)
    assert job.state == 'failed'
    assert job.history[-2:] == ['running', 'failed']
    assert 'FileNotFoundError' in job.result.log_tail
    assert job.result.log_path


def test_success_remains_success_and_unknown_values_remain_unknown(tmp_path):
    class Converter:
        id = 'test'
        def validate(self, model): return []
        def write_input(self, model, workdir): return workdir / 'job.inp'
        def run(self, model, deck, workdir): return ResultSet(self.id, True)
    job = JobManager().submit(Converter(), cantilever_model(), tmp_path)
    assert job.state == 'succeeded'
    assert job.result.max_displacement is None
    assert '미확인' in job.result.summary()
    assert job.result.log_path


def test_gui_launch_probe_receives_exact_arguments_cwd_and_environment(tmp_path):
    import json
    import os
    import sys
    from caelab.connections import LaunchRequest
    folder = tmp_path / "한글 작업 공백"
    folder.mkdir()
    output = folder / "probe.json"
    native_file = folder / "선택 파일.geo"
    native_file.write_text("input")
    script = (
        "import json,os,sys; from pathlib import Path; "
        "Path('probe.json').write_text(json.dumps({'cwd':os.getcwd(), 'args':sys.argv[1:], "
        "'value':os.environ['CAELAB_PROBE_VALUE']}),encoding='utf-8')"
    )
    manager = JobManager()
    request = LaunchRequest([sys.executable, "-c", script, str(native_file)], folder,
                            {**os.environ, "CAELAB_PROBE_VALUE": "명시 환경"}, "probe")
    record = manager.launch(request)
    try:
        assert record.state == "opened" and record.pid is not None
        assert manager.launches[record.id] is record
        assert manager.jobs == {}
        assert record.process.wait(timeout=15) == 0
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data == {"cwd": str(folder), "args": [str(native_file)], "value": "명시 환경"}
        assert record.state == "opened"  # process exit is not a calculated result
    finally:
        if record.process is not None and record.process.poll() is None:
            record.process.terminate()
            record.process.wait(timeout=5)


def test_gui_process_start_failure_is_kept_as_failed_record(tmp_path):
    from caelab.connections import LaunchRequest
    manager = JobManager()
    record = manager.launch(LaunchRequest([str(tmp_path / "missing")], tmp_path, {}, "probe"))
    assert record.state == "failed"
    assert record.process is None and record.pid is None
    assert "FileNotFoundError" in record.error
    assert manager.launches[record.id] is record
