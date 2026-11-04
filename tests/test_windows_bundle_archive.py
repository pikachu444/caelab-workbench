"""Portable handoff verifies built bytes without running the bundled application."""

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from scripts import archive_windows_bundle as archiver
from scripts import build_windows_bundle as builder


def digest(data):
    return hashlib.sha256(data).hexdigest()


def inventory(root):
    return [{'path': path.relative_to(root).as_posix(), 'bytes': path.stat().st_size,
             'sha256': digest(path.read_bytes())}
            for path in sorted(root.rglob('*')) if path.is_file()]


@pytest.fixture
def candidate(tmp_path):
    build = tmp_path / 'private-build'
    bundle = build / 'dist' / 'CAELabWorkbench'
    paths = [
        'CAELabWorkbench.exe', '_internal/python313.dll', '_internal/PySide6/Qt6Core.dll',
        '_internal/PySide6/Qt6Svg.dll', '_internal/PySide6/plugins/platforms/qwindows.dll',
        '_internal/PySide6/plugins/iconengines/qsvgicon.dll',
        '_internal/PySide6/plugins/imageformats/qsvg.dll',
        '_internal/vtkmodules/vtkCommonCore.pyd', '_internal/vtkmodules/vtkRenderingOpenGL2.pyd',
        '_internal/caelab/solvers/installer/manifest.json', '_internal/자료 공백.txt',
    ]
    paths += ['_internal/apps/workbench_qt/icons/' + p.name
              for p in (builder.SOURCE / 'apps/workbench_qt/icons').glob('*.svg')]
    for relative in paths:
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('fixture, never loaded: ' + relative).encode('utf-8'))
    manifest = {
        'schema_version': 1, 'status': 'built_static_only', 'build_exit_code': 0,
        'source_root': 'C:/Users/private-person/source',
        'bundle_root': 'C:/Users/private-person/old-location/dist/CAELabWorkbench',
        'command': ['C:/Users/private-person/python.exe', 'private command'],
        'source_revision': 'a' * 40, 'base_revision': 'b' * 40,
        'source_files': [{'path': 'scripts/build_windows_bundle.py', 'bytes': 1, 'sha256': 'c' * 64}],
        'project': {'name': 'caelab-workbench', 'version': '0.1.0'},
        'builder': {'executable': 'C:/Users/private-person/python.exe',
                    'distributions': {name: '1.0' for name in builder.DISTRIBUTIONS},
                    'optional_build_inputs': {'torch': None, 'torchvision': None}},
        'static_checks': {**builder._static_checks(bundle), 'optional_package_metadata': True},
        'bundled_optional_packages': {'schema_version': 1, 'scope': 'torch_and_torchvision_only',
                                     'distributions': []},
    }
    path = build / 'build-manifest.json'
    (build / 'build.log').write_bytes(b'private build log must not be published')

    def save():
        manifest['inventory'] = inventory(bundle)
        manifest['bundle_file_count'] = len(manifest['inventory'])
        manifest['bundle_bytes'] = sum(r['bytes'] for r in manifest['inventory'])
        path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')

    save()
    return path, bundle, manifest, save


def assert_no_publication(tmp_path):
    assert not (tmp_path / 'delivery').exists()
    assert not list(tmp_path.glob('.delivery-*'))


def test_archive_preserves_every_payload_and_sanitizes_public_record(candidate, tmp_path):
    path, bundle, manifest, _ = candidate
    original = {record['path']: (bundle / record['path']).read_bytes() for record in manifest['inventory']}
    raw = path.read_bytes()
    result = archiver.archive_candidate(path, tmp_path / 'delivery')
    with zipfile.ZipFile(result) as zipped:
        assert zipped.testzip() is None
        assert set(zipped.namelist()) == {'CAELabWorkbench/' + n for n in original} | {'CAELabWorkbench/portable-manifest.json'}
        for name, data in original.items():
            assert zipped.read('CAELabWorkbench/' + name) == data
        record_bytes = zipped.read('CAELabWorkbench/portable-manifest.json')
        public = json.loads(record_bytes)
        assert b'private-person' not in record_bytes and b'private command' not in record_bytes
        assert public['source_revision'] == 'a' * 40 and public['base_revision'] == 'b' * 40
        assert 'packaging_commit' not in public
        assert public['original_build_manifest_sha256'] == digest(raw)
        assert public['windows_runtime_validation'] == 'not_performed_by_archiver'
        assert public['redistribution_approval'] == 'not_granted_by_archiver'
        assert public['notice_review_scope'] == 'torch_and_torchvision_only'
    assert result.with_suffix('.zip.sha256').read_text() == f'{digest(result.read_bytes())}  {result.name}\n'
    assert path.read_bytes() == raw
    assert {name: (bundle / name).read_bytes() for name in original} == original


