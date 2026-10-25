"""Native result transfer checks; synthetic format tests and independent CCX checks."""
import base64
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
from xml.etree import ElementTree as ET

import pytest

from caelab.native.results import collect_results


def _geometry(ids=(40, 10, 90, 25), topology=3, connectivity=(10, 25, 40, 90), indicator=1):
    digits = 10 if indicator else 5
    def header(code, count):
        return f"{code:6s}{'':18}{count:12d}{'':37}{indicator}\n"
    text = "    1C\n" + header("    2C", len(ids))
    for i, identifier in enumerate(ids):
        text += f" -1{identifier:{digits}d}" + "".join(f"{value:12.5E}" for value in (i, 2 * i, -i)) + "\n"
    text += " -3\n" + header("    3C", 1)
    text += f" -1{731:{digits}d}{topology:5d}{4:5d}{9:5d}\n"
    for start in range(0, len(connectivity), 8):
        text += " -2" + "".join(f"{node:{digits}d}" for node in connectivity[start:start + 8]) + "\n"
    return text + " -3\n"


DISPLACEMENT = [("D1", 2, 1, 0, 0), ("D2", 2, 2, 0, 0),
                ("D3", 2, 3, 0, 0), ("ALL", 2, 0, 0, 1)]


def _field(rows, *, name="DISP", components=DISPLACEMENT, step=1, increment=2,
           value=0.5, kode=8, counter=7, declared=None, indicator=1, analysis_type=0):
    digits = 10 if indicator else 5
    declared = len(rows) if declared is None else declared
    text = f"    1PSTEP{'':14}{counter:12d}{increment:12d}{step:12d}\n"
    header = list(" " * 75)
    for start, token in ((0, "  100CL"), (7, f"{100 + kode:5d}"), (12, f"{value:12.5E}"),
                         (24, f"{declared:12d}"), (56, f"{analysis_type:2d}"),
                         (58, f"{kode:5d}"), (74, str(indicator))):
        header[start:start + len(token)] = token
    text += "".join(header) + "\n" + f" -4  {name:<8}{len(components):5d}{1:5d}\n"
    for label, kind, row, col, exist in components:
        text += f" -5  {label:<8}{1:5d}{kind:5d}{row:5d}{col:5d}{exist:5d}\n"
    for identifier, values in rows:
        text += f" -1{identifier:{digits}d}" + "".join(f"{v:12.5E}" for v in values[:6]) + "\n"
        if len(values) > 6:
            text += " -2" + " " * digits + "".join(f"{v:12.5E}" for v in values[6:]) + "\n"
    return text + " -3\n"


def _collect(tmp_path, text):
    (tmp_path / "model.frd").write_text(text + " 9999\n", encoding="ascii")
    return collect_results(tmp_path)


