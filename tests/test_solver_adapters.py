"""Test solver adapter contract and multi-solver dispatch."""

import os
import pytest
from pathlib import Path

from caelab.connections import ProgramSpec, ProgramStatus, LaunchRequest
from caelab.native.adapter import (
    get_solver_adapter,
    supported_solver_ids,
    CalculiXAdapter,
    CodeAsterAdapter,
    OpenRadiossAdapter,
    LSDynaAdapter,
)


def test_supported_solver_ids_covers_main_solvers():
    ids = supported_solver_ids()
    assert "calculix" in ids
    assert "codeaster" in ids
    assert "radioss" in ids or "openradioss" in ids
    assert "lsdyna" in ids


def test_calculix_adapter_contract():
    adapter = get_solver_adapter("calculix")
    assert isinstance(adapter, CalculiXAdapter)
    assert adapter.solver_id == "calculix"
    assert adapter.input_extensions == (".inp",)
    assert adapter.expected_output_names() == ("model.frd", "model.dat")


def test_codeaster_adapter_contract():
    adapter = get_solver_adapter("codeaster")
    assert isinstance(adapter, CodeAsterAdapter)
    assert adapter.solver_id == "codeaster"
    assert ".comm" in adapter.input_extensions
    assert "model.rmed" in adapter.expected_output_names()


def test_radioss_adapter_contract():
    adapter = get_solver_adapter("radioss")
    assert isinstance(adapter, OpenRadiossAdapter)
    assert adapter.solver_id == "radioss"
    assert ".rad" in adapter.input_extensions
    assert "A001" in adapter.expected_output_names()


def test_lsdyna_adapter_contract():
    adapter = get_solver_adapter("lsdyna")
    assert isinstance(adapter, LSDynaAdapter)
    assert adapter.solver_id == "lsdyna"
    assert ".k" in adapter.input_extensions
    assert "d3plot" in adapter.expected_output_names()


def test_unknown_solver_raises():
    with pytest.raises(ValueError, match="지원되지 않거나 등록되지 않은 솔버"):
        get_solver_adapter("nonexistent_solver_xyz")


def test_codeaster_run_request(tmp_path):
    from caelab.connections import codeaster_run_request
    spec = ProgramSpec("codeaster", "Code_Aster", "solver", "CAELAB_CODEASTER", ("run_aster",))
    exe = tmp_path / ("run_aster.exe" if os.name == "nt" else "run_aster")
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    comm_file = tmp_path / "case.comm"
    comm_file.write_text("DEBUT()\nFIN()\n", encoding="utf-8")
    status = ProgramStatus(spec, str(exe))
    req = codeaster_run_request(status, tmp_path, comm_file)
    assert isinstance(req, LaunchRequest)
    assert req.argv[0] == str(exe)
    assert "case.comm" in req.argv[1]


def test_radioss_run_request(tmp_path):
    from caelab.connections import radioss_run_request
    spec = ProgramSpec("radioss", "OpenRadioss", "solver", "CAELAB_OPENRADIOSS", ("starter",))
    exe = tmp_path / ("starter.exe" if os.name == "nt" else "starter")
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    rad_file = tmp_path / "model_0000.rad"
    rad_file.write_text("/BEGIN\n/END\n", encoding="utf-8")
    status = ProgramStatus(spec, str(exe))
    req = radioss_run_request(status, tmp_path, rad_file)
    assert isinstance(req, LaunchRequest)
    assert req.argv[0] == str(exe)
    assert "-i" in req.argv


def test_lsdyna_run_request(tmp_path):
    from caelab.connections import lsdyna_run_request
    spec = ProgramSpec("lsdyna", "LS-DYNA", "solver", "CAELAB_LSDYNA", ("lsdyna",))
    exe = tmp_path / ("lsdyna.exe" if os.name == "nt" else "lsdyna")
    exe.write_text("", encoding="utf-8")
    exe.chmod(0o755)
    k_file = tmp_path / "deck.k"
    k_file.write_text("*KEYWORD\n*END\n", encoding="utf-8")
    status = ProgramStatus(spec, str(exe))
    req = lsdyna_run_request(status, tmp_path, k_file)
    assert isinstance(req, LaunchRequest)
    assert req.argv[0] == str(exe)
    assert any("i=" in arg for arg in req.argv)


def test_codeaster_adapter_collect_results_delegation(tmp_path):
    import json
    adapter = get_solver_adapter("codeaster")
    rmed = tmp_path / "model.rmed"
    rmed.write_text(json.dumps({
        "format": "caelab-codeaster-rmed-fixture-v1",
        "time": 0.0,
        "nodes": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "elements": [{"type": "tetra", "nodes": [0, 1, 2, 3]}],
        "displacement": [[0, 0, 0], [1, 0, 0], [0, 0, 0], [0, 0, 0]],
        "stress": [[1, 0, 0, 0, 0, 0]] * 4,
    }), encoding="utf-8")
    result = adapter.collect_results(tmp_path)
    assert result["status"] == "readable"
    assert result["summary"]["solver_id"] == "codeaster"
    assert result["summary"]["max_displacement"] == 1.0


def test_radioss_adapter_collect_results_delegation(tmp_path):
    adapter = get_solver_adapter("radioss")
    out = tmp_path / "model_0001.out"
    out.write_text("KINETIC ENERGY = 10.0\nINTERNAL ENERGY = 20.0\nHOURGLASS ENERGY = 1.0\nTOTAL ENERGY = 31.0\nENERGY ERROR = -1.0 %\n")
    a001 = tmp_path / "A001"
    a001.write_text("NODE 1 0.0 0.0 0.0\nNODE 2 1.0 0.0 0.0\nNODE 3 0.0 1.0 0.0\nNODE 4 0.0 0.0 1.0\nTETRA 1 1 2 3 4\n")
    result = adapter.collect_results(tmp_path)
    assert result["status"] == "readable"
    assert result["summary"]["solver_id"] == "radioss"
    assert result["summary"]["internal_energy"] == 20.0


def test_lsdyna_adapter_collect_results_delegation(tmp_path):
    adapter = get_solver_adapter("lsdyna")
    glstat = tmp_path / "glstat"
    glstat.write_text(" time = 0.0\n kinetic energy = 1.0\n internal energy = 2.0\n hourglass energy = 0.1\n sliding interface energy = 0.0\n total energy = 3.1\n energy ratio = 1.0\n")
    deck = tmp_path / "model.k"
    deck.write_text("*KEYWORD\n*NODE\n1,0.0,0.0,0.0\n2,1.0,0.0,0.0\n3,1.0,1.0,0.0\n4,0.0,1.0,0.0\n5,0.0,0.0,1.0\n6,1.0,0.0,1.0\n7,1.0,1.0,1.0\n8,0.0,1.0,1.0\n*ELEMENT_SOLID\n1,1,1,2,3,4,5,6,7,8\n*END\n")
    result = adapter.collect_results(tmp_path)
    assert result["status"] == "readable"
    assert result["summary"]["solver_id"] == "lsdyna"
    assert result["summary"]["internal_energy"] == 2.0
