"""Portable project references and coherent Save As snapshots; providers are mocked."""

import json
from pathlib import Path
import shutil
import threading

import pytest
from filelock import FileLock

from caelab.assist import discussions, experts
from caelab.assist.knowledge import KnowledgeSearchResult
from caelab.native.evidence import import_file_evidence, verify_evidence
from caelab.native.run_store import RunStore
from caelab.project import load_native_project, save_native_project
from caelab.workbench import Workbench
from test_native_evidence import make_run


def research(tmp_path):
    folder = tmp_path / '원래 프로젝트'
    folder.mkdir()
    run = make_run(folder)
    wb = Workbench()
    project = wb.new_native_project('이동할 연구')
    node = wb.add_program_node(project, 'analysis', 'calculix')
    wb.set_node_file(project, node.id, run.input_path)
    wb.set_node_file(project, node.id, run.workdir / 'model.frd', role='output')
    run.node_id = node.id
    save_native_project(project, folder / 'project.json')
    store = RunStore(project.run_store, expected=True)
    store.write(run)
    store.select(node.id, run.id)
    path = store.root / 'discussions/expert/discussion.json'
    document = discussions.create_discussion(path)
    evidence = import_file_evidence(path.parent / 'assets', files={'log': Path(run.log_path)}, solver_id='calculix')
    document = discussions.attach_evidence(path, evidence, expected_revision=document['revision'])
    save_native_project(project)
    return wb, project, node, run, path, document, evidence


def test_physical_folder_move_resolves_internal_files_and_preserves_history_without_calls(tmp_path, monkeypatch):
    import subprocess
    import urllib.request
    wb, project, node, run, docpath, document, evidence = research(tmp_path)
    external = tmp_path / 'external.FCStd'
    external.write_bytes(b'external original')
    geometry = wb.add_program_node(project, 'geometry', 'freecad')
    wb.set_node_file(project, geometry.id, external)
    save_native_project(project)
    serialized = json.loads(project.path.read_text(encoding='utf-8'))
    assert not Path(serialized['nodes'][node.id]['files']['input']['path']).is_absolute()
    assert not Path(serialized['nodes'][node.id]['files']['output']['path']).is_absolute()
    assert Path(serialized['nodes'][geometry.id]['files']['input']['path']).is_absolute()
    original_root = project.path.parent
    moved_root = tmp_path / '옮긴 프로젝트 공백'
    original_root.rename(moved_root)
    def forbidden(*args, **kwargs):
        pytest.fail('Reopen must not start a process/provider/network call')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(experts, 'get_provider', forbidden)
    reopened = load_native_project(moved_root / 'project.json')
    assert not reopened.issues
    assert Path(reopened.nodes[node.id].files['input'].path).is_relative_to(moved_root)
    assert Path(reopened.nodes[node.id].files['output'].path).is_file()
    assert reopened.nodes[geometry.id].files['input'].path == str(external.resolve())
    records = RunStore(reopened.run_store, expected=True).runs()
    assert records[0].id == run.id and records[0].workdir.parent == reopened.run_store
    # Historical source paths retain the location used at execution time.
    assert records[0].input_path == run.input_path
    new_doc = reopened.run_store / docpath.relative_to(project.run_store)
    assert discussions.load_discussion(new_doc) == document
    assert verify_evidence(evidence, new_doc.parent / 'assets') == []


def test_save_as_copies_owned_documents_assets_but_keeps_ordinary_native_originals(tmp_path):
    wb, project, node, run, docpath, document, evidence = research(tmp_path)
    original_input = project.nodes[node.id].files['input'].path
    source_store, original_json = project.run_store, project.path
    destination = tmp_path / 'new project/copy.json'
    save_native_project(project, destination)
    assert project.nodes[node.id].files['input'].path == original_input
    assert Path(original_input).is_file()
    data = json.loads(destination.read_text(encoding='utf-8'))
    assert Path(data['nodes'][node.id]['files']['input']['path']).is_absolute()
    assert data['nodes'][node.id]['files']['output']['path'].startswith('copy.runs/')
    copied_doc = project.run_store / docpath.relative_to(source_store)
    copy_document = discussions.load_discussion(copied_doc)
    assert copy_document == document
    assert verify_evidence(evidence, copied_doc.parent / 'assets') == []
    copy_document['constraints'] = ['new project only']
    discussions.save_discussion(copied_doc, copy_document, expected_revision=copy_document['revision'])
    assert discussions.load_discussion(docpath)['constraints'] == []
    assert load_native_project(original_json).run_store == source_store