@pytest.mark.parametrize('field,value', [
    ('status', 'build_failed'), ('build_exit_code', 1), ('schema_version', True),
    ('source_revision', 'short'), ('bundle_file_count', 0), ('bundle_bytes', 0),
    ('bundled_optional_packages', None), ('source_files', []), ('inventory', 'not a list'),
])
def test_malformed_or_unapproved_candidate_is_rejected(candidate, tmp_path, field, value):
    path, _, manifest, _ = candidate
    manifest[field] = value
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError):
        archiver.archive_candidate(path, tmp_path / 'delivery')
    assert_no_publication(tmp_path)


@pytest.mark.parametrize('path_name', ['../outside', '/absolute', 'C:/host', 'a\\b', 'x/./y',
                                       'x//y', 'CON.txt', 'name.', 'file ', 'bad?name'])
def test_inventory_rejects_unsafe_windows_paths(candidate, tmp_path, path_name):
    path, _, manifest, _ = candidate
    manifest['inventory'][0]['path'] = path_name
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError):
        archiver.archive_candidate(path, tmp_path / 'delivery')
    assert_no_publication(tmp_path)


@pytest.mark.parametrize('change', ['duplicate', 'missing', 'extra', 'bytes', 'reserved', 'false_check'])
def test_candidate_inventory_and_static_claims_are_rechecked(candidate, tmp_path, change):
    path, bundle, manifest, save = candidate
    if change == 'duplicate':
        duplicate = dict(manifest['inventory'][0])
        duplicate['path'] = duplicate['path'].upper()
        manifest['inventory'].append(duplicate)
    elif change == 'missing':
        (bundle / manifest['inventory'][0]['path']).unlink()
    elif change == 'extra':
        (bundle / 'extra.bin').write_bytes(b'unlisted')
    elif change == 'bytes':
        (bundle / manifest['inventory'][0]['path']).write_bytes(b'changed')
    elif change == 'reserved':
        (bundle / 'portable-manifest.json').write_bytes(b'conflict')
        save()
    else:
        manifest['static_checks']['future_required_gate'] = False
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError):
        archiver.archive_candidate(path, tmp_path / 'delivery')
    assert_no_publication(tmp_path)


def add_optional(candidate):
    _, bundle, manifest, save = candidate
    metadata = '_internal/torch-2.0.dist-info/METADATA'
    license_path = '_internal/torch-2.0.dist-info/licenses/LICENSE'
    for relative, data in [(metadata, b'Name: torch\nVersion: 2.0\nLicense-File: LICENSE\n'),
                           (license_path, b'fixture notice; preserve exactly')]:
        path = bundle / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    manifest['builder']['optional_build_inputs']['torch'] = '2.0'
    manifest['bundled_optional_packages']['distributions'] = [{
        'name': 'torch', 'version': '2.0',
        'analysis_files': [{'kind': 'binaries', 'identifier': 'libtorch.dll',
                            'source_relative': 'torch/lib/libtorch.dll', 'source_sha256': 'd' * 64}],
        'metadata': {'path': metadata, 'sha256': digest((bundle / metadata).read_bytes())},
        'licenses': [{'declared_path': 'LICENSE', 'path': license_path,
                      'sha256': digest((bundle / license_path).read_bytes())}],
    }]
    save()
    return metadata, license_path


def test_declared_notice_bytes_are_preserved(candidate, tmp_path):
    _, bundle, _, _ = candidate
    metadata, notice = add_optional(candidate)
    result = archiver.archive_candidate(candidate[0], tmp_path / 'delivery')
    with zipfile.ZipFile(result) as zipped:
        for name in (metadata, notice):
            assert zipped.read('CAELabWorkbench/' + name) == (bundle / name).read_bytes()


@pytest.mark.parametrize('failure', ['missing', 'changed', 'declaration', 'provenance', 'version'])
def test_optional_notice_failures_do_not_become_release_approval(candidate, tmp_path, failure):
    path, bundle, manifest, save = candidate
    metadata, notice = add_optional(candidate)
    if failure == 'missing':
        (bundle / notice).unlink()
    elif failure == 'changed':
        (bundle / notice).write_bytes(b'modified')
        save()  # A forged fresh inventory must not replace the recorded notice hash.
    elif failure == 'declaration':
        (bundle / metadata).write_bytes(b'Name: torch\nVersion: 2.0\nLicense-File: OTHER\n')
        manifest['bundled_optional_packages']['distributions'][0]['metadata']['sha256'] = digest((bundle / metadata).read_bytes())
        save()
    elif failure == 'provenance':
        del manifest['bundled_optional_packages']
    else:
        manifest['builder']['optional_build_inputs']['torch'] = 'different'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises((ValueError, OSError)):
        archiver.archive_candidate(path, tmp_path / 'delivery')
    assert_no_publication(tmp_path)