def _arrays(path):
    """Independently decode standard VTK XML arrays, including inline binary NaN."""
    root = ET.parse(path)
    arrays = {}
    for array in root.findall(".//DataArray"):
        if array.get("format") == "binary":
            payload = base64.b64decode(array.text)
            size = struct.unpack("<I", payload[:4])[0]
            assert size == len(payload) - 4
            values = list(struct.unpack("<" + "d" * (size // 8), payload[4:]))
        else:
            fn = int if array.get("type") == "Int64" else float
            values = [fn(token) for token in (array.text or "").split()]
        arrays[array.get("Name")] = (array, values)
    return root, arrays


@pytest.mark.parametrize("indicator", [0, 1])
def test_short_and_long_ascii_keep_ids_geometry_components_and_raw_forces(tmp_path, indicator):
    rows = [(25, [3, 4, 0]), (90, [0, 0, 2]), (10, [0, 0, 1]), (40, [0, 0, 0])]
    force = [(n, [5, 6, 7]) for n in (40, 10, 90, 25)]
    components = [("F1", 2, 1, 0, 0), ("F2", 2, 2, 0, 0), ("F3", 2, 3, 0, 0)]
    result = _collect(tmp_path, _geometry(indicator=indicator) + _field(rows, indicator=indicator)
                      + _field(force, name="FORC", components=components, counter=8, indicator=indicator))
    assert result["status"] == "readable"
    assert len(result["frames"]) == 1
    frame = result["frames"][0]
    assert (frame["step"], frame["increment"], frame["kode"], frame["value"]) == (1, 2, 8, 0.5)
    assert [f["name"] for f in frame["fields"]] == ["DISP", "FORC"]
    assert [c["name"] for c in frame["fields"][0]["components"]] == ["D1", "D2", "D3", "ALL"]
    assert result["summary"]["max_displacement"] == 5.0
    assert result["summary"]["units"] == {}
    assert result["summary"]["reaction_totals"] is None
    root, arrays = _arrays(tmp_path / frame["vtu_file"])
    assert arrays["NativeNodeId"][1] == [40, 10, 90, 25]
    assert arrays["NativeElementId"][1] == [731]
    assert arrays["Coordinates"][1] == [0, 0, 0, 1, 2, -1, 2, 4, -2, 3, 6, -3]
    assert arrays["connectivity"][1] == [1, 3, 0, 2]
    assert arrays["offsets"][1] == [4] and arrays["types"][1] == [10]
    assert arrays["DISP"][1] == [0, 0, 0, 0, 0, 1, 0, 0, 2, 3, 4, 0]
    assert arrays["FORC"][1] == [5, 6, 7] * 4
    assert arrays["NativeGroup"][1] == [4] and arrays["NativeMaterial"][1] == [9]
    assert arrays["DISP"][0].get("ComponentName2") == "D3"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("topology,count,vtk,permutation", [
    (1, 8, 12, None), (2, 6, 13, None), (3, 4, 10, None),
    (4, 20, 25, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 16, 17, 18, 19, 12, 13, 14, 15]),
    (5, 15, 26, [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14, 9, 10, 11]),
    (6, 10, 24, None), (7, 3, 5, None), (8, 6, 22, None), (9, 4, 9, None),
    (10, 8, 23, None), (11, 2, 3, None), (12, 3, 21, None),
])
def test_supported_frd_topologies_match_explicit_vtk_connectivity(tmp_path, topology, count, vtk, permutation):
    ids = [100 + 13 * i for i in range(count)]
    rows = [(identifier, [1.0]) for identifier in ids]
    text = _geometry(ids=list(reversed(ids)), topology=topology, connectivity=ids)
    text += _field(rows, name="NDTEMP", components=[("T", 1, 0, 0, 0)])
    result = _collect(tmp_path, text)
    assert result["status"] == "readable"
    _, arrays = _arrays(tmp_path / result["exchange_files"][0])
    assert arrays["types"][1] == [vtk]
    assert arrays["connectivity"][1] == [count - 1 - i for i in (permutation or list(range(count)))]
    assert arrays["offsets"][1] == [count]


def test_unknown_fields_and_original_component_order_need_no_displacement(tmp_path):
    components = [("B", 1, 0, 0, 0), ("A", 1, 0, 0, 0)]
    rows = [(identifier, [i, -i]) for i, identifier in enumerate((40, 10, 90, 25))]
    result = _collect(tmp_path, _geometry() + _field(rows, name="MYSTERY", components=components))
    assert result["status"] == "readable" and result["summary"]["max_displacement"] is None
    _, arrays = _arrays(tmp_path / result["frames"][0]["vtu_file"])
    array, values = arrays["MYSTERY"]
    assert [array.get("ComponentName0"), array.get("ComponentName1")] == ["B", "A"]
    assert values == [0, 0, 1, -1, 2, -2, 3, -3]


def test_partial_coverage_is_nan_with_explicit_counts_not_zero(tmp_path):
    rows = [(40, [1, 0, 0]), (10, [3, 4, 0]), (25, [1, 2, 2])]
    result = _collect(tmp_path, _geometry() + _field(rows, declared=4))
    assert result["status"] == "partial"
    coverage = result["frames"][0]["fields"][0]["coverage"]
    assert coverage["missing_node_ids"] == [90] and coverage["record_count"] == 3
    assert result["summary"]["max_displacement"] == 5.0
    assert result["summary"]["displacement_node_count"] == 3
    _, arrays = _arrays(tmp_path / result["frames"][0]["vtu_file"])
    assert all(math.isnan(value) for value in arrays["DISP"][1][6:9])
    assert arrays["Coverage_DISP"][1] == [1, 1, 0, 1]
    json.dumps(result, allow_nan=False)


def test_equal_values_different_step_or_increment_are_separate_frames(tmp_path):
    rows = [(n, [3, 4, 0]) for n in (40, 10, 90, 25)]
    text = _geometry()
    for step, increment, kode in [(1, 1, 15), (2, 1, 15), (2, 2, 16)]:
        text += _field(rows, step=step, increment=increment, kode=kode, value=1.0)
    result = _collect(tmp_path, text)
    assert result["status"] == "readable"
    assert [(f["step"], f["increment"], f["kode"]) for f in result["frames"]] == [(1, 1, 15), (2, 1, 15), (2, 2, 16)]
    assert result["summary"]["pvd_coordinate"] == "frame_index"
    assert result["summary"]["step"] == 2 and result["summary"]["increment"] == 2
    datasets = ET.parse(tmp_path / result["exchange_files"][0]).findall(".//DataSet")
    assert [float(dataset.get("timestep")) for dataset in datasets] == [0, 1, 2]
    for i, frame in enumerate(result["frames"]):
        _, arrays = _arrays(tmp_path / frame["vtu_file"])
        assert arrays["Native_value"][1] == [1.0]
        assert arrays["Native_step"][1] == [frame["step"]]
        assert frame["summary"]["max_displacement"] == 5.0


@pytest.mark.parametrize("damage", ["row", "count", "terminator", "block_end"])
def test_damaged_result_is_never_reported_fully_readable(tmp_path, damage):
    rows = [(n, [3, 4, 0]) for n in (40, 10, 90, 25)]
    text = _geometry() + _field(rows, declared=5 if damage == "count" else 4)
    if damage == "row":
        text = text.replace(" 3.00000E+00 4.00000E+00 0.00000E+00", " *********** 4.00000E+00 0.00000E+00", 1)
    if damage == "block_end":
        text = text[:-4]
    if damage != "terminator":
        text += " 9999\n"
    (tmp_path / "model.frd").write_text(text, encoding="ascii")
    result = collect_results(tmp_path)
    assert result["status"] == "partial"
    assert result["warnings"]
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("change", ["unsupported_cell", "binary", "connectivity", "geometry_count"])
def test_geometry_or_binary_limit_retains_originals_without_exchange(tmp_path, change):
    text = _geometry(topology=13 if change == "unsupported_cell" else 3,
                     connectivity=(10, 25, 40, 999) if change == "connectivity" else (10, 25, 40, 90))
    if change == "binary":
        text = text.replace("                                     1", "                                     2", 1)
    if change == "geometry_count":
        text = text.replace(f"{4:12d}", f"{5:12d}", 1)
    rows = [(n, [3, 4, 0]) for n in (40, 10, 90, 25)]
    (tmp_path / "model.frd").write_text(text + _field(rows) + " 9999\n", encoding="ascii")
    (tmp_path / "model.dat").write_bytes(b"original DAT evidence\n")
    before = {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in ("model.frd", "model.dat")}
    result = collect_results(tmp_path)
    assert result["status"] == ("unsupported" if change in ("binary", "unsupported_cell") else "failed")
    assert result["exchange_files"] == [] and result["warnings"]
    assert before == {name: hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() for name in before}


def _expanded_geometry(declared=1):
    ids = tuple(range(1, 21))
    text = _geometry(ids=ids, topology=4, connectivity=ids)
    lines = text.splitlines(keepends=True)
    header = next(i for i, line in enumerate(lines) if line.startswith("    3C"))
    lines[header] = lines[header][:24] + f"{declared:12d}" + lines[header][36:]
    second = f" -1{732:10d}{4:5d}{4:5d}{10:5d}\n"
    for start in range(0, len(ids), 8):
        second += " -2" + "".join(f"{node:10d}" for node in ids[start:start + 8]) + "\n"
    lines.insert(len(lines) - 1, second)
    return "".join(lines)


_CCX_222_HEADERS = f"    {'1UPGM':20}CalculiX\n    {'1UVERSION':20}Version 2.22\n"


@pytest.mark.parametrize("declared", [1, 2])
def test_ccx_222_complete_excess_elements_are_preserved_with_count_warning(tmp_path, declared):
    rows = [(n, [n, 0, 0]) for n in range(1, 21)]
    text = _CCX_222_HEADERS + _expanded_geometry(declared) + _field(rows)
    result = _collect(tmp_path, text)
    assert result["status"] == ("partial" if declared == 1 else "readable")
    assert len(result["warnings"]) == (1 if declared == 1 else 0)
    if declared == 1:
        assert "선언 수 1" in result["warnings"][0] and "레코드 수 2" in result["warnings"][0]
    root, arrays = _arrays(tmp_path / result["frames"][0]["vtu_file"])
    assert root.find(".//Piece").get("NumberOfCells") == "2"
    assert arrays["NativeElementId"][1] == [731, 732]
    assert arrays["NativeMaterial"][1] == [9, 10]
    assert arrays["types"][1] == [25, 25]
    assert arrays["connectivity"][1][:20] == arrays["connectivity"][1][20:]
    assert arrays["DISP"][1] == [value for n in range(1, 21) for value in (n, 0, 0)]
    assert (tmp_path / "model.frd").read_text(encoding="ascii") == text + " 9999\n"


@pytest.mark.parametrize("damage", [
    "missing_version", "other_program", "other_version", "version_suffix", "bad_prefix",
    "repeated_program", "conflicting_version", "late_program", "trailing_program", "fewer_records",
    "zero_count", "negative_count", "missing_block_end", "duplicate_id", "zero_id",
    "unknown_node", "short_connectivity", "unsupported_cell",
])
def test_ccx_222_count_exception_does_not_accept_ambiguous_producer_or_bad_geometry(tmp_path, damage):
    headers = _CCX_222_HEADERS
    declared = {"fewer_records": 3, "zero_count": 0, "negative_count": -1}.get(damage, 1)
    geometry = _expanded_geometry(declared)
    if damage == "missing_version":
        headers = headers.splitlines(keepends=True)[0]
    if damage == "other_program":
        headers = headers.replace("CalculiX", "Other")
    if damage == "other_version":
        headers = headers.replace("2.22", "2.21")
    if damage == "version_suffix":
        headers = headers.replace("2.22", "2.220")
    if damage == "bad_prefix":
        headers = headers.replace("    1UPGM", "xxxx1UPGM")
    if damage == "repeated_program":
        headers += headers.splitlines(keepends=True)[0]
    if damage == "conflicting_version":
        headers = headers.replace("Version 2.22\n", "Version 2.21\n") + _CCX_222_HEADERS
    if damage == "missing_block_end":
        geometry = geometry[:-4]
    if damage in {"duplicate_id", "zero_id"}:
        geometry = geometry.replace(f" -1{732:10d}", f" -1{731 if damage == 'duplicate_id' else 0:10d}")
    if damage == "unknown_node":
        geometry = geometry.replace(f"{20:10d}\n", f"{99:10d}\n", 1)
    if damage == "short_connectivity":
        geometry = geometry.replace(f"{20:10d}\n", "\n", 1)
    if damage == "unsupported_cell":
        geometry = geometry.replace(f" -1{732:10d}{4:5d}", f" -1{732:10d}{13:5d}")
    text = headers + geometry + _field([(n, [n, 0, 0]) for n in range(1, 21)])
    if damage == "late_program":
        text += _CCX_222_HEADERS
    original = text + " 9999\n" + (_CCX_222_HEADERS if damage == "trailing_program" else "")
    (tmp_path / "model.frd").write_text(original, encoding="ascii")
    result = collect_results(tmp_path)
    assert result["status"] in {"failed", "unsupported"}
    assert result["exchange_files"] == []
    assert (tmp_path / "model.frd").read_text(encoding="ascii") == original


@pytest.mark.parametrize("placement", ["repeated", "late", "after_end"])
def test_matching_element_count_keeps_existing_metadata_tolerance(tmp_path, placement):
    text = _CCX_222_HEADERS + (_CCX_222_HEADERS if placement == "repeated" else "")
    text += _expanded_geometry(2) + _field([(n, [n, 0, 0]) for n in range(1, 21)])
    text += " 9999\n" if placement == "after_end" else ""
    text += _CCX_222_HEADERS if placement != "repeated" else ""
    result = _collect(tmp_path, text)
    assert result["status"] == ("partial" if placement == "after_end" else "readable")
    assert result["exchange_files"]


def test_missing_output_and_exchange_write_failure_are_distinct(tmp_path):
    assert collect_results(tmp_path)["status"] == "missing"
    (tmp_path / "results").write_text("a file obstructs the output directory")
    rows = [(n, [0, 0, 0]) for n in (40, 10, 90, 25)]
    result = _collect(tmp_path, _geometry() + _field(rows))
    assert result["status"] == "failed"
    assert result["exchange_files"] == []
    assert any("쓰지 못했습니다" in warning for warning in result["warnings"])


def test_independent_vtk_reader_accepts_missing_values_and_original_ids(tmp_path):
    vtk = pytest.importorskip("vtk", reason="VTK Python reader unavailable; XML geometry checks run independently")
    result = _collect(tmp_path, _geometry() + _field([(40, [3, 4, 0])], declared=1))
    reader = vtk.vtkXMLUnstructuredGridReader()
    reader.SetFileName(str(tmp_path / result["frames"][0]["vtu_file"]))
    reader.Update()
    grid = reader.GetOutput()
    assert reader.GetErrorCode() == 0
    assert grid.GetNumberOfPoints() == 4 and grid.GetNumberOfCells() == 1
    assert grid.GetCellType(0) == 10
    assert [grid.GetCell(0).GetPointId(i) for i in range(4)] == [1, 3, 0, 2]
    assert [grid.GetPointData().GetArray("NativeNodeId").GetValue(i) for i in range(4)] == [40, 10, 90, 25]
    assert grid.GetPointData().GetArray("DISP").GetTuple(0) == (3, 4, 0)
    assert all(math.isnan(v) for v in grid.GetPointData().GetArray("DISP").GetTuple(1))


def _dat_text(value=1.0):
    return f""" displacements (vx,vy,vz) for set OBSERVED and time {value:.7E}

       20 3.0 4.0 0.0
       30 0.0 0.0 1.0

 forces (fx,fy,fz) for set OBSERVED and time {value:.7E}

       20 1.0 2.0 3.0
       30 -1.0 -2.0 -3.0

 total force (fx,fy,fz) for set OBSERVED and time {value:.7E}

 0.0 0.0 0.0
"""


def test_dat_only_preserves_final_set_output_without_claiming_geometry_or_reactions(tmp_path):
    source = (_dat_text(.5) + _dat_text(1.0)).encode("ascii")
    (tmp_path / "model.dat").write_bytes(source)
    result = collect_results(tmp_path)
    assert result["status"] == "readable"
    assert result["exchange_files"] == [] and result["frames"] == []
    dat = result["dat"]
    assert dat["selection"] == "final_frame"
    assert dat["frame"] == {"step": None, "increment": None, "value": 1.0}
    assert dat["sets"]["OBSERVED"]["displacements"] == {20: [3, 4, 0], 30: [0, 0, 1]}
    assert dat["sets"]["OBSERVED"]["nodal_forces"] == {20: [1, 2, 3], 30: [-1, -2, -3]}
    assert dat["sets"]["OBSERVED"]["total_force"] == [0, 0, 0]
    assert result["summary"]["source"] == "model.dat" and result["summary"]["max_displacement"] == 5.0
    assert result["summary"]["reaction_totals"] is None
    assert (tmp_path / "model.dat").read_bytes() == source
    json.dumps(result, allow_nan=False)


def _unsupported_dat(value="2.0", label="stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz)"):
    return f" {label} for set BODY and time {value}\n 71 1 20.0 0.0 0.0 0.0 0.0 0.0\n"


@pytest.mark.parametrize("label", ["stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz)", "temperatures"])
def test_later_unsupported_dat_output_never_reuses_earlier_displacements(tmp_path, label):
    source = (_dat_text(1.0) + _unsupported_dat(label=label)).encode("ascii")
    (tmp_path / "model.dat").write_bytes(source)
    result = collect_results(tmp_path)
    assert result["status"] == result["dat"]["status"] == "unsupported"
    assert result["dat"]["frame"]["value"] == 2.0
    assert result["dat"]["sets"] == {} and result["dat"]["max_displacement"] is None
    assert result["summary"].get("max_displacement") is None
    block = result["dat"]["unsupported_blocks"][0]
    assert block["header"] == _unsupported_dat(label=label).splitlines()[0].strip()
    assert block["set"] == "BODY" and block["frame"] == result["dat"]["frame"]
    assert block["line"] > 1
    assert (tmp_path / "model.dat").read_bytes() == source
    json.dumps(result, allow_nan=False)


def test_same_dat_frame_supported_and_unsupported_blocks_are_partial(tmp_path):
    (tmp_path / "model.dat").write_text(_dat_text() + _unsupported_dat("1.0"), encoding="ascii")
    result = collect_results(tmp_path)
    assert result["status"] == result["dat"]["status"] == "partial"
    assert result["summary"]["max_displacement"] == 5.0
    assert result["dat"]["sets"]["OBSERVED"]["displacements"][20] == [3, 4, 0]
    assert result["dat"]["unsupported_blocks"][0]["frame"]["value"] == 1.0


def test_later_supported_dat_frame_does_not_inherit_earlier_unsupported_blocks(tmp_path):
    (tmp_path / "model.dat").write_text(_unsupported_dat("1.0") + _dat_text(2.0), encoding="ascii")
    result = collect_results(tmp_path)
    assert result["status"] == "readable"
    assert result["dat"]["frame"]["value"] == 2.0
    assert result["dat"]["unsupported_blocks"] == []
    assert result["summary"]["max_displacement"] == 5.0


@pytest.mark.parametrize("value", ["NaN", "broken", ""])
def test_unknown_final_dat_time_does_not_restore_earlier_supported_frame(tmp_path, value):
    (tmp_path / "model.dat").write_text(_dat_text() + _unsupported_dat(value), encoding="ascii")
    result = collect_results(tmp_path)
    assert result["status"] == result["dat"]["status"] == "failed"
    assert result["dat"]["frame"]["value"] is None
    assert result["dat"]["sets"] == {} and result["dat"]["max_displacement"] is None
    assert result["dat"]["unsupported_blocks"] and result["dat"]["warnings"]
    json.dumps(result, allow_nan=False)


def test_good_dat_never_overrides_broken_frd_or_a_different_frd_frame(tmp_path):
    (tmp_path / "model.dat").write_text(_dat_text(999.0), encoding="ascii")
    (tmp_path / "model.frd").write_text("broken FRD", encoding="ascii")
    result = collect_results(tmp_path)
    assert result["status"] == "failed" and result["dat"]["status"] == "readable"
    assert result["summary"] == {} and result["exchange_files"] == []
    result = _collect(tmp_path, _geometry() + _field([(n, [1, 0, 0]) for n in (40, 10, 90, 25)]))
    assert result["status"] == "readable"
    assert result["summary"]["max_displacement"] == 1.0
    assert result["summary"]["value"] == .5 and result["dat"]["frame"]["value"] == 999.0


@pytest.mark.parametrize("exception", [RuntimeError("cancelled"), ValueError("deadline reached"), OSError("stopped")])
def test_cancellation_exception_propagates_and_originals_are_untouched(tmp_path, exception):
    (tmp_path / "model.frd").write_text(_geometry(), encoding="ascii")
    before = (tmp_path / "model.frd").read_bytes()
    def cancelled():
        raise exception
    with pytest.raises(type(exception), match=str(exception)):
        collect_results(tmp_path, check_cancelled=cancelled)
    assert (tmp_path / "model.frd").read_bytes() == before


def test_dat_parser_calls_cancellation_inside_its_line_loop(tmp_path):
    from caelab.solvers.calculix import parse_dat

    source = "\n" * 2048 + _dat_text()
    (tmp_path / "model.dat").write_text(source, encoding="ascii")
    calls = []
    def cancelled():
        calls.append(None)
        if len(calls) == 2:
            raise RuntimeError("cancelled while reading DAT")
    with pytest.raises(RuntimeError, match="reading DAT"):
        parse_dat(tmp_path / "model.dat", check_cancelled=cancelled)
    assert len(calls) == 2


def test_cancellation_during_frd_or_array_work_is_not_a_read_failure(tmp_path):
    text = _geometry() + _field([(n, [1, 0, 0]) for n in (40, 10, 90, 25)]) + " 9999\n"
    (tmp_path / "model.frd").write_text(text, encoding="ascii")
    calls = []
    def cancelled():
        calls.append(None)
        if (tmp_path / "results").exists():
            raise RuntimeError("cancelled during result work")
    with pytest.raises(RuntimeError, match="result work"):
        collect_results(tmp_path, check_cancelled=cancelled)
    assert (tmp_path / "model.frd").read_text(encoding="ascii") == text


@pytest.mark.parametrize("indicator", [0, 1])
def test_stored_component_continuations_and_unknown_metadata_are_preserved(tmp_path, indicator):
    components = [(f"V{i}", 1, 0, 0, 0) for i in range(9)]
    rows = [(node, list(range(9))) for node in (40, 10, 90, 25)]
    result = _collect(tmp_path, _geometry(indicator=indicator)
                      + _field(rows, name="UNKNOWN", components=components, indicator=indicator))
    assert result["status"] == "readable"
    _, arrays = _arrays(tmp_path / result["frames"][0]["vtu_file"])
    assert arrays["UNKNOWN"][1] == list(range(9)) * 4
    assert arrays["UNKNOWN"][0].get("ComponentName8") == "V8"


def test_cgx_marked_components_keep_metadata_values_and_vtu_order(tmp_path):
    components = [("MARKED", 1, 0, 0, 2), ("COMPUTED", 1, 0, 0, 1),
                  ("PLAIN", 1, 0, 0, 0), ("MARKED2", 1, 0, 0, 2)]
    rows = [(node, [12.5, -7.25, 3.75]) for node in (40, 10, 90, 25)]
    result = _collect(tmp_path, _geometry() + _field(rows, name="CGX", components=components))
    assert result["status"] == "readable"
    frame = result["frames"][0]
    metadata = frame["fields"][0]["components"]
    assert [(c["name"], c["exist"]) for c in metadata] == [
        ("MARKED", 2), ("COMPUTED", 1), ("PLAIN", 0), ("MARKED2", 2)]
    _, arrays = _arrays(tmp_path / frame["vtu_file"])
    array, values = arrays["CGX"]
    assert array.get("NumberOfComponents") == "3"
    assert [array.get(f"ComponentName{i}") for i in range(3)] == ["MARKED", "PLAIN", "MARKED2"]
    assert values == [12.5, -7.25, 3.75] * 4


@pytest.mark.parametrize("indicator", [0, 1])
def test_result_continuations_reject_nonblank_node_slot(tmp_path, indicator):
    components = [(f"V{i}", 1, 0, 0, 0) for i in range(9)]
    rows = [(node, list(range(9))) for node in (40, 10, 90, 25)]
    digits = 10 if indicator else 5
    field = _field(rows, name="UNKNOWN", components=components, indicator=indicator)
    field = field.replace(" -2" + " " * digits, " -2" + f"{40:{digits}d}")
    result = _collect(tmp_path, _geometry(indicator=indicator) + field)
    assert result["status"] == "partial"
    assert result["warnings"]


def test_incomplete_last_frame_does_not_show_an_earlier_displacement(tmp_path):
    first = _field([(n, [3, 4, 0]) for n in (40, 10, 90, 25)], step=1)
    last = _field([(n, [1, 0, 0]) for n in (40, 10, 90, 25)], step=2)
    last = last[:last.index(" -4")] + "malformed field header\n"
    result = _collect(tmp_path, _geometry() + first + last)
    assert result["status"] == "partial"
    assert [frame["step"] for frame in result["frames"]] == [1, 2]
    assert result["summary"]["step"] == 2
    assert result["summary"]["max_displacement"] is None


def test_nonfinite_components_and_overflow_never_enter_json_as_numbers(tmp_path):
    rows = [(40, [math.nan, 1, 0]), (10, [1.7e308] * 3), (90, [3, 4, 0]), (25, [0, 0, 0])]
    result = _collect(tmp_path, _geometry() + _field(rows))
    assert result["status"] == "partial"
    assert result["summary"]["max_displacement"] == 5.0
    assert result["summary"]["displacement_node_count"] == 2
    assert result["summary"]["displacement_norm_overflow_count"] == 1
    json.dumps(result, allow_nan=False)


def _affine_deck(topology, stress_only=False, two_steps=False):
    coordinates = {
        "C3D4": [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)],
        "C3D8": [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
                 (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)],
        "C3D10": [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1),
                  (.5, 0, 0), (.5, .5, 0), (0, .5, 0), (0, 0, .5), (.5, 0, .5), (0, .5, .5)],
    }[topology]
    lines = ["*HEADING", "Independent uniaxial strain/traction check", "*NODE"]
    ids = [10 * (i + 1) for i in range(len(coordinates))]
    lines += [f"{node},{x},{y},{z}" for node, (x, y, z) in zip(ids, coordinates)]
    lines += [f"*ELEMENT,TYPE={topology},ELSET=BODY", "71," + ",".join(map(str, ids)),
              "*MATERIAL,NAME=M", "*ELASTIC", "1000,0.25", "*SOLID SECTION,ELSET=BODY,MATERIAL=M", "*BOUNDARY"]
    for node, (x, y, z) in zip(ids, coordinates):
        lines.append(f"{node},1,1")
        if y == 0:
            lines.append(f"{node},2,2")
        if z == 0:
            lines.append(f"{node},3,3")
    for factor in ([.01, .02] if two_steps else [.01]):
        lines += ["*STEP", "*STATIC", "*BOUNDARY"]
        lines += [f"{node},1,1,{factor * xyz[0]}" for node, xyz in zip(ids, coordinates)]
        if not stress_only:
            lines += ["*NODE FILE", "U,RF"]
        lines += ["*EL FILE", "S", "*END STEP"]
    return "\n".join(lines) + "\n", coordinates


