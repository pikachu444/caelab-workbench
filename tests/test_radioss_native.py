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


def test_engine_listing_parses_cycle_table_summary(tmp_path):
    listing = tmp_path / "case_0001.out"
    listing.write_text("""
 02/11/2026
   CYCLE    TIME      TIME-STEP  ELEMENT          ERROR  I-ENERGY    K-ENERGY T  K-ENERGY R  EXT-WORK     MAS.ERR     TOTAL MASS  MASS ADDED
       0   0.000      0.7714E-07 SOLID        816   0.0%   0.000       992.8       0.000       0.000       0.000      0.1907E-03   0.000
     100  0.7714E-05  0.7714E-07 SOLID        816   0.0%   5.087       1170.       0.000       182.5       0.000      0.1907E-03   0.000
    1000  0.7712E-04  0.7714E-07 SOLID        816 -35.8%   265.4       1783.       20.01       2228.       0.000      0.1907E-03   0.000

     TOTAL NUMBER OF CYCLES  :    1039
""")
    parsed = parse_radioss_output(listing)
    assert parsed["cycle"] == 1000.0
    assert parsed["time"] == 0.7712e-04
    assert parsed["internal_energy"] == 265.4
    assert parsed["kinetic_energy"] == 1803.01
    assert parsed["kinetic_energy_t"] == 1783.0
    assert parsed["kinetic_energy_r"] == 20.01
    assert parsed["energy_error"] == -35.8
    assert parsed["external_work"] == 2228.0
    assert parsed["mass_error"] == 0.0
    assert parsed["total_cycles"] == 1039.0


def test_energy_labels_and_cycle_table_no_mixing(tmp_path):
    """Counterexample 1: Explicit labels take precedence and do not mix with cycle table components."""
    listing = tmp_path / "mixed_0001.out"
    listing.write_text("""
INTERNAL ENERGY = 10
KINETIC ENERGY = 20
CYCLE TIME TIME-STEP ELEMENT ERROR I-ENERGY K-ENERGY T K-ENERGY R EXT-WORK MAS.ERR
1000 1.0E-3 1.0E-6 SOLID 1 -1.0% 30 40 5 60 0
TOTAL NUMBER OF CYCLES : 1039
""")
    parsed = parse_radioss_output(listing)
    assert parsed["internal_energy"] == 10.0
    assert parsed["kinetic_energy"] == 20.0
    # No component breakdown attached because explicit total KE was 20, avoiding 20 != 40 + 5 contradiction
    assert "kinetic_energy_t" not in parsed
    assert "kinetic_energy_r" not in parsed
    assert parsed["total_cycles"] == 1039.0
    assert parsed["printed_cycle"] == 1000.0
    assert parsed["printed_time"] == 1.0e-3


def test_cycle_table_header_required_and_corrupted_rows_defense(tmp_path):
    """Counterexample 3: Require cycle header and reject corrupted/trailing NaN rows."""
    # Case A: Numbers without cycle header are not parsed as cycle table
    listing_no_header = tmp_path / "no_header.out"
    listing_no_header.write_text("""
1000 1.0E-3 1.0E-6 SOLID 1 -1.0% 30 40 5 60 0
TOTAL NUMBER OF CYCLES : 1039
""")
    parsed_no_header = parse_radioss_output(listing_no_header)
    assert "printed_cycle" not in parsed_no_header
    assert "kinetic_energy" not in parsed_no_header
    assert parsed_no_header.get("total_cycles") == 1039.0

    # Case B: Header present, row 500 valid, but row 1000 is corrupted with NaN / ***
    listing_corrupt = tmp_path / "corrupted.out"
    listing_corrupt.write_text("""
CYCLE TIME TIME-STEP ELEMENT ERROR I-ENERGY K-ENERGY T K-ENERGY R EXT-WORK MAS.ERR
 500 5.0E-4 1.0E-6 SOLID 1 -0.5% 15 20 2 30 0
1000 1.0E-3 1.0E-6 SOLID 1 NaN NaN *** *** *** 0
TOTAL NUMBER OF CYCLES : 1039
""")
    parsed_corrupt = parse_radioss_output(listing_corrupt)
    # The corrupted row must invalidate earlier cycle row so 500 is NOT falsely reported as last cycle
    assert "printed_cycle" not in parsed_corrupt
    assert "internal_energy" not in parsed_corrupt
    assert parsed_corrupt.get("total_cycles") == 1039.0


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
