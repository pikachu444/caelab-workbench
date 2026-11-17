"""Unit tests for CORE01 contracts: artifact_refs, materials.units, and materials.contracts."""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import numpy.lib.format as npy_format
import pytest

from caelab.artifact_refs import (
    ArtifactOwner,
    ArtifactRefV1,
    DatasetManifestV1,
    EvidenceEnvelopeV1,
    NumericResultV1,
    ProducerIdentity,
    ResultRefV1,
    SeriesManifestV1,
    canonical_array_sha256,
    canonical_json_bytes,
    canonical_sha256,
    read_safe_npy_v2,
    validate_relative_artifact_path,
    write_safe_npy_v2,
)
from caelab.materials import (
    AdapterIdentity,
    AssessmentResultV1,
    CampaignReceipt,
    CancelReceipt,
    CapabilityReport,
    ComparisonSpec,
    EvaluationReceipt,
    ExperimentChannelSpec,
    ExperimentPredictionV1,
    ExperimentSpec,
    ImportReceipt,
    ModelDescriptorV1,
    ObservableCapability,
    ObservableSpec,
    ParameterSpec,
    PredictedChannelV1,
    PredictionBundle,
    PredictionBundleV1,
    QuantityUnitSpec,
    ScalarResponseSpecV1,
    SelectionRecordV1,
    StudySnapshot,
    convert_frequency_angular,
    convert_values,
)
from caelab.mdo import variables as mdo_variables
from caelab.mdo.constraints import ConstraintSpec, Feasibility
from caelab.mdo.optimize import BudgetUsage, EvaluationObservation


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_artifact_ref(
    aid: str = "art-1",
    role: str = "source_csv",
    path: str = "sources/test.csv",
    sha: str | None = None,
    size: int = 128,
    format_id: str = "text/csv",
) -> ArtifactRefV1:
    return ArtifactRefV1(
        id=aid,
        role=role,
        path=path,
        sha256=sha or _sha(aid),
        size=size,
        format_id=format_id,
        producer=ProducerIdentity(id="wb.test", version="1.0"),
    )


def _make_result_ref(run_id: str = "run-1", node_id: str = "node-1") -> ResultRefV1:
    receipt_ref = _make_artifact_ref("receipt-1", "source_receipt", "inputs/source_receipt.json")
    return ResultRefV1(
        source_run_id=run_id,
        source_node_id=node_id,
        source_receipt_sha256=receipt_ref.sha256,
        source_receipt_ref=receipt_ref,
        operation_id="op-1",
        operation_manifest_sha256=_sha("op-1"),
        dataset_id="ds-1",
        dataset_manifest_sha256=_sha("ds-1"),
        source_artifacts=(_make_artifact_ref("vtk-1", "converted_field", "outputs/a001.vtk"),),
        frame_id="anim:001",
        field_id="stress.shell.layer3",
        component="xx",
        selection={"selection_id": "sel-1", "kind": "element_ids", "count": 2},
        location={"association": "element", "ip": 3, "layer": "middle"},
        basis={"kind": "element_local", "orientation_sha256": _sha("orient")},
        measure="cauchy",
        shear_convention="tensor",
        units={"symbol": "MPa", "dimension": "pressure", "scale_to_si": 1e6},
        geometry_state="deformed",
        operator={"id": "identity", "version": "1", "params": {}},
        coverage={"expected": 2, "valid": 2, "missing": 0},
        eligibility={"view": True, "query": True, "fit": True, "review": True, "reason_codes": []},
    )


def _make_observable(obs_id: str = "stress_xx", unit: str = "MPa") -> ObservableSpec:
    return ObservableSpec(
        id=obs_id,
        quantity_kind="stress",
        unit=unit,
        measure="cauchy",
        location="material_point",
        basis="global",
        shear_convention="tensor",
        component_order=("xx",),
        value_kind="real",
        representation="scalar",
    )


def test_pure_layer_imports_without_gui_vtk_or_solvers():
    source_refs = r"""
import importlib.abc
import sys

blocked = (
    "PySide6", "vtk", "scipy", "sklearn", "pymoo", "SALib", "apps",
    "caelab.mdo", "caelab.native", "caelab.solvers", "caelab.pde", "caelab.adapters", "caelab.jobs",
)
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("Forbidden import in caelab.artifact_refs: " + fullname)

sys.meta_path.insert(0, BlockOptional())
import caelab.artifact_refs
"""
    source_materials = r"""
import importlib.abc
import sys

blocked = (
    "PySide6", "vtk", "scipy", "sklearn", "pymoo", "SALib", "apps",
    "caelab.numerical", "caelab.solvers", "caelab.pde", "caelab.adapters", "caelab.jobs",
)
class BlockOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in blocked):
            raise AssertionError("Forbidden import in pure CORE01 contract layer: " + fullname)

sys.meta_path.insert(0, BlockOptional())
import caelab.artifact_refs
import caelab.materials
import caelab.materials.units
import caelab.materials.contracts
"""
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root))
    for source in (source_refs, source_materials):
        res = subprocess.run([sys.executable, "-c", source], cwd=root, env=env, text=True, capture_output=True, timeout=30)
        assert res.returncode == 0, res.stdout + res.stderr

    # Verify our owned pure modules do not directly import PySide6, vtk, or solver modules.
    import ast
    for rel in (
        "caelab/artifact_refs.py",
        "caelab/materials/__init__.py",
        "caelab/materials/units.py",
        "caelab/materials/contracts.py",
    ):
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                assert not any(
                    name == bad or name.startswith(bad + ".")
                    for bad in ("PySide6", "vtk", "caelab.solvers", "caelab.native", "caelab.pde", "caelab.adapters", "apps")
                ), f"{rel} imports forbidden module {name!r}"

    assert "Mg" not in mdo_variables._UNITS
    assert "degC" not in mdo_variables._UNITS
    assert "mol" not in mdo_variables._UNITS


