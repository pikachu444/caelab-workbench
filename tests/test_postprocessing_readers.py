"""Exercise the post-processing boundary with synthetic producer outputs."""

import base64
import gzip
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import replace

import numpy as np
import pytest

from caelab.artifact_refs import DatasetManifestV1, ResultRefV1, canonical_json_bytes, read_safe_npy_v2, write_safe_npy_v2
from caelab.jobs import JobManager
from caelab.native import converters
from caelab.native.operations import (TERMINAL_OPERATIONS, cancel_operation, operation_dir, operation_snapshot,
                                      plan_conversion, resolve_result_ref, submit_conversion)
from caelab.native.result_adapters import inspect_dataset, make_result_ref, read_result
from caelab.native.run_store import NativeRun, RunStore, atomic_json
from caelab.result_readers import ResultError
from caelab.result_readers.opencourant_history import (bind_history_columns, extract_history, official_impulse_secant,
                                                      read_th_csv, read_titles)
from caelab.result_readers.vtk_fields import query_field, read_vtk, read_vtu, shell_basis

ROOT_ID = "a" * 32
NODE = "test-source-node"
TENSORS = {
    "3DELEM_Stress": (0, 12),
    "2DELEM_Stress_(upper)": (10, 0),
    "2DELEM_Stress_(lower)": (-4, 0),
    **{f"2DELEM_Stress_(layer__{i})_": (i + 2, 0) for i in range(1, 6)},
}
ORIGINAL_POINTS = [[0, 0, 0], [0, 1, 0], [-1, 0, 0], [0, 0, 2], [1, 0, 2], [0, 1, 2], [0, 0, 3]]
MESH = {
    "node_coordinates": {str(i): point for i, point in enumerate(ORIGINAL_POINTS, 11)},
    "elements": {
        "101": {"part_id": 10, "cell_type": 5, "node_ids": [11, 12, 13]},
        "202": {"part_id": 20, "cell_type": 10, "node_ids": [14, 15, 16, 17]},
    },
}


def _vtk(*, missing=(), replacements=None, permutation=tuple(range(7)), extra_shell=False):
    points, ids = np.array(ORIGINAL_POINTS), np.arange(11, 18)
    u = np.array([[3, 4, 0], [3, 4, 0], [3, 4, 0], [0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 0, 2]])
    indices = [permutation.index(i) for i in range(7)]
    triangles = [indices[i] for i in (0, 1, 2, 2)]
    solid = sorted(indices[3:])
    cell_count = 3 if extra_shell else 2
    triangle_row = "4 " + " ".join(map(str, triangles))
    data = [
        "# vtk DataFile Version 3.0", "synthetic animation", "ASCII", "DATASET UNSTRUCTURED_GRID",
        "FIELD FieldData " + ("1" if "TIME" in missing else "2"),
        *([] if "TIME" in missing else ["TIME 1 1 double", "0.125"]),
        "CYCLE 1 1 int", "0", "POINTS 7 float",
        *(" ".join(map(str, points[i] + u[i])) for i in permutation),
        f"CELLS {cell_count} {5 * cell_count}", triangle_row, "4 " + " ".join(map(str, solid)),
        *([triangle_row] if extra_shell else []),
        f"CELL_TYPES {cell_count}", "5 10 5" if extra_shell else "5 10",
        "POINT_DATA 7", "SCALARS NODE_ID int 1", "LOOKUP_TABLE default",
        " ".join(str(ids[i]) for i in permutation),
        "VECTORS Displacement float", *(" ".join(map(str, u[i])) for i in permutation),
        "VECTORS Velocity float", *(" ".join(map(str, u[i] * 2)) for i in permutation),
        f"CELL_DATA {cell_count}", "SCALARS ELEMENT_ID int 1", "LOOKUP_TABLE default", "101 202 303" if extra_shell else "101 202",
        "SCALARS PART_ID int 1", "LOOKUP_TABLE default", "10 20 30" if extra_shell else "10 20",
        "SCALARS 2DELEM int 1", "LOOKUP_TABLE default", "1 0 1" if extra_shell else "1 0",
        "SCALARS 3DELEM int 1", "LOOKUP_TABLE default", "0 1 0" if extra_shell else "0 1",
    ]
    for name, values in TENSORS.items():
        if name not in missing:
            if extra_shell:
                values = (*values, values[0])
            data.extend((f"TENSORS {name} float", *(f"{v} 0 0 0 0 0 0 0 0" for v in values)))
    output = "\n".join(data) + "\n"
    for before, after in (replacements or {}).items():
        assert before in output
        output = output.replace(before, after, 1)
    return output


def _vtu(vector="3 4 0 0 0 2 0 2 0 2 0 0", coverage="1 1 1 1"):
    return f"""<VTKFile type="UnstructuredGrid" byte_order="LittleEndian">
<UnstructuredGrid><Piece NumberOfPoints="4" NumberOfCells="1">
<Points><DataArray type="Float64" NumberOfComponents="3">0 0 0 1 0 0 0 1 0 0 0 1</DataArray></Points>
<Cells>
<DataArray type="Int64" Name="connectivity">0 1 2 3</DataArray>
<DataArray type="Int64" Name="offsets">4</DataArray>
<DataArray type="UInt8" Name="types">10</DataArray>
</Cells>
<PointData>
<DataArray type="Int64" Name="NativeNodeId">81 82 83 84</DataArray>
<DataArray type="Float64" Name="DISP" NumberOfComponents="3" ComponentName0="D1" ComponentName1="D2" ComponentName2="D3">{vector}</DataArray>
<DataArray type="Int64" Name="Coverage_DISP">{coverage}</DataArray>
</PointData>
<CellData><DataArray type="Int64" Name="NativeElementId">12</DataArray></CellData>
</Piece></UnstructuredGrid></VTKFile>"""


@pytest.mark.parametrize(("missing", "replacements", "code"), [
    (("TIME",), {}, "MISSING_TIME"),
    ((), {"TIME 1 1 double\n0.125": "TIME 1 1 double\nnan"}, "INVALID_DATASET"),
    (("2DELEM_Stress_(layer__3)_",), {}, "SCHEMA_MISMATCH"),
    (("2DELEM_Stress_(upper)", "2DELEM_Stress_(lower)"), {}, "SCHEMA_MISMATCH"),
    ((), {"(layer__3)_": "(layer__30)_"}, "SCHEMA_MISMATCH"),
    ((), {"101 202": "202 101"}, "SOURCE_MISMATCH"),
    ((), {"11 12 13 14 15 16 17": "12 11 13 14 15 16 17"}, "SOURCE_MISMATCH"),
    ((), {"11 12 13 14 15 16 17": "11 11 13 14 15 16 17"}, "INVALID_DATASET"),
    ((), {"LOOKUP_TABLE default\n0 1": "LOOKUP_TABLE default\n1 0"}, "OBSERVABLE_MISMATCH"),
    ((), {"12 0 0 0 0 0 0 0 0": "12 1 0 0 0 0 0 0 0"}, "INVALID_DATASET"),
    ((), {"CELL_TYPES 2\n5 10": "CELL_TYPES 2\n5 12"}, "INVALID_CONNECTIVITY"),
    ((), {"4 0 1 2 2": "4 0 1 1 2"}, "INVALID_CONNECTIVITY"),
    ((), {"4 0 1 2 2": "4 0 0 2 2"}, "INVALID_CONNECTIVITY"),
    ((), {"4 3 4 5 6": "4 4 3 5 6"}, "SOURCE_MISMATCH"),
    ((), {"CELL_DATA 2": "SCALARS Coverage_Displacement int 1\nLOOKUP_TABLE default\n1 1 1 0 1 1 1\nCELL_DATA 2"},
     "PARTIAL_REQUIRED_FIELD"),
    ((), {"TENSORS 3DELEM_Stress float\n0 0": "TENSORS 3DELEM_Stress float\n9 0"}, "OBSERVABLE_MISMATCH"),
])
def test_vtk_rejects_invalid_native_layout(tmp_path, missing, replacements, code):
    path = tmp_path / "animation.vtk"
    path.write_text(_vtk(missing=missing, replacements=replacements), encoding="ascii")
    with pytest.raises(ResultError, match=code):
        read_vtk(path, exact_required_set=TENSORS, expected=MESH)


def test_tetra_requires_sorted_vtk_point_indices(tmp_path):
    path = tmp_path / "tetra.vtk"
    permutation = (0, 1, 2, 3, 5, 4, 6)
    path.write_text(_vtk(permutation=permutation), encoding="ascii")
    grid = read_vtk(path, expected=MESH)
    assert grid.node_ids[list(grid.cells[1])].tolist() == [14, 16, 15, 17]
    for row in ("4 3 5 4 6", "4 3 4 6 5"):
        path.write_text(_vtk(permutation=permutation, replacements={"4 3 4 5 6": row}), encoding="ascii")
        with pytest.raises(ResultError, match="SOURCE_MISMATCH"):
            read_vtk(path, expected=MESH)


def test_vtk_physical_time_rotation_undeformed_points_and_family(tmp_path):
    path = tmp_path / "animation.vtk"
    permutation = (1, 2, 4, 0, 6, 5, 3)
    path.write_text(_vtk(permutation=permutation), encoding="ascii")
    grid = read_vtk(path, exact_required_set=TENSORS, expected=MESH)
    assert grid.time == .125
    assert grid.reference_points == pytest.approx(np.array(ORIGINAL_POINTS)[list(permutation)])
    assert grid.family_masks["2DELEM"].tolist() == [True, False]
    assert grid.family_masks["3DELEM"].tolist() == [False, True]
    layer = "2DELEM_Stress_(layer__3)_"
    for basis, component, expected in [("element_local", "xx", 5), ("global", "yy", 5),
                                       ("global", "xx", 0), ("global", "vm", 5)]:
        values, ids = query_field(grid, layer, ids=[101], basis=basis, component=component)
        assert ids == (101,)
        assert values.tolist() == pytest.approx([expected])
    assert query_field(grid, "3DELEM_Stress", component="vm")[0].tolist() == pytest.approx([12])
    with pytest.raises(ResultError, match="OBSERVABLE_MISMATCH"):
        query_field(grid, layer, ids=[202], component="xx")
    with pytest.raises(ResultError, match="UNSUPPORTED_BASIS"):
        query_field(grid, layer, ids=[101], basis="material", component="xx")


def test_quad_axes_and_degenerate_ratio():
    square = [[0, 0, 0], [0, 1, 0], [-1, 1, 0], [-1, 0, 0]]
    assert shell_basis(square) == pytest.approx(np.array([[0, 1, 0], [-1, 0, 0], [0, 0, 1]]))
    with pytest.raises(ResultError, match="UNSUPPORTED_BASIS"):
        shell_basis([[0, 0, 0], [1, 0, 0], [1, 1e-13, 0]])


@pytest.mark.parametrize(("operator", "expected"), [
    ("values", [[0., 0., 2.], [3., 4., 0.]]), ("min", [0., 0., 0.]),
    ("max", [3., 4., 2.]), ("mean", [1.5, 2., 1.]), ("norm", [2., 5.]),
])
def test_vtu_nodal_query_preserves_native_ids_and_selection_order(tmp_path, operator, expected):
    path = tmp_path / "frame.vtu"
    path.write_text(_vtu(), encoding="ascii")
    grid = read_vtu(path)
    assert grid.time is None
    values, ids = query_field(grid, "DISP", ids=[82, 81], operator=operator, units={"unit": "mm"})
    assert ids == (82, 81)
    assert np.array_equal(values, np.asarray(expected))
    assert query_field(grid, "DISP", ids=[81], component="D2")[0].tolist() == [4.]


def test_vtu_rejects_missing_nodal_coverage_and_inconsistent_norm_units(tmp_path):
    path = tmp_path / "frame.vtu"
    path.write_text(_vtu(coverage="1 0 1 1"), encoding="ascii")
    grid = read_vtu(path)
    with pytest.raises(ResultError, match="COVERAGE_INCOMPLETE"):
        query_field(grid, "DISP", ids=[82])
    with pytest.raises(ResultError, match="UNIT_UNKNOWN"):
        query_field(grid, "DISP", ids=[81], operator="norm", units={"unit": "mm", "components": ("mm", "m", "mm")})
    with pytest.raises(ResultError, match="UNIT_UNKNOWN"):
        query_field(grid, "DISP", ids=[81], operator="norm")


def test_vtu_supports_inline_binary_masked_native_arrays(tmp_path):
    payload = struct.pack("<12d", 3., 4., 0., 0., float("nan"), 2., 0., 2., 0., 2., 0., 0.)
    binary = base64.b64encode(struct.pack("<I", len(payload)) + payload).decode("ascii")
    xml = _vtu(vector=binary, coverage="1 0 1 1").replace('Name="DISP"', 'Name="DISP" format="binary"')
    path = tmp_path / "frame.vtu"
    path.write_text(xml, encoding="ascii")
    grid = read_vtu(path)
    assert query_field(grid, "DISP", ids=[81], component="D2")[0].tolist() == [4.]
    with pytest.raises(ResultError, match="COVERAGE_INCOMPLETE"):
        query_field(grid, "DISP", ids=[82], component="D2")


