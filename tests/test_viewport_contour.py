import math
import os
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from caelab.model.schema import StructuralModel, MeshData
from caelab.solvers.base import ResultSet
from apps.workbench_qt.viewport import (
    compute_von_mises,
    deduce_stress_unit,
    build_grid,
    Viewport,
)


def test_compute_von_mises_pure_uniaxial():
    """Uniaxial stress Sxx=100.0 must yield von_mises=100.0."""
    stresses = {1: [100.0, 0.0, 0.0, 0.0, 0.0, 0.0]}
    vm = compute_von_mises(stresses, [1])
    assert math.isclose(vm[0], 100.0, rel_tol=1e-6)


def test_compute_von_mises_pure_shear():
    """Pure shear Sxy=50.0 must yield von_mises = sqrt(3)*50.0 ~= 86.6025."""
    stresses = {1: [0.0, 0.0, 0.0, 50.0, 0.0, 0.0]}
    vm = compute_von_mises(stresses, [1])
    assert math.isclose(vm[0], math.sqrt(3.0) * 50.0, rel_tol=1e-6)


def test_compute_von_mises_hydrostatic():
    """Hydrostatic stress Sxx=Syy=Szz=100.0 must yield von_mises=0.0."""
    stresses = {1: [100.0, 100.0, 100.0, 0.0, 0.0, 0.0]}
    vm = compute_von_mises(stresses, [1])
    assert math.isclose(vm[0], 0.0, abs_tol=1e-12)


