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
    deduce_length_unit,
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

def test_warp_vector_active_vectors_and_actual_coordinates(sample_model, sample_result):
    """PR80-WB2: Verify that vtkWarpVector uses active vector 'U' and warps coordinates to x_orig + S * u."""
    from PySide6.QtWidgets import QApplication
    import vtk

    app = QApplication.instance() or QApplication([])
    vp = Viewport()
    vp.available = True
    vp._renderer = vtk.vtkRenderer()
    vp._grid, vp._node_ids = build_grid(sample_model)

    # Real scale (S = 1.0)
    vp.show_results(sample_model, sample_result, field_name="U_mag", scale_mode="real", scale_factor=1.0)
    warp = vp._last_warp
    assert warp is not None
    assert warp.GetScaleFactor() == 1.0

    in_grid = warp.GetInput()
    assert in_grid.GetPointData().GetVectors().GetName() == "U"

    warp.Update()
    out_grid = warp.GetOutput()

    for idx, nid in enumerate(vp._node_ids):
        orig = sample_model.mesh.nodes[nid]
        u = sample_result.displacements[nid]
        pt = out_grid.GetPoint(idx)
        assert math.isclose(pt[0], orig[0] + 1.0 * u[0], rel_tol=1e-6)
        assert math.isclose(pt[1], orig[1] + 1.0 * u[1], rel_tol=1e-6)
        assert math.isclose(pt[2], orig[2] + 1.0 * u[2], rel_tol=1e-6)

    # Real scale with factor 2.5 (S = 2.5)
    vp.show_results(sample_model, sample_result, field_name="U_mag", scale_mode="real", scale_factor=2.5)
    warp = vp._last_warp
    assert warp.GetScaleFactor() == 2.5
    warp.Update()
    out_grid2 = warp.GetOutput()
    for idx, nid in enumerate(vp._node_ids):
        orig = sample_model.mesh.nodes[nid]
        u = sample_result.displacements[nid]
        pt = out_grid2.GetPoint(idx)
        assert math.isclose(pt[0], orig[0] + 2.5 * u[0], rel_tol=1e-6)
        assert math.isclose(pt[1], orig[1] + 2.5 * u[1], rel_tol=1e-6)
        assert math.isclose(pt[2], orig[2] + 2.5 * u[2], rel_tol=1e-6)


def test_warp_vector_unit_consistency_si_vs_mm():
    """PR80-WB3: Verify x_orig + S * u maintains identical length units without 1000x distortion."""
    from PySide6.QtWidgets import QApplication
    import vtk

    app = QApplication.instance() or QApplication([])

    # 1. SI model: mesh in meters, disp in meters (0.01 m + 0.001 m = 0.011 m)
    mesh_si = MeshData(
        nodes={1: (0.01, 0.0, 0.0), 2: (0.02, 0.0, 0.0), 3: (0.01, 0.01, 0.0), 4: (0.01, 0.0, 0.01)},
        elements={1: {"type": "C3D4", "nodes": [1, 2, 3, 4]}},
    )
    model_si = StructuralModel(name="si_model", mesh=mesh_si, unit_system="si")
    result_si = ResultSet(
        solver_id="calculix",
        success=True,
        displacements={1: [0.001, 0.0, 0.0], 2: [0.001, 0.0, 0.0], 3: [0.001, 0.0, 0.0], 4: [0.001, 0.0, 0.0]},
        units={"length": "m", "force": "N"},
    )
    vp_si = Viewport()
    vp_si.available = True
    vp_si._renderer = vtk.vtkRenderer()
    vp_si._grid, vp_si._node_ids = build_grid(model_si)
    vp_si.show_results(model_si, result_si, field_name="U_mag", scale_mode="real", scale_factor=1.0)
    vp_si._last_warp.Update()
    out_si = vp_si._last_warp.GetOutput()
    pt1_si = out_si.GetPoint(0)
    # Must be 0.01 + 0.001 = 0.011 m (NOT 0.01 + 1.0 = 1.01 m!)
    assert math.isclose(pt1_si[0], 0.011, rel_tol=1e-6)

    # 2. mm model: mesh in mm, disp in mm (10.0 mm + 0.1 mm = 10.1 mm)
    mesh_mm = MeshData(
        nodes={1: (10.0, 0.0, 0.0), 2: (20.0, 0.0, 0.0), 3: (10.0, 10.0, 0.0), 4: (10.0, 0.0, 10.0)},
        elements={1: {"type": "C3D4", "nodes": [1, 2, 3, 4]}},
    )
    model_mm = StructuralModel(name="mm_model", mesh=mesh_mm, unit_system="mm")
    result_mm = ResultSet(
        solver_id="calculix",
        success=True,
        displacements={1: [0.1, 0.0, 0.0], 2: [0.1, 0.0, 0.0], 3: [0.1, 0.0, 0.0], 4: [0.1, 0.0, 0.0]},
        units={"length": "mm", "force": "N"},
    )
    vp_mm = Viewport()
    vp_mm.available = True
    vp_mm._renderer = vtk.vtkRenderer()
    vp_mm._grid, vp_mm._node_ids = build_grid(model_mm)
    vp_mm.show_results(model_mm, result_mm, field_name="U_mag", scale_mode="real", scale_factor=1.0)
    vp_mm._last_warp.Update()
    out_mm = vp_mm._last_warp.GetOutput()
    pt1_mm = out_mm.GetPoint(0)
    assert math.isclose(pt1_mm[0], 10.1, rel_tol=1e-6)