def test_missing_expected_store_is_an_issue_and_never_a_successful_empty_history(tmp_path):
    _, project, _, _, _, _, _ = research(tmp_path)
    missing = project.run_store
    held = missing.with_name('preserved-store')
    missing.rename(held)
    reopened = load_native_project(project.path)
    assert any(str(missing) in issue and '기대 실행 저장소' in issue for issue in reopened.issues)
    assert reopened.refresh() == reopened.issues
    assert len(reopened.refresh()) == len(reopened.issues)  # no accumulating duplicates
    store = RunStore(reopened.run_store, expected=reopened.run_store_expected)
    store.recover()
    assert not missing.exists()
    with pytest.raises(ValueError, match='기대 실행 저장소'):
        store.runs()
    with pytest.raises(ValueError, match='기대 실행 저장소'):
        save_native_project(reopened)
    held.rename(missing)
    restored_issues = reopened.refresh()
    assert not any('기대 실행 저장소' in issue or '기록된 실행 파일이 없습니다' in issue for issue in restored_issues)


@pytest.mark.parametrize('missing_kind', ['output', 'selected_record'])
def test_missing_expected_result_or_selected_record_is_not_normal_history(tmp_path, missing_kind):
    _, project, _, run, _, _, _ = research(tmp_path)
    if missing_kind == 'output':
        (run.workdir / 'results/frame-000001.vtu').unlink()
    else:
        (run.workdir / 'job.json').unlink()
    reopened = load_native_project(project.path)
    assert reopened.issues
    with pytest.raises(ValueError, match='없습니다'):
        RunStore(reopened.run_store, expected=True).runs()


def test_legacy_empty_project_without_expected_marker_remains_readable(tmp_path):
    path = tmp_path / 'old.json'
    path.write_text(json.dumps({'schema_version': 1, 'name': 'legacy', 'nodes': {}, 'connections': []}), encoding='utf-8')
    project = load_native_project(path)
    assert not project.run_store_expected and not project.issues
    assert RunStore(project.run_store).runs() == []
    save_native_project(project)
    assert project.run_store_expected and project.run_store.is_dir()


def test_initial_save_creates_expected_empty_store_and_failure_only_removes_new_empty_store(tmp_path, monkeypatch):
    import caelab.project as storage
    wb = Workbench()
    project = wb.new_native_project()
    destination = tmp_path / 'fresh.json'
    def denied(*args):
        raise OSError('injected document commit failure')
    monkeypatch.setattr(storage.os, 'replace', denied)
    with pytest.raises(OSError, match='injected'):
        save_native_project(project, destination)
    assert project.path is None and not project.run_store_expected
    assert not destination.exists() and not destination.with_suffix('.runs').exists()


@pytest.mark.parametrize('lock_suffix', ['.lock', '.request.lock'])
def test_save_as_refuses_existing_writer_or_live_request_lock(tmp_path, lock_suffix):
    _, project, _, _, docpath, _, _ = research(tmp_path)
    before = docpath.read_bytes()
    destination = tmp_path / 'blocked.json'
    with FileLock(str(docpath) + lock_suffix, timeout=0):
        with pytest.raises(ValueError, match='대화 질문/저장'):
            save_native_project(project, destination)
    assert docpath.read_bytes() == before
    assert not destination.exists() and not destination.with_suffix('.runs').exists()


def test_save_as_refuses_actual_live_mocked_ask_and_preserves_pending_source(tmp_path, monkeypatch):
    _, project, _, _, docpath, document, evidence = research(tmp_path)
    entered, release = threading.Event(), threading.Event()
    class Provider(experts.ExpertProvider):
        id, name = 'test', 'mock'
        def availability(self):
            return True, 'test'
        def discuss(self, prompt):
            entered.set()
            assert release.wait(5)
            return 'mock reply'
    monkeypatch.setattr(experts, 'get_provider', lambda *_: Provider())
    monkeypatch.setattr(experts, 'search_knowledge_result', lambda *args, **kwargs: KnowledgeSearchResult('no_material', [], 'calculix'))
    errors = []
    def ask():
        try:
            discussions.ask_discussion(docpath, 'question', evidence_id=evidence['id'], template_id='interpretation', expected_revision=document['revision'])
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=ask)
    thread.start()
    try:
        assert entered.wait(5)
        before = docpath.read_bytes()
        with pytest.raises(ValueError, match='대화 질문/저장'):
            save_native_project(project, tmp_path / 'blocked.json')
        assert docpath.read_bytes() == before
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive() and not errors