HISTORY = '"time","INTERNAL ENERGY","  BRIC 101  hex  SX ","NODE 12 n12 DX"\n0,0,0,0\n1,4,2,3\n3,16,5,9\n'
TITLES = f"{1:10d}\r\n{'SX':10s}\r\n{0:10d}{'DX':10s}n12\r\n".encode("ascii")


def test_history_titles_header_bindings_and_safe_npy(tmp_path):
    csv_file, titles_file = tmp_path / "history.csv", tmp_path / "TITLES"
    csv_file.write_bytes(HISTORY.encode("ascii"))
    titles_file.write_bytes(TITLES)
    titles, table = read_titles(titles_file), read_th_csv(csv_file)
    assert titles["raw_bytes"] == TITLES
    assert titles["sha256"] == hashlib.sha256(TITLES).hexdigest()
    assert table["raw_header"][2] == "  BRIC 101  hex  SX "
    bindings = bind_history_columns(titles, table["raw_header"], expected_columns=["BRIC 101 hex SX", "NODE 12 n12 DX"],
                                    solid_formulation="hex8_1pt")
    assert [binding["source_titles_pair_index"] for binding in bindings] == [None, 0, 1]
    assert bindings[1]["output_type"] == "Solid"
    assert bindings[1]["entity_id"] == 101
    assert bindings[1]["component"] == "xx"
    assert bindings[2]["entity_title"] == "n12"
    manifest = extract_history(csv_file, titles_file, {"operation_dir": tmp_path, "solid_formulation": "hex8_1pt"})
    assert isinstance(manifest, DatasetManifestV1)
    assert manifest.dataset_kind == "history" and manifest.frames == ()
    series, = manifest.series
    assert series.series_id == "history:all" and series.row_count == 3
    assert read_safe_npy_v2(series.coordinate_ref.path, root=tmp_path).tolist() == [0, 1, 3]
    assert read_safe_npy_v2(series.source_row_index_ref.path, root=tmp_path).tolist() == [0, 1, 2]


@pytest.mark.parametrize(("change", "code"), [
    (("time", "cycle"), "MISSING_TIME"), (("1,4", "0,4"), "INVALID_DATASET"),
    (("3,16", ".5,16"), "INVALID_DATASET"), (("1,4", "nan,4"), "INVALID_DATASET"),
    (("1,4,2,3", "1,4,2"), "INVALID_DATASET"),
    (("INTERNAL ENERGY", "BRIC 101 hex SX"), "INVALID_DATASET"),
    (("BRIC 101  hex  SX", "NODE 101 hex SX"), "INVALID_DATASET"),
    (("NODE 12 n12 DX", "NODE 12 other DX"), "INVALID_DATASET"),
    (("BRIC 101  hex  SX", "BRIC 101 hex SY"), "INVALID_DATASET"),
])
def test_history_refuses_corrupt_columns_and_times(tmp_path, change, code):
    csv_file, titles_file = tmp_path / "history.csv", tmp_path / "TITLES"
    csv_file.write_bytes(HISTORY.replace(*change).encode("ascii"))
    titles_file.write_bytes(TITLES)
    with pytest.raises(ResultError, match=code):
        table = read_th_csv(csv_file)
        bind_history_columns(read_titles(titles_file), table["raw_header"])


def test_impulse_secant_uses_one_sided_endpoints_and_real_times():
    assert official_impulse_secant([0, 1, 3], [0, 1, 9]).tolist() == [1, 3, 4]
    values, operator = official_impulse_secant([0, 1, 3], [0, 1, 9], converter_sha256="a" * 64)
    assert values.tolist() == [1, 3, 4]
    assert operator["id"] == "official_impulse_secant" and operator["version"] == "a" * 64
    for times, impulse in [([0, 1], [0, 1]), ([0, 1, 1], [0, 1, 9]), ([0, 3, 2], [0, 1, 9])]:
        with pytest.raises(ResultError, match="INVALID_DATASET"):
            official_impulse_secant(times, impulse)


def _run(tmp_path, files, solver="opencourant"):
    store = RunStore(tmp_path / "solver runs")
    source = store.root / ROOT_ID
    source.mkdir(parents=True)
    outputs = {}
    for name, data in files.items():
        target = source / name
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(data)
        outputs[name] = hashlib.sha256(data).hexdigest()
    run = NativeRun(ROOT_ID, NODE, source, solver_id=solver, state="completed", parse_state="readable",
                    units={"length": "mm", "velocity": "mm/s", "stress": "MPa", "energy": "mJ"},
                    input_revision="a" * 64, snapshot={"artifacts": outputs})
    store.write(run)
    return store, source


def _fixture_animation(tmp_path, program=None, *, name="caseA001", options=None):
    if program is None:
        program = f"import sys\nsys.stdout.write({_vtk()!r})\nprint('converter diagnostic', file=sys.stderr)"
    payload = program.encode("ascii")
    store, source = _run(tmp_path, {name: gzip.compress(payload) if name.endswith(".gz") else payload})
    deck = _rad_mesh_deck()
    (source / "fixture.rad").write_bytes(deck)
    live = tmp_path / "fixture.rad"
    live.write_bytes(deck)
    digest = hashlib.sha256(deck).hexdigest()
    job = json.loads((source / "job.json").read_bytes())
    job["snapshot"]["files"] = [{"source_copy": "fixture.rad", "execution_path": "fixture.rad",
                                "original_sha256": digest, "executed_sha256": digest,
                                "source_path": str(live), "stat": {}}]
    atomic_json(source / "job.json", job)
    plan = plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"],
                           {"allow_custom_executable": True, "expected_mesh": MESH, **(options or {})})
    return store, source, plan


def _wait(manager, store, receipt, states=TERMINAL_OPERATIONS):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        current = operation_snapshot(manager, store, ROOT_ID, receipt["id"])
        if current["state"] in states:
            return current
        time.sleep(.025)
    pytest.fail(f"timed out waiting for states {states}; latest receipt {current}")


@pytest.fixture
def manager():
    manager = JobManager()
    yield manager
    assert manager.close(timeout=15)


def test_job_conversion_and_saveas_keep_the_same_result_ref(tmp_path, monkeypatch, manager):
    launched = []
    real_popen = subprocess.Popen

    def executor(argv, **kwargs):
        launched.append((argv, kwargs))
        return real_popen(argv, **kwargs)

    monkeypatch.setenv("QT_PLUGIN_PATH", "contamination")
    monkeypatch.setenv("QML2_IMPORT_PATH", "contamination")
    store, source, plan = _fixture_animation(tmp_path, name="run with spacesA001.gz",
        options={"exact_required_set": list(TENSORS), "expected_mesh": MESH})
    source_job = (source / "job.json").read_bytes()
    assert not launched
    assert plan == plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"], plan["options"])
    queued = submit_conversion(manager, store, plan, executor=executor)
    assert queued["state"] == "queued"
    receipt = _wait(manager, store, queued)
    assert receipt["state"] == "completed", receipt["error"]
    folder = operation_dir(store, ROOT_ID, receipt["id"])
    assert (source / "job.json").read_bytes() == source_job
    assert (folder / "inputs/source-receipt.json").read_bytes() == source_job
    assert (folder / "stderr.log").read_text().strip() == "converter diagnostic"
    assert (folder / "outputs/frame-001.vtk").read_text().startswith("# vtk DataFile")
    assert (folder / "inputs/inA001").exists() and (folder / "inputs/inA001.gz").exists()
    ((argv, kwargs),) = launched
    command = json.loads(argv[-4]) if os.name == "nt" else argv
    assert command == [sys.executable, "inA001"]
    assert kwargs["shell"] is False and kwargs["stdin"] == subprocess.DEVNULL
    assert not {"PYTHONPATH", "QT_PLUGIN_PATH", "QML2_IMPORT_PATH"} & kwargs["env"].keys()
    assert kwargs["cwd"] == folder / "inputs"
    dataset = inspect_dataset(store, receipt["id"])
    assert dataset.dataset_kind == "field" and dataset.frames[0]["time"] == .125
    base = make_result_ref(store, ROOT_ID, receipt["id"], "A001", "Displacement", selection={"ids": [11, 15]})
    ref = base.with_operator({"id": "norm", "version": "1", "params": {}})
    result = read_result(store, ref)
    assert result.ref is ref and result.values == (5, 1)
    assert result.operator_provenance["requested_ref_sha256"] != result.operator_provenance["base_ref_sha256"]
    assert result.operator_provenance["frame_time"] == .125
    destination = tmp_path / "다른 이름"
    store.copy_to(destination)
    store.root.rename(tmp_path / "original moved")
    assert read_result(RunStore(destination), ResultRefV1.from_dict(ref.to_dict())).to_dict() == result.to_dict()


def test_history_job_and_registered_curve_query(tmp_path, manager):
    program = f"from pathlib import Path\nimport sys\nPath(sys.argv[1] + '.csv').write_bytes({HISTORY.encode()!r})"
    store, source = _run(tmp_path, {"sampleT01": program.encode(), "sampleT01_TITLES": TITLES})
    _snapshot_sidecar(source, _th_deck("BRIC", ("SX",), "hex", node=True), tmp_path / "working.rad")
    plan = plan_conversion(store, ROOT_ID, "OC-TH4-1", sys.executable,
                           options={"expected_columns": ["BRIC 101 hex SX", "NODE 12 n12 DX"],
                                    "allow_custom_executable": True, "solid_formulation": "hex8_1pt"})
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "completed", receipt["error"]
    assert receipt["execution"][0]["argv"][1:] == ["inT01", "history"]
    assert inspect_dataset(store, receipt["id"], consumer_kind="history").series[0].row_count == 3
    ref = make_result_ref(store, ROOT_ID, receipt["id"], "history:all", "BRIC 101 hex SX")
    result = read_result(store, ref)
    assert result.ref is ref and ref.geometry_state == "unknown"
    assert result.sample_coordinates == (0, 1, 3) and result.values == (0, 2, 5)
    assert result.operator_provenance["column_binding"]["output_type"] == "Solid"
    with pytest.raises(ResultError, match="UNSUPPORTED_OBSERVABLE"):
        inspect_dataset(store, ref, consumer_kind="field")
    with pytest.raises(ResultError, match="UNSUPPORTED_OBSERVABLE"):
        read_result(store, ref.with_operator({"id": "mean"}))
    with pytest.raises(ResultError, match="COVERAGE_INCOMPLETE"):
        read_result(store, replace(ref, coverage={"time_interval": [0, 3.5]}))


def test_vtu_registered_extraction_needs_no_converter(tmp_path, manager):
    store, _ = _run(tmp_path, {"results/selected.vtu": _vtu().encode()}, solver="calculix")
    plan = plan_conversion(store, ROOT_ID, "VTU-1", frame_ids=["results/selected.vtu"])

    def unexpected(*args, **kwargs):
        pytest.fail("VTU extraction should not create a process")

    receipt = _wait(manager, store, submit_conversion(manager, store, plan, executor=unexpected))
    assert receipt["state"] == "completed", receipt["error"]
    ref = make_result_ref(store, ROOT_ID, receipt["id"], "results/selected.vtu", "DISP",
                          component="x", selection={"kind": "node_ids", "ids": [82, 81]})
    assert read_result(store, ref).values == (0, 3)
    assert receipt["execution"] == []


@pytest.mark.parametrize(("program", "timeout", "state", "error"), [
    ("raise SystemExit(7)", 15., "failed", "CONVERTER_EXIT"),
    ("import time; time.sleep(60)", 1., "failed", "TIMEOUT"),
    ("", 15., "failed", "EMPTY_OUTPUT"),
    ("print('not vtk')", 15., "failed", "INVALID_DATASET"),
])
def test_job_failure_does_not_publish_dataset(tmp_path, manager, program, timeout, state, error):
    store, _, plan = _fixture_animation(tmp_path, program)
    receipt = _wait(manager, store, submit_conversion(manager, store, plan, timeout=timeout))
    assert receipt["state"] == state and receipt["error"]["code"] == error
    assert not (operation_dir(store, ROOT_ID, receipt["id"]) / "dataset.json").exists()


