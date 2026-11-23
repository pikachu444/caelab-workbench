"""P0 Windows entry points and data passed to the normal Qt frame viewer."""
from __future__ import annotations

import hashlib
import json
import marshal
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = "caelab-install-registry.json"


def test_packaging_declares_material_tool_and_console_entry():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"]["caelab-workbench"] == "apps.workbench_qt.__main__:main"
    from scripts.build_windows_bundle import DISTRIBUTIONS

    assert "pyvisco" in DISTRIBUTIONS
    spec = (ROOT / "scripts/workbench_bundle.spec").read_text(encoding="utf-8")
    for name in ("pyvisco", "pyvisco.prony", "pyvisco.shift", "apps.workbench_qt.material_study"):
        assert f'"{name}"' in spec
    assert 'module_collection_mode={"pyvisco": "pyz+py"}' in spec


def test_desktop_material_dispatch_and_environment_registry(tmp_path, monkeypatch):
    from apps.workbench_qt import desktop_entry

    exe = tmp_path / "venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True)
    entry = tmp_path / "apps" / "workbench_qt" / "desktop_entry.py"
    local, beside_exe = tmp_path / REGISTRY, exe.parent / REGISTRY
    local.touch()
    beside_exe.touch()
    monkeypatch.setattr(desktop_entry.sys, "executable", str(exe))
    monkeypatch.setattr(desktop_entry, "__file__", str(entry))
    monkeypatch.delenv("CAELAB_INSTALL_REGISTRY", raising=False)
    calls = []
    monkeypatch.setattr(desktop_entry, "import_module", lambda name: SimpleNamespace(
        main=lambda argv: calls.append((name, argv, os.environ["CAELAB_INSTALL_REGISTRY"])) or 0))
    module = "apps.workbench_qt.material_study"
    assert desktop_entry.main(["--caelab-module", module]) == 0
    assert calls == [(module, [], str(beside_exe))]
    monkeypatch.setenv("CAELAB_INSTALL_REGISTRY", str(local))
    assert desktop_entry.main(["--caelab-solver-gate", "arg"]) == 0
    assert calls[-1] == ("caelab.native.windows_gate", ["arg"], str(local))


def test_desktop_gate_remains_qt_free_with_repo_registry(tmp_path):
    (tmp_path / REGISTRY).touch()
    script = """
import os, pathlib, sys
from apps.workbench_qt import desktop_entry
root = pathlib.Path(sys.argv[1])
desktop_entry.__file__ = str(root / 'apps/workbench_qt/desktop_entry.py')
sys.executable = str(root / 'missing/python.exe')
assert desktop_entry.main(['--caelab-solver-gate']) == 2
assert pathlib.Path(os.environ['CAELAB_INSTALL_REGISTRY']) == root / 'caelab-install-registry.json'
assert not any(name.startswith('PySide6') for name in sys.modules)
"""
    env = {key: value for key, value in os.environ.items() if key != "CAELAB_INSTALL_REGISTRY"}
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], cwd=ROOT, env=env,
                            capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr.decode()


