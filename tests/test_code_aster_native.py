import json
from xml.etree import ElementTree as ET

import pytest

from caelab.native.code_aster import collect_codeaster_results, parse_comm_keywords


def test_parse_comm_keywords(tmp_path):
    deck = tmp_path / "model.comm"
    deck.write_text(
        """DEBUT()\nmesh = LIRE_MAILLAGE(FORMAT='MED', UNITE=20)\n"
        "material = AFFE_MATERIAU(MAILLAGE=mesh)\nresult = MECA_STATIQUE()\n# FIN() ignored\nFIN()\n""",
        encoding="utf-8",
    )
    assert parse_comm_keywords(deck) == ["DEBUT", "LIRE_MAILLAGE", "AFFE_MATERIAU", "MECA_STATIQUE", "FIN"]


def _write_fixture(path):
    path.write_text(json.dumps({
        "format": "caelab-codeaster-rmed-fixture-v1",
        "time": 1.25,
        "nodes": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "elements": [{"type": "tetra", "nodes": [0, 1, 2, 3]}],
        "displacement": [[0, 0, 0], [3, 4, 0], [0, 0, 0], [0, 0, 0]],
        "stress": [[10, 0, 0, 0, 0, 0]] * 4,
    }), encoding="utf-8")


@pytest.mark.parametrize("extension", [".rmed", ".resu"])
def test_collect_mock_result_writes_vtu_and_summary(tmp_path, extension):
    _write_fixture(tmp_path / f"model{extension}")
    result = collect_codeaster_results(tmp_path)

    assert result["status"] == "readable"
    assert result["exchange_files"] == ["results/frame-000000.vtu"]
    assert result["summary"] == {
        "max_displacement": 5.0,
        "max_stress": 10.0,
        "num_nodes": 4,
        "num_elements": 1,
        "solver_id": "codeaster",
    }
    root = ET.parse(tmp_path / result["exchange_files"][0]).getroot()
    piece = root.find("./UnstructuredGrid/Piece")
    assert piece.attrib == {"NumberOfPoints": "4", "NumberOfCells": "1"}
    arrays = {item.attrib.get("Name"): (item.text or "").split() for item in root.findall(".//DataArray")}
    assert arrays["connectivity"] == ["0", "1", "2", "3"]
    assert arrays["types"] == ["10"]
    assert arrays["DISP_Magnitude"] == ["0.0", "5.0", "0.0", "0.0"]
    assert arrays["VON_MISES"] == ["10.0"] * 4


@pytest.mark.parametrize("contents", ["not json", '{"nodes": []}'])
def test_collect_corrupt_rmed_fails_without_exchange(tmp_path, contents):
    (tmp_path / "model.rmed").write_text(contents, encoding="utf-8")
    result = collect_codeaster_results(tmp_path)
    assert result["status"] == "failed"
    assert result["summary"]["solver_id"] == "codeaster"
    assert not result["exchange_files"]
    assert not (tmp_path / "results" / "frame-000000.vtu").exists()


def test_collect_rejects_unmarked_json_and_removes_stale_exchange(tmp_path):
    target = tmp_path / "results" / "frame-000000.vtu"
    target.parent.mkdir()
    target.write_text("stale", encoding="utf-8")
    (tmp_path / "model.rmed").write_text(json.dumps({
        "nodes": [[0, 0, 0], [1, 0, 0]],
        "elements": [{"type": "line", "nodes": [0, 1]}],
    }), encoding="utf-8")
    result = collect_codeaster_results(tmp_path)
    assert result["status"] == "failed"
    assert "format=" in result["warnings"][0]
    assert not target.exists()


def test_collect_missing_and_resu_without_mesh_fail_explicitly(tmp_path):
    missing = collect_codeaster_results(tmp_path)
    assert missing["status"] == "failed"
    (tmp_path / "model.resu").write_text("ordinary printed table", encoding="utf-8")
    text_only = collect_codeaster_results(tmp_path)
    assert text_only["status"] == "failed"
    assert "테스트 형식" in text_only["warnings"][0]


def test_collect_reports_convergence_error_before_result_conversion(tmp_path):
    _write_fixture(tmp_path / "model.rmed")
    (tmp_path / "model.resu").write_text("NO_CONVERGENCE after iteration 12", encoding="utf-8")
    result = collect_codeaster_results(tmp_path)
    assert result["status"] == "failed"
    assert "수렴" in result["warnings"][0]
    assert not (tmp_path / "results" / "frame-000000.vtu").exists()


def test_collect_honours_cancellation(tmp_path):
    _write_fixture(tmp_path / "model.rmed")
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        collect_codeaster_results(tmp_path, check_cancelled=cancelled)
