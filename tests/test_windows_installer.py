"""Installer preparation contracts; no installer, compiler or application runs."""

import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from scripts import build_windows_installer as installer
from test_windows_bundle_archive import candidate  # reuse the established built-candidate fixture


def read_report(output):
    return json.loads((output / 'installer-manifest.json').read_text(encoding='utf-8'))


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def no_output(output):
    assert not output.exists()
    assert not list(output.parent.glob('.' + output.name + '-*'))


def fake_compiler(tmp_path, monkeypatch, compile_body=None):
    compiler = tmp_path / 'compiler fixture.exe'
    compiler.write_bytes(b'compiler fixture, never executed')
    monkeypatch.setattr(installer, '_compiler_info', lambda path: {
        'name': path.name, 'version': '6.0.0.0', 'sha256': installer.archive._sha(path)})

    def compile_fixture(path, script, timeout):
        target = script.parent / 'compiler-output' / installer.INSTALLER
        target.parent.mkdir()
        target.write_bytes(b'MZ compiler-result fixture; not executable')
        if compile_body:
            compile_body(script)

    monkeypatch.setattr(installer, '_run_compiler', compile_fixture)
    return compiler


def test_prepare_preserves_bundle_and_user_data_and_emits_pinned_snapshot(candidate, tmp_path):
    manifest, bundle, _, _ = candidate
    original, raw = snapshot(bundle), manifest.read_bytes()
    project = tmp_path / 'user project.json'
    settings = tmp_path / 'user settings.ini'
    project.write_bytes(b'project must stay')
    settings.write_bytes(b'preferences must stay')
    output = installer.build_installer(manifest, tmp_path / 'prepared', prepare_only=True)
    report = read_report(output)
    assert report['status'] == 'prepared_not_compiled'
    assert report['compiler'] is None and report['installer'] is None
    assert report['compile_exit_code'] is None
    assert report['input_build_manifest_sha256'] == hashlib.sha256(raw).hexdigest()
    assert report['bundle_source_revision'] == 'a' * 40
    assert report['baseline_revision'] == 'b' * 40
    assert report['windows_install_uninstall_upgrade_validation'] == 'not_performed'
    assert not (output / installer.INSTALLER).exists()
    assert 'private-person' not in (output / 'installer-manifest.json').read_text(encoding='utf-8')
    assert snapshot(bundle) == original and manifest.read_bytes() == raw
    assert project.read_bytes() == b'project must stay' and settings.read_bytes() == b'preferences must stay'
    assert (output / 'payload/_internal/자료 공백.txt').read_bytes() == original['_internal/자료 공백.txt']
    assert not (output / 'verified').exists()


def test_generated_install_policy_separates_versions_workspace_and_settings(candidate, tmp_path):
    manifest, _, _, _ = candidate
    output = installer.build_installer(manifest, tmp_path / 'prepared', prepare_only=True)
    text = (output / 'setup.iss').read_text(encoding='utf-8-sig')
    identity = read_report(output)['bundle_id']
    assert f'payloads\\{identity}\\CAELabWorkbench.exe' in text
    assert 'AppId=CAELabWorkbench.User' in text and 'PrivilegesRequired=lowest' in text
    assert 'WorkingDir: "{localappdata}\\CAELAB\\Workbench\\Workspace"' in text
    assert 'Flags: uninsneveruninstall' in text
    for forbidden in ('[InstallDelete]', '[UninstallDelete]', '[Registry]', '[INI]', '[Run]', '[UninstallRun]', 'AppMutex='):
        assert forbidden not in text
    assert 'CloseApplications=no' in text and 'RestartApplications=no' in text
    assert 'function PrepareToInstall' in text and 'function InitializeUninstall' in text
    assert 'if Processes.Count = 0 then' in text and 'except' in text
    assert '@@' not in text


def test_new_bundle_same_app_version_has_separate_payload_and_keeps_previous_preparation(candidate, tmp_path):
    manifest, bundle, values, save = candidate
    first = installer.build_installer(manifest, tmp_path / 'v1', prepare_only=True)
    previous = snapshot(first)
    (bundle / '_internal/資料.txt').write_bytes(b'new release')
    save()
    second = installer.build_installer(manifest, tmp_path / 'v2', prepare_only=True)
    assert values['project']['version'] == '0.1.0'
    assert read_report(first)['bundle_id'] != read_report(second)['bundle_id']
    assert read_report(first)['workspace'] == read_report(second)['workspace']
    assert snapshot(first) == previous


def test_literal_source_braces_resolve_to_snapshot_while_destination_escapes_constants(candidate, tmp_path):
    manifest, bundle, _, save = candidate
    name = '_internal/{tool}/name{part}.txt'
    source = bundle / name
    source.parent.mkdir()
    source.write_bytes(b'literal braces')
    save()
    output = installer.build_installer(manifest, tmp_path / 'prepared', prepare_only=True)
    text = (output / 'setup.iss').read_text(encoding='utf-8-sig')
    line = next(line for line in text.splitlines() if 'name{part}.txt' in line)
    assert 'Source: "payload\\_internal\\{tool}\\name{part}.txt";' in line
    assert '\\_internal\\{{tool}"; Flags:' in line
    literal = line.split('"')[1]
    assert output.joinpath(*literal.split('\\')).read_bytes() == b'literal braces'


