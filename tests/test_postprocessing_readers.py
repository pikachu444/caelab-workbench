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
from dataclasses import replace

import numpy as np
import pytest

from caelab.artifact_refs import DatasetManifestV1, ResultRefV1, canonical_json_bytes, read_safe_npy_v2
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
    solid = indices[3:]
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


def _rad_mesh_deck(nip=5):
    return ("#RADIOSS STARTER\n/NODE\n" + "".join(
        f"{i:10d}{x:20}{y:20}{z:20}\n" for i, (x, y, z) in enumerate(ORIGINAL_POINTS, 11))
        + "/SH3N/10\n" + "".join(f"{i:10d}" for i in (101, 11, 12, 13)) + "\n"
        + "/TETRA4/20\n" + "".join(f"{i:10d}" for i in (202, 14, 15, 16, 17)) + "\n"
        + "/PART/10\nshell part\n         7         1\n/PART/20\nsolid part\n         8         1\n"
        + "/PROP/SHELL/7\nshell\n        24\n                   0\n"
        + f"{nip:10d}\n/PROP/SOLID/8\nsolid\n         1\n/END\n").encode()


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
    trusted = plan_conversion(store, ROOT_ID, "OC-ANIM-1", tool, ["A001"],
                              options={"expected_mesh": MESH, "install_root": str(tmp_path)})
    assert trusted["options"]["erosion_active_value"] == plan["options"]["erosion_active_value"] == 1
    if os.name == "nt":
        env = converters.converter_environment(trusted, {"Path": "original"})
        assert env["PATH"].split(os.pathsep)[0] == str(tool.parent)
        assert env["PATH"].split(os.pathsep)[-1] == "original"
        assert env["RAD_CFG_PATH"] == str(tmp_path / "hm_cfg_files") and env["KMP_STACKSIZE"] == "400m"


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
    for lock in (control["operation_lock"], control["lease"]):
        with monkeypatch.context() as patch:
            patch.setattr(lock, "release", unconfirmed)
            assert not manager.close()
            assert manager.operation_snapshot(store, ROOT_ID, queued["id"])["cleanup_failed"]
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


def _snapshot_sidecar(source, deck, live_path, *, suffix=".inc"):
    original, execution = f"input/frozen{suffix}", f"input/exec{suffix}"
    for relative in (original, execution):
        target = source / relative
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(deck)
    digest = hashlib.sha256(deck).hexdigest()
    live_path.write_bytes(deck)
    atomic_json(source / "input/snapshot.json", {"files": [{
        "source_path": str(live_path), "source_copy": original, "execution_path": execution,
        "original_sha256": digest, "executed_sha256": digest}]})


@pytest.mark.parametrize("nip", [0, 5])
def test_saved_rad_authority_shell_layers_and_live_deck(tmp_path, manager, nip):
    missing = tuple(name for name in TENSORS if "layer__" in name and
                    ("layer__1" not in name if nip == 0 else "layer__3" in name))
    vtk = _vtk(missing=missing)
    store, source = _run(tmp_path, {"caseA001": f"print({vtk!r})".encode()})
    live = tmp_path / "working.inc"
    _snapshot_sidecar(source, _rad_mesh_deck(nip), live)
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


def test_dat_snapshot_preserves_effective_nip_per_shell(tmp_path, manager):
    rows = ("/MAT/LAW1/1\nelastic\n/MAT/LAW2/2\nplastic\n/PART/30\nsecond shell\n         7         2\n"
            "/SH3N/30\n" + "".join(f"{i:10d}" for i in (303, 11, 12, 13)) + "\n/END\n").encode()
    deck = _rad_mesh_deck().replace(b"/END\n", rows)
    vtk = _vtk(extra_shell=True)
    store, source = _run(tmp_path, {"caseA001": f"print({vtk!r})".encode()})
    _snapshot_sidecar(source, deck, tmp_path / "mesh.dat", suffix=".dat")
    plan = plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"],
                           {"allow_custom_executable": True, "exact_required_set": []})
    assert plan["options"]["expected_mesh"]["shell_nip_by_id"] == {"101": 0, "303": 5}
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
                                                        ("0 0", None, []), ("1 2", None, []),
                                                        ("0 0", 0, [12]), ("0 1", 0, [])])
def test_erosion_rule_matches_producer_unless_explicitly_overridden(tmp_path, statuses, active, value):
    marker = "SCALARS EROSION_STATUS int 1\nLOOKUP_TABLE default\n" + statuses + "\n"
    path = tmp_path / "erosion.vtk"
    path.write_text(_vtk().replace("SCALARS 3DELEM int 1", marker + "SCALARS 3DELEM int 1"), encoding="ascii")
    grid = read_vtk(path, erosion_active_value=active)
    assert query_field(grid, "3DELEM_Stress", component="xx")[0].tolist() == value


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


def _th_deck(group, codes, title, *, node=False):
    return ("/PART/10\nbrick\n         7         1\n/PROP/SOLID/7\nsolid\n         1\n/BRICK/10\n"
            + "".join(f"{i:10d}" for i in [101, *range(1, 9)])
            + f"\n/TH/BRIC/7\n{group}\n" + "".join(f"{code:10s}" for code in codes) + f"\n{101:10d}{'':10s}{title}\n"
            + (f"/TH/NODE/8\nNODE\nDX\n{12:10d}{'':10s}n12\n" if node else "") + "/END\n").encode()


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


def test_cross_manager_duplicate_waits_for_the_queued_receipt(tmp_path, monkeypatch, manager):
    import caelab.jobs as jobs
    from concurrent.futures import ThreadPoolExecutor

    other = JobManager()
    store, _, plan = _fixture_animation(tmp_path, "import time; time.sleep(60)")
    entered, release = threading.Event(), threading.Event()
    writer = jobs.atomic_json

    def delayed(path, data):
        if path.name == "operation.json" and data["state"] == "queued":
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
