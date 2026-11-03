"""Tests for CalculiX C3D10 simulation deck assembly and execution."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from caelab.adapters.msh_c3d10 import (
    ConvertedMesh,
    MshC3D10Error,
    assemble_c3d10_master_deck,
    build_c3d10_structural_model,
    convert_msh41_to_c3d10_file,
    extract_boundary_nodes,
    read_msh41_straight_tet10,
    run_c3d10_simulation,
)
from caelab.model.schema import StructuralModel
from caelab.result_summary import format_summary_ko, summarize_result
from caelab.solvers.calculix import CalculiXConverter, find_ccx
from caelab.validate.checker import validate_model

FIXED_L_MSH_SHA = "045acf69709618a849cd6aca028999bcb5012766a8da27ff8fbba79087d4980b"
FIXED_L_SIDECAR_SHA = "52fc715c56b473d38ab46ca51e59a170c7d2cbb47bd8a408fa88557e52004926"


def get_fixed_l_dir() -> Path | None:
    override = os.environ.get("CAELAB_FIXED_L_ASSET_DIR")
    if override:
        p = Path(override).expanduser().resolve()
        if p.is_dir():
            return p

    default_path = (
        Path.home()
        / ".codex"
        / "visualizations"
        / "2026"
        / "11"
        / "03"
        / "surface-autotet"
        / "prepared-tet10-native-L-probe"
        / "captured"
        / "msh-d290fa6e-e1af-42b1-a593-75f5c3107dd0"
    )
    if default_path.is_dir():
        return default_path
    return None


@pytest.fixture
def minimal_c3d10_mesh():
    """Minimal valid synthetic ConvertedMesh with 1 Tet10 and 1 Tri6 boundary."""
    nodes = {
        1: (0.0, 0.0, 0.0),
        2: (1.0, 0.0, 0.0),
        3: (0.0, 1.0, 0.0),
        4: (0.0, 0.0, 1.0),
        5: (0.5, 0.0, 0.0),
        6: (0.5, 0.5, 0.0),
        7: (0.0, 0.5, 0.0),
        8: (0.0, 0.0, 0.5),
        9: (0.5, 0.0, 0.5),
        10: (0.0, 0.5, 0.5),
    }
    elements = {
        100: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
    }
    tri6_boundary = {
        200: [1, 2, 3, 5, 6, 7],
    }
    metadata = {
        "schema": "caelab.standalone-msh",
        "version": 1,
        "length_unit": "mm",
        "format_version": "4.1",
        "element_profile": "straight_tet10",
        "source": {"sha256": "fake"},
    }
    return ConvertedMesh(
        nodes=nodes,
        elements=elements,
        tri6_boundary=tri6_boundary,
        metadata=metadata,
    )


def test_extract_boundary_nodes_all_and_filtered(minimal_c3d10_mesh):
    """Test extracting all or predicate-filtered boundary nodes from Tri6 annotations."""
    all_bnd = extract_boundary_nodes(minimal_c3d10_mesh)
    assert all_bnd == [1, 2, 3, 5, 6, 7]

    # Predicate: nodes on y=0 line
    y_zero = extract_boundary_nodes(minimal_c3d10_mesh, lambda x, y, z: abs(y) < 1e-6)
    assert y_zero == [1, 2, 5]

    # Predicate matching no boundary nodes
    z_high = extract_boundary_nodes(minimal_c3d10_mesh, lambda x, y, z: z > 0.5)
    assert z_high == []


def test_assemble_c3d10_master_deck_keywords():
    """Verify master deck contains standard CalculiX cards and formats node sets within 16-item limits."""
    fixed_nodes = list(range(1, 25))  # 24 nodes -> spans 2 lines
    loaded_nodes = [101, 102]
    deck = assemble_c3d10_master_deck(
        "sub/c3d10_mesh.inp",
        fixed_nodes=fixed_nodes,
        loaded_nodes=loaded_nodes,
        load_vector=(0.0, 0.0, -500.0),
        youngs_modulus=200000.0,
        poissons_ratio=0.28,
        density=7.8e-9,
        heading="Test Simulation Deck",
        include_dat_totals=True,
    )

    assert "*HEADING\nTest Simulation Deck" in deck
    assert "*INCLUDE, INPUT=sub/c3d10_mesh.inp" in deck
    assert "*NSET, NSET=FIXED_NODES" in deck
    assert "*NSET, NSET=LOAD_NODES" in deck
    assert "*MATERIAL, NAME=STEEL" in deck
    assert "*ELASTIC\n200000.0, 0.28" in deck
    assert "*DENSITY\n7.8e-09" in deck
    assert "*SOLID SECTION, ELSET=EL_C3D10, MATERIAL=STEEL" in deck
    assert "*BOUNDARY\nFIXED_NODES, 1, 3, 0.0" in deck
    assert "*STEP\n*STATIC" in deck
    assert "*CLOAD\nLOAD_NODES, 3, -250.0" in deck  # -500 / 2 nodes
    assert "*NODE FILE\nU, RF" in deck
    assert "*EL FILE\nS" in deck
    assert "*NODE PRINT, NSET=FIXED_NODES, TOTALS=ONLY\nRF" in deck
    assert "*END STEP" in deck

    # Verify line item wrapping: 16 items on first line, 8 items on second line
    idx = deck.index("*NSET, NSET=FIXED_NODES")
    lines = deck[idx:].splitlines()
    assert len(lines[1].split(",")) == 16
    assert len(lines[2].split(",")) == 8


def test_fail_closed_master_deck_empty_include():
    """Fail closed when include filename is empty or blank."""
    with pytest.raises(MshC3D10Error, match="Include deck filename must not be empty"):
        assemble_c3d10_master_deck("  ", fixed_nodes=[1, 2])


def test_fail_closed_master_deck_empty_fixed():
    """Fail closed when fixed nodes list is empty."""
    with pytest.raises(MshC3D10Error, match="Fixed nodes list must not be empty"):
        assemble_c3d10_master_deck("mesh.inp", fixed_nodes=[])


def test_fail_closed_master_deck_colliding_fixed_and_loaded():
    """Fail closed when fixed nodes and loaded nodes intersect."""
    with pytest.raises(MshC3D10Error, match="Fixed and loaded node sets must be disjoint"):
        assemble_c3d10_master_deck("mesh.inp", fixed_nodes=[1, 2, 3], loaded_nodes=[3, 4, 5])


def test_build_c3d10_structural_model_validates(minimal_c3d10_mesh):
    """Test build_c3d10_structural_model conforms to schema and passes model/solver validation."""
    model = build_c3d10_structural_model(
        minimal_c3d10_mesh,
        name="test_model",
        unit_system="mm-t-s",
        fixed_nodes=[1, 2, 3],
        loaded_nodes=[4],
        load_vector=(0.0, 0.0, -100.0),
        youngs_modulus=210000.0,
        poissons_ratio=0.3,
        density=7.85e-9,
    )

    # General schema validation
    schema_issues = validate_model(model)
    assert not [i for i in schema_issues if i.severity == "error"]

    # CalculiX converter support and semantic validation
    conv = CalculiXConverter()
    conv_issues = conv.validate(model)
    assert not [i for i in conv_issues if i.severity == "error"]

    assert model.name == "test_model"
    assert model.unit_system == "mm-t-s"
    assert len(model.mesh.nodes) == 10
    assert len(model.mesh.elements) == 1
    assert model.mesh.elements[100]["type"] == "C3D10"
    assert "FIXED_NODES" in model.sets
    assert "LOAD_NODES" in model.sets
    assert "EL_C3D10" in model.sets


def test_fail_closed_model_colliding_nodes(minimal_c3d10_mesh):
    """Fail closed when build_c3d10_structural_model receives intersecting sets."""
    with pytest.raises(MshC3D10Error, match="Fixed and loaded node sets must be disjoint"):
        build_c3d10_structural_model(
            minimal_c3d10_mesh,
            fixed_nodes=[1, 2],
            loaded_nodes=[2, 3],
            load_vector=(10.0, 0.0, 0.0),
        )


def _frd_block(name, values, time=1.0, step=1, increment=1, count=None):
    header = "    1PSTEP".ljust(24) + f"{1:12d}{increment:12d}{step:12d}"
    frame = "  100CL".ljust(12) + f"{time:12.5E}{len(values) if count is None else count:12d}"
    data = [header, frame, " -4  " + name.ljust(8)]
    data += [f" -1{node:10d}" + "".join(f"{value:12.5E}" for value in vector) for node, vector in values.items()]
    return "\n".join(data + [" -3"]) + "\n"


def test_mock_solver_run_and_result_summary(tmp_path: Path, monkeypatch, minimal_c3d10_mesh):
    """Test full run and result summarization using mock CalculiX execution."""
    model = build_c3d10_structural_model(
        minimal_c3d10_mesh,
        unit_system="mm-t-s",
        fixed_nodes=[1, 2, 3],
        loaded_nodes=[4],
        load_vector=(0.0, 0.0, -100.0),
    )

    deck_path = tmp_path / "job.inp"
    deck_path.write_text("*NODE FILE\nU, RF\n*EL FILE\nS\n*END STEP\n")

    # 10 nodes for DISP and FORC
    disp_values = {nid: [0.0, 0.0, 0.0] for nid in range(1, 11)}
    disp_values[4] = [1.2e-4, -3.4e-4, -5.6e-4]

    forc_values = {nid: [0.0, 0.0, 0.0] for nid in range(1, 11)}
    forc_values[1] = [1.0, 2.0, 30.0]
    forc_values[2] = [-1.0, 1.0, 40.0]
    forc_values[3] = [0.0, -3.0, 30.0]
    forc_values[4] = [0.0, 0.0, -100.0]

    stress_values = {100: [10.0, 20.0, 30.0, 0.0, 0.0, 0.0]}

    frd_content = (
        _frd_block("DISP", disp_values)
        + _frd_block("FORC", forc_values)
        + _frd_block("STRESS", stress_values)
    )

    def fake_run(cmd, cwd=None, **kwargs):
        (Path(cwd) / "job.frd").write_text(frd_content, encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout="Job finished", stderr="")

    monkeypatch.setattr("caelab.solvers.calculix.find_ccx", lambda: "fake_ccx")
    monkeypatch.setattr("subprocess.run", fake_run)

    result, summary = run_c3d10_simulation(tmp_path, model, deck_path)

    assert result.success is True
    assert result.solver_id == "calculix"
    assert result.max_displacement is not None
    assert result.max_displacement > 0.0

    # Reactions at fixed nodes 1, 2, 3:
    # Fz sum = 30 + 40 + 30 = 100.0 (exact static equilibrium with -100.0 load)
    assert result.reaction_totals["fz"] == pytest.approx(100.0)

    # Unit preservation
    assert result.units == {"length": "mm", "force": "N"}
    assert summary["success"] is True
    assert summary["max_displacement_mm"] == pytest.approx(result.max_displacement)
    assert summary["max_displacement_m"] == pytest.approx(result.max_displacement / 1000.0)

    ko_text = format_summary_ko(summary)
    assert "calculix 계산 완료" in ko_text
    assert "반력 합 Fy" in ko_text or "최대 변위" in ko_text


def test_physical_fixed_l_asset_full_simulation_flow(tmp_path: Path):
    """End-to-end integration test with the physical Fixed L fixture asset."""
    asset_dir = get_fixed_l_dir()
    if asset_dir is None:
        pytest.skip("External fixed L asset directory not found in CAELAB_FIXED_L_ASSET_DIR or ~/.codex")

    msh_path = asset_dir / "input.msh"
    sidecar_path = asset_dir / "metadata.json"
    if not msh_path.is_file():
        pytest.skip(f"Fixed L input.msh missing in {asset_dir}")

    # 1. Read MSH 4.1 and verify contract
    mesh = read_msh41_straight_tet10(msh_path, sidecar_path)
    assert len(mesh.nodes) == 87
    assert len(mesh.elements) == 36
    assert len(mesh.tri6_boundary) == 28

    # 2. Convert to include deck
    include_deck = tmp_path / "c3d10_include.inp"
    convert_msh41_to_c3d10_file(msh_path, include_deck, sidecar_path)
    assert include_deck.is_file()

    # 3. Extract boundary conditions:
    # Fixed support: bottom face z=0 (21 nodes)
    fixed_nodes = extract_boundary_nodes(mesh, lambda x, y, z: abs(z - 0.0) < 1e-4)
    assert len(fixed_nodes) == 21

    # Load application: top face z=1.0 (21 nodes, disjoint from z=0)
    load_nodes = extract_boundary_nodes(mesh, lambda x, y, z: abs(z - 1.0) < 1e-4)
    assert len(load_nodes) == 21
    assert set(fixed_nodes).isdisjoint(set(load_nodes))

    # 4. Assemble master deck referencing include deck
    master_deck_str = assemble_c3d10_master_deck(
        "c3d10_include.inp",
        fixed_nodes=fixed_nodes,
        loaded_nodes=load_nodes,
        load_vector=(0.0, 0.0, -100.0),  # 100 N downward
        youngs_modulus=210000.0,         # 210 GPa in MPa
        poissons_ratio=0.3,
        heading="Physical Fixed L C3D10 Simulation",
        include_dat_totals=True,
    )
    master_deck_path = tmp_path / "job.inp"
    master_deck_path.write_text(master_deck_str, encoding="utf-8")

    # 5. Build matching StructuralModel
    model = build_c3d10_structural_model(
        mesh,
        name="fixed_l_simulation",
        unit_system="mm-t-s",
        fixed_nodes=fixed_nodes,
        loaded_nodes=load_nodes,
        load_vector=(0.0, 0.0, -100.0),
        youngs_modulus=210000.0,
        poissons_ratio=0.3,
    )
    assert not [i for i in validate_model(model) if i.severity == "error"]
    assert not [i for i in CalculiXConverter().validate(model) if i.severity == "error"]

    # 6. Execute simulation
    # If ccx is available (or via WSL bridge), run actual solve
    ccx = find_ccx()
    wsl_available = False
    if ccx is None and shutil.which("wsl"):
        res = subprocess.run(["wsl", "which", "ccx"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip():
            # Create temporary bridge script
            bridge = tmp_path / "wsl_ccx.bat"
            bridge.write_text("@echo off\nwsl ccx %*\n", encoding="utf-8")
            os.environ["CAELAB_CCX"] = str(bridge)
            ccx = str(bridge)
            wsl_available = True

    result, summary = run_c3d10_simulation(tmp_path, model, master_deck_path)

    if ccx is not None:
        assert result.success is True
        assert result.max_displacement is not None
        assert result.max_displacement > 0.0
        # Reaction in z must equal applied load +100.0 N
        assert result.reaction_totals["fz"] == pytest.approx(100.0, rel=1e-3)
        assert summary["success"] is True
        assert summary["max_displacement_mm"] == pytest.approx(result.max_displacement)
        assert summary["max_displacement_m"] == pytest.approx(result.max_displacement / 1000.0)
    else:
        # Fails closed with explicit message when solver not found
        assert result.success is False
        assert "ccx 실행 파일을 찾지 못했습니다" in result.log_tail
        assert summary["success"] is False
