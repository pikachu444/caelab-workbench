"""Prep handoff preserves file roles and uses the existing external launcher."""
import json
import os
from types import SimpleNamespace
import zipfile

import pytest

from caelab.connections import PROGRAMS, launch_request, program_statuses
from caelab.workbench import Workbench


def native_document(path, **extra):
    # Only the codec discriminator belongs to Workbench; this is not a model.
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"format": "caelab-prep-project", **extra}))
    return path


@pytest.fixture
def prep_status(tmp_path, monkeypatch):
    executable = tmp_path / ("caelab-prep.exe" if os.name == "nt" else "caelab-prep")
    executable.write_text("launcher probe", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("CAELAB_PREP", str(executable))
    return next(status for status in program_statuses() if status.spec.id == "caelab_prep")


@pytest.mark.parametrize("suffix", [".rad", ".caeprep"])
def test_prep_discovery_and_native_file_argv(prep_status, tmp_path, suffix):
    source = tmp_path / ("한글 입력 공백" + suffix)
    if suffix == ".caeprep":
        native_document(source, version=123)  # Payload/version validation stays in Prep.
    else:
        source.write_text("/BEGIN\nuser native deck\n/END\n")
    request = launch_request(prep_status, source)
    assert prep_status.available and prep_status.source == "environment"
    assert prep_status.spec.role == "preprocessor"
    assert request.argv == [prep_status.path, "gui", str(source.resolve())]
    assert request.cwd == tmp_path and request.program_id == "caelab_prep"
    assert request.env["CAELAB_PREP"] == prep_status.path
    assert next(spec for spec in PROGRAMS if spec.id == "prepomax").role == "preprocessor"


def test_prep_invalid_explicit_install_does_not_fall_back(tmp_path, monkeypatch):
    monkeypatch.setenv("CAELAB_PREP", str(tmp_path / "not-installed"))
    status = next(status for status in program_statuses() if status.spec.id == "caelab_prep")
    assert not status.available and status.source == "environment"


@pytest.mark.parametrize("case", ["missing", "unsupported", "broken", "no_manifest", "invalid_json", "neutral", "large_manifest"])
def test_unusable_or_non_rad_documents_are_not_launched(prep_status, tmp_path, case):
    source = tmp_path / ("document.step" if case == "unsupported" else "document.caeprep")
    if case == "unsupported":
        source.write_text("STEP")
    elif case == "broken":
        source.write_bytes(b"not a ZIP document")
    elif case != "missing":
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            if case == "no_manifest":
                archive.writestr("other.json", "{}")
            elif case == "invalid_json":
                archive.writestr("manifest.json", "{ invalid")
            elif case == "neutral":
                archive.writestr("manifest.json", json.dumps({"format": "caelab-material-curves"}))
            else:
                archive.writestr("manifest.json", json.dumps({"format": "caelab-prep-project", "large": "x" * (1024 * 1024)}))
    with pytest.raises(ValueError) as error:
        launch_request(prep_status, source)
    if case == "large_manifest":
        assert "1 MiB" in str(error.value)
    elif case == "neutral":
        assert "중립" in str(error.value)


def test_prep_document_input_export_and_changes_survive_restart(prep_status, tmp_path, monkeypatch):
    wb = Workbench()
    try:
        project = wb.new_native_project()
        pre = wb.add_program_node(project, "preprocessor", "caelab_prep")
        solver = wb.add_program_node(project, "analysis", "opencourant")
        source = tmp_path / "original.rad"
        source.write_text("original RAD")
        document = native_document(tmp_path / "work.caeprep")
        exported = tmp_path / "export.rad"
        exported.write_text("exported RAD")
        units = {"length": "mm", "time": "s"}
        wb.set_node_file(project, pre.id, source, "input", units)
        wb.set_node_file(project, pre.id, document, "document")
        wb.set_node_file(project, pre.id, exported, "output", units)
        wb.connect_nodes(project, pre.id, solver.id)
        saved = wb.save_native_project(project, tmp_path / "project.json")
        project = wb.open_native_project(saved)
        pre = project.nodes[pre.id]
        assert wb.input_file(project, pre.id).path == str(source)
        assert wb.input_file(project, solver.id).path == str(exported)
        assert pre.files["output"].units == units
        assert pre.files["document"].path == str(document)
        with pytest.raises(ValueError, match="역할"):
            wb.connect_nodes(project, pre.id, solver.id, "document")

        launches = []
        monkeypatch.setattr(wb.jobs, "launch", lambda request: (launches.append(request) or SimpleNamespace(state="opened")))
        wb.open_program(project, pre.id)
        wb.open_program(project, pre.id, use_input=True)
        assert [request.argv[-1] for request in launches] == [str(document), str(source)]

        original_revision = pre.files["document"].revision
        native_document(document, revision=1)
        wb.refresh_native_project(project)
        assert pre.files["output"].stale
        assert project.nodes[solver.id].state == "stale"
        wb.open_program(project, pre.id)
        assert pre.state == "changed"
        assert pre.files["document"].revision == original_revision
        wb.set_node_file(project, pre.id, document, "document")
        assert pre.files["output"].stale  # Saving the document is not exporting RAD.
        assert wb.input_file(project, pre.id).path == str(source)
        exported.write_text("new exported RAD")
        wb.set_node_file(project, pre.id, exported, "output", units)
        assert not pre.files["output"].stale
        assert source.read_text() == "original RAD"
    finally:
        wb.close()


def test_wrong_prep_codec_fails_before_job_manager(prep_status, tmp_path, monkeypatch):
    wb = Workbench()
    try:
        project = wb.new_native_project()
        pre = wb.add_program_node(project, "preprocessor", "caelab_prep")
        document = native_document(tmp_path / "neutral.caeprep", format="neutral-curves")
        wb.set_node_file(project, pre.id, document, "document")
        monkeypatch.setattr(wb.jobs, "launch", lambda request: pytest.fail("Wrong codec reached the process manager"))
        with pytest.raises(ValueError, match="중립"):
            wb.open_program(project, pre.id)
        assert pre.state == "failed"
    finally:
        wb.close()


def test_project_ui_offers_prep_and_keeps_file_roles(prep_status, tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui.ini"))
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from apps.workbench_qt.project_window import ProjectWindow, _problem_text
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.Ok)
    window = ProjectWindow()
    try:
        box = window.add_box("preprocessor")
        assert window.project.nodes[box.node_id].program_id == "prepomax"
        window.scene.clearSelection()
        box.setSelected(True)
        window._selection_changed()
        choice = window.program_choice.findData("caelab_prep")
        assert choice >= 0
        window.program_choice.setCurrentIndex(choice)
        assert window.project.nodes[box.node_id].program_id == "caelab_prep"
        document = native_document(tmp_path / "작업 문서.caeprep")
        seen_filters = []
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (seen_filters.append(args[3]) or (str(document), "")))
        monkeypatch.setattr(window, "_ask_file_units", lambda *args: pytest.fail("Document is not a physical exchange file"))
        assert window.choose_file("document")
        assert "*.caeprep" in seen_filters[0]
        assert "따로 등록" in window.document_hint.text()
        assert window.open_button.text() == "CAELab Prep 편집 문서 열기"
        assert window.project.nodes[box.node_id].files["document"].path == str(document)
        assert "input" not in window.project.nodes[box.node_id].files
        monkeypatch.setattr(window, "_ask_file_units", lambda *args: {"length": "mm"})
        for role in ("input", "output"):
            target = tmp_path / (role + ".rad")
            target.write_text("user RAD " + role)
            monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, target=target: (seen_filters.append(args[3]) or (str(target), "")))
            assert window.choose_file(role)
            assert "*.rad" in seen_filters[-1]
            assert window.project.nodes[box.node_id].files[role].path == str(target)
        assert window.project.nodes[box.node_id].files["document"].path == str(document)
        assert "설치" in _problem_text(ValueError("CAELab Prep 실행 파일을 찾지 못했습니다 (environment)."))
        window.show()
        app.processEvents()
    finally:
        window._dirty = False
        window.close()
        app.processEvents()
