"""Actual collected optional packages need owned metadata and final notice bytes."""

from email.message import Message
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.bundled_optional_metadata import collect_optional_metadata, verify_optional_metadata


@pytest.fixture
def installed(tmp_path):
    root = tmp_path / 'installed'
    files = ['torch/__init__.py', 'torch-2.13.0.dist-info/METADATA',
             'torch-2.13.0.dist-info/licenses/LICENSE']
    contents = [b'# optional module', b'Name: torch\nVersion: 2.13.0\nLicense-File: LICENSE\n', b'license fixture']
    for relative, data in zip(files, contents):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    headers = Message()
    headers['Name'], headers['Version'], headers['License-File'] = 'torch', '2.13.0', 'LICENSE'
    distribution = SimpleNamespace(metadata=headers, version='2.13.0', files=[Path(p) for p in files],
                                   locate_file=lambda p: root / p)
    analysis = SimpleNamespace(pure=[('torch', str(root / files[0]), 'PYMODULE')], binaries=[], datas=[])
    def copy(name, *, recursive):
        assert name == 'torch' and recursive is False
        return [(str(root / 'torch-2.13.0.dist-info'), 'torch-2.13.0.dist-info')]
    def expand(pairs):
        for source, target in pairs:
            for path in Path(source).rglob('*'):
                if path.is_file():
                    yield (str(Path(target) / path.relative_to(source)), str(path))
    def collect():
        def lookup(name):
            if name != 'torch':
                raise PackageNotFoundError(name)
            return distribution
        return collect_optional_metadata(analysis, distribution=lookup,
                                         copy_metadata=copy, expand_datas=expand)
    return root, distribution, analysis, collect


def test_uncollected_optional_packages_are_not_added(installed):
    _, _, analysis, collect = installed
    analysis.pure = [('torchlike', 'unrelated.py', 'PYMODULE')]
    report = collect()
    assert report['scope'] == 'torch_and_torchvision_only'
    assert report['distributions'] == [] and analysis.datas == []


def test_collected_package_metadata_notices_and_final_hashes(installed, tmp_path):
    _, _, analysis, collect = installed
    report = collect()
    package = report['distributions'][0]
    assert package['name'] == 'torch' and package['version'] == '2.13.0'
    assert package['analysis_files'][0]['source_relative'] == 'torch/__init__.py'
    bundle = tmp_path / 'bundle'
    for destination, source, kind in analysis.datas:
        target = bundle / '_internal' / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(Path(source).read_bytes())
    verify_optional_metadata(bundle, report)
    license_path = bundle / package['licenses'][0]['path']
    license_path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='LICENSE'):
        verify_optional_metadata(bundle, report)
    license_path.unlink()
    with pytest.raises((ValueError, OSError)):
        verify_optional_metadata(bundle, report)


def test_flattened_binary_is_classified_by_owned_source(installed, tmp_path):
    root, distribution, analysis, collect = installed
    relative = Path('torch/lib/libtorch.dll')
    (root / relative).parent.mkdir(parents=True)
    (root / relative).write_bytes(b'owned binary fixture; never loaded')
    distribution.files.append(relative)
    analysis.pure = []
    analysis.binaries = [('libtorch.dll', str(root / relative), 'BINARY')]
    report = collect()
    assert [item['name'] for item in report['distributions']] == ['torch']
    assert report['distributions'][0]['analysis_files'][0]['identifier'] == 'libtorch.dll'
    assert any(target.endswith('/METADATA') for target, _, _ in analysis.datas)
    bundle = tmp_path / 'bundle'
    for target, source, _ in [*analysis.binaries, *analysis.datas]:
        path = bundle / '_internal' / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(Path(source).read_bytes())
    verify_optional_metadata(bundle, report)


@pytest.mark.parametrize('collected', [False, True])
def test_absent_metadata_is_required_only_for_observed_optional_sources(installed, collected, monkeypatch):
    root, _, analysis, _ = installed
    analysis.pure = []
    monkeypatch.syspath_prepend(str(root))
    analysis.binaries = [('flat.dll', str(root / 'torch/lib/flat.dll'), 'BINARY')] if collected else []
    def absent(name):
        raise PackageNotFoundError(name)
    if collected:
        with pytest.raises(ValueError, match='metadata'):
            collect_optional_metadata(analysis, distribution=absent, copy_metadata=None, expand_datas=None)
    else:
        report = collect_optional_metadata(analysis, distribution=absent, copy_metadata=None, expand_datas=None)
        assert report['distributions'] == [] and analysis.datas == []


def test_scipy_torch_compatibility_wrapper_is_not_a_torch_distribution_file(installed):
    root, _, analysis, collect = installed
    wrapper = root / 'scipy/_external/array_api_compat/torch/__init__.py'
    wrapper.parent.mkdir(parents=True)
    wrapper.write_bytes(b'# compatibility wrapper fixture')
    analysis.pure = [('scipy._external.array_api_compat.torch', str(wrapper), 'PYMODULE')]
    report = collect()
    assert report['distributions'] == [] and analysis.datas == []


