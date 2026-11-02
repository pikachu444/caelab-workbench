"""Provenance for the two optional packages observed in the Windows candidate.

This is not an inventory or license review of all bundled dependencies.
"""

from email.parser import BytesParser
import hashlib
from importlib import metadata
from pathlib import Path, PurePosixPath
import sys


PACKAGES = ('torch', 'torchvision')
SCOPE = 'torch_and_torchvision_only'


def _relative(value):
    if not isinstance(value, str) or not value.strip() or '\0' in value:
        raise ValueError(f'Invalid package-relative path: {value!r}')
    path = PurePosixPath(value.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in str(path):
        raise ValueError(f'Path is outside the package: {value}')
    return path.as_posix()


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def collect_optional_metadata(analysis, *, copy_metadata, expand_datas,
                              distribution=metadata.distribution):
    """Validate actual Analysis origins, then add owned dist-info before COLLECT."""
    report = {'schema_version': 1, 'scope': SCOPE, 'distributions': []}
    additions = []
    entries = [(kind, name, Path(source).resolve())
               for kind, values in (('pure', analysis.pure), ('binaries', analysis.binaries), ('datas', analysis.datas))
               for name, source, _ in values]
    existing = {}
    for name, source, _ in [*analysis.binaries, *analysis.datas]:
        existing.setdefault(_relative(name).casefold(), set()).add(Path(source).resolve())
    for package in PACKAGES:
        hinted = set()
        for entry in entries:
            kind, name, source = entry
            target = name.replace('\\', '/')
            prefix = (name == package or name.startswith(package + '.')) if kind == 'pure' else target.startswith(package + '/')
            if prefix:
                hinted.add(entry)
        try:
            installed = distribution(package)
        except metadata.PackageNotFoundError as error:
            import_packages = {(Path(root or '.').resolve() / package).resolve() for root in sys.path}
            if hinted or any(source.is_relative_to(root) for _, _, source in entries for root in import_packages):
                raise ValueError(f'{package}: metadata for collected files is missing') from error
            continue
        files = installed.files or []
        owned = {Path(installed.locate_file(item)).resolve(): str(item).replace('\\', '/') for item in files}
        collected = [entry for entry in entries if entry[2] in owned or entry in hinted]
        if not collected:
            continue
        version = installed.metadata.get('Version')
        if installed.metadata.get('Name', '').casefold() != package or not isinstance(version, str) or not version.strip():
            raise ValueError(f'{package}: package metadata Name/Version is missing or ambiguous')
        if not files:
            raise ValueError(f'{package}: installed file ownership is unavailable')
        evidence = []
        for kind, name, source in collected:
            if source not in owned:
                raise ValueError(f'{package}: Analysis source belongs to another installation: {source}')
            evidence.append({'kind': kind, 'identifier': name, 'source_relative': owned[source],
                             'source_sha256': _sha(source)})
        metadata_files = [path for path, relative in owned.items()
                          if relative.endswith('.dist-info/METADATA')]
        if len(metadata_files) != 1:
            raise ValueError(f'{package}: one owned dist-info/METADATA is required')
        metadata_path = metadata_files[0]
        metadata_root = metadata_path.parent
        copies = copy_metadata(package, recursive=False)
        if len(copies) != 1 or Path(copies[0][0]).resolve() != metadata_root:
            raise ValueError(f'{package}: copied metadata belongs to another installation')
        destination = _relative(copies[0][1])
        declarations = installed.metadata.get_all('License-File') or []
        if not declarations:
            raise ValueError(f'{package}: declared license files are missing')
        licenses = []
        for declared in declarations:
            declared = _relative(declared)
            candidates = {path.resolve() for path in (metadata_root / declared, metadata_root / 'licenses' / declared)
                          if path.resolve() in owned and path.is_file()}
            if len(candidates) != 1:
                raise ValueError(f'{package}: missing or ambiguous declared license: {declared}')
            path = candidates.pop()
            if not path.is_relative_to(metadata_root):
                raise ValueError(f'{package}: license is outside owned metadata: {declared}')
            licenses.append({'declared_path': declared,
                             'path': '_internal/' + destination + '/' + path.relative_to(metadata_root).as_posix(),
                             'sha256': _sha(path)})
        expanded = [(target, source, 'DATA') for target, source in sorted(expand_datas(copies))]
        destinations = {}
        for target, source, _ in expanded:
            target = _relative(target)
            source = Path(source).resolve()
            if not source.is_relative_to(metadata_root) or source not in owned:
                raise ValueError(f'{package}: unowned metadata source: {source}')
            key = target.casefold()
            if existing.get(key, set()) - {source}:
                raise ValueError(f'{package}: conflicting Windows bundle destination: {target}')
            existing.setdefault(key, set()).add(source)
            destinations['_internal/' + target] = source
            additions.append((target, str(source), 'DATA'))
        metadata_record = {'path': '_internal/' + destination + '/METADATA', 'sha256': _sha(metadata_path)}
        for record in [metadata_record, *licenses]:
            if record['path'] not in destinations or _sha(destinations[record['path']]) != record['sha256']:
                raise ValueError(f'{package}: metadata copy omits a required file: {record["path"]}')
        report['distributions'].append({'name': package, 'version': version,
                                        'analysis_files': evidence, 'metadata': metadata_record,
                                        'licenses': licenses})
    analysis.datas.extend(additions)
    return report


def verify_optional_metadata(bundle, report):
    """Compare generated provenance with final copied bytes; never approve old E."""
    root = Path(bundle).resolve()
    if not isinstance(report, dict) or report.get('schema_version') != 1 or report.get('scope') != SCOPE:
        raise ValueError('Optional package provenance scope/schema is missing')
    packages = report.get('distributions')
    if not isinstance(packages, list):
        raise ValueError('Optional distribution evidence is missing')
    names = [item['name'] for item in packages]
    if len(set(names)) != len(names) or set(names) - set(PACKAGES):
        raise ValueError('Ambiguous optional distribution names')
    for package in PACKAGES:
        if (root / '_internal' / package).exists() and package not in names:
            raise ValueError(f'{package}: collected files have no distribution evidence')
    for package in packages:
        if not package.get('analysis_files') or not package.get('licenses'):
            raise ValueError(f'{package["name"]}: collection/license evidence is missing')
        for record in [package['metadata'], *package['licenses']]:
            path = (root / _relative(record['path'])).resolve()
            if not path.is_relative_to(root) or _sha(path) != record['sha256']:
                raise ValueError(f'Bundled metadata/license bytes differ: {record["path"]}')
        raw = (root / package['metadata']['path']).read_bytes()
        headers = BytesParser().parsebytes(raw)
        if headers.get('Name', '').casefold() != package['name'] or headers.get('Version') != package['version']:
            raise ValueError(f'{package["name"]}: bundled metadata Name/Version differs')
        if sorted(headers.get_all('License-File') or []) != sorted(item['declared_path'] for item in package['licenses']):
            raise ValueError(f'{package["name"]}: bundled license declarations differ')