def test_prony_hashes_frozen_code_when_source_is_absent(tmp_path, monkeypatch):
    pytest.importorskip("pyvisco")
    from pyvisco import prony, shift
    from caelab.materials.adapters import prony_trs

    assert prony_trs.verify_pyvisco_sources()["source_match"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    hashes = {}
    for module in (prony, shift, prony_trs):
        code = compile("value = 42", module.__name__, "exec")
        hashes[module.__name__] = hashlib.sha256(marshal.dumps(code)).hexdigest()
        monkeypatch.setattr(module, "__file__", str(tmp_path / f"{module.__name__}.py"))
        monkeypatch.setattr(module, "__loader__", SimpleNamespace(get_code=lambda _, code=code: code))
    status = prony_trs.verify_pyvisco_sources()
    assert not status["source_match"]
    for module, check in zip((prony, shift), status["loaded_source_checks"]):
        assert check["loaded_file_sha256"] == hashes[module.__name__]
        assert check["loaded_file_path"] == f"frozen://{module.__name__}"
    assert len(prony_trs.PronyTRSAdapter()._impl_sha) == 64
    monkeypatch.delattr(prony, "__file__")
    monkeypatch.setattr(prony, "__loader__", None)
    assert prony_trs.verify_pyvisco_sources()["prony_sha256"] == ""


def test_opencourant_has_windows_starter_name():
    from caelab.connections import PROGRAMS

    spec = next(item for item in PROGRAMS if item.id == "opencourant")
    assert "starter_win64.exe" in spec.executables


@pytest.fixture
def qt_app(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui.ini"))
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_material_study_menu_loads_korean_font_and_readable_provenance(qt_app, monkeypatch):
    pytest.importorskip("pyvisco")
    import matplotlib
    from PySide6.QtCore import Qt, QCoreApplication, QEvent
    from PySide6.QtWidgets import QDialog, QFormLayout, QLabel
    from apps.workbench_qt.project_window import ProjectWindow

    project = ProjectWindow()
    assert not project.toolbox_dock.isHidden()
    assert project.material_study_action in project.tools_menu.actions()
    with matplotlib.rc_context({"font.family": ["DejaVu Sans"]}):
        project.material_study_action.trigger()
        study, = project._material_study_windows
        assert study.isVisible() and study.isWindow() and not study.isModal()
        assert study.testAttribute(Qt.WA_DeleteOnClose)
        assert matplotlib.rcParams["font.family"][0] == "Malgun Gothic"

        inspected = []

        def inspect(dialog):
            form = dialog.findChild(QFormLayout)
            rows = [(form.itemAt(row, QFormLayout.LabelRole).widget().text(),
                     form.itemAt(row, QFormLayout.FieldRole).widget().text()) for row in range(form.rowCount())]
            visible = "\n".join(label.text() for label in dialog.findChildren(QLabel))
            inspected.append((rows, visible))
            return QDialog.Rejected

        monkeypatch.setattr(QDialog, "exec", inspect)
        study.act_provenance.trigger()
        (rows, visible), = inspected
        assert rows == [
            ("연구 폴더명", "untitled.caemat.json"),
            ("재료 모델", "B-VIS-TRS-1 (점탄성 2항 Prony + WLF)"),
            ("계산 엔진", "caelab.prony_trs v1.0.0"),
            ("솔버 카드 내보내기", "지원"),
            ("열린 문서 적용", "미지원"),
        ]
        assert not re.search(r"[0-9a-f]{64}|\{|[\\/]", visible)
        assert study.close()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert project._material_study_windows == []
    project.close()


def test_program_settings_path_browse_save_and_reload(qt_app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QDialogButtonBox, QFileDialog, QLabel, QPushButton
    from apps.workbench_qt.project_window import ExternalProgramsDialog, ProjectWindow
    from caelab.connections import PROGRAMS
    from caelab.workbench import Workbench

    registry = tmp_path / REGISTRY
    saved = {
        "root": ".",
        "env": {"CAELAB_GMSH": "old/gmsh.exe"},
        "runtime_env": {"gmsh": {"CUSTOM_FLAG": "yes"}},
        "programs": {"gmsh": {"label": "keep"}, "other-program": {"path": "keep.exe"}},
    }
    registry.write_text(json.dumps(saved), encoding="utf-8")
    monkeypatch.setenv("CAELAB_INSTALL_REGISTRY", str(registry))
    monkeypatch.delenv("CAELAB_GMSH", raising=False)
    selected = tmp_path / "새 경로" / "gmsh.exe"
    selected.parent.mkdir()
    selected.touch()
    selected.chmod(0o755)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(selected), ""))
    observed = []

    def save_or_reopen(dialog):
        assert set(dialog.path_edits) == {spec.id for spec in PROGRAMS}
        edit = dialog.path_edits["gmsh"]
        observed.append(edit.text())
        if len(observed) == 1:
            browse = next(b for b in dialog.findChildren(QPushButton) if b.accessibleName() == "Gmsh 찾아보기")
            browse.click()
            assert edit.text() == str(selected)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
        else:
            assert any("시스템 환경변수 우선" in label.text() for label in dialog.findChildren(QLabel))
        return dialog.result()

    monkeypatch.setattr(ExternalProgramsDialog, "exec", save_or_reopen)
    window = ProjectWindow(Workbench())
    assert window.program_settings_action in window.tools_menu.actions()
    window.program_settings_action.trigger()
    assert window.workbench.registry_path == registry
    assert os.environ["CAELAB_INSTALL_REGISTRY"] == str(registry)
    assert "저장했습니다" in window.status.text()
    loaded = next(status for status in Workbench().programs() if status.spec.id == "gmsh")
    assert loaded.available and loaded.path == str(selected)
    document = json.loads(registry.read_text(encoding="utf-8"))
    assert document["env"]["CAELAB_GMSH"] == str(selected)
    assert document["runtime_env"] == saved["runtime_env"]
    assert document["programs"]["gmsh"] == {"path": str(selected), "label": "keep"}
    assert document["programs"]["other-program"] == saved["programs"]["other-program"]
    monkeypatch.setenv("CAELAB_GMSH", str(tmp_path / "system-gmsh.exe"))
    window.program_settings_action.trigger()
    assert observed == ["old/gmsh.exe", str(selected)]
    window.close()


LEGACY = """# vtk DataFile Version 3.0
Verified shape and vector fixture
ASCII
DATASET UNSTRUCTURED_GRID
FIELD FieldData 1
TIME 1 1 float
0.25
POINTS 4 float
1 2 2  1 0 0  0 1 0  0 0 1
CELLS 1 5
4 0 1 2 3
CELL_TYPES 1
10
POINT_DATA 4
SCALARS NODE_ID int
LOOKUP_TABLE default
20 10 30 40
VECTORS Displacement double
1 2 2  0 0 0  0 0 0  0 0 0
CELL_DATA 1
SCALARS ELEMENT_ID int
LOOKUP_TABLE default
72
"""


@pytest.mark.parametrize("source", ["ascii", "converter", "vtk", "vtu", "failure"])
def test_radioss_frame_contract(tmp_path, monkeypatch, source):
    from caelab.native.frame_view import read_native_frame
    from caelab.native.radioss import collect_radioss_results
    from caelab.native.run_store import NativeRun
    from caelab.result_readers import ResultError, vtk_fields
    from caelab import jobs

    directory = tmp_path / "project.runs" / ("a" * 32)
    directory.mkdir(parents=True)
    (directory / "test_0001.out").write_text("INTERNAL ENERGY = 24\n")
    binary = directory / "testA001"
    binary.write_bytes(b"\x00animation")
    installation = tmp_path / "solver"
    installation.mkdir()
    converter = installation / ("anim_to_vtk_win64.exe" if os.name == "nt" else "anim_to_vtk_linux64_gf")
    converter.touch()
    converter.chmod(0o755)
    monkeypatch.setenv("CAELAB_OPENCOURANT", str(installation))
    invocations = []

    def convert(executable, animation, target, *, check_cancelled):
        invocations.append((executable, animation))
        check_cancelled()
        if source == "failure":
            raise ResultError("CONVERTER_EXIT", "the converter failed")
        target.write_text(LEGACY, encoding="ascii")

    monkeypatch.setattr(jobs.JobManager, "run_animation_converter", staticmethod(convert))
    if source == "ascii":
        binary.write_text("NODE 20 0 0 0\nNODE 10 1 0 0\nNODE 30 0 1 0\nNODE 40 0 0 1\nTETRA 72 20 10 30 40\n")
    elif source == "vtk":
        binary.with_suffix(".vtk").write_text(LEGACY, encoding="ascii")
    elif source == "vtu":
        binary.with_suffix(".vtk").write_text(LEGACY, encoding="ascii")
        first_result = collect_radioss_results(directory)
        pre_rendered = directory / first_result["frames"][0]["vtu_file"]
        pre_rendered.rename(directory / "results/testA001.vtu")
        binary.with_suffix(".vtk").unlink()

    result = collect_radioss_results(directory)
    assert invocations == ([(converter, binary)] if source in {"converter", "failure"} else [])
    if source == "failure":
        assert result["status"] == "partial" and not result["frames"]
        assert result["native_files"] == ["test_0001.out", binary.name]
        assert "CONVERTER_EXIT" in result["warnings"][0]
        return
    assert result["status"] == "readable"
    frame, = result["frames"]
    relative = frame["vtu_file"]
    assert relative == frame["vtu"] == "results/frame-000000.vtu"
    digest = hashlib.sha256((directory / relative).read_bytes()).hexdigest()
    run = NativeRun(id="a" * 32, workdir=directory, result=result, state="completed", parse_state="readable",
                    node_id="node", finished_at="2026-11-23T00:00:00Z", input_path="test.rad",
                    input_revision="b" * 64, log_path="", units={"length": "mm"},
                    snapshot={"artifacts": {relative: digest}})
    data = read_native_frame(run, 0)
    grid = vtk_fields.read_vtu(data.path)
    assert grid.field_data["FrameIndex"].item() == 0
    assert grid.node_ids.tolist() == [10, 20, 30, 40]
    assert grid.element_ids.tolist() == [72]
    assert grid.cells == ((1, 0, 2, 3),)
    assert grid.points[1].tolist() == [0, 0, 0]
    assert frame["summary"]["mesh_node_count"] == 4
    if source != "ascii":
        assert grid.time == frame["time"] == 0.25
        assert grid.component_names["DISP"] == ("D1", "D2", "D3")
        assert grid.point_data["DISP"][1].tolist() == [1, 2, 2]
        assert frame["summary"]["max_displacement"] == 3
        assert frame["summary"]["displacement_node_count"] == 4


def test_native_results_show_filenames_and_leave_room_for_dat_rows(qt_app, tmp_path):
    from apps.workbench_qt.native_analysis import NativeAnalysisWindow
    from caelab.native.run_store import NativeRun
    from caelab.workbench import Workbench

    wb = Workbench()
    project = wb.new_native_project()
    node = wb.add_program_node(project, "analysis", "opencourant")
    wb.save_native_project(project, tmp_path / "test.json")
    window = NativeAnalysisWindow(project.path, node.id, wb)
    directory = tmp_path / "test.runs" / ("a" * 32)
    run = NativeRun(id="a" * 32, workdir=directory, result={"native_files": ["test_0001.out", "testA001"]},
                    state="completed", parse_state="partial", node_id=node.id, created_at="2026-11-23T00:00:00Z",
                    input_path="test.rad", input_revision="b" * 64, log_path="", units={})
    window._runs[run.id] = run
    window._displayed_run_id = run.id
    window.show_result()
    content = window.result_text.toPlainText()
    assert "원본 결과: test_0001.out, testA001" in content
    assert str(directory) not in content and ".runs" not in content
    assert window.dat_table.maximumHeight() >= 160
    window.close()


def test_radioss_partial_geometry_uses_fixed_label_and_does_not_claim_final(qt_app, tmp_path, monkeypatch):
    import time

    pytest.importorskip("vtk")
    from apps.workbench_qt.native_analysis import NativeAnalysisWindow
    from caelab.native import radioss
    from caelab.native.run_store import NativeRun, RunStore, utc_now
    from caelab.project import file_revision
    from caelab.workbench import Workbench

    monkeypatch.setattr(radioss, "_find_anim_to_vtk", lambda *_: None)
    wb = Workbench()
    project = wb.new_native_project()
    node = wb.add_program_node(project, "analysis", "opencourant")
    wb.save_native_project(project, tmp_path / "test.json")
    directory = tmp_path / "test.runs" / ("a" * 32)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "test_0001.out").write_text("INTERNAL ENERGY = 24\n")
    for number in (1, 2):
        (directory / f"testA{number:03d}").write_bytes(b"\x00animation")
    vector = "VECTORS Displacement double\n1 2 2  0 0 0  0 0 0  0 0 0\n"
    (directory / "testA001.vtk").write_text(LEGACY.replace(vector, ""), encoding="ascii")
    result = radioss.collect_radioss_results(directory)
    frame, = result["frames"]
    assert result["status"] == "partial"
    assert frame["geometry_state"] == "deformed" and not frame["is_final"]
    assert frame["summary"]["max_displacement"] is None
    assert any("testA002" in warning for warning in result["warnings"])
    relative = frame["vtu_file"]
    log = directory / "job.log"
    log.touch()
    run = NativeRun(id=directory.name, node_id=node.id, workdir=directory, solver_id="radioss",
                    state="completed", parse_state="partial", finished_at=utc_now(), result=result,
                    log_path=str(log), snapshot={"artifacts": {relative: file_revision(directory / relative)}})
    store = RunStore(project.run_store)
    store.write(run)
    store.select(node.id, run.id)
    window = NativeAnalysisWindow(project.path, node.id, wb)
    try:
        assert window.frame_selector.itemText(0).startswith("프레임 1")
        assert "[최종]" not in window.frame_selector.itemText(0)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and window.native_view.frame_data is None:
            qt_app.processEvents()
            time.sleep(.01)
        assert window.native_view.frame_data is not None
        assert window.native_view.scale_combo.currentText() == "변형 형상"
        assert not window.native_view.scale_combo.isEnabled()
    finally:
        window.close()


