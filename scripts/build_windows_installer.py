"""Prepare or compile a per-user installer from an existing verified app bundle.

Never installs software or runs the bundled app. Inno Setup must already exist.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

if __package__:
    from . import archive_windows_bundle as archive
else:
    import archive_windows_bundle as archive


SOURCE = Path(__file__).resolve().parents[1]
TEMPLATE = Path(__file__).with_name('windows_installer') / 'caelab-workbench.iss'
INSTALLER = 'caelab-workbench-setup.exe'
APP_ID = 'CAELabWorkbench.User'


def _extract(verified_zip: Path, payload: Path) -> tuple[dict, dict]:
    """Copy only the expected ZIP members, checking bytes before compilation."""
    prefix = archive.APP_NAME + '/'
    public_name = prefix + archive.PUBLIC_MANIFEST
    with zipfile.ZipFile(verified_zip) as zipped:
        public_bytes = zipped.read(public_name)
        public = json.loads(public_bytes.decode('utf-8'), object_pairs_hook=archive._unique_object)
        if not isinstance(public, dict) or public.get('build_status') != 'built_static_only':
            raise ValueError('The verified ZIP has no static candidate record')
        records = archive._records(public.get('inventory'))
        records[archive.PUBLIC_MANIFEST] = {
            'path': archive.PUBLIC_MANIFEST, 'bytes': len(public_bytes),
            'sha256': hashlib.sha256(public_bytes).hexdigest(),
        }
        expected = {prefix + name for name in records}
        members = zipped.infolist()
        if len(members) != len(expected) or {item.filename for item in members} != expected:
            raise ValueError('Verified ZIP member inventory differs')
        for item in members:
            name = archive._relative(item.filename[len(prefix):])
            if item.is_dir() or ((item.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Directories and links are not installer payload members')
            record = records[name]
            if item.file_size != record['bytes']:
                raise ValueError(f'ZIP member size differs: {name}')
            destination = payload.joinpath(*PurePosixPath(name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with zipped.open(item) as incoming, destination.open('xb') as outgoing:
                for chunk in iter(lambda: incoming.read(1024 * 1024), b''):
                    digest.update(chunk)
                    outgoing.write(chunk)
            if digest.hexdigest() != record['sha256']:
                raise ValueError(f'ZIP member hash differs: {name}')
    _check_snapshot(payload, records)
    return public, records


def _check_snapshot(payload: Path, records: dict) -> None:
    archive._no_link_parents(payload)
    if not payload.is_dir():
        raise ValueError('Installer snapshot is missing')
    files = {}
    for path in payload.rglob('*'):
        archive._no_link(path)
        if path.is_file():
            files[path.relative_to(payload).as_posix()] = path
        elif not path.is_dir():
            raise ValueError('Unexpected installer payload entry')
    if files.keys() != records.keys():
        raise ValueError('Installer snapshot has missing or extra files')
    for name, path in files.items():
        if path.stat().st_size != records[name]['bytes'] or archive._sha(path) != records[name]['sha256']:
            raise ValueError(f'Installer snapshot changed: {name}')


def _render(public: dict, records: dict, template: bytes) -> tuple[str, str]:
    bundle_id = archive._digest(public.get('original_build_manifest_sha256'))
    version = public.get('project', {}).get('version')
    if not isinstance(version, str) or not re.fullmatch(r'[0-9][0-9A-Za-z.+_-]{0,63}', version):
        raise ValueError('Unsupported installer version text')
    files = []
    for name in sorted(records):
        # Source is a compiler literal; only DestDir expands runtime constants.
        relative = archive._relative(name).replace('/', '\\')
        parent = relative.rsplit('\\', 1)[0].replace('{', '{{') if '\\' in relative else ''
        target = '{app}\\payloads\\' + bundle_id + ('\\' + parent if parent else '')
        files.append(f'Source: "payload\\{relative}"; DestDir: "{target}"; Flags: ignoreversion')
    rendered = template.decode('utf-8-sig').replace('@@VERSION@@', version)
    rendered = rendered.replace('@@BUNDLE_ID@@', bundle_id).replace('@@FILES@@', '\n'.join(files))
    if '@@' in rendered:
        raise ValueError('Unresolved installer template placeholder')
    return rendered, bundle_id


def _compiler_info(compiler: Path) -> dict:
    if os.name != 'nt':
        raise OSError('A Windows Inno Setup compiler is required')
    version = ctypes.WinDLL('version', use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint)]
    version.GetFileVersionInfoSizeW.restype = ctypes.c_uint
    version.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                    ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
    unused, length, pointer = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_void_p()
    size = version.GetFileVersionInfoSizeW(str(compiler), ctypes.byref(unused))
    if not size:
        raise OSError('Cannot read compiler file version')
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(compiler), 0, size, data):
        raise OSError('Cannot read compiler version resource')
    if not version.VerQueryValueW(data, '\\', ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
        raise OSError('Cannot read compiler version fields')
    fields = (ctypes.c_uint32 * 13).from_address(pointer.value)
    if fields[0] != 0xFEEF04BD:
        raise ValueError('Invalid compiler version resource')
    values = (fields[2] >> 16, fields[2] & 65535, fields[3] >> 16, fields[3] & 65535)
    return {'name': compiler.name, 'version': '.'.join(map(str, values)), 'sha256': archive._sha(compiler)}


def _run_compiler(compiler: Path, script: Path, timeout: float) -> None:
    command = [str(compiler), str(script)]
    result = subprocess.run(command, cwd=script.parent, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout, shell=False,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    (script.parent / 'compiler.log').write_bytes(result.stdout)
    if result.returncode:
        tail = result.stdout[-2000:].decode('utf-8', errors='replace')
        raise ValueError(f'Compiler exited {result.returncode}: {tail}')


def build_installer(manifest: Path, output: Path, *, compiler: Path | None = None,
                    prepare_only: bool = False, timeout: float = 600) -> Path:
    """Return a new preparation/compiled directory; never run an installer."""
    manifest, output = Path(manifest).absolute(), Path(output).absolute()
    archive._no_link_parents(manifest)
    archive._no_link_parents(output)
    manifest, output = manifest.resolve(), output.resolve()
    if output.exists() or not output.parent.is_dir() or output.is_relative_to(manifest.parent) or output.is_relative_to(SOURCE):
        raise ValueError('Output must be new and outside the source/candidate with an existing parent')
    compiler_record = None
    if not prepare_only:
        found = str(compiler) if compiler is not None else shutil.which('ISCC.exe')
        if not found:
            raise ValueError('Inno Setup compiler not found; use --prepare-only or provide an already installed --compiler')
        compiler = Path(found).absolute()
        archive._no_link_parents(compiler)
        if not compiler.is_file():
            raise ValueError('The selected compiler does not exist')
        compiler_record = _compiler_info(compiler)
    stage = Path(tempfile.mkdtemp(prefix=f'.{output.name}-', dir=output.parent))
    try:
        verified_zip = archive.archive_candidate(manifest, stage / 'verified')
        zip_hash = archive._sha(verified_zip)
        public, records = _extract(verified_zip, stage / 'payload')
        template = TEMPLATE.read_bytes()
        builder_hash = archive._sha(Path(__file__))
        template_hash = hashlib.sha256(template).hexdigest()
        text, bundle_id = _render(public, records, template)
        script = stage / 'setup.iss'
        script.write_text(text, encoding='utf-8-sig')
        report = {
            'schema_version': 1, 'status': 'prepared_not_compiled', 'app_id': APP_ID,
            'bundle_id': bundle_id, 'bundle_source_revision': public['source_revision'],
            'baseline_revision': public['base_revision'],
            'revision_provenance': 'caller_supplied_build_record',
            'input_build_manifest_sha256': bundle_id, 'verified_zip_sha256': zip_hash,
            'inventory': list(records.values()), 'compiler': compiler_record,
            'compile_exit_code': None, 'installer': None,
            'tools': {'builder_sha256': builder_hash,
                      'template_sha256': template_hash,
                      'rendered_script_sha256': archive._sha(script)},
            'compile_command': ['ISCC.exe', 'setup.iss'],
            'install_scope': 'current_user',
            'workspace': '{localappdata}/CAELAB/Workbench/Workspace',
            'windows_install_uninstall_upgrade_validation': 'not_performed',
            'redistribution_approval': 'not_granted_by_installer_builder',
        }
        if not prepare_only:
            _run_compiler(compiler, script, timeout)
            compiled = stage / 'compiler-output' / INSTALLER
            archive._no_link_parents(compiled)
            if not compiled.is_file():
                raise ValueError('Compiler did not produce the expected Windows installer')
            with compiled.open('rb') as stream:
                if stream.read(2) != b'MZ':
                    raise ValueError('Compiler output is not a Windows executable')
            _check_snapshot(stage / 'payload', records)
            if archive._sha(verified_zip) != zip_hash or archive._sha(compiler) != compiler_record['sha256']:
                raise ValueError('Verified input or compiler changed during compilation')
            final = stage / INSTALLER
            shutil.move(str(compiled), final)
            digest = archive._sha(final)
            (stage / (INSTALLER + '.sha256')).write_text(f'{digest}  {INSTALLER}\n', encoding='ascii')
            report.update(status='compiled_not_installed', compile_exit_code=0,
                          installer={'path': INSTALLER, 'bytes': final.stat().st_size, 'sha256': digest})
        if (archive._sha(TEMPLATE) != template_hash or archive._sha(Path(__file__)) != builder_hash
                or archive._sha(script) != report['tools']['rendered_script_sha256']):
            raise ValueError('Installer build sources changed during preparation')
        # These are owned temporary children, never the caller's candidate.
        for child in ('verified', 'compiler-output'):
            target = stage / child
            if target.exists():
                if target.resolve().parent != stage.resolve():
                    raise ValueError('Unexpected staging location')
                shutil.rmtree(target)
        if not prepare_only:
            payload = stage / 'payload'
            if payload.resolve().parent != stage.resolve():
                raise ValueError('Unexpected payload cleanup location')
            shutil.rmtree(payload)
        (stage / 'installer-manifest.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        archive._publish(stage, output)
        return output
    finally:
        if stage.exists():
            if stage.resolve().parent != output.parent.resolve():
                raise ValueError('Unexpected staging cleanup location')
            shutil.rmtree(stage)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--compiler', type=Path)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = build_installer(args.manifest, args.output, compiler=args.compiler, prepare_only=args.prepare_only)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, subprocess.SubprocessError) as error:
        print(f'Installer preparation failed: {error}', file=sys.stderr)
        return 1
    print(f'Installer output: {result}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