def test_canonical_json_and_sha256():
    obj_a = {"b": 1, "a": "한글", "content_sha256": "ignored", "nested": {"z": 2.5, "content_sha256": "kept"}}
    obj_b = {"a": "한글", "nested": {"content_sha256": "kept", "z": 2.5}, "b": 1, "content_sha256": "other"}
    assert canonical_json_bytes(obj_a, exclude_keys=("content_sha256",)) == (
        '{"a":"한글","b":1,"nested":{"content_sha256":"kept","z":2.5}}'.encode("utf-8")
    )
    assert canonical_sha256(obj_a) == canonical_sha256(obj_b)

    with pytest.raises(ValueError):
        canonical_json_bytes({"x": float("nan")})
    with pytest.raises(ValueError):
        canonical_json_bytes({"x": [float("inf")]})
    with pytest.raises(ValueError):
        canonical_json_bytes({1: "non-string-key"})


def test_validate_relative_artifact_path_and_reparse_escape(tmp_path: Path):
    assert validate_relative_artifact_path("sources/실험 데이터 1.csv") == "sources/실험 데이터 1.csv"
    assert validate_relative_artifact_path("arrays/a.npy", root=tmp_path) == "arrays/a.npy"

    bad_paths = [
        "",
        "   ",
        "bad\x00path.csv",
        "sources\\data.csv",
        "/abs/path.csv",
        "sources/trailing/",
        "sources//data.csv",
        "C:data.csv",
        "C:/data.csv",
        "//server/share/data.csv",
        ".",
        "..",
        "sources/../escape.csv",
        "./data.csv",
    ]
    for bad in bad_paths:
        with pytest.raises(ValueError):
            validate_relative_artifact_path(bad, root=tmp_path)

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    study_root = tmp_path / "study"
    study_root.mkdir()

    if os.name == "nt":
        junction = study_root / "link_dir"
        proc = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        target_link = junction
    else:
        target_link = study_root / "link_dir"
        target_link.symlink_to(outside, target_is_directory=True)

    # Defect 2 regressions: relative under root, absolute inside root containing junction/symlink,
    # root=None with internal junction/symlink, and '..' inside absolute Path.
    for bad_call in (
        lambda: validate_relative_artifact_path("link_dir/secret.txt", root=study_root),
        lambda: read_safe_npy_v2("link_dir/arr.npy", root=study_root),
        lambda: write_safe_npy_v2("link_dir/arr.npy", np.array([1.0]), root=study_root),
        lambda: write_safe_npy_v2(target_link / "arr.npy", np.array([1.0]), root=study_root),
        lambda: read_safe_npy_v2(target_link / "arr.npy", root=study_root),
        lambda: write_safe_npy_v2(target_link / "arr.npy", np.array([1.0])),
        lambda: read_safe_npy_v2(target_link / "arr.npy"),
        lambda: write_safe_npy_v2(study_root / "sub" / ".." / "arr.npy", np.array([1.0])),
        lambda: read_safe_npy_v2(study_root / "sub" / ".." / "arr.npy"),
        lambda: write_safe_npy_v2(study_root / "sub" / ".." / "arr.npy", np.array([1.0]), root=study_root),
        lambda: read_safe_npy_v2(study_root / "sub" / ".." / "arr.npy", root=study_root),
    ):
        with pytest.raises(ValueError):
            bad_call()


def test_canonical_array_hash_and_safe_npy_v2_roundtrip(tmp_path: Path):
    arr_f = np.asfortranarray(np.array([[1.0, 2.0], [3.0, 4.0]], dtype=">f8"))
    arr_c = np.array([[1.0, 2.0], [3.0, 4.0]], dtype="<f8")
    expected_header = b'{"dtype":"<f8","order":"C","shape":[2,2]}\n'
    expected_hash = hashlib.sha256(expected_header + arr_c.tobytes(order="C")).hexdigest()
    assert canonical_array_sha256(arr_f) == expected_hash
    assert canonical_array_sha256(arr_c) == expected_hash

    arr_i = np.array([10, -20], dtype=">i8")
    assert len(canonical_array_sha256(arr_i)) == 64
    arr_b = np.array([True, False, True], dtype="|b1")
    assert len(canonical_array_sha256(arr_b)) == 64

    out_path = tmp_path / "arrays" / "sample.npy"
    file_sha = write_safe_npy_v2("arrays/sample.npy", arr_f, root=tmp_path)
    assert file_sha == hashlib.sha256(out_path.read_bytes()).hexdigest()
    with out_path.open("rb") as f:
        assert npy_format.read_magic(f) == (2, 0)
    loaded = read_safe_npy_v2("arrays/sample.npy", root=tmp_path)
    assert loaded.dtype == np.dtype("<f8")
    assert np.array_equal(loaded, arr_c)

    for invalid in (
        np.array([{"k": 1}], dtype=object),
        np.array([1.0 + 2.0j]),
        np.array([1.0], dtype=np.float32),
        np.array([1], dtype=np.int32),
        np.array([1.0, float("nan")]),
        np.array([1.0, float("inf")]),
        [[1.0, 2.0], [3.0]],
        [True, 1],
    ):
        with pytest.raises(ValueError):
            canonical_array_sha256(invalid)

    bad_pickle = tmp_path / "arrays" / "pickle.npy"
    np.save(bad_pickle, np.array([{"x": 1}], dtype=object), allow_pickle=True)
    with pytest.raises(ValueError):
        read_safe_npy_v2("arrays/pickle.npy", root=tmp_path)

    bad_nan = tmp_path / "arrays" / "nan.npy"
    np.save(bad_nan, np.array([1.0, float("nan")]), allow_pickle=False)
    with pytest.raises(ValueError):
        read_safe_npy_v2("arrays/nan.npy", root=tmp_path)