def test_model_execution_stresses_restoration_roundtrip(tmp_path, monkeypatch):
    """PR80-WB1: Verify that model_execution converts string stress node IDs back to int upon reloading."""
    from tests.test_model_execution import completed
    from caelab.model_execution import read_model_result

    raw_result = ResultSet(
        "calculix",
        True,
        displacements={1: [0.0, -0.002, 0.0]},
        reactions={1: [0.0, 10.0, 0.0]},
        nodal_forces={1: [0.0, 10.0, 0.0]},
        stresses={1: [100.0, 20.0, 0.0, 10.0, 0.0, 0.0]},
        units={"length": "m", "force": "N"},
    )

    _, _, run, _, _ = completed(tmp_path, monkeypatch, outcome=raw_result)

    # Read back from persisted run JSON
    loaded = read_model_result(run)
    assert loaded.result is not None
    assert 1 in loaded.result.stresses
    assert "1" not in loaded.result.stresses
    assert isinstance(list(loaded.result.stresses.keys())[0], int)
    assert loaded.result.stresses[1] == [100.0, 20.0, 0.0, 10.0, 0.0, 0.0]

    # Check compute_von_mises works directly on reloaded stresses without NaN
    vm = compute_von_mises(loaded.result.stresses, [1])
    assert math.isfinite(vm[0])
    assert not np.isnan(vm[0])


def test_deduce_length_unit_contract():
    """PR80-WB3: Test deduce_length_unit for mm, SI, cross units, and undeclared units."""
    m_mm = StructuralModel(name="m", mesh=MeshData(nodes={}, elements={}), unit_system="mm")
    m_si = StructuralModel(name="m", mesh=MeshData(nodes={}, elements={}), unit_system="si")
    m_none = StructuralModel(name="m", mesh=MeshData(nodes={}, elements={}), unit_system=None)
    m_unknown = StructuralModel(name="m", mesh=MeshData(nodes={}, elements={}), unit_system="custom_inch")

    r_mm = ResultSet(solver_id="ccx", success=True, units={"length": "mm", "force": "N"})
    r_m = ResultSet(solver_id="ccx", success=True, units={"length": "m", "force": "N"})
    r_empty = ResultSet(solver_id="ccx", success=True, units={})
    r_unknown = ResultSet(solver_id="ccx", success=True, units={"length": "inch"})

    # Both declared
    assert deduce_length_unit(m_mm, r_mm) == ("mm", "mm", True)
    assert deduce_length_unit(m_si, r_m) == ("m", "m", True)
    assert deduce_length_unit(m_mm, r_m) == ("mm", "m", True)
    assert deduce_length_unit(m_si, r_mm) == ("m", "mm", True)

    # Undeclared or unverified
    assert deduce_length_unit(m_none, r_m) == (None, "m", False)
    assert deduce_length_unit(m_unknown, r_m) == (None, "m", False)
    assert deduce_length_unit(m_mm, r_empty) == ("mm", None, False)
    assert deduce_length_unit(m_mm, r_unknown) == ("mm", None, False)
    assert deduce_length_unit(m_none, r_empty) == (None, None, False)