@pytest.mark.parametrize("stage", ["running", "validating"])
def test_cancel_prevents_publication_and_active_duplicate_reuses_id(tmp_path, manager, monkeypatch, stage):
    entered = threading.Event()
    release = threading.Event()
    if stage == "running":
        store, _, plan = _fixture_animation(tmp_path, "import time; time.sleep(60)")
    else:
        store, _, plan = _fixture_animation(tmp_path)
        original = converters.validate_oc_outputs

        def blocked(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return original(*args, **kwargs)

        monkeypatch.setattr(converters, "validate_oc_outputs", blocked)
    queued = submit_conversion(manager, store, plan)
    try:
        _wait(manager, store, queued, {stage})
        if stage == "validating":
            assert entered.wait(5)
        assert submit_conversion(manager, store, plan)["id"] == queued["id"]
        with pytest.raises(ValueError):
            store.copy_to(tmp_path / "busy save")
        assert cancel_operation(manager, queued["id"])
    finally:
        release.set()
    receipt = _wait(manager, store, queued)
    assert receipt["state"] == "cancelled", receipt["error"]
    assert receipt["returncode"] == (0 if stage == "validating" else None)
    assert receipt["dataset_ref"] is None
    assert not (operation_dir(store, ROOT_ID, queued["id"]) / "dataset.json").exists()
    assert not cancel_operation(manager, queued["id"])
    with store.lease():
        pass


def test_source_changes_before_submit_during_validation_and_after_publication(tmp_path, manager, monkeypatch):
    store, source, plan = _fixture_animation(tmp_path)
    path = source / "caseA001"
    original_input = path.read_bytes()
    path.write_bytes(b"changed")
    failed = _wait(manager, store, submit_conversion(manager, store, plan))
    assert failed["error"]["code"] == "SOURCE_CHANGED"
    path.write_bytes(original_input)
    validate = converters.validate_oc_outputs

    def tamper(*args, **kwargs):
        result = validate(*args, **kwargs)
        path.write_bytes(b"tampered during validation")
        return result

    with monkeypatch.context() as context:
        context.setattr(converters, "validate_oc_outputs", tamper)
        failed = _wait(manager, store, submit_conversion(manager, store, plan))
    assert failed["error"]["code"] == "SOURCE_CHANGED"
    assert failed["dataset_ref"] is None
    path.write_bytes(original_input)
    completed = _wait(manager, store, submit_conversion(manager, store, plan))
    assert completed["state"] == "completed"
    ref = make_result_ref(store, ROOT_ID, completed["id"], "A001", "Displacement", component="x", selection={"ids": [11]})
    job_path = source / "job.json"
    job = json.loads(job_path.read_bytes())
    job["stale"] = True
    atomic_json(job_path, job)
    assert resolve_result_ref(store, ref)["availability"] == "stale"
    assert read_result(store, ref).values == (3,)
    path.write_bytes(b"tampered after completion")
    with pytest.raises(ResultError, match="HASH_MISMATCH") as error:
        resolve_result_ref(store, ref)
    assert error.value.availability == "corrupt"


def test_recovery_quarantines_unlocked_partial_files(tmp_path, manager):
    store, _ = _run(tmp_path, {})
    operation_id = "b" * 32
    folder = operation_dir(store, ROOT_ID, operation_id)
    atomic_json(folder / "operation.json", {"schema_version": 1, "id": operation_id, "source_run_id": ROOT_ID,
                                           "state": "validating", "pid": os.getpid()})
    (folder / "some.vtk.part").touch()
    (folder / "dataset.json").touch()
    with store.lease():
        assert manager.operation_snapshot(store, ROOT_ID, operation_id)["state"] == "validating"
    assert manager.operation_snapshot(store, ROOT_ID, operation_id)["state"] == "validating"
    assert (folder / "some.vtk.part").exists() and (folder / "dataset.json").exists()
    store.recover()
    receipt = manager.operation_snapshot(store, ROOT_ID, operation_id)
    assert receipt["state"] == "failed" and receipt["error"]["code"] == "INTERRUPTED"
    assert (folder / "some.vtk.interrupted").exists()
    assert not (folder / "dataset.json").exists()
    atomic_json(folder / "operation.json", {"schema_version": 2, "id": operation_id})
    with pytest.raises(ResultError, match="UNSUPPORTED_CAPABILITY"):
        manager.operation_snapshot(store, ROOT_ID, operation_id)


def test_gunzip_limit_and_missing_titles_are_enforced(tmp_path, manager):
    store, _, plan = _fixture_animation(tmp_path, name="caseA001.gz", options={"gunzip_limit_bytes": 128})
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "failed" and "gunzip size limit" in receipt["error"]["detail"]
    assert not (operation_dir(store, ROOT_ID, receipt["id"]) / "inputs/inA001").exists()
    with pytest.raises(ResultError, match="UNSUPPORTED_OBSERVABLE"):
        plan_conversion(store, ROOT_ID, "OC-TH4-1", sys.executable)


def test_opencourant_planning_requires_saved_deck_authority(tmp_path):
    store, source = _run(tmp_path, {"caseA001": b"animation", "caseT01": b"history", "caseT01_TITLES": TITLES})
    with pytest.raises(ResultError, match="SOURCE_MISMATCH"):
        plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"],
                        {"allow_custom_executable": True, "expected_mesh": MESH})
    _snapshot_sidecar(source, _rad_mesh_deck(), tmp_path / "working.rad")
    with pytest.raises(ResultError, match="SOURCE_MISMATCH"):
        plan_conversion(store, ROOT_ID, "OC-TH4-1", sys.executable,
                        options={"allow_custom_executable": True, "solid_formulation": "hex8_1pt",
                                 "expected_columns": ["BRIC 101 hex SX"]})


def test_close_cancels_active_conversion(tmp_path, manager):
    store, _, plan = _fixture_animation(tmp_path, "import time; time.sleep(60)")
    queued = submit_conversion(manager, store, plan)
    _wait(manager, store, queued, {"running"})
    assert manager.close()
    assert manager.operation_snapshot(store, ROOT_ID, queued["id"])["state"] == "cancelled"
    with store.lease():
        pass


def test_solid_shears_follow_the_official_converter_slots(tmp_path):
    path = tmp_path / "stress.vtk"
    path.write_text(_vtk(replacements={"12 0 0 0 0 0 0 0 0": "12 1 2 1 0 7 2 7 0"}), encoding="ascii")
    grid = read_vtk(path)
    for component, expected in (("xy", 1.), ("yz", 2.), ("xz", 7.), ("zy", 2.), ("zx", 7.)):
        assert query_field(grid, "3DELEM_Stress", component=component)[0].tolist() == [expected]
    tensor, _ = query_field(grid, "3DELEM_Stress")
    assert tensor[0].tolist() == [[12, 1, 7], [1, 0, 2], [7, 2, 0]]
    vm, _ = query_field(grid, "3DELEM_Stress", component="vm")
    assert vm == pytest.approx([np.sqrt(12**2 + 3 * (1 + 4 + 49))])


def test_shell_layer_default_and_reference_rounding(tmp_path):
    path = tmp_path / "animation.vtk"
    path.write_text(_vtk(replacements={"3 4 0\n": "2.9999809 4 0\n"}), encoding="ascii")
    assert read_vtk(path, expected=MESH).reference_points[0, 0] == pytest.approx(-.0000191)
    missing = tuple(name for name in TENSORS if "layer__" in name and "layer__1" not in name)
    path.write_text(_vtk(missing=missing), encoding="ascii")
    with pytest.raises(ResultError, match="SCHEMA_MISMATCH"):
        read_vtk(path)
    assert read_vtk(path, exact_required_set=set(TENSORS) - set(missing),
                    expected={**MESH, "shell_nip": {"101": 0}}).time == .125


def test_eroded_stresses_are_missing_and_raw_status_is_queryable(tmp_path, manager):
    vtk = _vtk().replace("SCALARS 3DELEM int 1", "SCALARS EROSION_STATUS int 1\nLOOKUP_TABLE default\n1 0\nSCALARS 3DELEM int 1")
    store, _, plan = _fixture_animation(tmp_path, f"print({vtk!r})")
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "completed", receipt["error"]
    ref = make_result_ref(store, ROOT_ID, receipt["id"], "A001", "3DELEM_Stress", component="xx")
    for operator in ("values", "min", "max", "mean"):
        result = read_result(store, ref.with_operator({"id": operator}))
        assert result.values == ()
        assert dict(result.counts) == {"total": 1, "valid": 0, "expected": 1, "missing": 1, "eroded": 1}
        assert result.operator_provenance["eroded_ids"] == (202,)
    status = make_result_ref(store, ROOT_ID, receipt["id"], "A001", "EROSION_STATUS", selection={"ids": [202]})
    assert read_result(store, status).values == (0,)
    path = tmp_path / "official.vtk"
    path.write_text(vtk, encoding="ascii")
    overridden = read_vtk(path, erosion_active_value=0)
    assert query_field(overridden, "3DELEM_Stress", component="xx", operator="mean")[0] == 12


@pytest.mark.parametrize(("array", "analysis_type", "time", "kind"), [
    ("TimeValue", None, .75, None), ("TIME", None, .75, None),
    ("Native_value", 1, .75, "time"), ("Native_value", 2, None, "frequency"),
])
def test_vtu_preserves_native_time_metadata_and_reordered_labels(tmp_path, manager, array, analysis_type, time, kind):
    meta = f'<DataArray Name="{array}" type="Float64">0.75</DataArray>'
    if analysis_type is not None:
        meta += f'<DataArray Name="Native_analysis_type" type="Int32">{analysis_type}</DataArray>'
    meta += '<DataArray Name="Native_step" type="Int32">3</DataArray><DataArray Name="increment" type="Int32">7</DataArray>'
    xml = _vtu().replace("<UnstructuredGrid>", f"<UnstructuredGrid><FieldData>{meta}</FieldData>")
    xml = xml.replace('ComponentName0="D1" ComponentName1="D2" ComponentName2="D3"',
                      'ComponentName0="D3" ComponentName1="D1" ComponentName2="D2"')
    xml = xml.replace('<CellData><DataArray type="Int64" Name="NativeElementId">12</DataArray></CellData>', "")
    store, _ = _run(tmp_path, {"frame.vtu": xml.encode()}, solver="calculix")
    plan = plan_conversion(store, ROOT_ID, "VTU-1", frame_ids=["frame.vtu"], options={"exact_required_set": ["DISP"]})
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "completed", receipt["error"]
    frame = inspect_dataset(store, receipt["id"]).frames[0]
    assert frame["time"] == time and frame["step"] == 3 and frame["increment"] == 7
    assert frame.get("coordinate_kind") == kind
    for axis in ("x", "D1"):
        ref = make_result_ref(store, ROOT_ID, receipt["id"], "frame.vtu", "DISP",
                              component=axis, selection={"ids": [81]})
        result = read_result(store, ref)
        assert result.values == (4.,)
        assert result.operator_provenance["frame_time"] == time


def test_vtu_requires_declared_ordinal_ids_and_rejects_empty_physics(tmp_path, manager):
    xml = _vtu().replace('<DataArray type="Int64" Name="NativeNodeId">81 82 83 84</DataArray>', "")
    path = tmp_path / "ordinal.vtu"
    path.write_text(xml, encoding="ascii")
    with pytest.raises(ResultError, match="MISSING_ID"):
        read_vtu(path)
    grid = read_vtu(path, node_id_policy="one_based")
    assert query_field(grid, "DISP", ids=[1], component="x")[0].tolist() == [3.]
    xml = xml.replace(xml.split("<PointData>")[1].split("</PointData>")[0], "")
    store, _ = _run(tmp_path, {"empty.vtu": xml.encode()}, solver="calculix")
    for options in ({}, {"exact_required_set": ["DISP"]}):
        plan = plan_conversion(store, ROOT_ID, "VTU-1", frame_ids=["empty.vtu"],
                               options={"node_id_policy": "one_based", **options})
        result = _wait(manager, store, submit_conversion(manager, store, plan))
        assert result["error"]["code"] == "SCHEMA_MISMATCH"


def test_history_requires_matching_source_entities_and_formulation(tmp_path):
    path = tmp_path / "titles"
    path.write_bytes(TITLES)
    titles = read_titles(path)
    headers = ["time", "INTERNAL ENERGY", "BRIC 101 hex SX", "NODE 12 n12 DX"]
    with pytest.raises(ResultError, match="SOURCE_MISMATCH"):
        bind_history_columns(titles, headers)
    with pytest.raises(ResultError, match="SOURCE_MISMATCH"):
        bind_history_columns(titles, headers, solid_formulation="hex8_1pt",
                             expected_bindings=[{"entity_id": 102, "group_title": "BRIC", "variable_code": "SX"}])
    with pytest.raises(ResultError, match="SCHEMA_MISMATCH"):
        bind_history_columns(titles, headers, solid_formulation="hex8_1pt", exact_required_set=["BRIC 101 hex SY"])
    expected = [{"entity_id": 101, "group_title": "BRIC", "variable_code": "SX", "entity_title": "hex"}]
    with pytest.raises(ResultError, match="unexpected history column"):
        bind_history_columns(titles, headers, solid_formulation={"101": "hex8_1pt"}, expected_bindings=expected)
    bindings = bind_history_columns(titles, headers, solid_formulation={"101": "hex8_1pt"},
        expected_bindings=[*expected, {"entity_id": 12, "group_title": "NODE", "variable_code": "DX"}])
    assert bindings[1]["entity_id"] == 101 and bindings[1]["quantity"] == "stress"