@pytest.mark.parametrize('change', ['payload', 'manifest', 'extra'])
def test_changes_during_compression_are_rejected(candidate, tmp_path, monkeypatch, change):
    path, bundle, _, _ = candidate
    original_write = zipfile.ZipFile.write
    touched = []
    def mutate(zipped, filename, arcname=None, *args, **kwargs):
        original_write(zipped, filename, arcname, *args, **kwargs)
        if not touched:
            touched.append(True)
            if change == 'payload':
                Path(filename).write_bytes(b'changed after compression')
            elif change == 'manifest':
                path.write_bytes(path.read_bytes() + b' ')
            else:
                (bundle / 'added-after-inventory').write_bytes(b'extra')
    monkeypatch.setattr(zipfile.ZipFile, 'write', mutate)
    with pytest.raises(ValueError):
        archiver.archive_candidate(path, tmp_path / 'delivery')
    assert_no_publication(tmp_path)


def test_existing_output_and_publication_failure_preserve_originals(candidate, tmp_path, monkeypatch):
    path, bundle, manifest, _ = candidate
    output = tmp_path / 'delivery'
    output.mkdir()
    marker = output / 'keep.txt'
    marker.write_bytes(b'existing delivery')
    with pytest.raises(ValueError):
        archiver.archive_candidate(path, output)
    assert marker.read_bytes() == b'existing delivery'
    original = path.read_bytes()
    def fail(stage, target):
        raise OSError('publication failure')
    monkeypatch.setattr(archiver, '_publish', fail)
    with pytest.raises(OSError):
        archiver.archive_candidate(path, tmp_path / 'another-delivery')
    assert not (tmp_path / 'another-delivery').exists()
    assert not list(tmp_path.glob('.another-delivery-*'))
    assert path.read_bytes() == original
    assert inventory(bundle) == manifest['inventory']


def test_publication_never_replaces_a_concurrent_empty_directory(tmp_path):
    stage, output = tmp_path / 'stage', tmp_path / 'delivery'
    stage.mkdir()
    (stage / 'archive').write_bytes(b'finished')
    output.mkdir()
    with pytest.raises(OSError):
        archiver._publish(stage, output)
    assert output.is_dir() and not list(output.iterdir())
    assert (stage / 'archive').read_bytes() == b'finished'


def test_missing_rendering_runtime_is_not_approved_by_a_fresh_inventory(candidate, tmp_path):
    path, bundle, _, save = candidate
    (bundle / '_internal/PySide6/Qt6Svg.dll').unlink()
    save()
    with pytest.raises(ValueError, match='static checks'):
        archiver.archive_candidate(path, tmp_path / 'delivery')
    assert_no_publication(tmp_path)


def test_duplicate_json_fields_are_rejected(candidate, tmp_path):
    path = candidate[0]
    raw = path.read_text(encoding='utf-8')
    path.write_text(raw[:-1] + ', "status": "built_static_only"}', encoding='utf-8')
    with pytest.raises(ValueError, match='Duplicate JSON key'):
        archiver.archive_candidate(path, tmp_path / 'delivery')
    assert_no_publication(tmp_path)


def test_junction_payload_is_rejected_before_descent(candidate, tmp_path, monkeypatch):
    path, bundle, _, _ = candidate
    junction = bundle / 'junction-fixture'
    junction.mkdir()
    monkeypatch.setattr(Path, 'is_junction', lambda p: p == junction, raising=False)
    with pytest.raises(ValueError, match='Links'):
        archiver.archive_candidate(path, tmp_path / 'delivery')
    assert_no_publication(tmp_path)


def test_symlink_payload_is_rejected(candidate, tmp_path):
    path, bundle, _, _ = candidate
    target = tmp_path / 'outside'
    target.write_bytes(b'do not include')
    link = bundle / 'linked'
    try:
        link.symlink_to(target)
    except OSError as error:
        pytest.skip(f'The host cannot create a symlink fixture: {error}')
    with pytest.raises(ValueError, match='Links'):
        archiver.archive_candidate(path, tmp_path / 'delivery')
    assert_no_publication(tmp_path)


def test_cli_creates_the_handoff_directory(candidate, tmp_path):
    assert archiver.main(['--manifest', str(candidate[0]), '--output', str(tmp_path / 'delivery')]) == 0
    assert (tmp_path / 'delivery' / archiver.ARCHIVE_NAME).is_file()