def test_artifact_ref_owner_result_ref_manifest_and_numeric_result():
    owner_op = ArtifactOwner(scope="operation", id="op-1", source_run_id="run-1")
    assert ArtifactOwner.from_dict(owner_op.to_dict()) == owner_op
    owner_run = ArtifactOwner(scope="run", id="run-1")
    assert owner_run.to_dict() == {"scope": "run", "id": "run-1"}
    with pytest.raises(ValueError):
        ArtifactOwner(scope="operation", id="op-1")
    with pytest.raises(ValueError):
        ArtifactOwner(scope="invalid", id="x")

    ref = _make_artifact_ref()
    assert ArtifactRefV1.from_dict(ref.to_dict()) == ref
    bad_ref_dict = ref.to_dict()
    bad_ref_dict["size"] = True
    with pytest.raises(ValueError):
        ArtifactRefV1.from_dict(bad_ref_dict)
    bad_ref_dict = ref.to_dict()
    bad_ref_dict["sha256"] = "A" * 64
    with pytest.raises(ValueError):
        ArtifactRefV1.from_dict(bad_ref_dict)
    bad_ref_dict = ref.to_dict()
    bad_ref_dict["extra"] = 1
    with pytest.raises(ValueError):
        ArtifactRefV1.from_dict(bad_ref_dict)
    bad_ref_dict = ref.to_dict()
    bad_ref_dict["schema_version"] = 2
    with pytest.raises(ValueError):
        ArtifactRefV1.from_dict(bad_ref_dict)

    res_ref = _make_result_ref()
    assert ResultRefV1.from_dict(res_ref.to_dict()) == res_ref
    op_payload = {"id": "mean", "version": "1", "params": {"axis": "x"}}
    derived_ref = res_ref.with_operator(op_payload)
    op_payload["params"]["axis"] = "mutated"
    assert res_ref.operator["id"] == "identity"
    assert derived_ref.operator["id"] == "mean"
    assert derived_ref.operator["params"]["axis"] == "x"
    with pytest.raises(TypeError):
        derived_ref.operator["id"] = "other"  # type: ignore[index]

    series = SeriesManifestV1(
        series_id="history:all",
        coordinate_ref=_make_artifact_ref("coord-1", "time_coords", "arrays/t.npy"),
        row_count=4,
        source_row_index_ref=_make_artifact_ref("row-1", "row_index", "arrays/rows.npy"),
        field_ids=("stress.bric.101.xx",),
    )
    dm_field = DatasetManifestV1(dataset_id="ds-field", dataset_kind="field", frames=({"frame_id": "anim:001", "time": 0.01},))
    dm_hist = DatasetManifestV1(dataset_id="ds-hist", dataset_kind="history", series=(series,))
    assert DatasetManifestV1.from_dict(dm_field.to_dict()) == dm_field
    assert DatasetManifestV1.from_dict(dm_hist.to_dict()) == dm_hist
    with pytest.raises(ValueError):
        DatasetManifestV1(dataset_id="bad-1", dataset_kind="field", series=(series,))
    with pytest.raises(ValueError):
        DatasetManifestV1(dataset_id="bad-2", dataset_kind="history", frames=({"frame_id": "anim:001"},))

    num_res = NumericResultV1(
        ref=res_ref,
        values=(1.25, None, 2.5),
        values_ref=_make_artifact_ref("vals-1", "values", "arrays/vals.npy"),
        sample_coordinates=(0.0, 0.0025, 0.005),
        counts={"expected": 3, "valid": 2, "missing": 1},
        status="ok",
        reason_codes=(),
        warnings=("partial_sample",),
        operator_provenance={"id": "identity", "version": "1"},
    )
    assert NumericResultV1.from_dict(num_res.to_dict()) == num_res
    with pytest.raises(ValueError):
        NumericResultV1(ref=res_ref, values=(True,), values_ref=None, sample_coordinates=(0.0,), counts={}, status="ok")
    with pytest.raises(ValueError):
        NumericResultV1(ref=res_ref, values=(float("nan"),), values_ref=None, sample_coordinates=(0.0,), counts={}, status="ok")
    with pytest.raises(ValueError):
        NumericResultV1(ref=res_ref, values=(1.0,), values_ref=None, sample_coordinates=(False,), counts={}, status="ok")


def test_evidence_envelope_all_four_kinds_and_tamper_rejection():
    prov = {
        "producer": {"id": "wb.host", "version": "1.0"},
        "sources": [
            {"source_kind": "native_run", "source_id": "run-1", "source_revision": "rev-1", "url": None}
        ],
        "artifact_owners": {"art-1": {"scope": "run", "id": "run-1"}},
    }
    avail = {"state": "available", "reason_codes": [], "missing_artifact_ids": []}
    res_ref = _make_result_ref(run_id="run-1", node_id="node-1")

    env_native = EvidenceEnvelopeV1.create(
        id="ev-1",
        kind="native_run",
        provenance=prov,
        availability=avail,
        payload={
            "legacy_evidence_ref": _make_artifact_ref("leg-1", "legacy_evidence", "ev/desc.json"),
            "source_run_id": "run-1",
            "source_node_id": "node-1",
            "derived_result_ref": res_ref,
        },
    )
    assert env_native.payload["derived_result_ref"].source_run_id == "run-1"
    assert EvidenceEnvelopeV1.from_dict(env_native.to_dict()) == env_native

    tampered = env_native.to_dict()
    tampered["payload"]["source_node_id"] = "node-999"
    with pytest.raises(ValueError):
        EvidenceEnvelopeV1.from_dict(tampered)

    with pytest.raises(ValueError):
        EvidenceEnvelopeV1.create(
            id="ev-bad-run",
            kind="native_run",
            provenance=prov,
            availability=avail,
            payload={
                "legacy_evidence_ref": _make_artifact_ref("leg-1"),
                "source_run_id": "run-OTHER",
                "source_node_id": "node-1",
                "derived_result_ref": res_ref,
            },
        )

    env_manual = EvidenceEnvelopeV1.create(
        id="ev-2",
        kind="manual",
        provenance=prov,
        availability=avail,
        payload={"legacy_evidence_ref": _make_artifact_ref("man-1", "manual_file", "ev/manual.vtk")},
    )
    assert EvidenceEnvelopeV1.from_dict(env_manual.to_dict()) == env_manual

    env_cal = EvidenceEnvelopeV1.create(
        id="ev-3",
        kind="calibration",
        provenance=prov,
        availability=avail,
        payload={
            "evaluation_ref": _make_artifact_ref("eval-1", "evaluation", "evaluations/e1.json"),
            "study_id": "study-1",
            "content_revision": "crev-1",
            "evaluation_id": "e1",
        },
    )
    assert EvidenceEnvelopeV1.from_dict(env_cal.to_dict()) == env_cal

    env_doc = EvidenceEnvelopeV1.create(
        id="ev-4",
        kind="document_chunk",
        provenance=prov,
        availability=avail,
        payload={
            "corpus_id": "corp-1",
            "corpus_revision": "v1",
            "source_id": "src-1",
            "source_ref": _make_artifact_ref("src-ref", "corpus_source", "sources/doc.txt"),
            "chunk_id": "chk-1",
            "chunk_ref": _make_artifact_ref("chk-ref", "corpus_chunk", "chunks/chk1.txt"),
            "document_sha256": _sha("doc"),
            "text_sha256": _sha("txt"),
            "edition": "2.23",
            "span": {"line_start": 1, "line_end": 10, "char_start": 0, "char_end": 42},
            "page_start": None,
            "page_end": None,
            "section_path": ["sec1", "subsec2"],
        },
    )
    assert EvidenceEnvelopeV1.from_dict(env_doc.to_dict()) == env_doc

    with pytest.raises(ValueError):
        EvidenceEnvelopeV1.create(
            id="ev-bad-kind",
            kind="result",
            provenance=prov,
            availability=avail,
            payload={"legacy_evidence_ref": _make_artifact_ref()},
        )

    bad_span_payload = dict(env_doc.to_dict()["payload"])
    bad_span_payload["span"] = {"line_start": 0, "line_end": 1, "char_start": 0, "char_end": 5}
    with pytest.raises(ValueError):
        EvidenceEnvelopeV1.create(
            id="ev-bad-span", kind="document_chunk", provenance=prov, availability=avail, payload=bad_span_payload
        )