def _rad_mesh_deck(nip=5, *, material="LAW2"):
    return ("#RADIOSS STARTER\n/NODE\n" + "".join(
        f"{i:10d}{x:20}{y:20}{z:20}\n" for i, (x, y, z) in enumerate(ORIGINAL_POINTS, 11))
        + "/SH3N/10\n" + "".join(f"{i:10d}" for i in (101, 11, 12, 13)) + "\n"
        + "/TETRA4/20\n" + "".join(f"{i:10d}" for i in (202, 14, 15, 16, 17)) + "\n"
        + "/PART/10\nshell part\n         7         1\n/PART/20\nsolid part\n         8         1\n"
        + "/PROP/SHELL/7\nshell\n        24\n                   0\n"
        + f"{nip:10d}\n/PROP/SOLID/8\nsolid\n         1\n"
        + (f"/MAT/{material}/1\nmaterial\n" if material else "") + "/END\n").encode()


def test_manifest_deck_selection_semantics_and_reader_digest(tmp_path, monkeypatch, manager):
    store, source, plan = _fixture_animation(tmp_path)
    deck = source / "original.rad"
    deck.write_bytes(_rad_mesh_deck())
    job = json.loads((source / "job.json").read_bytes())
    job["snapshot"]["files"] = [{"source_copy": "original.rad", "original_sha256": hashlib.sha256(deck.read_bytes()).hexdigest(),
                                "source_path": "original.rad", "execution_path": "original.rad", "stat": {}}]
    atomic_json(source / "job.json", job)
    fresh = plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"], plan["options"])
    assert fresh["source_fingerprint"] != plan["source_fingerprint"]
    receipt = _wait(manager, store, submit_conversion(manager, store, fresh))
    assert receipt["state"] == "completed", receipt["error"]
    folder = operation_dir(store, ROOT_ID, receipt["id"])
    selection_file = store.root / "selections" / "s1" / "payload.json"
    saved = {"schema": "caelab.selection.v1", "entity_kind": "node", "ids": [11], "run_id": ROOT_ID,
             "mesh_revision": job["input_revision"]}
    saved["revision_sha256"] = hashlib.sha256(canonical_json_bytes({"selection": saved, "payload": [11]})).hexdigest()
    atomic_json(selection_file, saved)
    ref = make_result_ref(store, ROOT_ID, receipt["id"], "A001", "Displacement", component="x",
                          selection={"selection_id": "s1", "count": 1})
    saved_bytes = selection_file.read_bytes()
    assert ref.selection["payload_sha256"] == hashlib.sha256(saved_bytes).hexdigest()
    selection_file.write_bytes(canonical_json_bytes(saved))
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        read_result(store, ref)
    selection_file.write_bytes(saved_bytes)
    expected_hash = hashlib.sha256(canonical_json_bytes(json.loads((folder / "dataset.json").read_bytes()))).hexdigest()
    assert receipt["dataset_manifest_sha256"] == ref.dataset_manifest_sha256 == expected_hash
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        resolve_result_ref(store, replace(ref, dataset_manifest_sha256=receipt["dataset_ref"]["sha256"]))
    result = read_result(RunStore(folder), ref, live_stale=lambda run: run["id"] == ROOT_ID)
    assert result.values == (3,) and result.status == "stale"
    assert result.operator_provenance["base_quantity_ref_sha256"] == result.operator_provenance["requested_quantity_ref_sha256"]
    values = read_result(store, ref.with_operator({"id": "values"}))
    assert values.operator_provenance["requested_quantity_ref_sha256"] == hashlib.sha256(
        canonical_json_bytes(values.ref.to_dict())).hexdigest()
    for invalid in (replace(ref, units={"symbol": "mm", "unit_dimension": "pressure"}),
                    replace(ref, location={"association": "point", "integration_point": "IP2"}),
                    replace(ref, eligibility={"fit_eligible": True}),
                    replace(ref, eligibility={"query": False}),
                    replace(ref, basis={"kind": "global", "orientation_sha256": "a" * 64}),
                    replace(ref, basis={"kind": "global", "orientation_sha256": ""}),
                    replace(ref, basis={"kind": "local", "orientation_sha256": ""}),
                    replace(ref, location={**ref.location, "entity_id": 999}),
                    replace(ref, location={"association": "point", "layer": "middle"})):
        with pytest.raises(ResultError, match="SEMANTIC_MISMATCH"):
            read_result(store, invalid)
    atomic_json(selection_file, {**saved, "ids": 11})
    with pytest.raises(ResultError, match="SCHEMA_MISMATCH"):
        read_result(store, ref)
    atomic_json(selection_file, {**saved, "mesh_revision": "other"})
    with pytest.raises(ResultError, match="SOURCE_MISMATCH"):
        read_result(store, ref)
    atomic_json(selection_file, saved)
    with monkeypatch.context() as patch:
        patch.setattr(converters, "reader_code_sha256", lambda *_: "f" * 64)
        with pytest.raises(ResultError, match="HASH_MISMATCH"):
            read_result(store, ref)
    deck.write_bytes(b"modified input")
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        resolve_result_ref(store, ref)
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        store.copy_to(tmp_path / "refused")
    deck.write_bytes(_rad_mesh_deck())
    manifest = folder / "dataset.json"
    manifest.write_bytes(manifest.read_bytes() + b" ")
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        store.copy_to(tmp_path / "refused")


def test_reopen_save_as_duplicate_and_unrelated_invalid_receipt(tmp_path, manager):
    other = JobManager()
    store, _, plan = _fixture_animation(tmp_path, "import time; time.sleep(60)")
    interrupted = operation_dir(store, ROOT_ID, "b" * 32)
    orphan = {"schema_version": 1, "id": "b" * 32, "source_run_id": ROOT_ID, "state": "validating"}
    atomic_json(interrupted / "operation.json", orphan)
    (interrupted / "dataset.json").touch()
    (interrupted / "partial.part").touch()
    store.copy_to(tmp_path / "saved")
    copy = operation_dir(RunStore(tmp_path / "saved"), ROOT_ID, orphan["id"])
    assert not (copy / "dataset.json").exists()
    assert (copy / "partial.interrupted").exists()
    assert json.loads((copy / "operation.json").read_bytes())["error"]["code"] == "INTERRUPTED"
    atomic_json(interrupted / "operation.json", {**orphan, "state": "running"})
    RunStore(store.root).recover()
    assert json.loads((interrupted / "operation.json").read_bytes())["error"]["code"] == "INTERRUPTED"
    malformed = operation_dir(store, "c" * 32, "d" * 32)
    atomic_json(malformed / "operation.json", {**orphan, "state": []})
    queued = submit_conversion(manager, store, plan)
    try:
        _wait(manager, store, queued, {"running"})
        assert submit_conversion(other, RunStore(store.root), plan)["id"] == queued["id"]
        tampered = {**plan, "options": {**plan["options"], "exact_required_set": ["Velocity"]}}
        for owner in (manager, other):
            with pytest.raises(ResultError, match="STALE_PLAN"):
                submit_conversion(owner, store, tampered)
        assert not other.cancel_operation(queued["id"])
    finally:
        assert manager.cancel_operation(queued["id"])
        assert other.close()
    assert _wait(manager, store, queued)["state"] == "cancelled"
    assert queued["id"] not in manager._operations


def test_verify_plan_preserves_deadline_and_requires_trusted_converter(tmp_path, monkeypatch):
    store, _, plan = _fixture_animation(tmp_path)
    with pytest.raises(ResultError, match="UNTRUSTED_CONVERTER_EXECUTABLE"):
        plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"],
                        options={"expected_mesh": MESH, "install_root": str(tmp_path)})
    def deadline():
        raise ResultError("TIMEOUT", "from source digest")

    with pytest.raises(ResultError, match="TIMEOUT"):
        converters.verify_plan(store, plan, deadline)
    tool = tmp_path / "exec" / "anim_to_vtk_win64.exe"
    tool.parent.mkdir()
    tool.write_bytes(b"fixture")
    with pytest.raises(ResultError, match="UNTRUSTED_CONVERTER_EXECUTABLE"):
        plan_conversion(store, ROOT_ID, "OC-ANIM-1", tool, ["A001"], {"install_root": str(tmp_path)})
    plan_conversion(store, ROOT_ID, "OC-ANIM-1", tool, ["A001"],
                    {"install_root": str(tmp_path), "allow_custom_executable": True})
    shutil.copyfile(sys.executable, tool)
    with tool.open("ab") as binary:
        binary.write(b"anim_to_vtk\0OpenCourant\0DATASET UNSTRUCTURED_GRID\0TIME 1 1 double\0EROSION_STATUS int 1\0")
    with pytest.raises(ResultError, match="UNTRUSTED_CONVERTER_EXECUTABLE"):
        plan_conversion(store, ROOT_ID, "OC-ANIM-1", tool, ["A001"], {"install_root": str(tmp_path)})
    trusted = plan_conversion(store, ROOT_ID, "OC-ANIM-1", tool, ["A001"],
                              options={"allow_custom_executable": True, "expected_mesh": MESH,
                                       "install_root": str(tmp_path)})
    assert trusted["options"]["erosion_active_value"] == plan["options"]["erosion_active_value"] == 1
    if os.name == "nt":
        env = converters.converter_environment(trusted, {"Path": "original"})
        assert env["PATH"].split(os.pathsep)[0] == str(tool.parent)
        assert env["PATH"].split(os.pathsep)[-1] == "original"
        assert env["RAD_CFG_PATH"] == str(tmp_path / "hm_cfg_files") and env["KMP_STACKSIZE"] == "400m"


