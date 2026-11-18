"""Exercise the post-processing boundary with synthetic producer outputs."""

import base64
import gzip
import hashlib
import json
import os
import struct
import subprocess
import sys
import threading
import time
from dataclasses import replace

import numpy as np
import pytest

from caelab.artifact_refs import DatasetManifestV1, ResultRefV1, read_safe_npy_v2
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


def _vtk(*, missing=(), replacements=None, permutation=tuple(range(7))):
    points, ids = np.array(ORIGINAL_POINTS), np.arange(11, 18)
    u = np.array([[3, 4, 0], [3, 4, 0], [3, 4, 0], [0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 0, 2]])
    indices = [permutation.index(i) for i in range(7)]
    triangles = [indices[i] for i in (0, 1, 2, 2)]
    solid = indices[3:]
    data = [
        "# vtk DataFile Version 3.0", "synthetic animation", "ASCII", "DATASET UNSTRUCTURED_GRID",
        "FIELD FieldData " + ("1" if "TIME" in missing else "2"),
        *([] if "TIME" in missing else ["TIME 1 1 double", "0.125"]),
        "CYCLE 1 1 int", "0", "POINTS 7 float",
        *(" ".join(map(str, points[i] + u[i])) for i in permutation),
        "CELLS 2 10", "4 " + " ".join(map(str, triangles)), "4 " + " ".join(map(str, solid)),
        "CELL_TYPES 2", "5 10",
        "POINT_DATA 7", "SCALARS NODE_ID int 1", "LOOKUP_TABLE default",
        " ".join(str(ids[i]) for i in permutation),
        "VECTORS Displacement float", *(" ".join(map(str, u[i])) for i in permutation),
        "VECTORS Velocity float", *(" ".join(map(str, u[i] * 2)) for i in permutation),
        "CELL_DATA 2", "SCALARS ELEMENT_ID int 1", "LOOKUP_TABLE default", "101 202",
        "SCALARS PART_ID int 1", "LOOKUP_TABLE default", "10 20",
        "SCALARS 2DELEM int 1", "LOOKUP_TABLE default", "1 0",
        "SCALARS 3DELEM int 1", "LOOKUP_TABLE default", "0 1",
    ]
    for name, values in TENSORS.items():
        if name not in missing:
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
    (("2DELEM_Stress_(layer__3)_",), {}, "PARTIAL_REQUIRED_FIELD"),
    (("2DELEM_Stress_(upper)", "2DELEM_Stress_(lower)"), {}, "PARTIAL_REQUIRED_FIELD"),
    ((), {"(layer__3)_": "(layer__30)_"}, "PARTIAL_REQUIRED_FIELD"),
    ((), {"101 202": "202 101"}, "OBSERVABLE_MISMATCH"),
    ((), {"11 12 13 14 15 16 17": "12 11 13 14 15 16 17"}, "OBSERVABLE_MISMATCH"),
    ((), {"11 12 13 14 15 16 17": "11 11 13 14 15 16 17"}, "INVALID_DATASET"),
    ((), {"LOOKUP_TABLE default\n0 1": "LOOKUP_TABLE default\n1 0"}, "OBSERVABLE_MISMATCH"),
    ((), {"12 0 0 0 0 0 0 0 0": "12 1 0 0 0 0 0 0 0"}, "INVALID_DATASET"),
    ((), {"CELL_TYPES 2\n5 10": "CELL_TYPES 2\n5 12"}, "INVALID_DATASET"),
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
    bindings = bind_history_columns(titles, table["raw_header"], expected_columns=["BRIC 101 hex SX", "NODE 12 n12 DX"])
    assert [binding["source_titles_pair_index"] for binding in bindings] == [None, 0, 1]
    assert bindings[1]["output_type"] == "Solid"
    assert bindings[1]["entity_id"] == 101
    assert bindings[1]["component"] == "xx"
    assert bindings[2]["entity_title"] == "n12"
    manifest = extract_history(csv_file, titles_file, {"operation_dir": tmp_path})
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
    plan = plan_conversion(store, ROOT_ID, "OC-ANIM-1", sys.executable, ["A001"], options)
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
    assert len(launched) == 1 and launched[0][0] == [sys.executable, "inA001"]
    kwargs = launched[0][1]
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
    store, _ = _run(tmp_path, {"sampleT01": program.encode(), "sampleT01_TITLES": TITLES})
    plan = plan_conversion(store, ROOT_ID, "OC-TH4-1", sys.executable,
                           options={"expected_columns": ["BRIC 101 hex SX", "NODE 12 n12 DX"]})
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
    ("import time; time.sleep(60)", 1., "timed_out", "TIMEOUT"),
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
    assert failed["error"]["code"] == "STALE_PLAN"
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
    receipt = manager.operation_snapshot(store, ROOT_ID, operation_id)
    assert receipt["state"] == "interrupted"
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


def test_close_cancels_active_conversion(tmp_path, manager):
    store, _, plan = _fixture_animation(tmp_path, "import time; time.sleep(60)")
    queued = submit_conversion(manager, store, plan)
    _wait(manager, store, queued, {"running"})
    assert manager.close()
    assert manager.operation_snapshot(store, ROOT_ID, queued["id"])["state"] == "cancelled"
    with store.lease():
        pass