def test_units_conversions_and_rejections():
    assert convert_values(1.0, QuantityUnitSpec("Mg/mm**3", "density"), QuantityUnitSpec("kg/m**3", "density")) == 1e12
    assert convert_values(2.5, QuantityUnitSpec("MPa", "stress"), QuantityUnitSpec("Pa", "stress")) == 2.5e6
    assert convert_values(5.0, QuantityUnitSpec("N", "force"), QuantityUnitSpec("N", "force")) == 5.0
    assert convert_values(250.0, QuantityUnitSpec("mJ", "energy"), QuantityUnitSpec("J", "energy")) == pytest.approx(0.25)

    assert convert_values(1.5, QuantityUnitSpec("kJ/mol", "molar_energy"), QuantityUnitSpec("J/mol", "molar_energy")) == 1500.0
    assert convert_values(8.314, QuantityUnitSpec("J/(mol*K)", "gas_constant"), QuantityUnitSpec("kJ/(mol*K)", "gas_constant")) == pytest.approx(0.008314)

    # Absolute temperature vs temperature difference
    assert convert_values(25.0, QuantityUnitSpec("degC", "absolute_temperature"), QuantityUnitSpec("K", "absolute_temperature")) == pytest.approx(298.15)
    assert convert_values([298.15, 273.15], QuantityUnitSpec("K", "absolute_temperature"), QuantityUnitSpec("degC", "absolute_temperature")) == pytest.approx([25.0, 0.0])
    for bad_temp, unit in [(0.0, "K"), (-5.0, "K"), (-273.15, "degC"), (-300.0, "degC")]:
        with pytest.raises(ValueError):
            convert_values(bad_temp, QuantityUnitSpec(unit, "absolute_temperature"), QuantityUnitSpec("K", "absolute_temperature"))

    diff = convert_values((-10, 0.0, 20.0), QuantityUnitSpec("degC", "temperature_difference"), QuantityUnitSpec("K", "temperature_difference"))
    assert isinstance(diff, tuple) and diff == (-10.0, 0.0, 20.0)

    # Frequency vs angular_frequency
    assert convert_values(2.0, QuantityUnitSpec("kHz", "frequency"), QuantityUnitSpec("Hz", "frequency")) == 2000.0
    assert convert_values(1.5, QuantityUnitSpec("MHz", "frequency"), QuantityUnitSpec("kHz", "frequency")) == 1500.0
    with pytest.raises(ValueError):
        convert_values(1.0, QuantityUnitSpec("Hz", "frequency"), QuantityUnitSpec("rad/s", "angular_frequency"))
    with pytest.raises(ValueError):
        convert_values(1.0, QuantityUnitSpec("Hz", "frequency"), QuantityUnitSpec("rad/s", "frequency"))
    assert convert_frequency_angular(1.0, QuantityUnitSpec("Hz", "frequency"), QuantityUnitSpec("rad/s", "angular_frequency")) == pytest.approx(2.0 * math.pi)
    assert convert_frequency_angular(2.0 * math.pi, QuantityUnitSpec("rad/s", "angular_frequency"), QuantityUnitSpec("Hz", "frequency")) == pytest.approx(1.0)

    # Scalar float/int vs bool, ndarray, policy, empty/unknown units
    out_scalar = convert_values(2, QuantityUnitSpec("mm", "length"), QuantityUnitSpec("m", "length"))
    assert type(out_scalar) is float and out_scalar == pytest.approx(0.002)
    out_arr = convert_values(np.array([[1, 2], [3, 4]]), QuantityUnitSpec("MPa", "stress"), QuantityUnitSpec("kPa", "stress"))
    assert isinstance(out_arr, np.ndarray) and out_arr.dtype == np.float64
    np.testing.assert_allclose(out_arr, [[1000.0, 2000.0], [3000.0, 4000.0]])

    for bad_call in (
        lambda: convert_values(True, QuantityUnitSpec("m", "length"), QuantityUnitSpec("mm", "length")),
        lambda: convert_values(np.array([True]), QuantityUnitSpec("m", "length"), QuantityUnitSpec("mm", "length")),
        lambda: convert_values(float("nan"), QuantityUnitSpec("m", "length"), QuantityUnitSpec("mm", "length")),
        lambda: convert_values(1.0, QuantityUnitSpec("m", "length"), QuantityUnitSpec("mm", "length"), policy_revision="v2"),
        lambda: convert_values(1.0, QuantityUnitSpec("m", "length"), QuantityUnitSpec("s", "length")),
        lambda: convert_values(1.0, QuantityUnitSpec("", "stress"), QuantityUnitSpec("", "stress")),
        lambda: convert_values(1.0, QuantityUnitSpec("unknown", "stress"), QuantityUnitSpec("MPa", "stress")),
        lambda: convert_values(1.0, QuantityUnitSpec("__import__('os')", "length"), QuantityUnitSpec("m", "length")),
    ):
        with pytest.raises(ValueError):
            bad_call()


