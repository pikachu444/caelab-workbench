from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from caelab.native.lsdyna import collect_lsdyna_results, parse_glstat, parse_include_references


def test_reads_lsdyna_include_references(tmp_path):
    deck = tmp_path / "deck.k"
    deck.write_text("*KEYWORD\n*INCLUDE\nparts/mesh.k\n*INCLUDE, material.key\n*END\n", encoding="utf-8")

    assert parse_include_references(deck) == ["parts/mesh.k", "material.key"]


def test_include_parser_does_not_treat_include_path_as_a_file(tmp_path):
    deck = tmp_path / "deck.k"
    deck.write_text("*INCLUDE_PATH_RELATIVE\nparts\n*END\n", encoding="utf-8")

    assert parse_include_references(deck) == []


def test_parses_final_glstat_energy_balance(tmp_path):
    glstat = tmp_path / "glstat"
    glstat.write_text(
        " time = 0.0\n kinetic energy = 1.0D+00\n internal energy = 2.0\n"
        " hourglass energy = 0.1\n sliding interface energy = 0.2\n total energy = 3.3\n energy ratio = 1.000\n"
        " time = 1.0\n kinetic energy = 4.0\n internal energy = 5.0\n"
        " hourglass energy = 0.3\n sliding interface energy = 0.4\n total energy = 9.7\n energy ratio = 0.998\n",
        encoding="utf-8",
    )

    assert parse_glstat(glstat) == {
        "kinetic_energy": 4.0,
        "internal_energy": 5.0,
        "hourglass_energy": 0.3,
        "sliding_energy": 0.4,
        "total_energy": 9.7,
        "energy_ratio": 0.998,
    }


def test_glstat_distinguishes_eroded_and_standard_energy_ratios(tmp_path):
    glstat = tmp_path / "glstat"
    glstat.write_text("energy ratio = .998\nenergy ratio without eroded energy = .997\n", encoding="utf-8")

    assert parse_glstat(glstat)["energy_ratio"] == pytest.approx(0.998)


def test_collects_mock_outputs_to_vtu_and_energy_summary(tmp_path):
    (tmp_path / "deck.k").write_text(
        "*KEYWORD\n*NODE\n1,0,0,0\n2,1,0,0\n3,1,1,0\n4,0,1,0\n"
        "5,0,0,1\n6,1,0,1\n7,1,1,1\n8,0,1,1\n*ELEMENT_SOLID\n1,1,1,2,3,4,5,6,7,8\n*END\n",
        encoding="utf-8",
    )
    (tmp_path / "d3plot").write_bytes(b"mock binary plot")
    (tmp_path / "nodout").write_text(
        " nodal print out for time step 1 (time = 1.0)\n"
        + "\n".join(f"{node} {node / 10:g} 0.0 0.0" for node in range(1, 9)),
        encoding="utf-8",
    )
    (tmp_path / "glstat").write_text(
        "kinetic energy = 4\ninternal energy = 5\nhourglass energy = .3\n"
        "sliding interface energy = .4\ntotal energy = 9.7\nenergy ratio = .998\n",
        encoding="utf-8",
    )

    result = collect_lsdyna_results(tmp_path)

    assert result["status"] == "readable"
    assert result["exchange_files"] == ["results/frame-000000.vtu"]
    assert result["summary"]["solver_id"] == "lsdyna"
    numeric_summary = {key: value for key, value in result["summary"].items() if key != "solver_id"}
    assert numeric_summary == pytest.approx({
        "kinetic_energy": 4.0,
        "internal_energy": 5.0,
        "hourglass_energy": 0.3,
        "sliding_energy": 0.4,
        "total_energy": 9.7,
        "energy_ratio": 0.998,
        "max_displacement": 0.8,
    })
    piece = ET.parse(tmp_path / result["exchange_files"][0]).find("./UnstructuredGrid/Piece")
    assert piece is not None
    assert piece.attrib == {"NumberOfPoints": "8", "NumberOfCells": "1"}


def test_collection_reads_mesh_from_included_deck(tmp_path):
    (tmp_path / "deck.k").write_text("*KEYWORD\n*INCLUDE\nmesh.k\n*END\n", encoding="utf-8")
    (tmp_path / "mesh.k").write_text("*NODE\n1,0,0,0\n", encoding="utf-8")
    (tmp_path / "nodout").write_text("time = 0\n1 0 0 0\n", encoding="utf-8")

    result = collect_lsdyna_results(tmp_path)

    piece = ET.parse(tmp_path / result["exchange_files"][0]).find("./UnstructuredGrid/Piece")
    assert piece is not None
    assert piece.attrib["NumberOfPoints"] == "1"


@pytest.mark.parametrize("message", [
    "Error termination due to input", "out-of-core error", "negative volume in solid element 10",
])
def test_error_termination_is_failed_and_writes_no_exchange_file(tmp_path, message):
    (tmp_path / "d3hsp").write_text(message, encoding="utf-8")
    (tmp_path / "glstat").write_text("kinetic energy = 1\n", encoding="utf-8")

    result = collect_lsdyna_results(tmp_path)

    assert result["status"] == "failed"
    assert result["exchange_files"] == []
    assert message.split()[0].lower() in result["warnings"][0].lower()


def test_negative_volume_with_normal_termination_is_not_automatically_failed(tmp_path):
    (tmp_path / "d3hsp").write_text(
        "negative volume in solid element 10\nnormal termination", encoding="utf-8"
    )
    (tmp_path / "glstat").write_text("kinetic energy = 1\n", encoding="utf-8")

    result = collect_lsdyna_results(tmp_path)

    assert result["status"] == "partial"


def test_glstat_without_deck_does_not_fabricate_empty_vtu_or_displacement(tmp_path):
    (tmp_path / "glstat").write_text("kinetic energy = 1\n", encoding="utf-8")

    result = collect_lsdyna_results(tmp_path)

    assert result["status"] == "partial"
    assert result["exchange_files"] == []
    assert result["summary"]["max_displacement"] is None


def test_collection_honours_cancellation(tmp_path):
    (tmp_path / "glstat").write_text("kinetic energy = 1\n", encoding="utf-8")

    def cancelled():
        raise RuntimeError("cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        collect_lsdyna_results(tmp_path, check_cancelled=cancelled)