@pytest.mark.parametrize('damage', ['missing', 'changed', 'extra', 'manifest-status', 'path-traversal'])
def test_invalid_candidate_is_rejected_without_partial_installer(candidate, tmp_path, damage):
    manifest, bundle, values, save = candidate
    if damage == 'missing':
        (bundle / 'CAELabWorkbench.exe').unlink()
    elif damage == 'changed':
        (bundle / 'CAELabWorkbench.exe').write_bytes(b'tampered')
    elif damage == 'extra':
        (bundle / 'unexpected').write_bytes(b'extra')
    elif damage == 'manifest-status':
        values['status'] = 'building'
        save()
    else:
        values['inventory'][0]['path'] = '../outside'
        manifest.write_text(json.dumps(values), encoding='utf-8')
    original = snapshot(manifest.parent)
    output = tmp_path / 'failure'
    with pytest.raises(ValueError):
        installer.build_installer(manifest, output, prepare_only=True)
    no_output(output)
    assert snapshot(manifest.parent) == original


def test_existing_output_is_never_replaced(candidate, tmp_path):
    manifest, _, _, _ = candidate
    output = tmp_path / 'existing'
    output.mkdir()
    (output / 'user file').write_bytes(b'keep')
    with pytest.raises(ValueError):
        installer.build_installer(manifest, output, prepare_only=True)
    assert (output / 'user file').read_bytes() == b'keep'


def test_compiler_missing_is_failure_before_archiving(candidate, tmp_path, monkeypatch):
    manifest, _, _, _ = candidate
    monkeypatch.setattr(installer.shutil, 'which', lambda name: None)
    monkeypatch.setattr(installer.archive, 'archive_candidate', lambda *args: pytest.fail('must not archive'))
    assert installer.main(['--manifest', str(manifest), '--output', str(tmp_path / 'failure')]) == 1
    no_output(tmp_path / 'failure')


def test_compile_adapter_success_records_provenance_but_never_installation(candidate, tmp_path, monkeypatch):
    manifest, bundle, _, _ = candidate
    original = snapshot(bundle)
    compiler = fake_compiler(tmp_path, monkeypatch)
    output = installer.build_installer(manifest, tmp_path / 'compiled', compiler=compiler)
    report = read_report(output)
    assert report['status'] == 'compiled_not_installed' and report['compile_exit_code'] == 0
    assert report['compiler']['sha256'] == installer.archive._sha(compiler)
    assert report['installer']['sha256'] == installer.archive._sha(output / installer.INSTALLER)
    assert (output / (installer.INSTALLER + '.sha256')).read_text().startswith(report['installer']['sha256'])
    assert report['windows_install_uninstall_upgrade_validation'] == 'not_performed'
    assert not (output / 'payload').exists() and snapshot(bundle) == original


@pytest.mark.parametrize('mode', ['compiler-error', 'cancel', 'snapshot-change', 'script-change', 'compiler-change', 'output-race'])
def test_failure_or_cancellation_preserves_candidate_and_existing_outputs(candidate, tmp_path, monkeypatch, mode):
    manifest, bundle, _, _ = candidate
    original = snapshot(bundle)
    output = tmp_path / 'failed'

    def action(script):
        if mode == 'compiler-error':
            raise ValueError('deliberate compiler failure')
        if mode == 'cancel':
            raise KeyboardInterrupt()
        if mode == 'snapshot-change':
            (script.parent / 'payload/CAELabWorkbench.exe').write_bytes(b'tampered')
        if mode == 'script-change':
            script.write_text('modified script')
        if mode == 'compiler-change':
            compiler.write_bytes(b'changed compiler')
        if mode == 'output-race':
            output.mkdir()
            (output / 'keep').write_bytes(b'someone else created this')

    compiler = fake_compiler(tmp_path, monkeypatch, action)
    with pytest.raises((ValueError, OSError, KeyboardInterrupt)):
        installer.build_installer(manifest, output, compiler=compiler)
    if mode == 'output-race':
        assert (output / 'keep').read_bytes() == b'someone else created this'
        assert not list(tmp_path.glob('.failed-*'))
    else:
        no_output(output)
    assert snapshot(bundle) == original


@pytest.mark.parametrize('corruption', ['extra-member', 'changed-bytes'])
def test_extraction_rechecks_zip_boundary(candidate, tmp_path, corruption):
    manifest, _, _, _ = candidate
    zipped = installer.archive.archive_candidate(manifest, tmp_path / 'verified')
    if corruption == 'extra-member':
        with zipfile.ZipFile(zipped, 'a') as output:
            output.writestr('CAELabWorkbench/../../escape', b'bad')
    else:
        replacement = tmp_path / 'altered.zip'
        with zipfile.ZipFile(zipped) as incoming, zipfile.ZipFile(replacement, 'w') as outgoing:
            for item in incoming.infolist():
                data = incoming.read(item)
                outgoing.writestr(item, b'x' * len(data) if item.filename.endswith('/CAELabWorkbench.exe') else data)
        zipped = replacement
    with pytest.raises(ValueError):
        installer._extract(zipped, tmp_path / 'snapshot')
    assert not (tmp_path / 'escape').exists()


def test_compiler_runner_uses_literal_arguments_and_reports_failure(tmp_path, monkeypatch):
    script = tmp_path / 'path with spaces' / 'setup.iss'
    script.parent.mkdir()
    script.write_text('fixture')
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7, b'compiler rejected the script')

    monkeypatch.setattr(installer.subprocess, 'run', run)
    with pytest.raises(ValueError, match='exited 7'):
        installer._run_compiler(Path('ISCC.exe'), script, 30)
    assert calls[0][0] == ['ISCC.exe', str(script)]
    assert calls[0][1]['cwd'] == script.parent and calls[0][1]['shell'] is False