def test_material_contracts_roundtrip_and_validations():
    adapter = AdapterIdentity(id="caelab.prony_trs", version="1.0.0", implementation_sha256=_sha("impl"))
    assert AdapterIdentity.from_dict(adapter.to_dict()) == adapter

    obs_cap = ObservableCapability(
        observable_id="E_storage",
        value_kinds=("real",),
        representations=("scalar",),
        locations=("material_point",),
        supported_units=("MPa", "Pa"),
    )
    resp_spec = ScalarResponseSpecV1(response_id="fit_loss", unit="", source_kind="residual", source_name="fit_loss")
    bulk_resp = ScalarResponseSpecV1(response_id="bulk_ratio", unit="", source_kind="parameter_expression", source_name="K/mu1")
    cap = CapabilityReport(
        adapter=adapter,
        model_id="B-HYP-COMP-1",
        model_revision="1",
        level="analytic",
        supported=True,
        can_run=True,
        differentiable=False,
        observable_capabilities=(obs_cap,),
        optimizer_responses=(resp_spec, bulk_resp),
        optimizer_constraints=(
            {"id": "bulk_lower", "response_id": "bulk_ratio", "kind": "LOWER", "limit": 10.0, "scale": 10.0, "tolerance": 0.0, "unit": ""},
            ConstraintSpec(id="bulk_upper", response_id="bulk_ratio", kind="UPPER", limit=1000.0, scale=1000.0, tolerance=0.0, unit=""),
        ),
        evidence_refs=(_make_artifact_ref("ev-ref"),),
    )
    assert CapabilityReport.from_dict(cap.to_dict()) == cap
    with pytest.raises(ValueError):
        CapabilityReport(adapter=adapter, model_id="m", model_revision="1", level="analytic", supported=False, can_run=True, differentiable=False)

    p_free = ParameterSpec(id="mu1", name="mu1", unit="MPa", lower=0.1, upper=100.0, default=1.0, transform="log", role="free")
    p_linked = ParameterSpec(id="K", name="K", unit="MPa", lower=1.0, upper=1000.0, role="linked", expression="mu1*10", dependencies=("mu1",))
    desc = ModelDescriptorV1(model_id="B-HYP-COMP-1", model_revision="1", family="hyperelastic", name="Ogden", parameter_specs=(p_free, p_linked), observable_capabilities=(obs_cap,))
    assert ModelDescriptorV1.from_dict(desc.to_dict()) == desc
    with pytest.raises(ValueError):
        ParameterSpec(id="bad", name="bad", unit="MPa", lower=-1.0, upper=10.0, transform="log", role="free")

    obs = _make_observable("stress_xx", "MPa")
    ch = ExperimentChannelSpec(observable_id="stress_xx", observable=obs, column_name="sig", source_unit="MPa", independent_column="t", independent_unit="s")
    exp = ExperimentSpec(experiment_id="exp-1", experiment_revision="er-1", split="train", source_ref=_make_artifact_ref(), channels=(ch,), conditions={"T": 298.15})
    assert ExperimentSpec.from_dict(exp.to_dict()) == exp

    # ComparisonSpec rules
    cmp_fit = ComparisonSpec(id="c-fit", experiment_id="exp-1", observable_id="stress_xx", split="train", scale=10.0, required_for_fit=True)
    cmp_val = ComparisonSpec(id="c-val", experiment_id="exp-2", observable_id="stress_xx", split="holdout", scale=10.0, required_for_validation=True)
    assert ComparisonSpec.from_dict(cmp_fit.to_dict()) == cmp_fit
    assert ComparisonSpec.from_dict(cmp_val.to_dict()) == cmp_val
    for bad_cmp in (
        dict(id="c1", experiment_id="e", observable_id="o", split="train", scale=0.0),
        dict(id="c2", experiment_id="e", observable_id="o", split="train", scale=1.0, weight=-0.1),
        dict(id="c3", experiment_id="e", observable_id="o", split="train", scale=1.0, required_for_fit=True, required_for_validation=True),
        dict(id="c4", experiment_id="e", observable_id="o", split="validation", scale=1.0, required_for_fit=True),
        dict(id="c5", experiment_id="e", observable_id="o", split="holdout", scale=1.0, required_for_fit=True),
        dict(id="c6", experiment_id="e", observable_id="o", split="train", scale=1.0, required_for_validation=True),
    ):
        with pytest.raises(ValueError):
            ComparisonSpec(**bad_cmp)

    # PredictionBundleV1 + validate_level + forbid fit_loss
    assert PredictionBundle is PredictionBundleV1
    pch = PredictedChannelV1(
        observable_id="stress_xx",
        observable=obs,
        independent_ref=_make_artifact_ref("ind"),
        sample_keys_ref=_make_artifact_ref("sk"),
        sample_key_columns=("source_row",),
        values_ref=_make_artifact_ref("vals"),
        mask_ref=_make_artifact_ref("mask"),
        component_order=("xx",),
        value_kind="real",
        representation="scalar",
        coverage={"expected": 5, "valid": 5, "missing": 0},
        source_refs=(_make_artifact_ref("src"),),
        source_result_refs=(),
    )
    ep = ExperimentPredictionV1(experiment_id="exp-1", experiment_revision="er-1", state="completed", channels=(pch,))
    bundle = PredictionBundleV1(
        evaluation_id="eval-1",
        request_id="req-1",
        study_revision="srev-1",
        model_revision="1",
        adapter=adapter,
        experiments=(ep,),
        optimizer_scalars={"bulk_ratio": 100.0},
        artifacts=(_make_artifact_ref("vals"),),
        artifact_owners={"vals": ArtifactOwner(scope="consumer", id="material-study:st-1")},
        run_ids=(),
    )
    assert PredictionBundleV1.from_dict(bundle.to_dict()) == bundle
    bundle.validate_level("analytic")
    bundle.validate_level("material_library")
    with pytest.raises(ValueError):
        PredictionBundleV1(
            evaluation_id="eval-1",
            request_id="req-1",
            study_revision="srev-1",
            model_revision="1",
            adapter=adapter,
            optimizer_scalars={"fit_loss": 0.1},
        )
    bundle_with_run = dataclasses.replace(bundle, run_ids=("run-1",))
    with pytest.raises(ValueError):
        bundle_with_run.validate_level("analytic")
    bundle_with_run.validate_level("material_library")

    pch_fe = dataclasses.replace(pch, source_result_refs=(_make_result_ref(),))
    bundle_fe = dataclasses.replace(bundle, experiments=(dataclasses.replace(ep, channels=(pch_fe,)),), run_ids=("run-1",))
    with pytest.raises(ValueError):
        bundle_fe.validate_level("analytic")
    with pytest.raises(ValueError):
        bundle_fe.validate_level("material_library")
    bundle_fe.validate_level("native_fe")

    # AssessmentResultV1 + forbid train_fit
    assess = AssessmentResultV1(
        objective={"eligible": True, "fit_loss": 0.01, "reason_codes": []},
        assessment={
            "fit_status": "pass",
            "validation_status": "not_assessed",
            "holdout_status": "not_assessed",
            "adequacy_status": "not_assessed",
            "uncertainty_status": "not_computed",
            "identifiability_status": "not_identifiable",
            "constraint_status": "FEASIBLE",
            "constraint_checks": [],
            "policy_ref": _make_artifact_ref("pol"),
            "channel_checks": [],
            "split_checks": [],
            "warnings": [],
        },
    )
    assert AssessmentResultV1.from_dict(assess.to_dict()) == assess
    bad_assess = assess.to_dict()
    bad_assess["assessment"]["train_fit"] = "pass"
    with pytest.raises(ValueError):
        AssessmentResultV1.from_dict(bad_assess)
    with pytest.raises(ValueError):
        AssessmentResultV1(objective={"eligible": False, "fit_loss": 0.1, "reason_codes": []}, assessment=assess.assessment)

    # SelectionRecordV1 & C2/C7 receipts
    sel = SelectionRecordV1(
        selection_id="sel-1",
        study_id="study-1",
        content_revision="crev-1",
        model_revision="1",
        adapter=adapter,
        physical_parameters={"mu1": 1.5, "K": 15.0},
        parameter_values_sha256=_sha("params"),
        numerical_settings_sha256=_sha("num"),
        normalization_ref=_make_artifact_ref("norm"),
        assessment_policy_ref=_make_artifact_ref("ap"),
        selection_policy_ref=_make_artifact_ref("sp"),
        source_evaluation_ids=("eval-1",),
        training_experiment_revisions=({"experiment_id": "exp-1", "experiment_revision": "er-1"},),
        created_at="2026-11-18T00:00:00Z",
    )
    assert SelectionRecordV1.from_dict(sel.to_dict()) == sel

    snap = StudySnapshot(
        study_id="study-1",
        document_revision="drev-1",
        content_revision="crev-1",
        path="study.caemat.json",
        data_root="study.caemat-data",
        read_only=False,
        experiment_ids=("exp-1",),
    )
    assert StudySnapshot.from_dict(snap.to_dict()) == snap
    bad_snap = snap.to_dict()
    bad_snap["revision"] = "ambiguous"
    with pytest.raises(ValueError):
        StudySnapshot.from_dict(bad_snap)

    imp = ImportReceipt(
        study_id="study-1",
        previous_document_revision="drev-0",
        document_revision="drev-1",
        previous_content_revision=None,
        content_revision="crev-1",
        experiment_id="exp-1",
        source_ref=_make_artifact_ref("src"),
        converted_refs=(_make_artifact_ref("conv"),),
    )
    assert ImportReceipt.from_dict(imp.to_dict()) == imp

    evr = EvaluationReceipt(
        campaign_id="camp-1",
        evaluation_id="eval-1",
        document_revision="drev-1",
        content_revision="crev-1",
        state="completed",
        artifact_ref=_make_artifact_ref("eval"),
        observation={"request_id": "req-1", "responses": {"fit_loss": 0.01}, "valid": True},
    )
    assert EvaluationReceipt.from_dict(evr.to_dict()) == evr

    camp = CampaignReceipt(
        campaign_id="camp-1",
        content_revision="crev-1",
        state="completed",
        termination_reason="CONVERGED",
        evaluation_ids=("eval-1",),
        selected_ids=("point:0",),
        budget_usage={"dispatched_requests": 5},
    )
    assert CampaignReceipt.from_dict(camp.to_dict()) == camp

    canc = CancelReceipt(campaign_id="camp-1", accepted=True, state="requested")
    assert CancelReceipt.from_dict(canc.to_dict()) == canc

    with pytest.raises(dataclasses.FrozenInstanceError):
        snap.study_id = "other"  # type: ignore[misc]