@pytest.mark.skipif(os.name != "nt", reason="Windows converter release archive")
@pytest.mark.parametrize(("archive_level", "archive_name", "prefix"), [
    (0, "OpenCourant_win64.zip", "OpenCourant"),
    (1, "OpenRadioss_win64.zip", "OpenRadioss"),
    (2, "OpenRadioss_win64_20261011.zip", "OpenRadioss_win64"),
    (1, "OpenCourant_win64_20261011.zip", "OpenCourant_win64"),
])
def test_windows_converter_must_match_member_of_trusted_release(tmp_path, monkeypatch, archive_level, archive_name, prefix):
    monkeypatch.setattr(converters, "_VERIFIED_WINDOWS_MEMBERS", {})
    store, _, _ = _fixture_animation(tmp_path)
    install = tmp_path / "distribution" / "OpenCourant"
    tool = install / "exec" / "anim_to_vtk_win64.exe"
    tool.parent.mkdir(parents=True)
    shutil.copyfile(sys.executable, tool)
    archive = (install, install.parent, install.parent.parent)[archive_level] / archive_name
    with zipfile.ZipFile(archive, "w") as package:
        package.write(tool, f"{prefix}/exec/anim_to_vtk_win64.exe")
    opts = {"install_root": str(install)}
    with pytest.raises(ResultError, match="UNTRUSTED_CONVERTER_EXECUTABLE"):
        plan_conversion(store, ROOT_ID, "OC-ANIM-1", tool, ["A001"], opts)
    monkeypatch.setattr(converters, "_PINNED_WINDOWS_ZIP_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    plan = plan_conversion(store, ROOT_ID, "OC-ANIM-1", tool, ["A001"], opts)
    assert plan["executable"]["sha256"] == hashlib.sha256(tool.read_bytes()).hexdigest()
    archive.unlink()
    assert plan_conversion(store, ROOT_ID, "OC-ANIM-1", tool, ["A001"], opts) == plan
    with tool.open("ab") as binary:
        binary.write(b"modified but still executable")
    with pytest.raises(ResultError, match="UNTRUSTED_CONVERTER_EXECUTABLE"):
        plan_conversion(store, ROOT_ID, "OC-ANIM-1", tool, ["A001"], opts)


def test_final_log_size_and_owned_descendant_exit(tmp_path, monkeypatch, manager):
    import caelab.jobs as jobs

    program = "import sys\nsys.stderr.write('x' * 1000)"
    store, _, plan = _fixture_animation(tmp_path / "log", program, options={"max_log_bytes": 100})
    with monkeypatch.context() as patch:
        patch.setattr(jobs, "_native_exited", lambda process: process.wait(timeout=5) is not None)
        receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["returncode"] == 0 and receipt["error"]["code"] == "LOG_LIMIT_EXCEEDED"
    assert receipt["dataset_ref"] is None
    marker = tmp_path / "natural exit"
    child = f"import time; from pathlib import Path; time.sleep(.4); Path({str(marker)!r}).touch()"
    worker = f"import subprocess,sys\nsubprocess.Popen([sys.executable,'-c',{child!r}])\nprint({_vtk()!r})"
    store, _, plan = _fixture_animation(tmp_path / "descendant", worker)
    completed = _wait(manager, store, submit_conversion(manager, store, plan))
    assert completed["state"] == "completed", completed["error"]
    assert marker.exists()


def test_unconfirmed_cleanup_keeps_store_lease_and_publishes_no_dataset(tmp_path, monkeypatch):
    import caelab.jobs as jobs

    manager = JobManager()
    store, _, plan = _fixture_animation(tmp_path)
    real_finish = jobs._finish_owned

    def unconfirmed(*args, **kwargs):
        raise TimeoutError("tree did not confirm exit")

    with monkeypatch.context() as patch:
        patch.setattr(jobs, "_finish_owned", unconfirmed)
        queued = submit_conversion(manager, store, plan)
        failed = _wait(manager, store, queued)
        assert failed["state"] == "failed" and failed["cleanup_failed"]
        assert failed["error"]["code"] == "PROCESS_CLEANUP_FAILED"
        assert not (operation_dir(store, ROOT_ID, queued["id"]) / "dataset.json").exists()
        with pytest.raises(ValueError):
            store.lease()
        assert not manager.close()
    assert jobs._finish_owned is real_finish
    control = manager._operations[queued["id"]]
    receipt_path = operation_dir(store, ROOT_ID, queued["id"]) / "operation.json"
    write = jobs.atomic_json

    def verify_cleanup_order(path, data):
        if path == receipt_path and not data["cleanup_failed"]:
            assert not control["operation_lock"].is_locked
            with pytest.raises(ValueError):
                store.lease()
            raise OSError("receipt write unavailable")
        write(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(jobs, "atomic_json", verify_cleanup_order)
        assert not manager.close()
        assert json.loads(receipt_path.read_bytes())["cleanup_failed"]
        assert control["lease"].is_locked
    for lock in (control["operation_lock"], control["lease"]):
        with monkeypatch.context() as patch:
            patch.setattr(lock, "release", unconfirmed)
            assert not manager.close()
            assert manager.operation_snapshot(store, ROOT_ID, queued["id"])["cleanup_failed"]
    release = control["lease"].release

    def release_after_publication():
        assert not json.loads(receipt_path.read_bytes())["cleanup_failed"]
        release()

    with monkeypatch.context() as patch:
        patch.setattr(control["lease"], "release", release_after_publication)
        assert manager.close()
    assert queued["id"] not in manager._operations
    assert not manager.operation_snapshot(store, ROOT_ID, queued["id"])["cleanup_failed"]
    with store.lease():
        pass


def test_solid_strain_uses_native_shear_order(tmp_path):
    tensor = "0 1 2 1 0 7 2 7 0"
    content = _vtk() + "TENSORS 3DELEM_Strain float\n0 0 0 0 0 0 0 0 0\n" + tensor + "\n"
    path = tmp_path / "strain.vtk"
    path.write_text(content, encoding="ascii")
    grid = read_vtk(path)
    for name, number in (("xy", 1), ("yz", 2), ("xz", 7)):
        assert query_field(grid, "3DELEM_Strain", component=name)[0].tolist() == [number]


def _snapshot_sidecar(source, deck, live_path, *, suffix=".inc", append=False):
    original, execution = f"input/frozen{suffix}", f"input/exec{suffix}"
    for relative in (original, execution):
        target = source / relative
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(deck)
    digest = hashlib.sha256(deck).hexdigest()
    live_path.write_bytes(deck)
    sidecar = source / "input/snapshot.json"
    files = json.loads(sidecar.read_bytes())["files"] if append else []
    atomic_json(sidecar, {"files": [*files, {
        "source_path": str(live_path), "source_copy": original, "execution_path": execution,
        "original_sha256": digest, "executed_sha256": digest}]})


@pytest.mark.parametrize("nip", [0, 5])
def test_saved_rad_authority_shell_layers_and_live_deck(tmp_path, manager, nip):
    missing = tuple(name for name in TENSORS if "layer__" in name and
                    ("layer__1" not in name if nip == 0 else "layer__3" in name))
    vtk = _vtk(missing=missing)
    store, source = _run(tmp_path, {"caseA001": f"print({vtk!r})".encode()})
    live = tmp_path / "working.inc"
    _snapshot_sidecar(source, _rad_mesh_deck(nip, material="LAW1" if nip == 0 else "LAW2"), live)
    opts = {"allow_custom_executable": True, "exact_required_set": []}
    plan = plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"], opts)
    mesh = plan["options"]["expected_mesh"]
    assert mesh["shell_ids"] == [101] and mesh["solid_ids"] == [202]
    assert mesh["shell_nip_by_id"] == {"101": nip}
    with pytest.raises(ResultError, match="SOURCE_MISMATCH"):
        plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"],
                        {**opts, "expected_mesh": {"shell_ids": [202], "solid_ids": [101]}})
    queued = submit_conversion(manager, store, plan)
    assert queued["plan_sha256"] == plan["plan_hash"]
    assert queued["progress"] == {"completed": 0, "total": 1, "unit": "steps"}
    receipt = _wait(manager, store, queued)
    if nip:
        assert receipt["state"] == "failed" and receipt["error"]["code"] == "SCHEMA_MISMATCH"
        assert receipt["error"]["message"]
        return
    assert receipt["state"] == "completed", receipt["error"]
    assert receipt["progress"] == {"completed": 1, "total": 1, "unit": "steps"}
    assert receipt["result"]["dataset_id"] == plan["cache_key"]
    ref = make_result_ref(store, ROOT_ID, receipt["id"], "A001", "Displacement", component="x", selection={"ids": [11]})
    assert read_result(store, ref).status == "available"
    from caelab.workbench import Workbench
    from types import SimpleNamespace
    node = SimpleNamespace(program_id="calculix")
    project = SimpleNamespace(path=tmp_path / "project.json", run_store=store.root, run_store_expected=True,
                              nodes={NODE: node}, input_file=lambda _: None)
    workbench = Workbench()
    assert read_result(workbench._run_store(project), ref, live_stale=False).status == "stale"
    node.program_id = "opencourant"
    job = json.loads((source / "job.json").read_bytes())
    project.input_file = lambda _: SimpleNamespace(path=job["input_path"], stale=False,
                                                   units=job["units"], unit_source=job["unit_source"])
    assert read_result(workbench._run_store(project), ref, live_stale=False).status == "available"
    live.write_bytes(b"edits to the working include")
    assert read_result(store, ref, live_stale=False).status == "stale"
    (source / "input/exec.inc").write_bytes(b"corrupt archived execution copy")
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        read_result(store, ref)


@pytest.mark.parametrize("stat_mode", ["absent", "empty", "matching"])
def test_workbench_run_list_checks_working_deck_hash_even_without_metadata_change(tmp_path, monkeypatch, stat_mode):
    from types import SimpleNamespace
    from caelab import workbench as workbench_module

    store, source = _run(tmp_path, {})
    live = tmp_path / "working.rad"
    _snapshot_sidecar(source, _rad_mesh_deck(), live)
    if stat_mode != "absent":
        path = source / "input/snapshot.json"
        sidecar = json.loads(path.read_bytes())
        metadata = workbench_module.fingerprint(live)
        sidecar["files"][0]["stat"] = metadata if stat_mode == "matching" else {}
        atomic_json(path, sidecar)
        monkeypatch.setattr(workbench_module, "fingerprint", lambda _: metadata)
    job = json.loads((source / "job.json").read_bytes())
    reference = SimpleNamespace(path=job["input_path"], stale=False, units=job["units"], unit_source=job["unit_source"])
    project = SimpleNamespace(path=tmp_path / "project.json", run_store=store.root, run_store_expected=True,
                              nodes={NODE: SimpleNamespace(program_id=job["solver_id"])},
                              input_file=lambda _: reference)
    workbench = workbench_module.Workbench()
    assert not workbench.native_runs(project)[0].stale
    live.write_bytes(live.read_bytes().replace(b"#RADIOSS", b"#CHANGED"))
    assert workbench.native_runs(project)[0].stale
    assert workbench.jobs.close()


@pytest.mark.parametrize("sidecar_content", ["{", '{"files":[null]}', '{"files":[],"prerequisites":[null]}'])
def test_workbench_marks_only_the_run_with_a_corrupt_input_sidecar_stale(tmp_path, sidecar_content):
    from types import SimpleNamespace
    from caelab.workbench import Workbench

    store, source = _run(tmp_path, {})
    healthy = store.read(source / "job.json")
    bad_id = "b" * 32
    broken = replace(healthy, id=bad_id, workdir=store.root / bad_id)
    store.write(broken)
    sidecar = broken.workdir / "input/snapshot.json"
    sidecar.parent.mkdir()
    sidecar.write_text(sidecar_content, encoding="ascii")
    reference = SimpleNamespace(path=healthy.input_path, stale=False, units=healthy.units, unit_source=healthy.unit_source)
    project = SimpleNamespace(path=tmp_path / "project.json", run_store=store.root, run_store_expected=True,
                              nodes={NODE: SimpleNamespace(program_id=healthy.solver_id)}, input_file=lambda _: reference)
    workbench = Workbench()
    runs = {run.id: run for run in workbench.native_runs(project)}
    assert not runs[healthy.id].stale
    assert runs[bad_id].stale and runs[bad_id].stale_reason
    assert workbench.jobs.close()


@pytest.mark.parametrize(("alias", "effective"), [("ELAST", 0), ("JOHNS", 3), ("ZERIL", 3), ("PLAS_PREDEF", 3),
                                                 ("PLAS_TAB", 3), ("COMPSH", 3), ("FABR", 3), ("HYD_JCOOK", 3),
                                                 ("VOID", 0), ("GLOBAL_REINFORCED_CONCRETE", 0)])
def test_shell_nip_uses_starter_material_alias(tmp_path, alias, effective):
    store, source = _run(tmp_path, {"caseA001": b"fixture"})
    _snapshot_sidecar(source, _rad_mesh_deck(0, material=alias), tmp_path / "work.rad")
    plan = plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"], {"allow_custom_executable": True})
    assert plan["options"]["expected_mesh"]["shell_nip_by_id"] == {"101": effective}


@pytest.mark.parametrize(("material", "effective"), [("LAW1", 0), ("JOHNS", 3)])
def test_shell_nip_resolves_verified_mat_include_and_normalizes_negative_n(tmp_path, material, effective):
    store, source = _run(tmp_path, {"caseA001": b"fixture"})
    root = _rad_mesh_deck(-1, material=None) + b"/MAT/LAW1/1\nignored after END\n"
    _snapshot_sidecar(source, root, tmp_path / "main.key", suffix=".key")
    opts = {"allow_custom_executable": True}
    with pytest.raises(ResultError, match="SOURCE_MISMATCH.*material"):
        plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"], opts)
    include = f"/MAT/{material}/1\nsteel\n  #EndData end of include\n/MAT/LAW1/1\nignored duplicate\n".encode()
    _snapshot_sidecar(source, include, tmp_path / "steel.mat",
                      suffix=".mat", append=True)
    plan = plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"], opts)
    assert plan["options"]["expected_mesh"]["shell_nip_by_id"] == {"101": effective}


def test_vtk_normalizes_negative_shell_nip(tmp_path):
    path = tmp_path / "shell.vtk"
    layers = [f"2DELEM_Stress_(layer__{layer})_" for layer in (2, 3, 4, 5)]
    path.write_text(_vtk(missing=layers), encoding="ascii")
    assert read_vtk(path, expected={"shell_nip_by_id": {"101": -1}}).shell_nip_by_id == {101: 0}
    with pytest.raises(ResultError, match="invalid shell integration count"):
        read_vtk(path, expected={"shell_nip_by_id": {"101": -2}})


def test_shell_deck_rejects_negative_nip_other_than_global_integration_switch(tmp_path):
    store, source = _run(tmp_path, {"caseA001": b"fixture"})
    _snapshot_sidecar(source, _rad_mesh_deck(-2), tmp_path / "working.rad")
    with pytest.raises(ResultError, match="SOURCE_MISMATCH.*invalid negative NIP -2"):
        plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"], {"allow_custom_executable": True})


def test_history_snapshot_excludes_inactive_includes_in_new_and_retained_archives(tmp_path):
    from caelab.native.input_snapshot import snapshot_radioss_input

    store, source = _run(tmp_path, {"caseT01": b"fixture", "caseT01_TITLES": TITLES})
    inputs = tmp_path / "working"
    inputs.mkdir()
    starter = inputs / "case_0000.rad"
    mesh, history = _th_deck("BRIC", ("SX",), "hex").split(b"/TH/", 1)
    starter.write_bytes(b"#include active.inc\n" + mesh + b" /eNd   $ finished\n#include stale-root.inc\n")
    (inputs / "case_0001.rad").write_bytes(b"/RUN/case/1\n/END\n")
    (inputs / "active.inc").write_bytes(b"/TH/" + history.removesuffix(b"/END\n")
                                       + b"#enddata\n#include stale-nested.inc\n")
    snapshot = snapshot_radioss_input(starter, source)
    assert {item["execution_path"] for item in snapshot["files"]} == {"case_0000.rad", "case_0001.rad", "active.inc"}
    sidecar = source / "input/snapshot.json"
    opts = {"allow_custom_executable": True}
    for retained in (False, True):
        if retained:
            for name in ("stale-root.inc", "stale-nested.inc"):
                raw = b"/EULER/MAT/1\n"
                (source / name).write_bytes(raw)
                sha = hashlib.sha256(raw).hexdigest()
                snapshot["files"].append({"execution_path": name, "source_copy": name,
                                          "original_sha256": sha, "execution_sha256": sha})
        atomic_json(sidecar, snapshot)
        plan = plan_conversion(store, ROOT_ID, "OC-TH4-1", sys.executable, options=opts)
        assert plan["options"]["solid_formulation"] == {"101": "hex8_1pt"}


@pytest.mark.parametrize(("requested", "plastic"), [(5, 5), (0, 3)])
def test_dat_snapshot_preserves_effective_nip_per_shell(tmp_path, manager, requested, plastic):
    rows = ("/MAT/LAW1/1\nelastic\n/MAT/LAW2/2\nplastic\n/PART/30\nsecond shell\n         7         2\n"
            "/SH3N/30\n" + "".join(f"{i:10d}" for i in (303, 11, 12, 13)) + "\n/END\n").encode()
    deck = _rad_mesh_deck(requested, material=None).replace(b"/END\n", rows)
    vtk = _vtk(extra_shell=True)
    store, source = _run(tmp_path, {"caseA001": f"print({vtk!r})".encode()})
    _snapshot_sidecar(source, deck, tmp_path / "mesh.dat", suffix=".dat")
    plan = plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"],
                           {"allow_custom_executable": True, "exact_required_set": []})
    expected = plan["options"]["expected_mesh"]
    assert expected["shell_nip_by_id"] == {"101": 0, "303": plastic}
    path = tmp_path / "missing-layer.vtk"
    path.write_text(_vtk(extra_shell=True, missing=["2DELEM_Stress_(layer__3)_"]), encoding="ascii")
    with pytest.raises(ResultError, match="SCHEMA_MISMATCH"):
        read_vtk(path, expected=expected, exact_required_set=[])
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "completed", receipt["error"]
    field = "2DELEM_Stress_(layer__3)_"
    ref = make_result_ref(store, ROOT_ID, receipt["id"], "A001", field, component="xx", selection={"ids": [303]})
    assert read_result(store, ref).values == (5,)
    membrane = make_result_ref(store, ROOT_ID, receipt["id"], "A001", field, component="xx", selection={"ids": [101]})
    with pytest.raises(ResultError, match="COVERAGE_INCOMPLETE"):
        read_result(store, membrane)
    path = tmp_path / "missing-face.vtk"
    missing = tuple(name for name in TENSORS if "upper" in name or ("layer__" in name and "layer__1" not in name))
    path.write_text(_vtk(missing=missing), encoding="ascii")
    with pytest.raises(ResultError, match="SCHEMA_MISMATCH"):
        read_vtk(path, expected={**MESH, "shell_nip_by_id": {"101": 0}}, exact_required_set=[])