@pytest.mark.parametrize('failure', ['version', 'notice', 'ownership', 'collision', 'ambiguous_notice', 'escaping_notice'])
def test_incomplete_or_ambiguous_collection_fails_before_adding_datas(installed, failure):
    root, distribution, analysis, collect = installed
    if failure == 'version':
        del distribution.metadata['Version']
    elif failure == 'notice':
        (root / 'torch-2.13.0.dist-info/licenses/LICENSE').unlink()
    elif failure == 'ownership':
        analysis.pure = [('torch', str(root / 'another-install/torch.py'), 'PYMODULE')]
    elif failure == 'collision':
        analysis.binaries = [('TORCH-2.13.0.DIST-INFO/METADATA', str(root / 'other'), 'BINARY')]
    elif failure == 'ambiguous_notice':
        path = Path('torch-2.13.0.dist-info/LICENSE')
        (root / path).write_bytes(b'another declaration location')
        distribution.files.append(path)
    else:
        distribution.metadata.replace_header('License-File', '../LICENSE')
    with pytest.raises((ValueError, OSError)):
        collect()
    assert analysis.datas == []


def test_existing_bundle_with_torch_but_no_provenance_is_rejected(tmp_path):
    path = tmp_path / '_internal/torch/lib/probe.dll'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'never loaded')
    with pytest.raises(ValueError, match='torch'):
        verify_optional_metadata(tmp_path, {'schema_version': 1, 'scope': 'torch_and_torchvision_only',
                                          'distributions': []})


def test_metadata_copy_must_include_declared_notice(installed):
    root, distribution, analysis, _ = installed
    info = 'torch-2.13.0.dist-info'
    with pytest.raises(ValueError, match='omits'):
        collect_optional_metadata(analysis, distribution=lambda name: distribution,
            copy_metadata=lambda *args, **kwargs: [(str(root / info), info)],
            expand_datas=lambda pairs: [(info + '/METADATA', str(root / info / 'METADATA'))])
    assert analysis.datas == []


@pytest.mark.parametrize('mode', ['missing_report', 'empty_report', 'changed_dependency', 'malformed_report'])
def test_builder_does_not_approve_missing_provenance_or_changed_inputs(tmp_path, monkeypatch, mode):
    import json
    from scripts import build_windows_bundle as builder
    source, output = tmp_path / 'source', tmp_path / 'output'
    for relative in ('apps/workbench_qt/desktop_entry.py', 'caelab/native/windows_gate.py',
                     'caelab/process_entry.py', 'scripts/pyi_runtime_hook.py',
                     'scripts/workbench_bundle.spec', 'scripts/bundled_optional_metadata.py',
                     'caelab/solvers/installer/manifest.json', 'apps/workbench_qt/icons/folder.svg'):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'build input fixture; not executed')
    (source / 'pyproject.toml').write_text('[project]\nname="fixture"\nversion="1"\n', encoding='utf-8')
    built = []
    monkeypatch.setattr(builder, 'SOURCE', source)
    monkeypatch.setattr(builder.sys, 'platform', 'win32')
    monkeypatch.setattr(builder.platform, 'platform', lambda: 'fixture')
    monkeypatch.setattr(builder, '_source_inventory', lambda: [])
    monkeypatch.setattr(builder, '_versions', lambda names: {n: '2' if built and mode == 'changed_dependency' else '1' for n in names})
    monkeypatch.setattr(builder, '_static_checks', lambda bundle: {'presence_fixture': True})
    def build(*args, **kwargs):
        built.append(True)
        (output / 'dist/CAELabWorkbench').mkdir(parents=True)
        if mode in ('empty_report', 'malformed_report'):
            path = output / 'build/workbench_bundle/collected-package-provenance.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'schema_version': 1, 'scope': 'torch_and_torchvision_only',
                                        'distributions': [{}] if mode == 'malformed_report' else []}), encoding='utf-8')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(builder.subprocess, 'run', build)
    code = builder.main(['--output', str(output), '--base-revision', 'a' * 40, '--source-revision', 'b' * 40])
    manifest = json.loads((output / 'build-manifest.json').read_text(encoding='utf-8'))
    if mode == 'empty_report':
        assert code == 0 and manifest['status'] == 'built_static_only'
        assert manifest['bundled_optional_packages']['distributions'] == []
    elif mode in ('missing_report', 'malformed_report'):
        assert code == 1 and manifest['status'] == 'build_error'
        assert manifest['static_checks']['optional_package_metadata'] is False
    else:
        assert code == 1 and manifest['status'] == 'dependencies_changed_during_build'