def test_unit_consistency_scalars_and_legends_si():
    """PR80-WB3: In SI model (0.01m + 0.001m), scalars and both legends must be [m], not [mm]."""
    from PySide6.QtWidgets import QApplication
    from apps.workbench_qt.main_window import MainWindow
    import vtk

    app = QApplication.instance() or QApplication([])

    mesh_si = MeshData(
        nodes={1: (0.01, 0.0, 0.0), 2: (0.02, 0.0, 0.0), 3: (0.01, 0.01, 0.0), 4: (0.01, 0.0, 0.01)},
        elements={1: {"type": "C3D4", "nodes": [1, 2, 3, 4]}},
    )
    model_si = StructuralModel(name="si_model", mesh=mesh_si, unit_system="si")
    result_si = ResultSet(
        solver_id="calculix",
        success=True,
        displacements={1: [0.001, 0.0, 0.0], 2: [0.001, 0.0, 0.0], 3: [0.001, 0.0, 0.0], 4: [0.001, 0.0, 0.0]},
        units={"length": "m", "force": "N"},
    )

    vp = Viewport()
    vp.available = True
    vp._renderer = vtk.vtkRenderer()
    vp._grid, vp._node_ids = build_grid(model_si)
    vp.show_results(model_si, result_si, field_name="U_mag", scale_mode="real", scale_factor=1.0)

    # 1. Scalar array in grid must be in meters (0.001)
    mag_arr = vp._last_warp.GetInput().GetPointData().GetArray("displacement_magnitude")
    assert math.isclose(mag_arr.GetValue(0), 0.001, rel_tol=1e-6)

    # 2. VTK ScalarBar title must indicate [m], NOT [mm]
    assert vp._scalar_bar is not None
    assert "Displacement [m]" in vp._scalar_bar.GetTitle()

    # 3. MainWindow Qt legend label must indicate (m), NOT (mm)
    win = MainWindow()
    win.model = model_si
    win.scale_mode_combo.setCurrentIndex(win.scale_mode_combo.findData("real"))
    win._update_legend_label(model_si, result_si)
    assert "변위 크기 (m) [절점]" in win.legend_label.text()
    assert "mm" not in win.legend_label.text()