@pytest.mark.parametrize('change', ['source_asset', 'copied_asset', 'new_document'])
def test_copy_detects_concurrent_staging_or_corruption_and_rolls_back_only_created_target(tmp_path, monkeypatch, change):
    _, project, _, _, docpath, _, evidence = research(tmp_path)
    original_project_path, original_store = project.path, project.run_store
    original_doc = docpath.read_bytes()
    asset_relative = docpath.parent.relative_to(project.run_store) / 'assets' / evidence['files'][0]['stored_path']
    destination = tmp_path / 'failed-copy.json'
    destination.write_bytes(b'existing destination document')
    original_copytree = shutil.copytree
    def changed(source, target, *args, **kwargs):
        result = original_copytree(source, target, *args, **kwargs)
        if Path(source) == original_store:
            if change == 'source_asset':
                (original_store / asset_relative).write_bytes(b'import still writing')
            elif change == 'copied_asset':
                (Path(target) / asset_relative).write_bytes(b'corrupt copied bytes')
            else:
                (original_store / 'discussions/new.json').write_text('{}')
        return result
    monkeypatch.setattr(shutil, 'copytree', changed)
    with pytest.raises(ValueError, match='변경'):
        save_native_project(project, destination)
    assert project.path == original_project_path and project.run_store == original_store
    assert docpath.read_bytes() == original_doc
    assert destination.read_bytes() == b'existing destination document'
    assert not destination.with_suffix('.runs').exists()


def test_missing_referenced_discussion_asset_refuses_save_as(tmp_path):
    _, project, _, _, docpath, _, evidence = research(tmp_path)
    (docpath.parent / 'assets' / evidence['files'][0]['stored_path']).unlink()
    with pytest.raises(ValueError, match='대화 근거 자산'):
        save_native_project(project, tmp_path / 'missing-assets.json')
    assert not (tmp_path / 'missing-assets.runs').exists()


def test_partial_or_wrong_job_json_is_an_explicit_resume_issue(tmp_path):
    _, project, _, run, _, _, _ = research(tmp_path)
    (run.workdir / 'job.json').write_text('[]', encoding='ascii')
    reopened = load_native_project(project.path)
    assert any('실행 기록' in issue for issue in reopened.issues)
    with pytest.raises(ValueError, match='실행 기록'):
        RunStore(project.run_store, expected=True).runs()


def test_explicit_store_outside_project_root_stays_an_absolute_reference(tmp_path):
    wb = Workbench()
    project = wb.new_native_project()
    project.run_store = tmp_path / 'external owned store'
    path = save_native_project(project, tmp_path / 'documents/project.json')
    data = json.loads(path.read_text(encoding='utf-8'))
    assert Path(data['run_store']).is_absolute()
    assert load_native_project(path).run_store == project.run_store


@pytest.mark.parametrize('mutation,field', [
    (lambda snapshot: snapshot.update(outputs=None), 'snapshot.outputs'),
    (lambda snapshot: snapshot.update(outputs=[]), 'snapshot.outputs'),
    (lambda snapshot: snapshot.update(artifacts=['model.frd']), 'snapshot.artifacts'),
    (lambda snapshot: snapshot.update(outputs={'model.frd': None}), 'snapshot.outputs.model.frd'),
    (lambda snapshot: snapshot.update(outputs={'\x00bad': 'sha'}), 'snapshot.outputs'),
    (lambda snapshot: snapshot.update(files=None), 'snapshot.files'),
    (lambda snapshot: snapshot.update(files={}), 'snapshot.files'),
    (lambda snapshot: snapshot.update(files=[1]), 'snapshot.files[0]'),
    (lambda snapshot: snapshot.update(files=[{}]), 'snapshot.files[0].source_copy'),
    (lambda snapshot: snapshot['files'][0].update(source_copy=[]), 'snapshot.files[0].source_copy'),
    (lambda snapshot: snapshot['files'][0].update(execution_path=None), 'snapshot.files[0].execution_path'),
    (lambda snapshot: snapshot['files'][0].update(execution_path='\x00bad'), 'snapshot.files[0].execution_path'),
    (lambda snapshot: snapshot['files'][0].update(source_path=[]), 'snapshot.files[0].source_path'),
    (lambda snapshot: snapshot['files'][0].update(stat=None), 'snapshot.files[0].stat'),
    (lambda snapshot: snapshot.update(prerequisites=None), 'snapshot.prerequisites'),
    (lambda snapshot: snapshot.update(prerequisites=[1]), 'snapshot.prerequisites[0]'),
    (lambda snapshot: snapshot.update(prerequisites=[{'source_path': [], 'stat': {}}]), 'snapshot.prerequisites[0].source_path'),
    (lambda snapshot: snapshot.update(prerequisites=[{'source_path': '/original/file', 'stat': []}]), 'snapshot.prerequisites[0].stat'),
])
def test_malformed_saved_manifest_has_specific_issues_instead_of_uncaught_status_error(tmp_path, mutation, field):
    _, project, node, run, _, _, _ = research(tmp_path)
    record = run.workdir / 'job.json'
    data = json.loads(record.read_text(encoding='utf-8'))
    mutation(data['snapshot'])
    record.write_text(json.dumps(data), encoding='utf-8')
    reopened = Workbench().open_native_project(project.path)
    assert any(field in issue for issue in reopened.issues)
    with pytest.raises(ValueError, match='실행 기록을 온전히 읽을 수 없습니다'):
        Workbench().native_runs(reopened, node.id)