def test_compute_von_mises_nan_masking_for_uncovered_nodes():
    """AC-3: Nodes missing stress or having non-finite values must be masked with NaN."""
    stresses = {
        1: [100.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        2: [float("nan"), 0.0, 0.0, 0.0, 0.0, 0.0],
        3: [float("inf"), 0.0, 0.0, 0.0, 0.0, 0.0],
        # node 4 is missing entirely
    }
    vm = compute_von_mises(stresses, [1, 2, 3, 4])
    assert math.isclose(vm[0], 100.0, rel_tol=1e-6)
    assert np.isnan(vm[1])
    assert np.isnan(vm[2])
    assert np.isnan(vm[3])


def test_deduce_stress_unit_contract():
    """AC-3: Unit deduction from result units dictionary."""
    assert deduce_stress_unit({"length": "mm", "force": "N"}) == "MPa"
    assert deduce_stress_unit({"length": "m", "force": "N"}) == "Pa"
    # Undeclared / partial units must return None
    assert deduce_stress_unit({"length": "mm"}) is None
    assert deduce_stress_unit({"force": "N"}) is None
    assert deduce_stress_unit({}) is None
    assert deduce_stress_unit({"length": "unknown", "force": "N"}) is None


@pytest.fixture
def sample_model():
    mesh = MeshData(
        nodes={
            1: (0.0, 0.0, 0.0),
            2: (10.0, 0.0, 0.0),
            3: (0.0, 10.0, 0.0),
            4: (0.0, 0.0, 10.0),
        },
        elements={
            1: {"type": "C3D4", "nodes": [1, 2, 3, 4]},
        },
    )
    return StructuralModel(name="test_model", mesh=mesh, unit_system="mm")


@pytest.fixture
def sample_result():
    return ResultSet(
        solver_id="calculix",
        success=True,
        displacements={
            1: [0.0, 0.0, 0.0],
            2: [0.1, 0.0, -0.05],
            3: [0.0, 0.1, -0.05],
            4: [0.0, 0.0, -0.2],
        },
        stresses={
            1: [120.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            2: [100.0, 20.0, 0.0, 10.0, 0.0, 0.0],
            3: [80.0, 10.0, 5.0, 0.0, 0.0, 0.0],
            4: [50.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
        units={"length": "mm", "force": "N"},
    )


def test_viewport_show_results_and_field_switching(sample_model, sample_result):
    """Test viewport display with displacement and Von Mises stress fields."""
    from PySide6.QtWidgets import QApplication
    import vtk

    app = QApplication.instance() or QApplication([])
    vp = Viewport()
    vp.available = True
    vp._renderer = vtk.vtkRenderer()
    vp._grid, vp._node_ids = build_grid(sample_model)

    # 1. Default displacement magnitude
    vp.show_results(sample_model, sample_result, field_name="U_mag", scale_mode="auto", scale_factor=1.0)
    assert vp._last_field == "U_mag"
    assert vp._last_scale_mode == "auto"
    assert vp._base_scale is not None
    auto_s = vp._auto_scale

    # 2. Real scale mode (AC-4: S = 1.0)
    vp.show_results(sample_model, sample_result, field_name="U_mag", scale_mode="real", scale_factor=1.0)
    assert vp._last_scale_mode == "real"
    assert math.isclose(vp._base_scale, 1.0, rel_tol=1e-6)

    # 3. Real scale with custom multiplier (S = 1.0 * factor)
    vp.show_results(sample_model, sample_result, field_name="U_mag", scale_mode="real", scale_factor=2.5)
    assert math.isclose(vp._base_scale, 2.5, rel_tol=1e-6)

    # 4. Auto scale with custom multiplier (S = auto_scale * factor)
    vp.show_results(sample_model, sample_result, field_name="U_mag", scale_mode="auto", scale_factor=1.5)
    assert math.isclose(vp._base_scale, auto_s * 1.5, rel_tol=1e-6)

    # 5. Von Mises stress with confirmed MPa units (AC-3)
    vp.show_results(sample_model, sample_result, field_name="von_mises", scale_mode="auto", scale_factor=1.0)
    assert vp._last_field == "von_mises"

    # 6. Von Mises stress with undeclared units (AC-3: suspend display with warning)
    result_no_units = ResultSet(
        solver_id="calculix",
        success=True,
        displacements=sample_result.displacements,
        stresses=sample_result.stresses,
        units={},  # undeclared
    )
    vp.show_results(sample_model, result_no_units, field_name="von_mises")
    assert vp._last_field == "von_mises"


def test_main_window_field_and_scale_controls(tmp_path, monkeypatch, sample_model, sample_result):
    """Test MainWindow integration of field selector and deformation scale combos."""
    from PySide6.QtWidgets import QApplication
    from apps.workbench_qt.main_window import MainWindow

    app = QApplication.instance() or QApplication([])

    win = MainWindow()
    win.model = sample_model
    win._displayed_model = sample_model
    win._last_result = sample_result

    # Verify combo boxes exist in view_bar
    assert hasattr(win, "field_combo")
    assert hasattr(win, "scale_mode_combo")
    assert win.field_combo.count() == 5  # U_mag, Ux, Uy, Uz, von_mises
    assert win.scale_mode_combo.count() == 2  # auto, real

    # Initial update
    win._update_legend_label(sample_model, sample_result)
    assert "변위 크기 (mm)" in win.legend_label.text()

    # Switch to Von Mises
    idx_vm = win.field_combo.findData("von_mises")
    assert idx_vm != -1
    win.field_combo.setCurrentIndex(idx_vm)
    assert "Von Mises 응력 (MPa)" in win.legend_label.text()
    assert "[절점 외삽]" in win.legend_label.text()

    # Switch to Ux
    idx_ux = win.field_combo.findData("U_x")
    win.field_combo.setCurrentIndex(idx_ux)
    assert "변위 X (mm)" in win.legend_label.text()

    # Test undeclared units warning in MainWindow
    result_undeclared = ResultSet(
        solver_id="calculix",
        success=True,
        displacements=sample_result.displacements,
        stresses=sample_result.stresses,
        units={},
    )
    win.field_combo.setCurrentIndex(idx_vm)
    win._update_legend_label(sample_model, result_undeclared)
    assert "[단위 미선언: 파생 응력 보류]" in win.legend_label.text()