def test_unit_consistency_scalars_and_legends_cross_mm_and_m():
    """PR80-WB3: Cross mm-mesh and m-displacement must convert scalars and legends to mm consistently."""
    from PySide6.QtWidgets import QApplication
    from apps.workbench_qt.main_window import MainWindow
    import vtk

    app = QApplication.instance() or QApplication([])

    mesh_mm = MeshData(
        nodes={1: (10.0, 0.0, 0.0), 2: (20.0, 0.0, 0.0), 3: (10.0, 10.0, 0.0), 4: (10.0, 0.0, 10.0)},
        elements={1: {"type": "C3D4", "nodes": [1, 2, 3, 4]}},
    )
    model_mm = StructuralModel(name="mm_model", mesh=mesh_mm, unit_system="mm")
    result_m = ResultSet(
        solver_id="calculix",
        success=True,
        displacements={1: [0.001, 0.0, 0.0], 2: [0.001, 0.0, 0.0], 3: [0.001, 0.0, 0.0], 4: [0.001, 0.0, 0.0]},
        units={"length": "m", "force": "N"},
    )

    vp = Viewport()
    vp.available = True
    vp._renderer = vtk.vtkRenderer()
    vp._grid, vp._node_ids = build_grid(model_mm)
    vp.show_results(model_mm, result_m, field_name="U_mag", scale_mode="real", scale_factor=1.0)

    # 1. Displacements converted to mm (0.001 m -> 1.0 mm)
    mag_arr = vp._last_warp.GetInput().GetPointData().GetArray("displacement_magnitude")
    assert math.isclose(mag_arr.GetValue(0), 1.0, rel_tol=1e-6)

    # 2. Warp coordinate is 10.0 + 1.0 = 11.0 mm
    vp._last_warp.Update()
    out = vp._last_warp.GetOutput()
    assert math.isclose(out.GetPoint(0)[0], 11.0, rel_tol=1e-6)

    # 3. VTK ScalarBar title is [mm]
    assert "Displacement [mm]" in vp._scalar_bar.GetTitle()

    # 4. MainWindow Qt legend is (mm)
    win = MainWindow()
    win.model = model_mm
    win.scale_mode_combo.setCurrentIndex(win.scale_mode_combo.findData("real"))
    win._update_legend_label(model_mm, result_m)
    assert "변위 크기 (mm) [절점]" in win.legend_label.text()


def test_undeclared_units_suppresses_real_scale_and_indicates_status():
    """PR80-WB3: Undeclared/unverified units must suppress real deformation (S=0) and display unverified notice."""
    from PySide6.QtWidgets import QApplication
    from apps.workbench_qt.main_window import MainWindow
    import vtk

    app = QApplication.instance() or QApplication([])

    mesh = MeshData(
        nodes={1: (10.0, 0.0, 0.0), 2: (20.0, 0.0, 0.0), 3: (10.0, 10.0, 0.0), 4: (10.0, 0.0, 10.0)},
        elements={1: {"type": "C3D4", "nodes": [1, 2, 3, 4]}},
    )
    # Model has no unit_system declared
    model_undeclared = StructuralModel(name="no_unit_model", mesh=mesh, unit_system=None)
    # Result has no units declared
    result_undeclared = ResultSet(
        solver_id="calculix",
        success=True,
        displacements={1: [0.1, 0.0, 0.0], 2: [0.1, 0.0, 0.0], 3: [0.1, 0.0, 0.0], 4: [0.1, 0.0, 0.0]},
        units={},
    )

    vp = Viewport()
    vp.available = True
    vp._renderer = vtk.vtkRenderer()
    vp._grid, vp._node_ids = build_grid(model_undeclared)

    # 1. Real scale mode: deformation must be suppressed (S=0.0)
    vp.show_results(model_undeclared, result_undeclared, field_name="U_mag", scale_mode="real", scale_factor=1.0)
    assert vp._base_scale == 0.0
    assert "[단위 미선언: 실제 변형 억제]" in vp._scalar_bar.GetTitle()

    win = MainWindow()
    win.model = model_undeclared
    win.scale_mode_combo.setCurrentIndex(win.scale_mode_combo.findData("real"))
    win._update_legend_label(model_undeclared, result_undeclared)
    assert "[단위 미선언: 실제 변형 억제]" in win.legend_label.text()

    # 2. Auto scale mode: visual auto scaling allowed, but legend must note [단위 미선언: 절점]
    vp.show_results(model_undeclared, result_undeclared, field_name="U_mag", scale_mode="auto", scale_factor=1.0)
    assert vp._base_scale > 0.0
    assert "[단위 미선언: 절점]" in vp._scalar_bar.GetTitle()

    win.scale_mode_combo.setCurrentIndex(win.scale_mode_combo.findData("auto"))
    win._update_legend_label(model_undeclared, result_undeclared)
    assert "[단위 미선언: 절점]" in win.legend_label.text()