@pytest.mark.parametrize(("statuses", "active", "value"), [("1 1", None, [12]), ("0 1", None, [12]),
                                                        ("0 0", None, []),
                                                        ("0 0", 0, [12]), ("0 1", 0, [])])
def test_erosion_rule_matches_producer_unless_explicitly_overridden(tmp_path, statuses, active, value):
    marker = "SCALARS EROSION_STATUS int 1\nLOOKUP_TABLE default\n" + statuses + "\n"
    path = tmp_path / "erosion.vtk"
    path.write_text(_vtk().replace("SCALARS 3DELEM int 1", marker + "SCALARS 3DELEM int 1"), encoding="ascii")
    grid = read_vtk(path, erosion_active_value=active)
    assert query_field(grid, "3DELEM_Stress", component="xx")[0].tolist() == value


@pytest.mark.parametrize("corrupt", ["2", "0.5"])
def test_erosion_status_rejects_nonbinary_values(tmp_path, corrupt):
    marker = f"SCALARS EROSION_STATUS float 1\nLOOKUP_TABLE default\n1 {corrupt}\n"
    path = tmp_path / "erosion.vtk"
    path.write_text(_vtk().replace("SCALARS 3DELEM int 1", marker + "SCALARS 3DELEM int 1"), encoding="ascii")
    with pytest.raises(ResultError, match="INVALID_DATASET: Invalid EROSION_STATUS value"):
        read_vtk(path)


def test_vtu_timevalue_precedes_time_and_empty_coverage_is_invalid(tmp_path, manager):
    path = tmp_path / "clock.vtu"
    metadata = ('<FieldData><DataArray Name="TIME" type="Float64">0.25</DataArray>'
                '<DataArray Name="TimeValue" type="Float64">0.75</DataArray></FieldData>')
    path.write_text(_vtu().replace("<UnstructuredGrid>", "<UnstructuredGrid>" + metadata), encoding="ascii")
    grid = read_vtu(path)
    from caelab.result_readers.vtk_fields import sample_metadata
    assert grid.time == .75 and sample_metadata(grid)["time_source"] == "TimeValue"
    zero = _vtu(coverage="0 0 0 0")
    path.write_text(zero, encoding="ascii")
    grid = read_vtu(path)
    with pytest.raises(ResultError, match="COVERAGE_INCOMPLETE"):
        query_field(grid, "DISP")
    with pytest.raises(ResultError, match="COVERAGE_INCOMPLETE"):
        read_vtu(path, exact_required_set=["DISP"])
    store, _ = _run(tmp_path, {"zero.vtu": zero.encode()}, solver="calculix")
    plan = plan_conversion(store, ROOT_ID, "VTU-1", frame_ids=["zero.vtu"], options={"exact_required_set": ["DISP"]})
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "failed" and receipt["error"]["code"] == "COVERAGE_INCOMPLETE"
    plan = plan_conversion(store, ROOT_ID, "VTU-1", frame_ids=["zero.vtu"])
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "failed" and receipt["error"]["code"] == "COVERAGE_INCOMPLETE"


def test_optional_vtu_zero_coverage_does_not_block_required_or_requested_field(tmp_path, manager):
    optional = ('<DataArray Name="Acceleration" type="Float64" NumberOfComponents="3">'
                '0 0 0 0 0 0 0 0 0 0 0 0</DataArray>'
                '<DataArray Name="Coverage_Acceleration" type="Int64">0 0 0 0</DataArray>')
    data = _vtu().replace("</PointData>", optional + "</PointData>")
    store, _ = _run(tmp_path, {"covered.vtu": data.encode()}, solver="calculix")
    plan = plan_conversion(store, ROOT_ID, "VTU-1", frame_ids=["covered.vtu"],
                           options={"exact_required_set": ["DISP"]})
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "completed", receipt["error"]
    ref = make_result_ref(store, ROOT_ID, receipt["id"], "covered.vtu", "DISP", component="x")
    assert read_result(store, ref).values == (3, 0, 0, 2)
    optional_ref = make_result_ref(store, ROOT_ID, receipt["id"], "covered.vtu", "Acceleration", component="x")
    with pytest.raises(ResultError, match="COVERAGE_INCOMPLETE"):
        read_result(store, optional_ref)


def _th_deck(group, codes, title, *, node=False, isolid=1, ismstr=0, iale=0, motion=""):
    return (f"/PART/10\nbrick\n         7         1\n/PROP/SOLID/7\nsolid\n{isolid:10d}{ismstr:10d}{iale:10d}\n/BRICK/10\n"
            + "".join(f"{i:10d}" for i in [101, *range(1, 9)])
            + f"\n/TH/BRIC/7\n{group}\n" + "".join(f"{code:10s}" for code in codes) + f"\n{101:10d}{'':10s}{title}\n"
            + (f"/TH/NODE/8\nNODE\nDX\n{12:10d}{'':10s}n12\n" if node else "") + motion + "/END\n").encode()


def test_th_metadata_from_brick_deck_and_frozen_binding_payload(tmp_path, manager):
    deck = _th_deck("Zone 2", ("EPSXX", "VOL"), "Body 3")
    csv = "time,Zone 2 101 Body 3 EPSXX,Zone 2 101 Body 3 VOL\n0,1,10\n1,2,11\n"
    titles = "".join(f"{1:10d}\n{code:10s}\n" for code in ("EPSXX", "VOL")).encode()
    program = f"from pathlib import Path\nPath('history.csv').write_text({csv!r})".encode()
    store, source = _run(tmp_path, {"caseT01": program, "caseT01_TITLES": titles})
    _snapshot_sidecar(source, deck, tmp_path / "working.inc")
    plan = plan_conversion(store, ROOT_ID, "OC-TH4-1", sys.executable, options={"allow_custom_executable": True})
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "completed", receipt["error"]
    strain = make_result_ref(store, ROOT_ID, receipt["id"], "history:all", "Zone 2 101 Body 3 EPSXX")
    volume = make_result_ref(store, ROOT_ID, receipt["id"], "history:all", "Zone 2 101 Body 3 VOL")
    assert strain.location["integration_point"] == "ip1"
    assert "integration_point" not in volume.location
    assert read_result(store, volume).values == (10., 11.)
    binding = read_result(store, strain).operator_provenance["column_binding"]
    assert set(binding) == {"schema_version", "column_index", "raw_header", "normalized_header", "group_title",
                            "output_type", "entity_id", "entity_title", "variable_code", "source_titles_pair_index",
                            "quantity", "component", "measure", "basis", "unit", "operator"}
    assert (binding["entity_id"], binding["entity_title"], binding["component"]) == (101, "Body 3", "xx")
    extra_titles = {"pairs": (*read_titles(source / "caseT01_TITLES")["pairs"],
                             {"output_type": 1, "variable_code": "SX", "title": ""})}
    with pytest.raises(ResultError, match="unexpected history column"):
        bind_history_columns(extra_titles, ["time", "Zone 2 101 Body 3 EPSXX", "Zone 2 101 Body 3 VOL", "BRIC 999 wrong SX"],
                             source_bindings=plan["options"]["source_bindings"], solid_formulation={"101": "hex8_1pt"})


def test_history_uses_exact_archived_request_with_overlapping_group_titles(tmp_path):
    codes = ("SX", "EPSZX", "EPSZY", "EPSYX")
    titles_path = tmp_path / "TITLES"
    titles_path.write_bytes("".join(f"{1:10d}\n{code:10s}\n" for code in codes).encode())
    source = [{"output_type": "Solid", "group_title": "Zone", "entity_id": 2, "entity_title": "101 beam",
               "variable_code": "SX"}]
    source.extend({"output_type": "Solid", "group_title": "Zone 2", "entity_id": 101, "entity_title": "beam",
                   "variable_code": code} for code in codes[1:])
    bindings = bind_history_columns(read_titles(titles_path), ["time", *(f"Zone 2 101 beam {code}" for code in codes)],
                                    source_bindings=source, solid_formulation={"2": "hex8_1pt", "101": "hex8_1pt"})
    assert [(b["group_title"], b["entity_id"], b["entity_title"], b["component"]) for b in bindings] == [
        ("Zone", 2, "101 beam", "xx"), ("Zone 2", 101, "beam", "xz"),
        ("Zone 2", 101, "beam", "yz"), ("Zone 2", 101, "beam", "xy")]


