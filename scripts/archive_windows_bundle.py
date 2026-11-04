"""Verify a built Windows candidate and publish a portable ZIP in a new directory."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import tempfile
import zipfile

if __package__:
    from .build_windows_bundle import APP_NAME, DISTRIBUTIONS, SOURCE
    from .bundled_optional_metadata import PACKAGES, SCOPE, verify_optional_metadata
else:
    from build_windows_bundle import APP_NAME, DISTRIBUTIONS, SOURCE
    from bundled_optional_metadata import PACKAGES, SCOPE, verify_optional_metadata


PUBLIC_MANIFEST = 'portable-manifest.json'
ARCHIVE_NAME = f'{APP_NAME}-portable.zip'
PRIVATE_FILES = {'build-manifest.json', 'build.log', 'collected-package-provenance.json'}


def _relative(value):
    if not isinstance(value, str) or not value or any(char in value for char in '\\:*?"<>|'):
        raise ValueError('A canonical relative POSIX path is required')
    parts = value.split('/')
    if any(not part or part in {'.', '..'} or part.endswith(('.', ' ')) or
           any(ord(char) < 32 or ord(char) == 127 for char in part) or
           re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)
           for part in parts):
        raise ValueError(f'Unsafe portable path: {value!r}')
    if PurePosixPath(value).is_absolute():
        raise ValueError('An absolute path is not a portable payload path')
    return value


def _no_link(path):
    junction = getattr(path, 'is_junction', None)
    if path.is_symlink() or (callable(junction) and junction()):
        raise ValueError(f'Links/junctions are not archived: {path.name}')
    try:
        attributes = getattr(path.lstat(), 'st_file_attributes', None)
    except FileNotFoundError:
        return
    if attributes is not None and attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise ValueError(f'Links/junctions are not archived: {path.name}')
    if os.name == 'nt' and not callable(junction) and attributes is None:
        raise ValueError(f'Windows reparse-point inspection is unavailable: {path.name}')


def _no_link_parents(path):
    for current in (path, *path.parents):
        _no_link(current)


def _sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _digest(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{64}', value):
        raise ValueError('A full SHA-256 is required')
    return value.lower()


def _records(values):
    if not isinstance(values, list) or not values:
        raise ValueError('A nonempty file inventory is required')
    records, names = {}, set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError('Malformed inventory record')
        name = _relative(value.get('path'))
        if name.casefold() in names:
            raise ValueError(f'Duplicate Windows path: {name}')
        names.add(name.casefold())
        size = value.get('bytes')
        if type(size) is not int or size < 0:
            raise ValueError(f'Invalid byte count: {name}')
        records[name] = {'path': name, 'bytes': size, 'sha256': _digest(value.get('sha256'))}
    return records


def _files(root):
    _no_link_parents(root)
    if not root.is_dir():
        raise ValueError('The candidate payload directory is missing')
    pending, files, names = [root], {}, set()
    while pending:
        for path in pending.pop().iterdir():
            _no_link(path)
            name = _relative(path.relative_to(root).as_posix())
            if name.casefold() in names:
                raise ValueError(f'Duplicate Windows path: {name}')
            names.add(name.casefold())
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                if path.name.casefold() in PRIVATE_FILES or name.casefold() == PUBLIC_MANIFEST:
                    raise ValueError(f'Private/reserved build artifact in payload: {name}')
                files[name] = path
            else:
                raise ValueError(f'Not a regular payload file: {name}')
    return files


def _check_files(bundle, inventory):
    paths = _files(bundle)
    if paths.keys() != inventory.keys():
        raise ValueError('Candidate file inventory has missing or extra files')
    for name, path in paths.items():
        if path.stat().st_size != inventory[name]['bytes'] or _sha(path) != inventory[name]['sha256']:
            raise ValueError(f'Candidate bytes changed: {name}')
    return paths


def _candidate_checks(inventory, source_files):
    required_sources = {'pyproject.toml', 'scripts/build_windows_bundle.py',
                        'scripts/workbench_bundle.spec', 'scripts/pyi_runtime_hook.py',
                        'scripts/bundled_optional_metadata.py', 'caelab/solvers/installer/manifest.json'}
    if not required_sources <= source_files.keys():
        raise ValueError('Source inventory is missing required build inputs')
    icons = {path: record for path, record in source_files.items()
             if PurePosixPath(path).parent.as_posix() == 'apps/workbench_qt/icons'
             and path.endswith('.svg')}
    if not icons:
        raise ValueError('Source icon inventory is missing')
    dlls = [PurePosixPath(path).name.lower() for path in inventory if path.lower().endswith('.dll')]
    vtk = [PurePosixPath(path).name for path in inventory
           if PurePosixPath(path).parent.as_posix() == '_internal/vtkmodules' and path.endswith('.pyd')]
    return {
        'executable': f'{APP_NAME}.exe' in inventory,
        'native_dlls': bool(dlls),
        'python_runtime': any(name.startswith('python3') for name in dlls),
        'qt_core': 'qt6core.dll' in dlls,
        'qt_windows_platform': '_internal/PySide6/plugins/platforms/qwindows.dll' in inventory,
        'qt_svg_library': '_internal/PySide6/Qt6Svg.dll' in inventory,
        'qt_svg_icon_plugin': '_internal/PySide6/plugins/iconengines/qsvgicon.dll' in inventory,
        'qt_svg_image_plugin': '_internal/PySide6/plugins/imageformats/qsvg.dll' in inventory,
        'vtk_core_extension': any(name.startswith('vtkCommonCore') for name in vtk),
        'vtk_opengl_extension': any(name.startswith('vtkRenderingOpenGL2') for name in vtk),
        'solver_manifest': '_internal/caelab/solvers/installer/manifest.json' in inventory,
        'svg_icons': all(inventory.get('_internal/' + path, {}).get('sha256') == record['sha256']
                         and inventory.get('_internal/' + path, {}).get('bytes') == record['bytes']
                         for path, record in icons.items()),
    }


def _text(value):
    if not isinstance(value, str) or not value or any(char in value for char in '/\\:') or not value.isprintable():
        raise ValueError('Invalid public name/version')
    return value


def _optional(bundle, report, inventory, versions):
    if not isinstance(report, dict) or type(report.get('schema_version')) is not int or report['schema_version'] != 1 or report.get('scope') != SCOPE:
        raise ValueError('Optional package provenance scope/schema is missing')
    packages = report.get('distributions')
    if not isinstance(packages, list) or not isinstance(versions, dict) or set(PACKAGES) - versions.keys():
        raise ValueError('Optional package evidence/build inputs are missing')
    for version in versions.values():
        if version is not None:
            _text(version)
    public, names = [], set()
    for package in packages:
        if not isinstance(package, dict) or package.get('name') not in PACKAGES or package['name'] in names:
            raise ValueError('Invalid/duplicate optional distribution')
        name, version = package['name'], _text(package.get('version'))
        names.add(name)
        if versions.get(name) != version:
            raise ValueError(f'Optional distribution differs from build input: {name}')
        metadata, licenses = package.get('metadata'), package.get('licenses')
        analysis = package.get('analysis_files')
        if not isinstance(metadata, dict) or not isinstance(licenses, list) or not licenses or not isinstance(analysis, list) or not analysis:
            raise ValueError(f'Incomplete metadata/license/collection evidence: {name}')
        public_records = []
        for record in [metadata, *licenses]:
            if not isinstance(record, dict):
                raise ValueError('Invalid notice record')
            path, digest = _relative(record.get('path')), _digest(record.get('sha256'))
            if path not in inventory or inventory[path]['sha256'] != digest:
                raise ValueError(f'Notice is missing from inventory or differs: {path}')
            item = {'path': path, 'sha256': digest}
            if record is not metadata:
                item['declared_path'] = _relative(record.get('declared_path'))
            public_records.append(item)
        public_analysis = []
        for record in analysis:
            if not isinstance(record, dict) or record.get('kind') not in {'pure', 'binaries', 'datas'}:
                raise ValueError('Invalid collection evidence')
            public_analysis.append({'kind': record['kind'], 'source_relative': _relative(record.get('source_relative')),
                                    'source_sha256': _digest(record.get('source_sha256'))})
        public.append({'name': name, 'version': version, 'metadata': public_records[0],
                       'licenses': public_records[1:], 'analysis_files': public_analysis})
    verify_optional_metadata(bundle, report)
    return {'schema_version': 1, 'scope': SCOPE, 'distributions': public}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON key: {key}')
        result[key] = value
    return result


def _publish(stage, output):
    # Windows rename never replaces an existing directory. POSIX rename can.
    if os.name == 'nt':
        stage.rename(output)
    elif sys.platform.startswith('linux'):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        if rename is None:
            raise OSError('Atomic no-replace publication is unavailable')
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(stage), -100, os.fsencode(output), 1):
            raise OSError(ctypes.get_errno(), 'Cannot publish archive without replacement', str(output))
    else:
        raise OSError('Atomic no-replace publication is unavailable on this platform')


def archive_candidate(manifest_path: Path, output: Path) -> Path:
    """Archive only verified payload bytes; never build or execute the candidate."""
    manifest_path, output = Path(manifest_path).absolute(), Path(output).absolute()
    _no_link_parents(manifest_path)
    _no_link_parents(output)
    manifest_path, output = manifest_path.resolve(), output.resolve()
    build_root = manifest_path.parent
    if output.exists() or not output.parent.is_dir() or output.is_relative_to(build_root) or output.is_relative_to(SOURCE):
        raise ValueError('Output must be a new directory outside the candidate/source, with an existing parent')
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object)
    if not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
        raise ValueError('Unsupported build manifest schema')
    if manifest.get('status') != 'built_static_only' or type(manifest.get('build_exit_code')) is not int or manifest['build_exit_code'] != 0:
        raise ValueError('The candidate is not a successful static build')
    revisions = {}
    for key in ('source_revision', 'base_revision'):
        value = manifest.get(key)
        if not isinstance(value, str) or not re.fullmatch('[0-9a-fA-F]{40}', value):
            raise ValueError(f'A full {key} is required')
        revisions[key] = value.lower()
    inventory = _records(manifest.get('inventory'))
    source_files = _records(manifest.get('source_files'))
    if type(manifest.get('bundle_file_count')) is not int or manifest['bundle_file_count'] != len(inventory) or type(manifest.get('bundle_bytes')) is not int or manifest['bundle_bytes'] != sum(r['bytes'] for r in inventory.values()):
        raise ValueError('Candidate inventory totals differ')
    bundle = build_root / 'dist' / APP_NAME
    paths = _check_files(bundle, inventory)
    actual_checks = _candidate_checks(inventory, source_files)
    declared_checks = manifest.get('static_checks')
    if not isinstance(declared_checks, dict) or any(value is not True for value in declared_checks.values()) or any(declared_checks.get(name) is not True or passed is not True for name, passed in actual_checks.items()) or declared_checks.get('optional_package_metadata') is not True:
        raise ValueError('Required static checks are missing or failed')
    builder = manifest.get('builder')
    if not isinstance(builder, dict) or not isinstance(builder.get('distributions'), dict):
        raise ValueError('Build dependency versions are missing')
    versions = {name: _text(builder['distributions'].get(name)) for name in DISTRIBUTIONS}
    optional = _optional(bundle, manifest.get('bundled_optional_packages'), inventory, builder.get('optional_build_inputs'))
    project = manifest.get('project')
    if not isinstance(project, dict):
        raise ValueError('Project name/version is missing')
    public = {'schema_version': 1, 'source_revision': revisions['source_revision'],
              'base_revision': revisions['base_revision'], 'base_revision_role': 'caller_supplied_baseline',
              'original_build_manifest_sha256': hashlib.sha256(raw).hexdigest(),
              'source_files': list(source_files.values()),
              'project': {name: _text(project.get(name)) for name in ('name', 'version')},
              'build_distributions': versions, 'bundled_optional_packages': optional,
              'optional_build_inputs': {name: builder['optional_build_inputs'][name] for name in PACKAGES},
              'inventory': list(inventory.values()), 'build_status': 'built_static_only',
              'windows_runtime_validation': 'not_performed_by_archiver',
              'redistribution_approval': 'not_granted_by_archiver',
              'notice_review_scope': SCOPE,
              'archive_tool': {'path': 'scripts/archive_windows_bundle.py', 'sha256': _sha(Path(__file__))}}
    public_bytes = (json.dumps(public, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    expected = {f'{APP_NAME}/{name}': record['sha256'] for name, record in inventory.items()}
    expected[f'{APP_NAME}/{PUBLIC_MANIFEST}'] = hashlib.sha256(public_bytes).hexdigest()
    stage = Path(tempfile.mkdtemp(prefix=f'.{output.name}-', dir=output.parent))
    try:
        archive = stage / ARCHIVE_NAME
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zipped:
            for name, path in sorted(paths.items()):
                zipped.write(path, f'{APP_NAME}/{name}')
            zipped.writestr(f'{APP_NAME}/{PUBLIC_MANIFEST}', public_bytes)
        with zipfile.ZipFile(archive) as zipped:
            if zipped.testzip() is not None or set(zipped.namelist()) != expected.keys() or len(zipped.namelist()) != len(expected):
                raise ValueError('ZIP CRC or member inventory differs')
            for name, digest in expected.items():
                actual = hashlib.sha256()
                with zipped.open(name) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        actual.update(chunk)
                if actual.hexdigest() != digest:
                    raise ValueError(f'ZIP payload differs: {name}')
        if manifest_path.read_bytes() != raw:
            raise ValueError('Build manifest changed during archiving')
        _check_files(bundle, inventory)
        checksum = _sha(archive)
        (stage / f'{ARCHIVE_NAME}.sha256').write_text(f'{checksum}  {ARCHIVE_NAME}\n', encoding='ascii')
        if output.exists() or output.is_symlink():
            raise ValueError('Output appeared during archiving')
        _publish(stage, output)
        return output / ARCHIVE_NAME
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        archive = archive_candidate(args.manifest, args.output)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        print(f'Archive failed: {error}', file=sys.stderr)
        return 1
    print(f'Verified static candidate archive: {archive}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
