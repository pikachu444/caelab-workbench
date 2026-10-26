from xml.etree import ElementTree as ET

import pytest

from caelab.native.radioss import (
    collect_radioss_results,
    detect_radioss_failure,
    parse_radioss_output,
    parse_radioss_starter,
)


def test_starter_blocks_are_identified(tmp_path):
    deck = tmp_path / "impact_0000.rad"
    deck.write_text("""/BEGIN\nDemo\n/NODE\n1 0 0 0\n/PART/1\npart\n/MAT/PLAS_JOHNS_COOK/1\n""")
    parsed = parse_radioss_starter(deck)
    assert {"/BEGIN", "/NODE", "/PART/1", "/MAT/PLAS_JOHNS_COOK/1"} <= set(parsed["blocks"])


def test_engine_listing_energy_values_use_last_sample(tmp_path):
    listing = tmp_path / "impact_0001.out"
    listing.write_text("""
KINETIC ENERGY = 1.0D+01
INTERNAL ENERGY = 2.0D+01
HOURGLASS ENERGY = 1.0D+00
TOTAL ENERGY = 3.1D+01
ENERGY ERROR = -2.50 %
KINETIC ENERGY = 1.2D+01
INTERNAL ENERGY = 2.4D+01
HOURGLASS ENERGY = 1.2D+00
TOTAL ENERGY = 3.72D+01
ENERGY ERROR = -1.25 %
""")
    assert parse_radioss_output(listing) == {
        "kinetic_energy": 12.0,
        "internal_energy": 24.0,
        "hourglass_energy": 1.2,
        "total_energy": 37.2,
        "energy_error": -1.25,
    }


def test_collection_writes_valid_vtu_and_energy_summary(tmp_path):
    (tmp_path / "impact_0001.out").write_text("""
KINETIC ENERGY = 12
INTERNAL ENERGY = 24
HOURGLASS ENERGY = 1.2
TOTAL ENERGY = 37.2
ENERGY ERROR = -1.25 %
""")
    (tmp_path / "A001").write_text("""NODE 1 0 0 0
NODE 2 1 0 0
NODE 3 0 1 0
NODE 4 0 0 1
TETRA 1 2 3 4
""")
    result = collect_radioss_results(tmp_path)
    assert result["status"] == "readable"
    assert result["exchange_files"] == ["results/frame-000000.vtu"]
    assert result["summary"] == {
        "kinetic_energy": 12.0,
        "internal_energy": 24.0,
        "hourglass_energy": 1.2,
        "total_energy": 37.2,
        "energy_error": -1.25,
        "hourglass_ratio": pytest.approx(0.05),
        "solver_id": "radioss",
    }
    root = ET.parse(tmp_path / result["exchange_files"][0]).getroot()
    piece = root.find("./UnstructuredGrid/Piece")
    assert piece.attrib == {"NumberOfPoints": "4", "NumberOfCells": "1"}


@pytest.mark.parametrize("message", [
    "ERROR TERMINATION due to input",
    "WARNING: NEGATIVE VOLUME in solid element 12",
    "RUNAWAY ENERGY detected",
])
def test_failure_markers_are_detected(message):
    assert detect_radioss_failure(message)
    assert detect_radioss_failure("normal", return_code=17) == "OpenRadioss exited with return code 17"


def test_collection_marks_failure_and_checks_cancellation(tmp_path):
    (tmp_path / "impact_0001.out").write_text("NEGATIVE VOLUME\n")
    (tmp_path / "A001").write_text("opaque native data")
    calls = []
    result = collect_radioss_results(tmp_path, check_cancelled=lambda: calls.append(True))
    assert result["status"] == "failed"
    assert calls
    assert any("negative volume" in warning for warning in result["warnings"])


def test_opaque_animation_is_partial_and_zero_internal_ratio_is_json_safe(tmp_path):
    (tmp_path / "impact_0001.out").write_text(
        "INTERNAL ENERGY = 0\nHOURGLASS ENERGY = 1\nENERGY ERROR = 2 %\n"
    )
    (tmp_path / "A001").write_bytes(b"\x00native")
    result = collect_radioss_results(tmp_path)
    assert result["status"] == "partial"
    assert result["summary"]["hourglass_ratio"] is None
    assert any("undefined" in warning for warning in result["warnings"])