@pytest.mark.parametrize("topology,stress_only,two_steps", [
    ("C3D4", False, False), ("C3D8", False, True), ("C3D10", False, False), ("C3D4", True, False),
])
def test_actual_ccx_native_results_match_independent_uniform_strain_solution(tmp_path, topology, stress_only, two_steps):
    executable = shutil.which("ccx")
    if executable is None:
        pytest.skip("CalculiX ccx unavailable for actual native-result check")
    deck, coordinates = _affine_deck(topology, stress_only, two_steps)
    (tmp_path / "model.inp").write_text(deck, encoding="ascii")
    completed = subprocess.run([executable, "-i", "model"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    source = (tmp_path / "model.frd").read_bytes()
    result = collect_results(tmp_path)
    assert result["status"] == "readable", result["warnings"]
    assert (tmp_path / "model.frd").read_bytes() == source
    assert [f["step"] for f in result["frames"]] == ([1, 2] if two_steps else [1])
    for index, frame in enumerate(result["frames"]):
        factor = .01 * (index + 1)
        fields = {f["name"]: f for f in frame["fields"]}
        stress = {c["name"]: c for c in fields["STRESS"]["summary"]["components"]}
        analytical = 1000 * factor
        # CCX 2.21 extrapolate.f: Hex8 a8 rows sum to 0.99996; Tet10 a4
        # rows sum to 0.99999, with midside values averaged from corner values.
        # https://www.dhondt.de/ccx_2.21.src.tar.bz2
        # Preserve this known nodal extrapolation bias, rather than correcting FRD.
        expected_nodal = analytical * {"C3D8": 0.99996, "C3D10": 0.99999}.get(topology, 1.0)
        assert stress["SXX"]["minimum"] == pytest.approx(expected_nodal, rel=5e-6)
        assert stress["SXX"]["maximum"] == pytest.approx(expected_nodal, rel=5e-6)
        assert stress["SXX"]["minimum"] == pytest.approx(analytical, rel=4.5e-5)
        assert abs(stress["SYY"]["maximum"]) < 1e-9
        assert abs(stress["SZZ"]["maximum"]) < 1e-9
        if stress_only:
            assert "DISP" not in fields
            assert frame["summary"]["max_displacement"] is None
        else:
            expected = max(math.sqrt((factor * x) ** 2 + (.25 * factor * y) ** 2 + (.25 * factor * z) ** 2)
                           for x, y, z in coordinates)
            assert frame["summary"]["max_displacement"] == pytest.approx(expected, rel=2e-5)
        _, arrays = _arrays(tmp_path / frame["vtu_file"])
        assert arrays["STRESS"][1][::6] == pytest.approx([expected_nodal] * len(coordinates), rel=5e-6)
        assert arrays["Coordinates"][1] == [value for xyz in coordinates for value in xyz]
        assert arrays["NativeNodeId"][1] == [10 * (i + 1) for i in range(len(coordinates))]
        assert arrays["NativeElementId"][1] == [71]
    json.dumps(result, allow_nan=False)


def test_actual_ccx_dat_only_node_print_retains_named_set_and_raw_totals(tmp_path):
    executable = shutil.which("ccx")
    if executable is None:
        pytest.skip("CalculiX ccx unavailable for actual DAT-only output check")
    deck, _ = _affine_deck("C3D4", stress_only=True)
    deck = deck.replace("*STEP", "*NSET,NSET=OBSERVED\n20,30\n*STEP", 1)
    deck = deck.replace("*EL FILE\nS", "*NODE PRINT,NSET=OBSERVED,TOTALS=YES\nU,RF")
    (tmp_path / "model.inp").write_text(deck, encoding="ascii")
    completed = subprocess.run([executable, "-i", "model"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    original = (tmp_path / "model.dat").read_bytes()
    result = collect_results(tmp_path)
    assert result["status"] == "readable", result["warnings"]
    assert result["exchange_files"] == []
    values = result["dat"]["sets"]["OBSERVED"]
    assert values["displacements"][20] == pytest.approx([.01, 0, 0])
    assert values["displacements"][30] == pytest.approx([0, -.0025, 0])
    assert values["nodal_forces"][20][0] == pytest.approx(10 / 6, rel=2e-5)
    assert values["total_force"][0] == pytest.approx(10 / 6, rel=2e-5)
    assert result["summary"]["max_displacement"] == pytest.approx(.01)
    assert (tmp_path / "model.dat").read_bytes() == original


def test_actual_ccx_later_el_print_does_not_select_previous_node_print(tmp_path):
    executable = shutil.which("ccx")
    if executable is None:
        pytest.skip("CalculiX ccx unavailable for actual later EL PRINT frame check")
    deck, _ = _affine_deck("C3D8", stress_only=True, two_steps=True)
    deck = deck.replace("*NODE\n", "*NODE,NSET=NALL\n", 1)
    deck = deck.replace("*EL FILE\nS", "*NODE PRINT,NSET=NALL,GLOBAL=YES\nU", 1)
    deck = deck.replace("*EL FILE\nS", "*NODE PRINT,NSET=NALL,FREQUENCY=0\nU\n*EL PRINT,ELSET=BODY,GLOBAL=YES\nS", 1)
    (tmp_path / "model.inp").write_text(deck, encoding="ascii")
    completed = subprocess.run([executable, "-i", "model"], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    original = (tmp_path / "model.dat").read_bytes()
    assert b"displacements" in original and b"stresses (elem, integ.pnt." in original
    assert b"0.1000000E+01" in original and b"0.2000000E+01" in original
    result = collect_results(tmp_path)
    assert result["status"] == result["dat"]["status"] == "unsupported"
    assert result["dat"]["frame"] == {"step": 2, "increment": 1, "value": 2.0}
    assert result["dat"]["sets"] == {} and result["dat"]["max_displacement"] is None
    assert result["dat"]["unsupported_blocks"][0]["set"] == "BODY"
    assert result["summary"].get("max_displacement") is None
    assert result["exchange_files"] == []
    assert (tmp_path / "model.dat").read_bytes() == original