@pytest.mark.parametrize(("isolid", "ismstr", "iale", "motion", "supported"), [
    (None, 0, 0, "", False), (1, 0, 0, "", True), (2, 4, 0, "", True), (24, 0, 0, "", True),
    (1, 1, 0, "", True), (1, 2, 0, "", True), (2, 1, 0, "", True), (2, 2, 0, "", True),
    (24, 1, 0, "", True), (24, 2, 0, "", True), (17, 0, 0, "", False), (1, 4, 1, "", False),
    (1, 0, 0, "/ALE/MAT\n         1\n", False), (1, 0, 0, "/EULER/MAT\n         1\n", False),
    (1, 0, 0, "/UPWM\n", False),
    (0, 0, 0, "", True), (0, 0, 0, "/IMPLICIT\n", False),
    (0, 0, 0, "/DEF_SOLID\n        17\n", False),
    (0, 0, 0, "/DEF_SOLID\n        24\n", True),
])
def test_retained_legacy_ip_history_requires_archived_lagrangian_one_point_hex8(
        tmp_path, monkeypatch, manager, isolid, ismstr, iale, motion, supported):
    field = "BRIC 101 hex SX"
    csv = f"time,{field}\n0,1\n1,2\n"
    titles = f"{1:10d}\n{'SX':10s}\n".encode()
    program = f"from pathlib import Path\nPath('history.csv').write_text({csv!r})".encode()
    store, source = _run(tmp_path, {"caseT01": program, "caseT01_TITLES": titles})
    opts = {"allow_custom_executable": True}
    if isolid is not None:
        deck = _th_deck("BRIC", ("SX",), "hex", isolid=isolid, ismstr=ismstr, iale=iale, motion=motion)
        _snapshot_sidecar(source, deck, tmp_path / "working.inc")
        native_plan = plan_conversion(store, ROOT_ID, "OC-TH4-1", sys.executable, options=opts)
        assert native_plan["options"]["solid_formulation"] == ({"101": "hex8_1pt"} if supported else {})
    with monkeypatch.context() as patch:
        if not supported:
            bindings = [{"group_title": "BRIC", "entity_id": 101, "entity_title": "hex",
                         "output_type": "Solid", "variable_code": "SX"}]
            patch.setattr(converters, "_rad_snapshot_metadata", lambda *args, **kwargs: (None, bindings, {"101": "hex8_1pt"}))
        plan = plan_conversion(store, ROOT_ID, "OC-TH4-1", sys.executable, options=opts)
        receipt = _wait(manager, store, submit_conversion(manager, store, plan))
        assert receipt["state"] == "completed", receipt["error"]
    modern = make_result_ref(store, ROOT_ID, receipt["id"], "history:all", field)
    folder = operation_dir(store, ROOT_ID, receipt["id"])
    history_path = folder / "outputs/history-columns.json"
    columns = json.loads(history_path.read_bytes())
    columns["fields"][field].pop("semantics")
    atomic_json(history_path, columns)
    history_ref = next(ref for ref in receipt["outputs"] if ref["path"] == "outputs/history-columns.json")
    raw = history_path.read_bytes()
    history_ref.update(size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    for key in ("dataset_manifest_sha256", "result", "reader_code_sha256"):
        receipt.pop(key)
    original_reader = "7dd090b12f1935023a192c80facd74b29eeaf8a431d68b669ec8dbee62890b1e"
    receipt["adapter"]["code_sha256"] = receipt["adapter_code_sha256"] = original_reader
    atomic_json(folder / "operation.json", receipt)
    historical = replace(modern, operation_manifest_sha256=hashlib.sha256(canonical_json_bytes(receipt)).hexdigest(),
                         dataset_manifest_sha256=receipt["dataset_ref"]["sha256"],
                         location={**modern.location, "integration_point": "IPpoint"})
    if supported:
        assert read_result(store, historical).values == (1, 2)
    else:
        with pytest.raises(ResultError, match="UNSUPPORTED_OBSERVABLE.*one-point Lagrangian HEX8"):
            read_result(store, historical)


def test_saved_selection_payload_and_recomputed_revision_are_pinned(tmp_path, manager):
    store, _, plan = _fixture_animation(tmp_path)
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    directory = store.root / "selections" / "s2"
    directory.mkdir(parents=True)
    payload = directory / "payload.json"
    descriptor = directory / "selection.json"

    def save(ids):
        binary = canonical_json_bytes(ids)
        payload.write_bytes(binary)
        selection = {"schema": "caelab.selection.v1", "entity_kind": "node", "run_id": ROOT_ID,
                     "payload_ref": {"path": "payload.json", "sha256": hashlib.sha256(binary).hexdigest()}}
        revision = hashlib.sha256(canonical_json_bytes({"selection": selection, "payload": ids})).hexdigest()
        atomic_json(descriptor, {**selection, "revision_sha256": revision})

    save([11])
    ref = make_result_ref(store, ROOT_ID, receipt["id"], "A001", "Displacement", component="x", selection={"selection_id": "s2"})
    assert read_result(store, ref).values == (3,)
    nested = {"id": "s2", "sha256": ref.selection["revision_sha256"]}
    missing_revision = replace(ref, selection={**ref.selection, "entity_selection_ref": nested})
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        read_result(store, ResultRefV1.from_dict(missing_revision.to_dict()))
    pinned = replace(ref, selection={"entity_selection_ref": {"id": "s2", "revision_sha256": nested["sha256"]}})
    assert read_result(store, pinned).values == (3,)
    legacy = replace(ref, selection={"selection_id": "s2"})
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        read_result(store, ResultRefV1.from_dict(legacy.to_dict()))
    hashless = json.loads(descriptor.read_bytes())
    hashless.pop("revision_sha256")
    atomic_json(descriptor, hashless)
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        read_result(store, ref)
    save([11])
    (store.root / "payload.json").write_bytes(payload.read_bytes())
    payload.unlink()
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        read_result(store, ref)
    save([11])
    payload.write_bytes(b"[15]")
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        read_result(store, ref)
    save([15])
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        read_result(store, ref)


@pytest.mark.parametrize("state", [None, "failed", "cancelled", "completed"])
def test_save_as_rejects_orphaned_and_incomplete_terminal_operations(tmp_path, state):
    store, _ = _run(tmp_path, {})
    op_id = "f" * 32
    folder = operation_dir(store, ROOT_ID, op_id)
    folder.mkdir(parents=True)
    if state is None:
        (folder / "dataset.json").write_text("{}")
        with pytest.raises(ResultError, match="CORRUPT_OPERATION"):
            inspect_dataset(store, op_id)
        assert (folder / "dataset.json").is_file()
        assert not (folder / "dataset.json.orphaned").exists()
    else:
        atomic_json(folder / "operation.json", {"schema_version": 1, "source_run_id": ROOT_ID,
                                               "id": op_id, "state": state})
    with pytest.raises(ResultError, match="CORRUPT_OPERATION"):
        store.copy_to(tmp_path / "refused")
    if state is None:
        assert (folder / "dataset.json.orphaned").is_file()


def test_completed_receipt_accepts_top_level_or_result_manifest_reference(tmp_path, manager):
    store, _, plan = _fixture_animation(tmp_path)
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "completed", receipt["error"]
    path = operation_dir(store, ROOT_ID, receipt["id"]) / "operation.json"
    both = {**receipt, "result": {**receipt["result"], "dataset_manifest_ref": receipt["dataset_ref"],
                                  "dataset_manifest_sha256": receipt["dataset_manifest_sha256"]}}
    atomic_json(path, both)
    assert inspect_dataset(store, receipt["id"]).dataset_id == plan["cache_key"]
    for conflict in ({"dataset_manifest_sha256": "f" * 64},
                     {"dataset_manifest_ref": {**receipt["dataset_ref"], "sha256": "f" * 64}}):
        atomic_json(path, {**both, "result": {**both["result"], **conflict}})
        with pytest.raises(ResultError, match="CORRUPT_OPERATION"):
            operation_snapshot(manager, store, ROOT_ID, receipt["id"])
    receipt.pop("result")
    atomic_json(path, receipt)
    assert inspect_dataset(store, receipt["id"]).dataset_id == plan["cache_key"]
    receipt["result"] = {"dataset_manifest_ref": receipt.pop("dataset_ref"),
                         "dataset_manifest_sha256": receipt.pop("dataset_manifest_sha256")}
    atomic_json(path, receipt)
    assert inspect_dataset(store, receipt["id"]).dataset_id == plan["cache_key"]
    atomic_json(path, {**receipt, "result": {}})
    with pytest.raises(ResultError, match="CORRUPT_OPERATION"):
        inspect_dataset(store, receipt["id"])


@pytest.mark.parametrize("canonical", [False, True])
def test_legacy_completed_bundle_reopens_and_survives_save_as(tmp_path, manager, canonical):
    store, source, plan = _fixture_animation(tmp_path)
    receipt = _wait(manager, store, submit_conversion(manager, store, plan))
    assert receipt["state"] == "completed", receipt["error"]
    folder = operation_dir(store, ROOT_ID, receipt["id"])
    digest = receipt.pop("dataset_manifest_sha256")
    receipt.pop("result")
    frozen = json.loads((folder / "inputs/source-receipt.json").read_bytes())
    snapshot = frozen["snapshot"]
    legacy_identity = {
        "identity": {k: frozen.get(k) for k in ("id", "node_id", "solver_id", "input_revision", "units", "unit_source")},
        "inputs": [{k: f.get(k) for k in ("source_copy", "source_sha256", "execution_path", "execution_sha256")}
                   for f in snapshot["files"]],
        "outputs": snapshot.get("outputs", {}), "artifacts": snapshot.get("artifacts", {}),
    }
    receipt["source_fingerprint"] = hashlib.sha256(canonical_json_bytes(legacy_identity)).hexdigest()
    assert receipt["source_fingerprint"] != plan["source_fingerprint"]
    atomic_json(folder / "operation.json", {**receipt, "dataset_manifest_sha256": digest})
    with pytest.raises(ResultError, match="original receipt identity mismatch"):
        inspect_dataset(store, receipt["id"])
    legacy_reader = "35613ec816b8f7c63fd8dbc266413fc9c070ca48677e67bf48d0bcb713ebe593"
    receipt["adapter"]["code_sha256"] = receipt["reader_code_sha256"] = receipt["adapter_code_sha256"] = legacy_reader
    receipt_path = folder / "operation.json"
    atomic_json(receipt_path, receipt)
    with pytest.raises(ResultError, match="original receipt identity mismatch"):
        inspect_dataset(store, receipt["id"])
    receipt.pop("reader_code_sha256")
    for field in ("code_sha256", "adapter_code_sha256"):
        bad = {**receipt, "adapter": dict(receipt["adapter"])}
        target = bad["adapter"] if field == "code_sha256" else bad
        target[field] = "b" * 64
        atomic_json(receipt_path, bad)
        with pytest.raises(ResultError, match="original receipt identity mismatch"):
            inspect_dataset(store, receipt["id"])
        atomic_json(receipt_path, {**bad, "source_fingerprint": plan["source_fingerprint"]})
        unrecognized_reader = make_result_ref(store, ROOT_ID, receipt["id"], "A001", "Displacement")
        with pytest.raises(ResultError, match="HASH_MISMATCH.*reader code"):
            read_result(store, unrecognized_reader)
    other_EOL = "3d5c291d80078f4653e776c1aedc5234f1c9a0bd6cbe58a1e482a121fab14f76"
    conflicting = {**receipt, "adapter_code_sha256": other_EOL}
    atomic_json(receipt_path, conflicting)
    with pytest.raises(ResultError, match="original receipt identity mismatch"):
        inspect_dataset(store, receipt["id"])
    atomic_json(receipt_path, {**conflicting, "source_fingerprint": plan["source_fingerprint"]})
    conflicting_ref = make_result_ref(store, ROOT_ID, receipt["id"], "A001", "Displacement")
    with pytest.raises(ResultError, match="HASH_MISMATCH.*reader code"):
        read_result(store, conflicting_ref)
    if canonical:
        receipt["dataset_ref"]["sha256"] = digest
        receipt["adapter"].pop("code_sha256")
        receipt["adapter_code_sha256"] = None
    atomic_json(receipt_path, receipt)
    store.recover()
    saved = tmp_path / "saved"
    store.copy_to(saved)
    assert inspect_dataset(RunStore(saved), receipt["id"]).dataset_id == plan["cache_key"]
    assert (folder / "dataset.json").is_file()
    assert not (folder / "dataset.json.orphaned").exists()
    ref = make_result_ref(store, ROOT_ID, receipt["id"], "A001", "Displacement", component="x")
    historical_ref = replace(ref, dataset_manifest_sha256=receipt["dataset_ref"]["sha256"],
                             selection={"ids": [11]}, location={"association": "point"})
    for quantity_ref in (ref, historical_ref):
        assert resolve_result_ref(store, quantity_ref)["availability"] == "available"
    numeric = read_result(RunStore(saved), historical_ref)
    assert numeric.values == (3,)
    expected_reader = converters.reader_code_sha256("OC-ANIM-1") if canonical else legacy_reader
    assert numeric.operator_provenance["recorded_reader_code_sha256"] == expected_reader
    operation_payload = folder / "selections" / "set1" / "payload.json"
    root_payload = store.root / "selections" / "set1" / "payload.json"
    atomic_json(operation_payload, {"ids": [11]})
    atomic_json(root_payload, {"ids": [12]})
    digest = hashlib.sha256(operation_payload.read_bytes()).hexdigest()
    selection_ref = replace(historical_ref, selection={"entity_selection_ref": {"id": "set1", "sha256": digest}})
    assert read_result(store, selection_ref).values == (3,)
    full = converters.artifact(folder, operation_payload, "selection", "json", converters.POST_PRODUCER).to_dict()
    short = {"path": "payload.json", "sha256": digest}
    for selection in (
        {"artifact_ref": full},
        {"artifact_ref": full, "selection_source": "operation"},
        {"selection_id": "set1", "payload_ref": short, "selection_source": "operation"},
        {"entity_selection_ref": {"id": "set1", "artifact_ref": full, "selection_source": "operation"}},
        {"entity_selection_ref": {"payload_ref": full}},
    ):
        assert read_result(store, replace(historical_ref, selection=selection)).values == (3,)
    new_ref = make_result_ref(store, ROOT_ID, receipt["id"], "A001", "Displacement", component="x",
                              selection={"artifact_ref": full, "selection_source": "operation"})
    assert read_result(store, new_ref).values == (3,)
    unsigned_owner = replace(historical_ref, selection={"artifact_ref": full})
    with_id = replace(historical_ref, selection={"selection_id": "set1", "payload_ref": short})
    extensionless = operation_payload.with_name("extensionless")
    extensionless.write_bytes(operation_payload.read_bytes())
    extensionless_ref = {**full, "path": extensionless.relative_to(folder).as_posix(), "format_id": "JSON"}
    assert read_result(store, replace(historical_ref, selection={"artifact_ref": extensionless_ref})).values == (3,)
    numpy_path = operation_payload.with_name("integers")
    numpy_sha = write_safe_npy_v2(numpy_path, np.array([11]), root=folder)
    numpy_ref = {"path": numpy_path.relative_to(folder).as_posix(), "sha256": numpy_sha, "format_id": "numpy.npy.v2"}
    assert read_result(store, replace(historical_ref, selection={"artifact_ref": numpy_ref})).values == (3,)
    for format_id in ("vtu", "numpy.npy.v2"):
        with pytest.raises(ResultError, match="SCHEMA_MISMATCH"):
            read_result(store, replace(historical_ref, selection={"artifact_ref": {**full, "format_id": format_id}}))
    operation_payload.replace(root_payload)
    assert read_result(store, selection_ref).values == (3,)
    for operation_ref in (new_ref, unsigned_owner, with_id):
        with pytest.raises(ResultError, match="HASH_MISMATCH"):
            read_result(store, operation_ref)
    atomic_json(operation_payload, {"ids": [12]})
    for operation_ref in (selection_ref, new_ref, unsigned_owner, with_id):
        with pytest.raises(ResultError, match="HASH_MISMATCH"):
            read_result(store, operation_ref)
    root_ref = replace(historical_ref, selection={"payload_ref": full, "selection_source": "store"})
    assert read_result(store, root_ref).values == (3,)
    atomic_json(root_payload, {"ids": [13]})
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        read_result(store, selection_ref)
    deck = source / "fixture.rad"
    archived_deck = deck.read_bytes()
    deck.write_bytes(archived_deck + b"changed")
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        resolve_result_ref(store, historical_ref)
    deck.write_bytes(archived_deck)
    receipt["dataset_ref"]["sha256"] = "f" * 64
    atomic_json(folder / "operation.json", receipt)
    with pytest.raises(ResultError, match="HASH_MISMATCH"):
        inspect_dataset(store, receipt["id"])


@pytest.mark.parametrize(("state", "key", "value"), [
    ("failed", "result", {}), ("cancelled", "dataset_ref", {}),
    ("failed", "dataset_manifest_ref", {}), ("cancelled", "dataset_manifest_sha256", "a" * 64)])
def test_terminal_with_result_is_corrupt_for_read_and_recovery(tmp_path, manager, state, key, value):
    store, _ = _run(tmp_path, {})
    op_id = "b" * 32
    folder = operation_dir(store, ROOT_ID, op_id)
    atomic_json(folder / "operation.json", {"schema_version": 1, "source_run_id": ROOT_ID, "id": op_id,
                                          "state": state, "error": {"code": "CANCELLED", "message": "ended"},
                                          key: value})
    with pytest.raises(ResultError, match="CORRUPT_OPERATION"):
        operation_snapshot(manager, store, ROOT_ID, op_id)
    with pytest.raises(ResultError, match="CORRUPT_OPERATION"):
        store.recover()
    with pytest.raises(ResultError, match="CORRUPT_OPERATION"):
        store.copy_to(tmp_path / "refused")


def test_conversion_lease_wait_uses_short_caller_timeout(tmp_path, manager):
    store, _, plan = _fixture_animation(tmp_path)
    with store.lease():
        start = time.monotonic()
        with pytest.raises(ResultError, match="TIMEOUT"):
            submit_conversion(manager, store, plan, timeout=.1)
        assert time.monotonic() - start < 1


def test_same_manager_publication_lock_respects_submission_deadline(tmp_path, manager):
    store, _, plan = _fixture_animation(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def hold_lock():
        with manager._native_lock:
            entered.set()
            release.wait(1)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    try:
        assert entered.wait(2)
        start = time.monotonic()
        with pytest.raises(ResultError, match="TIMEOUT.*publication lock"):
            submit_conversion(manager, store, plan, timeout=.025)
        assert time.monotonic() - start < .18
    finally:
        release.set()
        holder.join(2)
    with store.lease():
        pass


def test_initial_queue_write_after_deadline_is_rolled_back(tmp_path, manager, monkeypatch):
    import caelab.jobs as jobs

    store, _, plan = _fixture_animation(tmp_path)
    write = jobs.atomic_json
    written = []

    def slow_queue(path, data):
        write(path, data)
        if data["state"] == "queued":
            written.append(path)
            time.sleep(.12)

    monkeypatch.setattr(jobs, "atomic_json", slow_queue)
    with pytest.raises(ResultError, match="TIMEOUT.*queued receipt"):
        submit_conversion(manager, store, plan, timeout=.04)
    assert len(written) == 1 and not written[0].parent.exists()
    assert not manager._operations
    with store.lease():
        pass


@pytest.mark.parametrize("contended", [False, True])
def test_operation_lock_deadline_rolls_back_without_starting_worker(tmp_path, manager, monkeypatch, contended):
    import caelab.jobs as jobs

    store, _, plan = _fixture_animation(tmp_path)
    make_lock = jobs.FileLock
    pending = []
    starts = []

    def operation_lock(path, *, timeout, thread_local):
        assert 0 <= timeout <= .04
        lock = make_lock(path, timeout=timeout, thread_local=thread_local)
        acquire = lock.acquire

        def acquire_and_delay():
            if contended:
                raise jobs.LockTimeout(str(path))
            acquire()
            time.sleep(.12)

        monkeypatch.setattr(lock, "acquire", acquire_and_delay)
        pending.append((path, lock))
        return lock

    monkeypatch.setattr(jobs, "FileLock", operation_lock)
    monkeypatch.setattr(manager, "_run_result_conversion", lambda *args: starts.append(args))
    with pytest.raises(ResultError, match="TIMEOUT"):
        submit_conversion(manager, store, plan, timeout=.04)
    (path, lock), = pending
    assert not lock.is_locked and not path.parent.exists()
    assert not starts and not manager._operations
    with store.lease():
        pass


@pytest.mark.parametrize("delay_at", ["lock", "write"])
def test_concurrent_submission_cannot_return_uncommitted_receipt(tmp_path, manager, monkeypatch, delay_at):
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
    from caelab import jobs

    other = JobManager()
    store, _, plan = _fixture_animation(tmp_path)
    entered, release = threading.Event(), threading.Event()
    delayed_folders = []

    def pause(folder):
        if delayed_folders:
            return
        delayed_folders.append(folder)
        entered.set()
        assert release.wait(5)

    if delay_at == "lock":
        make_lock = jobs.FileLock

        def delayed_lock(path, **kwargs):
            lock = make_lock(path, **kwargs)
            acquire = lock.acquire

            def acquire_and_pause():
                acquire()
                pause(path.parent)

            monkeypatch.setattr(lock, "acquire", acquire_and_pause)
            return lock

        monkeypatch.setattr(jobs, "FileLock", delayed_lock)
    else:
        write = jobs.atomic_json

        def delayed_write(path, data):
            write(path, data)
            if data.get("state") == "queued":
                pause(path.parent)

        monkeypatch.setattr(jobs, "atomic_json", delayed_write)

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(submit_conversion, manager, store, plan, timeout=.15)
        try:
            assert entered.wait(2)
            second = pool.submit(submit_conversion, other, store, plan, timeout=3)
            with pytest.raises(FutureTimeout):
                second.result(timeout=.2)
            assert not (delayed_folders[0] / "operation.json").exists()
        finally:
            release.set()
        with pytest.raises(ResultError, match="TIMEOUT"):
            first.result(timeout=2)
        queued = second.result(timeout=2)
    assert queued["id"] != delayed_folders[0].name
    assert not delayed_folders[0].exists()
    assert _wait(other, store, queued)["state"] == "completed"
    assert other.close()


def test_submission_timeout_survives_windows_sharing_denial(tmp_path, manager, monkeypatch):
    from pathlib import Path
    from caelab import jobs

    store, _, plan = _fixture_animation(tmp_path)
    unlink = Path.unlink
    write = jobs.atomic_json
    retained = []

    def blocking_unlink(path, *args, **kwargs):
        if path.name == "operation.lock":
            retained.append(path.parent)
            raise PermissionError("foreign handle")
        return unlink(path, *args, **kwargs)

    def slow_write(path, data):
        write(path, data)
        if data["state"] == "queued":
            time.sleep(.12)

    monkeypatch.setattr(Path, "unlink", blocking_unlink)
    monkeypatch.setattr(jobs, "atomic_json", slow_write)
    with pytest.raises(ResultError, match="TIMEOUT"):
        submit_conversion(manager, store, plan, timeout=.04)
    (folder,) = set(retained)
    assert not (folder / "operation.json").exists() and not (folder / "operation.json.part").exists()
    assert operation_snapshot(manager, store, ROOT_ID, folder.name) is None
    with store.lease():
        pass
    store.copy_to(tmp_path / "saved")


def test_cross_manager_duplicate_waits_for_the_queued_receipt(tmp_path, monkeypatch, manager):
    import caelab.jobs as jobs
    from concurrent.futures import ThreadPoolExecutor

    other = JobManager()
    store, _, plan = _fixture_animation(tmp_path, "import time; time.sleep(60)")
    entered, release = threading.Event(), threading.Event()
    writer = jobs.atomic_json

    def delayed(path, data):
        if path.name == "operation.json.part" and data["state"] == "queued":
            entered.set()
            assert release.wait(10)
        writer(path, data)

    monkeypatch.setattr(jobs, "atomic_json", delayed)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(submit_conversion, manager, store, plan)
        assert entered.wait(5)
        duplicate = pool.submit(submit_conversion, other, RunStore(store.root), plan, timeout=5)
        try:
            time.sleep(2.8)
            assert not duplicate.done()
        finally:
            release.set()
        assert duplicate.result(timeout=5)["id"] == first.result(timeout=5)["id"]
    assert manager.cancel_operation(first.result()["id"])
    assert other.close()


def test_cross_manager_duplicate_scan_cannot_return_receipt_after_deadline(tmp_path, monkeypatch, manager):
    from caelab.native import operations

    other = JobManager()
    store, _, plan = _fixture_animation(tmp_path, "import time; time.sleep(60)")
    queued = submit_conversion(manager, store, plan)
    _wait(manager, store, queued, {"running"})
    reader = operations._read_receipt
    entered, release = threading.Event(), threading.Event()

    def slow_receipt(*args):
        entered.set()
        release.wait(2)
        return reader(*args)

    with monkeypatch.context() as patch:
        patch.setattr(operations, "_read_receipt", slow_receipt)
        try:
            start = time.monotonic()
            with pytest.raises(ResultError, match="TIMEOUT"):
                submit_conversion(other, store, plan, timeout=.1)
            assert entered.is_set() and time.monotonic() - start < .8
        finally:
            release.set()
    assert manager.cancel_operation(queued["id"])
    assert other.close()


def test_posix_grace_tracks_descendants_after_the_leader_exits(monkeypatch):
    import caelab.jobs as jobs
    from types import SimpleNamespace

    clock = [0.]
    signals = []
    signal = SimpleNamespace(SIGTERM=15, SIGKILL=9)
    monkeypatch.setattr(jobs, "signal", signal)
    monkeypatch.setattr(jobs, "os", SimpleNamespace(name="posix", killpg=lambda _, sig: signals.append((sig, clock[0]))))
    monkeypatch.setattr(jobs, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=lambda step: clock.__setitem__(0, clock[0] + step)))
    monkeypatch.setattr(jobs, "_posix_group_alive", lambda _: not any(sig == signal.SIGKILL for sig, _ in signals))
    process = SimpleNamespace(pid=42, returncode=None, wait=lambda **_: 0)
    assert jobs._finish_owned({"process": process, "group": None}, cancel=True,
                              term_grace=5, confirm_tree=True) == 0
    assert signals[0] == (signal.SIGTERM, 0) and signals[-1][0] == signal.SIGKILL and signals[-1][1] >= 5


@pytest.mark.skipif(os.name != "nt", reason="Windows DLL search path")
def test_windows_converter_resets_dll_search_in_child_only(tmp_path, manager):
    import ctypes as c

    api = c.WinDLL("kernel32", use_last_error=True)
    buffer = c.create_unicode_buffer(4096)
    api.GetDllDirectoryW(len(buffer), buffer)
    initial = buffer.value
    assert api.SetDllDirectoryW(c.c_wchar_p(str(tmp_path)))
    try:
        code = ("import ctypes as c; import sys; "
                "b=c.create_unicode_buffer(4096); c.windll.kernel32.GetDllDirectoryW(len(b), b); "
                f"assert not b.value, b.value; print('child stderr', file=sys.stderr); print({_vtk()!r})")
        store, _, plan = _fixture_animation(tmp_path, code)

        def executor(argv, **kwargs):
            api.GetDllDirectoryW(len(buffer), buffer)
            assert buffer.value == str(tmp_path)
            return subprocess.Popen(argv, **kwargs)

        receipt = _wait(manager, store, submit_conversion(manager, store, plan, executor=executor))
        assert receipt["state"] == "completed", receipt["error"]
        assert (operation_dir(store, ROOT_ID, receipt["id"]) / "stderr.log").read_text().strip() == "child stderr"
        api.GetDllDirectoryW(len(buffer), buffer)
        assert buffer.value == str(tmp_path)
    finally:
        assert api.SetDllDirectoryW(c.c_wchar_p(initial or None))