def test_radioss_standalone_vtu_collection_preserves_sources_and_frame_identity(tmp_path):
    from caelab.native.radioss import collect_radioss_results

    source = tmp_path / "seed"
    source.mkdir()
    (source / "seedA001.vtk").write_text(LEGACY, encoding="ascii")
    generated = collect_radioss_results(source)["frames"][0]["vtu_file"]
    raw = (source / generated).read_bytes()
    directory = tmp_path / "case"
    results = directory / "results"
    results.mkdir(parents=True)
    (directory / "test_0001.out").write_text("INTERNAL ENERGY = 24\n")
    originals = [results / "frame-1.vtu", results / "frame-000001.vtu"]
    for original in originals:
        original.write_bytes(raw)
    first = collect_radioss_results(directory)
    assert [(frame["index"], frame["vtu_file"]) for frame in first["frames"]] == [
        (1, "results/decoded/frame-000001.vtu"),
    ]
    assert collect_radioss_results(directory) == first
    assert [original.read_bytes() for original in originals] == [raw, raw]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows double-click launcher requires PowerShell")
def test_windows_one_click_launcher_dry_run_preserves_workspace(tmp_path):
    import shutil

    folder = tmp_path / "CAELab Workbench"
    scripts = folder / "scripts"
    scripts.mkdir(parents=True)
    batch = folder / "CAELab_Install_And_Run.bat"
    shutil.copy2(ROOT / batch.name, batch)
    shutil.copy2(ROOT / "pyproject.toml", folder / "pyproject.toml")
    name = "install_and_run_windows.ps1"
    shutil.copy2(ROOT / "scripts" / name, scripts / name)
    result = subprocess.run(["cmd.exe", "/d", "/c", batch.name, "-DryRun", "-CreateShortcut", "-ForceReinstall"],
                            cwd=folder, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert "Python 3.11" in result.stdout.decode("utf-8", errors="replace")
    assert not (folder / ".venv").exists()
    assert not (folder / REGISTRY).exists()