def test_constraint_contract_json_and_additional_negative_validations():
    root = Path(__file__).resolve().parents[1]
    raw = json.loads((root / "docs/specs/cae-research/evidence/B/constraint_contract.json").read_text(encoding="utf-8"))
    responses = tuple(ScalarResponseSpecV1.from_dict(item) for item in raw["optimizer_responses"])
    cap = CapabilityReport(
        adapter=AdapterIdentity(id="caelab.ogden", version="1.0.0"),
        model_id=raw["capability_profile"],
        model_revision="1",
        level="analytic",
        supported=True,
        can_run=True,
        differentiable=raw["differentiable"],
        optimizer_responses=responses,
        optimizer_constraints=tuple(raw["optimizer_constraints"]),
    )
    assert len(cap.optimizer_responses) == 5
    assert len(cap.optimizer_constraints) == 5
    assert CapabilityReport.from_dict(cap.to_dict()) == cap

    # Additional negative checks across DTOs
    res_ref = _make_result_ref()
    bad_rr = res_ref.to_dict()
    bad_rr["source_receipt_sha256"] = _sha("other")
    with pytest.raises(ValueError):
        ResultRefV1.from_dict(bad_rr)
    bad_rr2 = res_ref.to_dict()
    bad_rr2["geometry_state"] = "warped"
    with pytest.raises(ValueError):
        ResultRefV1.from_dict(bad_rr2)

    with pytest.raises(ValueError):
        DatasetManifestV1.from_dict({"schema_version": 1, "dataset_id": "ds", "dataset_kind": "field", "frames": []})

    with pytest.raises(ValueError):
        ComparisonSpec(id="c", experiment_id="e", observable_id="o", split="train", scale=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ComparisonSpec(id="c", experiment_id="e", observable_id="o", split="train", scale=1.0, required_for_fit=1)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ComparisonSpec(id="c", experiment_id="e", observable_id="o", split="train", scale=1.0, acceptance_limit=-0.5)

    with pytest.raises(ValueError):
        ParameterSpec(id="p", name="p", unit="MPa", role="linked")
    with pytest.raises(ValueError):
        ParameterSpec(id="p", name="p", unit="MPa", role="free", expression="x*2")
    with pytest.raises(ValueError):
        ParameterSpec(id="p", name="p", unit="MPa", lower=True, upper=2.0)  # type: ignore[arg-type]

    base_assess = {
        "fit_status": "pass",
        "validation_status": "not_assessed",
        "holdout_status": "not_assessed",
        "adequacy_status": "not_assessed",
        "uncertainty_status": "not_computed",
        "identifiability_status": "pass",
        "constraint_status": "FEASIBLE",
        "constraint_checks": [],
        "policy_ref": None,
        "channel_checks": [],
        "split_checks": [],
        "warnings": [],
    }
    with pytest.raises(ValueError):
        AssessmentResultV1(objective={"eligible": True, "fit_loss": None, "reason_codes": []}, assessment=base_assess)
    with pytest.raises(ValueError):
        AssessmentResultV1.from_dict(
            {"schema_version": 1, "objective": {"eligible": True, "fit_loss": 0.1, "reason_codes": []}, "assessment": base_assess, "train_fit": "pass"}
        )
    bad_status_assess = dict(base_assess)
    bad_status_assess["identifiability_status"] = "unknown"
    with pytest.raises(ValueError):
        AssessmentResultV1(objective={"eligible": True, "fit_loss": 0.1, "reason_codes": []}, assessment=bad_status_assess)


def test_defect_regressions_observation_complex_freeze_and_units():
    # Defect 1: EvaluationObservation (MappingProxyType) and BudgetUsage inside receipts, plus dataclass with Enum/ndarray
    obs_record = EvaluationObservation(
        request_id="req-1",
        responses={"fit_loss": 0.1},
        valid=True,
        source_id="mat:1",
    )
    ev_receipt = EvaluationReceipt(
        campaign_id=None,
        evaluation_id="eval-1",
        document_revision="drev-1",
        content_revision="crev-1",
        state="completed",
        artifact_ref=_make_artifact_ref("eval-art"),
        observation=obs_record,
    )
    assert ev_receipt.observation["responses"]["fit_loss"] == 0.1
    assert EvaluationReceipt.from_dict(ev_receipt.to_dict()) == ev_receipt

    budget = BudgetUsage(
        dispatched_requests=5,
        dispatcher_calls=2,
        cache_hits=1,
        duplicate_proposals=0,
        predictions=0,
        native_attempts=0,
        failures=0,
        elapsed_seconds=1.25,
    )
    camp_receipt = CampaignReceipt(
        campaign_id="camp-1",
        content_revision="crev-1",
        state="completed",
        termination_reason="CONVERGED",
        evaluation_ids=("eval-1",),
        selected_ids=("p-1",),
        budget_usage=budget,
    )
    assert camp_receipt.budget_usage["dispatched_requests"] == 5
    assert CampaignReceipt.from_dict(camp_receipt.to_dict()) == camp_receipt

    @dataclasses.dataclass(frozen=True)
    class _NestedMeta:
        status: Feasibility
        weights: np.ndarray
        tags: dict

    ev_nested = EvaluationReceipt(
        campaign_id="camp-2",
        evaluation_id="eval-2",
        document_revision="drev-1",
        content_revision="crev-1",
        state="completed",
        observation=_NestedMeta(
            status=Feasibility.FEASIBLE,
            weights=np.array([0.5, 0.5]),
            tags=EvaluationObservation(request_id="r", responses={"a": 1.0}, valid=True, source_id="mat:2").responses,
        ),
    )
    assert ev_nested.observation["status"] == "FEASIBLE"
    assert ev_nested.observation["weights"] == (0.5, 0.5)
    assert ev_nested.observation["tags"]["a"] == 1.0

    # Defect 3: Complex representations, PredictedChannelV1 non-empty invariants, NumericResultV1 size/counts invariants
    obs_complex = ObservableSpec(
        id="E_star",
        quantity_kind="storage_loss_modulus",
        unit="MPa",
        measure="viscoelastic",
        component_order=("E_storage", "E_loss"),
        value_kind="complex",
        representation="storage_loss",
    )
    assert ObservableSpec.from_dict(obs_complex.to_dict()) == obs_complex

    obs_real_imag = ObservableSpec(
        id="Z_star",
        quantity_kind="impedance",
        unit="MPa",
        measure="harmonic",
        component_order=("real", "imag"),
        value_kind="complex",
        representation="real_imag",
    )
    assert ObservableSpec.from_dict(obs_real_imag.to_dict()) == obs_real_imag

    for bad_obs_kw in (
        dict(id="c1", quantity_kind="q", unit="MPa", measure="m", value_kind="complex", representation="scalar", component_order=("a", "b")),
        dict(id="c2", quantity_kind="q", unit="MPa", measure="m", value_kind="complex", representation="storage_loss", component_order=("a",)),
        dict(id="c3", quantity_kind="q", unit="MPa", measure="m", value_kind="real", representation="storage_loss", component_order=("a", "b")),
        dict(id="c4", quantity_kind="q", unit="MPa", measure="m", value_kind="real", representation="real_imag", component_order=("a", "b")),
    ):
        with pytest.raises(ValueError):
            ObservableSpec(**bad_obs_kw)

    cap_c = ObservableCapability(
        observable_id="E_star",
        value_kinds=("complex",),
        representations=("storage_loss",),
        locations=("material_point",),
        supported_units=("MPa",),
    )
    assert ObservableCapability.from_dict(cap_c.to_dict()) == cap_c
    cap_both = ObservableCapability(
        observable_id="E_both",
        value_kinds=("real", "complex"),
        representations=("scalar", "storage_loss"),
        locations=("material_point",),
        supported_units=("MPa",),
    )
    assert ObservableCapability.from_dict(cap_both.to_dict()) == cap_both

    for bad_cap_kw in (
        dict(observable_id="x", value_kinds=("complex",), representations=("scalar",), locations=("material_point",), supported_units=("MPa",)),
        dict(observable_id="x", value_kinds=("complex",), representations=("vector",), locations=("material_point",), supported_units=("MPa",)),
        dict(observable_id="x", value_kinds=("complex",), representations=("storage_loss", "scalar"), locations=("material_point",), supported_units=("MPa",)),
        dict(observable_id="x", value_kinds=("real",), representations=("storage_loss",), locations=("material_point",), supported_units=("MPa",)),
        dict(observable_id="x", value_kinds=("real", "complex"), representations=("scalar",), locations=("material_point",), supported_units=("MPa",)),
    ):
        with pytest.raises(ValueError):
            ObservableCapability(**bad_cap_kw)

    pch_complex = PredictedChannelV1(
        observable_id="E_star",
        observable=obs_complex,
        independent_ref=_make_artifact_ref("ind"),
        sample_keys_ref=_make_artifact_ref("sk"),
        sample_key_columns=("source_row",),
        values_ref=_make_artifact_ref("vals"),
        mask_ref=_make_artifact_ref("mask"),
        component_order=("E_storage", "E_loss"),
        value_kind="complex",
        representation="storage_loss",
        coverage={"expected": 4, "valid": 4, "missing": 0},
    )
    assert PredictedChannelV1.from_dict(pch_complex.to_dict()) == pch_complex

    obs_real_scalar = _make_observable("sig_xx", "MPa")
    obs_real_empty_comp = ObservableSpec(id="sig", quantity_kind="stress", unit="MPa", measure="cauchy", value_kind="real", representation="scalar")
    with pytest.raises(ValueError):
        PredictedChannelV1(
            observable_id="sig_xx",
            observable=obs_real_scalar,
            independent_ref=_make_artifact_ref("ind"),
            sample_keys_ref=_make_artifact_ref("sk"),
            sample_key_columns=(),
            values_ref=_make_artifact_ref("vals"),
            mask_ref=_make_artifact_ref("mask"),
            component_order=("xx",),
            value_kind="real",
            representation="scalar",
            coverage={"expected": 4, "valid": 4, "missing": 0},
        )
    with pytest.raises(ValueError):
        PredictedChannelV1(
            observable_id="sig",
            observable=obs_real_empty_comp,
            independent_ref=_make_artifact_ref("ind"),
            sample_keys_ref=_make_artifact_ref("sk"),
            sample_key_columns=("source_row",),
            values_ref=_make_artifact_ref("vals"),
            mask_ref=_make_artifact_ref("mask"),
            component_order=(),
            value_kind="real",
            representation="scalar",
            coverage={"expected": 4, "valid": 4, "missing": 0},
        )

    res_ref = _make_result_ref()
    with pytest.raises(ValueError):
        NumericResultV1(ref=res_ref, values=(1.0, 2.0), values_ref=None, sample_coordinates=(0.0, 1.0, 2.0), counts={}, status="ok")
    with pytest.raises(ValueError):
        NumericResultV1(ref=res_ref, values=(1.0, 2.0), values_ref=None, sample_coordinates=(0.0, 1.0), counts={"valid": True, "total": 2}, status="ok")
    with pytest.raises(ValueError):
        NumericResultV1(ref=res_ref, values=(1.0, 2.0), values_ref=None, sample_coordinates=(0.0, 1.0), counts={"valid": 3, "total": 2}, status="ok")
    with pytest.raises(ValueError):
        NumericResultV1(ref=res_ref, values=(1.0, 2.0), values_ref=None, sample_coordinates=(0.0, 1.0), counts={"valid": 2, "total": 5}, status="ok")

    # Defect 4: Reject mutable .to_dict objects and NUL bytes in mapping keys
    class _MutableToDict:
        def __init__(self):
            self.x = 1

        def to_dict(self):
            return {"x": self.x}

    with pytest.raises(ValueError):
        res_ref.with_operator({"id": "identity", "evil": _MutableToDict()})
    with pytest.raises(ValueError):
        res_ref.with_operator({"id": "identity", "bad\x00key": 1})
    bad_art_dict = _make_artifact_ref().to_dict()
    bad_art_dict["bad\x00key"] = 1
    with pytest.raises(ValueError):
        ArtifactRefV1.from_dict(bad_art_dict)

    # Known frozen ArtifactRefV1 inside ResultRefV1.selection still works and roundtrips
    sel_with_ref = ResultRefV1.from_dict(
        {
            **res_ref.to_dict(),
            "selection": {
                "selection_id": "sel-1",
                "kind": "element_ids",
                "artifact_ref": _make_artifact_ref("sel-art").to_dict(),
                "count": 2,
            },
        }
    )
    assert ResultRefV1.from_dict(sel_with_ref.to_dict()) == sel_with_ref

    # Defect 5: Dimensionless classification & unit gating
    for bad_unit, bad_kind in (
        ("", "strain_rate"),
        ("1", "strain_rate"),
        ("", "strain_energy"),
        ("1", "strain_energy_density"),
        ("", "energy_release_rate"),
        ("1", "stress"),
        ("MPa", "dimensionless"),
        ("MPa", "poisson_ratio"),
        ("MPa", "strain"),
    ):
        with pytest.raises(ValueError):
            convert_values(1.0, QuantityUnitSpec(bad_unit, bad_kind), QuantityUnitSpec(bad_unit, bad_kind))

    assert convert_values(1.0, QuantityUnitSpec("1/s", "strain_rate"), QuantityUnitSpec("1/ms", "strain_rate")) == pytest.approx(0.001)
    assert convert_values(0.02, QuantityUnitSpec("mm/mm", "strain"), QuantityUnitSpec("", "strain")) == pytest.approx(0.02)
    assert convert_values(0.02, QuantityUnitSpec("mm/mm", "nominal_strain"), QuantityUnitSpec("1", "nominal_strain")) == pytest.approx(0.02)
    assert convert_values(1.5, QuantityUnitSpec("1", "stress_ratio"), QuantityUnitSpec("", "stress_ratio")) == pytest.approx(1.5)
    assert convert_values(0.3, QuantityUnitSpec("", "poisson_ratio"), QuantityUnitSpec("1", "poisson_ratio")) == pytest.approx(0.3)
