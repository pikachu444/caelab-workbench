"""Registered numeric result queries over verified RunStore bundles."""

from __future__ import annotations

import re

import numpy as np

from ..artifact_refs import ArtifactRefV1, DatasetManifestV1, NumericResultV1, ResultRefV1, canonical_sha256, read_safe_npy_v2
from ..result_readers import ResultError, no_checkpoint
from ..result_readers import opencourant_history, vtk_fields
from .operations import artifact_path, load_completed_operation, read_object, resolve_result_ref, verify_artifact
from .run_store import RunStore

_SPATIAL_READERS = {"OC-ANIM-1": vtk_fields.read_vtk, "VTU-1": vtk_fields.read_vtu}
_OPERATORS = {"identity", "values", "min", "max", "mean", "norm"}
_CANONICAL_OPERATOR = {"id": "identity", "version": "1", "params": {}}


def _completed(store, operation_id, source_run_id, check):
    if source_run_id is None:
        if not isinstance(operation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", operation_id):
            raise ResultError("PATH_ESCAPE", "invalid operation ID")
        derived = artifact_path(store.root, ".derived")
        runs = [path.parent.name for path in derived.glob(f"*/{operation_id}")]
        if len(runs) != 1:
            raise ResultError("SOURCE_CHANGED", "operation does not identify one source run")
        source_run_id = runs[0]
    return load_completed_operation(store, source_run_id, operation_id, check=check)


def inspect_dataset(store: RunStore, operation_id_or_ref, *, source_run_id=None, consumer_kind=None,
                    check_cancelled=None) -> DatasetManifestV1:
    """Inspect a completed dataset by immutable reference or operation ID."""
    if isinstance(operation_id_or_ref, ResultRefV1):
        dataset = resolve_result_ref(store, operation_id_or_ref, check_cancelled=check_cancelled)["dataset"]
    else:
        _, _, dataset, _ = _completed(store, operation_id_or_ref, source_run_id, check_cancelled or no_checkpoint)
    if consumer_kind not in (None, "field", "history"):
        raise ResultError("UNSUPPORTED_CAPABILITY", str(consumer_kind))
    if consumer_kind is not None and dataset.dataset_kind != consumer_kind:
        raise ResultError("UNSUPPORTED_OBSERVABLE", f"{consumer_kind} consumer cannot read {dataset.dataset_kind}")
    return dataset


def _registered_output(folder, receipt, ref, check):
    if ref.to_dict() not in receipt["outputs"]:
        raise ResultError("HASH_MISMATCH", f"unregistered dataset artifact {ref.path}", availability="corrupt")
    return verify_artifact(folder, ref, check=check)


def _history_column(folder, receipt, field_id, check):
    records = [ArtifactRefV1.from_dict(value) for value in receipt["outputs"]
               if value["path"] == "outputs/history-columns.json"]
    if len(records) != 1:
        raise ResultError("INVALID_DATASET", "history column metadata is missing")
    record = read_object(_registered_output(folder, receipt, records[0], check))
    if type(record.get("schema_version")) is not int or record["schema_version"] != 1:
        raise ResultError("UNSUPPORTED_CAPABILITY", "unknown history binding schema")
    column = record.get("fields", {}).get(field_id)
    if column is None:
        raise ResultError("UNSUPPORTED_FIELD", field_id)
    return column


def _unit(receipt, dimension):
    value = receipt["units"].get(dimension, "unknown")
    if isinstance(value, dict):
        return {**value, "symbol": value.get("symbol", value.get("unit", "unknown")), "dimension": dimension}
    return {"symbol": value, "dimension": dimension}


def _find_location(dataset, frame_id, field_id):
    if dataset.dataset_kind == "history":
        locations = [item for item in dataset.series if item.series_id == frame_id and field_id in item.field_ids]
    else:
        locations = [item for item in dataset.frames if item["frame_id"] == frame_id and
                     (field_id in item["fields"] or field_id in item["element_fields"])]
    if len(locations) != 1:
        raise ResultError("UNSUPPORTED_FIELD", f"{frame_id}: {field_id}")
    return locations[0]


def _semantics(folder, receipt, dataset, frame_id, field_id, check):
    item = _find_location(dataset, frame_id, field_id)
    if dataset.dataset_kind == "history":
        binding = _history_column(folder, receipt, field_id, check)["binding"]
        association = {"Global": "global", "Solid": "element", "Node": "point"}[binding["output_type"]]
        location = {"association": association}
        if binding["output_type"] == "Solid":
            location["integration_point"] = "IPpoint"
        if binding["entity_id"] is not None:
            location["entity_id"] = binding["entity_id"]
        return item, location, binding["basis"], binding["measure"], (
            "tensor" if binding["quantity"] == "stress" else "unknown"), {"symbol": binding["unit"]}, "unknown", binding
    association = "point" if field_id in item["fields"] else "element"
    location = {"association": association}
    layer = re.search(r"\((upper|lower|layer__[1-9][0-9]*)\)", field_id)
    if layer:
        location["layer"] = layer[1]
    if "Stress" in field_id or field_id == "STRESS":
        measure, dimension, shear = "Cauchy", "stress", "tensor"
    elif "Strain" in field_id or field_id == "TOSTRAIN":
        measure, dimension, shear = "unknown", "strain", "unknown"
    elif field_id in {"Displacement", "DISP"}:
        measure, dimension, shear = "displacement", "length", "unknown"
    elif field_id in {"Velocity", "VELO"}:
        measure, dimension, shear = "velocity", "velocity", "unknown"
    else:
        measure, dimension, shear = "unknown", "unknown", "unknown"
    basis = "element_local" if field_id.startswith("2DELEM_") else "global"
    return item, location, basis, measure, shear, _unit(receipt, dimension), item["geometry_state"], None


def make_result_ref(store: RunStore, source_run_id: str, operation_id: str, frame_id: str, field_id: str,
                    *, component=None, selection=None, basis=None, operator=None, coverage=None) -> ResultRefV1:
    """Pin a user's exact frame, field and selection to the completed bundle."""
    folder, receipt, dataset, _ = load_completed_operation(store, source_run_id, operation_id)
    _, location, native_basis, measure, shear, units, geometry, binding = _semantics(
        folder, receipt, dataset, frame_id, field_id, no_checkpoint)
    if binding is not None and component is None:
        component = binding["component"]
    default_operator = _CANONICAL_OPERATOR if binding is None else {"id": "curve_extract", "version": "1", "params": {}}
    reason = "MEASURE_UNKNOWN" if measure == "unknown" else "PROFILE_NOT_PHYSICALLY_VERIFIED"
    return ResultRefV1(
        source_run_id=source_run_id, source_node_id=receipt["source_node_id"],
        source_receipt_sha256=receipt["source_receipt_sha256"],
        source_receipt_ref=ArtifactRefV1.from_dict(receipt["source_receipt_ref"]), operation_id=operation_id,
        operation_manifest_sha256=canonical_sha256(receipt, exclude_keys=()), dataset_id=dataset.dataset_id,
        dataset_manifest_sha256=receipt["dataset_ref"]["sha256"],
        source_artifacts=tuple(ArtifactRefV1.from_dict(item) for item in receipt["source_artifacts"]),
        frame_id=frame_id, field_id=field_id, component=component, selection=selection or {},
        location=location, basis={"kind": basis or native_basis}, measure=measure, shear_convention=shear,
        units=units, geometry_state=geometry, operator=operator or default_operator, coverage=coverage or {},
        eligibility={"fit": False, "reason_codes": [reason]},
    )


def _selection(folder, selection, location, check):
    ids = selection.get("ids")
    if "artifact_ref" in selection:
        if ids is not None:
            raise ResultError("OBSERVABLE_MISMATCH", "selection has two ID lists")
        ref = ArtifactRefV1.from_dict(selection["artifact_ref"])
        path = verify_artifact(folder, ref, check=check)
        if ref.format_id == "npy-v2":
            data = read_safe_npy_v2(path, root=folder)
            if data.ndim != 1 or data.dtype.kind not in "iu":
                raise ResultError("OBSERVABLE_MISMATCH", "selection artifact must contain integer IDs")
            ids = data.tolist()
        elif ref.format_id == "json":
            ids = read_object(path).get("ids")
        else:
            raise ResultError("UNSUPPORTED_OBSERVABLE", "unsupported selection artifact format")
    if ids is None and selection:
        raise ResultError("OBSERVABLE_MISMATCH", "selection does not provide IDs")
    kind = selection.get("kind")
    expected = {"point": "node_ids", "element": "element_ids"}.get(location["association"])
    if kind not in (None, expected) or ("count" in selection and selection["count"] != len(ids)):
        raise ResultError("OBSERVABLE_MISMATCH", "selection association or count")
    return ids


def _operator(ref):
    op = ref.operator.get("id")
    if (not isinstance(op, str) or ref.operator.get("version", "1") != "1"
            or ref.operator.get("params", {}) or set(ref.operator) - {"id", "version", "params"}):
        raise ResultError("UNSUPPORTED_OBSERVABLE", "unregistered operator or parameters")
    return op


def _validate_semantics(ref, location, basis, measure, shear, units, geometry, binding):
    if any(ref.location.get(key) != expected for key, expected in location.items()) or ref.geometry_state != geometry:
        raise ResultError("OBSERVABLE_MISMATCH", "result location or geometry state")
    if ref.measure.casefold() != measure.casefold() or ref.shear_convention != shear:
        raise ResultError("MEASURE_UNKNOWN", "result measure or shear convention is not verified")
    if ref.eligibility.get("fit") and measure == "unknown":
        raise ResultError("MEASURE_UNKNOWN", ref.field_id)
    if ref.units.get("symbol", ref.units.get("unit", "unknown")) != units["symbol"]:
        raise ResultError("UNIT_UNKNOWN", "result unit differs from the dataset declaration")
    if "scale_to_si" in ref.units and ref.units["scale_to_si"] != units.get("scale_to_si"):
        raise ResultError("UNIT_UNKNOWN", "result unit scale differs from the dataset declaration")
    requested_basis = ref.basis.get("kind")
    if requested_basis != basis and not (basis == "element_local" and requested_basis == "global"):
        raise ResultError("UNSUPPORTED_BASIS", str(requested_basis))
    if binding is not None and ref.component != binding["component"]:
        raise ResultError("OBSERVABLE_MISMATCH", "history column component")


def read_result(store: RunStore, ref: ResultRefV1, *, check_cancelled=None) -> NumericResultV1:
    """Read the pinned numeric selection with the reference preserved verbatim."""
    check = check_cancelled or no_checkpoint
    resolved = resolve_result_ref(store, ref, check_cancelled=check)
    folder, receipt, dataset = (resolved[key] for key in ("operation_dir", "operation", "dataset"))
    profile = receipt["adapter"]["id"]
    if receipt["adapter"]["version"] != "1" or profile not in {*_SPATIAL_READERS, "OC-TH4-1"}:
        raise ResultError("UNSUPPORTED_CAPABILITY", "unregistered numeric reader")
    item, location, basis, measure, shear, units, geometry, binding = _semantics(
        folder, receipt, dataset, ref.frame_id, ref.field_id, check)
    _validate_semantics(ref, location, basis, measure, shear, units, geometry, binding)
    op = _operator(ref)
    coordinates = ()
    audit = {"id": op, "version": "1", "params": {}}
    if dataset.dataset_kind == "history":
        if profile != "OC-TH4-1" or op != "curve_extract" or ref.selection:
            raise ResultError("UNSUPPORTED_OBSERVABLE", "history supports curve_extract over the original sample rows")
        values_ref = ArtifactRefV1.from_dict(_history_column(folder, receipt, ref.field_id, check)["values_ref"])
        arrays = []
        for artifact in (item.coordinate_ref, item.source_row_index_ref, values_ref):
            path = _registered_output(folder, receipt, artifact, check)
            arrays.append(read_safe_npy_v2(path, root=folder))
        times, rows, values = arrays
        if (any(array.shape != (item.row_count,) for array in arrays)
                or rows.dtype.kind not in "iu" or not np.array_equal(rows, np.arange(item.row_count))):
            raise ResultError("INVALID_DATASET", "history artifact row counts or source indices")
        opencourant_history._times(times)
        opencourant_history.require_coverage(times, ref.coverage.get("time_interval"))
        coordinates = tuple(map(float, times))
        count = item.row_count
        audit["column_binding"] = binding
    else:
        if profile not in _SPATIAL_READERS or op not in _OPERATORS:
            raise ResultError("UNSUPPORTED_OBSERVABLE", "unregistered spatial reader or operator")
        source = _registered_output(folder, receipt, ArtifactRefV1.from_dict(item["artifact_ref"]), check)
        options = receipt["options"]
        kwargs = {"exact_required_set": options.get("exact_required_set"),
                  "expected": options.get("expected_mesh")} if profile == "OC-ANIM-1" else {}
        grid = _SPATIAL_READERS[profile](source, check_cancelled=check, **kwargs)
        if grid.time != item["time"]:
            raise ResultError("HASH_MISMATCH", "frame TIME differs from its registered value")
        ids = _selection(folder, ref.selection, location, check)
        values, entity_ids = vtk_fields.query_field(
            grid, ref.field_id, ids=ids, component=ref.component, operator=op, basis=ref.basis["kind"],
            units={"unit": units["symbol"], "components": ref.units.get("components")}, check_cancelled=check)
        count = len(entity_ids)
        values_ref = None
        audit.update(entity_ids=entity_ids, shape=values.shape, frame_time=grid.time)
        if op in {"min", "max", "mean", "norm"}:
            audit["base_ref_sha256"] = canonical_sha256(ref.with_operator(_CANONICAL_OPERATOR).to_dict(), exclude_keys=())
            audit["requested_ref_sha256"] = canonical_sha256(ref.to_dict(), exclude_keys=())
        elif op == "values":
            audit["id"] = "identity"
    if "expected" in ref.coverage and ref.coverage["expected"] != count:
        raise ResultError("COVERAGE_INCOMPLETE", "valid sample count differs from expected coverage")
    check()
    reason_codes = ("MEASURE_UNKNOWN",) if measure == "unknown" else ()
    return NumericResultV1(ref, tuple(map(float, np.ravel(values))), values_ref, coordinates,
                           {"total": count, "valid": count, "expected": count, "missing": 0},
                           resolved["availability"], reason_codes=reason_codes, operator_provenance=audit)
