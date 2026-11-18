"""Registered numeric result queries over verified RunStore bundles."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

import numpy as np

from ..artifact_refs import ArtifactRefV1, DatasetManifestV1, NumericResultV1, ResultRefV1, canonical_sha256, read_safe_npy_v2
from ..result_readers import ResultError, no_checkpoint
from ..result_readers import opencourant_history, vtk_fields
from . import converters
from .operations import (_original_reader, artifact_path, decode_json, load_completed_operation, owning_store, read_object, resolve_result_ref,
                         sha256_file, verify_artifact, with_input_snapshot)
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
        _, _, dataset, _ = _completed(owning_store(store), operation_id_or_ref, source_run_id,
                                      check_cancelled or no_checkpoint)
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
    value = receipt["units"].get(dimension, receipt["units"].get("stress", "unknown") if dimension == "pressure" else "unknown")
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


def _require_archived_one_point_hex8(store, folder, receipt, entity_id, check):
    source = artifact_path(store.root, receipt["source_run_id"])
    frozen = read_object(artifact_path(folder, receipt["source_receipt_ref"]["path"]))
    frozen = with_input_snapshot(frozen, artifact_path(folder, "inputs/input-snapshot.json"))
    _, _, formulations = converters._rad_snapshot_metadata(source, frozen, check, animation=False)
    if formulations.get(str(entity_id)) != "hex8_1pt":
        raise ResultError("UNSUPPORTED_OBSERVABLE", "archived one-point Lagrangian HEX8 evidence is required for IPpoint")


def _semantics(store, folder, receipt, dataset, frame_id, field_id, check):
    item = _find_location(dataset, frame_id, field_id)
    if dataset.dataset_kind == "history":
        column = _history_column(folder, receipt, field_id, check)
        binding = column["binding"]
        association = {"Global": "global", "Solid": "element", "Node": "point"}[binding["output_type"]]
        location = {"association": association}
        if binding["entity_id"] is not None:
            location["entity_id"] = binding["entity_id"]
        if "semantics" not in column and _original_reader(receipt):
            quantity = binding["quantity"]
            units = {"symbol": binding["unit"]}
            if association == "element" and quantity in {"stress", "strain"}:
                _require_archived_one_point_hex8(store, folder, receipt, binding["entity_id"], check)
                location["integration_point"] = "IPpoint"
                units["dimension"] = "pressure" if quantity == "stress" else "strain"
            shear = "tensor" if quantity == "stress" else "unknown"
            return item, location, binding["basis"], binding["measure"], shear, units, "unknown", binding
        semantics = column["semantics"]
        if association == "point":
            location["integration_point"] = "none"
        if "integration_point" in semantics:
            if association == "element" and _original_reader(receipt):
                _require_archived_one_point_hex8(store, folder, receipt, binding["entity_id"], check)
            location["integration_point"] = semantics["integration_point"]
        return item, location, semantics["basis"], semantics["measure"], (
            "tensor" if binding["quantity"] == "stress" else "unknown"), {
                "symbol": semantics["unit"], "dimension": semantics["unit_dimension"]}, "unknown", binding
    association = "point" if field_id in item["fields"] else "element"
    location = {"association": association}
    if association == "point":
        location["integration_point"] = "none"
    layer = re.search(r"\((upper|lower|layer__[1-9][0-9]*)\)", field_id)
    if layer:
        location["layer"] = layer[1]
    if "Stress" in field_id or field_id == "STRESS":
        measure, dimension, shear = "Cauchy", "pressure", "tensor"
        if _original_reader(receipt):
            dimension = "stress"
    elif "Strain" in field_id or field_id == "TOSTRAIN":
        measure, dimension, shear = "unknown", "strain", "unknown"
    elif field_id in {"Displacement", "DISP"}:
        measure, dimension, shear = "displacement", "length", "unknown"
    elif field_id in {"Velocity", "VELO"}:
        measure, dimension, shear = "velocity", "velocity", "unknown"
    elif field_id in {"EROSION_STATUS", "Scalar:EROSION_STATUS"}:
        measure, dimension, shear = "erosion_status", "dimensionless", "unknown"
    else:
        measure, dimension, shear = "unknown", "unknown", "unknown"
    basis = "element_local" if field_id.startswith("2DELEM_") else "global"
    return item, location, basis, measure, shear, _unit(receipt, dimension), item["geometry_state"], None


def make_result_ref(store: RunStore, source_run_id: str, operation_id: str, frame_id: str, field_id: str,
                    *, component=None, selection=None, basis=None, operator=None, coverage=None) -> ResultRefV1:
    """Pin a user's exact frame, field and selection to the completed bundle."""
    store = owning_store(store)
    folder, receipt, dataset, _ = load_completed_operation(store, source_run_id, operation_id)
    _, location, native_basis, measure, shear, units, geometry, binding = _semantics(
        store, folder, receipt, dataset, frame_id, field_id, no_checkpoint)
    if binding is not None and component is None:
        component = binding["component"]
    default_operator = _CANONICAL_OPERATOR if binding is None else {"id": "curve_extract", "version": "1", "params": {}}
    reason = "MEASURE_UNKNOWN" if measure == "unknown" else "PROFILE_NOT_PHYSICALLY_VERIFIED"
    selection = selection or {}
    selection_id = _selection_id(selection)
    if _original_reader(receipt) and _legacy_payload_reference(selection, selection_id) is not None:
        _selection(store, folder, receipt, selection, location, no_checkpoint, legacy=True)
    elif selection_id is not None:
        _, revision, payload_sha = _saved_selection(store, receipt, selection, location, no_checkpoint,
                                                    pinning=True)
        selection = {**selection, "revision_sha256": revision}
        if payload_sha is not None:
            selection["payload_sha256"] = payload_sha
    elif "ids" in selection:
        revision = _selection_digest(selection, selection["ids"])
        _verify_selection_revision(selection, revision, required=False)
        selection = {**selection, "revision_sha256": revision}
    elif selection:
        raise ResultError("HASH_MISMATCH", "selection has no registered payload or inline IDs")
    return ResultRefV1(
        source_run_id=source_run_id, source_node_id=receipt["source_node_id"],
        source_receipt_sha256=receipt["source_receipt_sha256"],
        source_receipt_ref=ArtifactRefV1.from_dict(receipt["source_receipt_ref"]), operation_id=operation_id,
        operation_manifest_sha256=canonical_sha256(receipt, exclude_keys=()), dataset_id=dataset.dataset_id,
        dataset_manifest_sha256=canonical_sha256(dataset.to_dict(), exclude_keys=()),
        source_artifacts=tuple(ArtifactRefV1.from_dict(item) for item in receipt["source_artifacts"]),
        frame_id=frame_id, field_id=field_id, component=component, selection=selection or {},
        location=location, basis={"kind": basis or native_basis}, measure=measure, shear_convention=shear,
        units=units, geometry_state=geometry, operator=operator or default_operator, coverage=coverage or {},
        eligibility={"fit": False, "reason_codes": [reason]},
    )


def _selection_payload(directory, descriptor, check):
    if not isinstance(descriptor, Mapping):
        raise ResultError("SCHEMA_MISMATCH", "invalid selection payload reference")
    relative = descriptor.get("path")
    path = artifact_path(directory, relative)
    fmt = descriptor.get("format_id")
    fmt = fmt.lower() if isinstance(fmt, str) else None
    suffix = path.suffix.lower()
    json_formats = {"json", "caelab.selection.v1"}
    npy_formats = {"npy", "numpy.npy.v2"}
    if fmt is not None and fmt not in json_formats | npy_formats:
        raise ResultError("SCHEMA_MISMATCH", "selection payload must be JSON or NPY")
    if (suffix == ".json" and (fmt is None or fmt in json_formats)) or (not suffix and fmt in json_formats):
        is_npy = False
    elif (suffix == ".npy" and (fmt is None or fmt in npy_formats)) or (not suffix and fmt in npy_formats):
        is_npy = True
    else:
        raise ResultError("SCHEMA_MISMATCH", "selection payload format_id does not match file suffix")
    try:
        check()
        size = path.stat().st_size
        if "size" in descriptor and size != descriptor["size"]:
            raise ResultError("HASH_MISMATCH", "selection payload size changed")
        if is_npy:
            if sha256_file(path, check) != descriptor.get("sha256"):
                raise ResultError("HASH_MISMATCH", "selection payload bytes changed")
            array = read_safe_npy_v2(path, root=directory)
            if array.ndim != 1 or array.dtype.kind not in "iu":
                raise ResultError("SCHEMA_MISMATCH", "selection payload must be a one-dimensional integer array")
            payload = array.tolist()
        else:
            raw = path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != descriptor.get("sha256"):
                raise ResultError("HASH_MISMATCH", "selection payload bytes changed")
            payload = decode_json(raw)
    except FileNotFoundError as exc:
        raise ResultError("HASH_MISMATCH", "selection payload is missing") from exc
    return path, payload


def _selection_id(selection):
    entity_ref = selection.get("entity_selection_ref", {})
    if not isinstance(entity_ref, Mapping):
        raise ResultError("SCHEMA_MISMATCH", "invalid entity selection reference")
    legacy = entity_ref.get("selection_id", entity_ref.get("id"))
    selection_id = selection.get("selection_id", legacy)
    if legacy is not None and selection_id != legacy:
        raise ResultError("HASH_MISMATCH", "conflicting selection identifiers")
    return selection_id


def _selection_reference(selection):
    for scope in (selection, selection.get("entity_selection_ref", {})):
        for key in ("artifact_ref", "payload_ref"):
            if key in scope:
                return scope[key]
    return None


def _legacy_payload_reference(selection, selection_id):
    entity_ref = selection.get("entity_selection_ref", {})
    if "revision_sha256" in selection or "revision_sha256" in entity_ref:
        return None
    reference = _selection_reference(selection)
    if reference is None and selection_id is not None and "sha256" in entity_ref:
        reference = {"path": "payload.json", "sha256": entity_ref["sha256"]}
    return reference


def _selection_digest(descriptor, payload):
    return canonical_sha256({"selection": {k: v for k, v in descriptor.items() if k != "revision_sha256"},
                             "payload": payload}, exclude_keys=())


def _verify_selection_revision(descriptor, digest, *, required=True):
    entity_ref = descriptor.get("entity_selection_ref", {})
    if not isinstance(entity_ref, Mapping):
        raise ResultError("SCHEMA_MISMATCH", "invalid entity selection reference")
    if "entity_selection_ref" in descriptor and "revision_sha256" not in entity_ref:
        raise ResultError("HASH_MISMATCH", "entity selection reference has no revision_sha256")
    revision = descriptor.get("revision_sha256")
    nested = entity_ref.get("revision_sha256")
    for present, expected in (("revision_sha256" in descriptor, revision),
                              ("revision_sha256" in entity_ref, nested)):
        if present and expected != digest:
            raise ResultError("HASH_MISMATCH", "selection revision changed")
    if required and revision is None and nested is None:
        raise ResultError("HASH_MISMATCH", "selection has no revision digest")


def _saved_selection(store, receipt, selection, location, check, *, pinning=False):
    selection_id = _selection_id(selection)
    if not isinstance(selection_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", selection_id):
        raise ResultError("PATH_ESCAPE", f"invalid selection ID {selection_id!r}")
    directory = artifact_path(store.root, f"selections/{selection_id}")
    path = artifact_path(directory, "selection.json")
    if not path.is_file():
        path = artifact_path(directory, "payload.json")
    check()
    try:
        raw = path.read_bytes()
        saved = decode_json(raw)
        if not isinstance(saved, dict):
            raise ValueError("expected a JSON object")
    except FileNotFoundError as exc:
        raise ResultError("HASH_MISMATCH", f"missing selection {selection_id}") from exc
    except (OSError, ValueError) as exc:
        raise ResultError("SCHEMA_MISMATCH", f"selection {selection_id}: {exc}") from exc
    if saved.get("schema", saved.get("schema_id")) != "caelab.selection.v1":
        raise ResultError("SCHEMA_MISMATCH", "expected caelab.selection.v1")
    entity = {"point": "node", "element": "element"}.get(location["association"])
    aliases = {"node": ("node", "nodes", "node_ids"), "element": ("element", "elements", "element_ids")}
    if saved.get("entity_kind") not in aliases.get(entity, ()):
        raise ResultError("OBSERVABLE_MISMATCH", "selection entity kind")
    run_id, mesh_revision = saved.get("run_id"), saved.get("mesh_revision")
    expected_revision = receipt.get("source_mesh_revision") or receipt["source_input_revision"]
    if ((run_id is None and mesh_revision is None)
            or (run_id is not None and run_id != receipt["source_run_id"])
            or (mesh_revision is not None and mesh_revision != expected_revision)):
        raise ResultError("SOURCE_MISMATCH", "selection run or mesh revision")
    file_sha = hashlib.sha256(raw).hexdigest()
    if "sha256" in selection and file_sha != selection["sha256"]:
        raise ResultError("HASH_MISMATCH", "saved selection digest")
    payload_ref = saved.get("payload_ref")
    reference = _selection_reference(selection)
    if payload_ref is None:
        payload = saved.get("ids")
        if reference is not None:
            verified_path, _ = _selection_payload(directory, reference, check)
            if verified_path != path:
                raise ResultError("HASH_MISMATCH", "caller selection artifact differs from the saved selection")
    else:
        if not isinstance(payload_ref, dict) or "ids" in saved:
            raise ResultError("SCHEMA_MISMATCH", "invalid selection payload reference")
        payload_path, payload = _selection_payload(directory, payload_ref, check)
        if reference is not None:
            checked, _ = _selection_payload(directory, reference, check)
            if checked != payload_path:
                raise ResultError("HASH_MISMATCH", "caller selection artifact differs from the saved payload")
    ids = payload.get("ids") if isinstance(payload, dict) else payload
    if not isinstance(ids, (list, tuple)):
        raise ResultError("SCHEMA_MISMATCH", "selection IDs must be a list")
    digest = _selection_digest(saved, payload)
    _verify_selection_revision(saved, digest)
    _verify_selection_revision(selection, digest, required=False)
    payload_sha = file_sha if path.name == "payload.json" and payload_ref is None else None
    if "payload_sha256" in selection and selection["payload_sha256"] != payload_sha:
        raise ResultError("HASH_MISMATCH", "saved selection payload bytes changed")
    if payload_sha is not None and not pinning and "payload_sha256" not in selection and reference is None:
        raise ResultError("HASH_MISMATCH", "single-file selection has no payload byte digest")
    return ids, digest, payload_sha


def _legacy_selection_payload(store, folder, receipt, selection, selection_id, reference, location, check):
    if selection_id is not None and (not isinstance(selection_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", selection_id)):
        raise ResultError("PATH_ESCAPE", f"invalid selection ID {selection_id!r}")
    if not isinstance(reference, Mapping):
        raise ResultError("SCHEMA_MISMATCH", "invalid legacy selection reference")
    digest, path = reference.get("sha256"), reference.get("path")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ResultError("HASH_MISMATCH", "legacy selection has no payload byte digest")
    source = selection.get("selection_source", selection.get("entity_selection_ref", {}).get("selection_source"))
    explicit_ref = _selection_reference(selection) is not None
    if source in ("store", "run_store"):
        roots = (store.root,)
    elif source == "operation" or explicit_ref:
        roots = (folder,)
    else:
        roots = (folder, store.root)
    directories = []
    for root in roots:
        if selection_id is not None and isinstance(path, str) and "/" not in path:
            directories.append(artifact_path(root, f"selections/{selection_id}"))
            if not explicit_ref:
                continue  # A historical digest without a path pins the selected ID's payload.
        directories.append(root)
    for directory in directories:
        try:
            _, payload = _selection_payload(directory, reference, check)
        except ResultError as exc:
            if exc.code == "HASH_MISMATCH" and isinstance(exc.__cause__, FileNotFoundError):
                continue
            raise
        except ValueError as exc:
            raise ResultError("SCHEMA_MISMATCH", "invalid legacy selection payload") from exc
        if not isinstance(payload, dict):
            return payload
        schema = payload.get("schema", payload.get("schema_id"))
        if schema not in (None, "caelab.selection.v1"):
            raise ResultError("SCHEMA_MISMATCH", "expected caelab.selection.v1")
        entity_kinds = {"point": ("node", "nodes", "node_ids"), "element": ("element", "elements", "element_ids")}
        if "entity_kind" in payload and payload["entity_kind"] not in entity_kinds.get(location["association"], ()):
            raise ResultError("OBSERVABLE_MISMATCH", "selection entity kind")
        expected_revision = receipt.get("source_mesh_revision") or receipt["source_input_revision"]
        if (payload.get("run_id") not in (None, receipt["source_run_id"])
                or payload.get("mesh_revision") not in (None, expected_revision)):
            raise ResultError("SOURCE_MISMATCH", "selection run or mesh revision")
        return payload.get("ids")
    raise ResultError("HASH_MISMATCH", "legacy selection payload is missing or its bytes changed")


def _selection(store, folder, receipt, selection, location, check, *, legacy=False, legacy_inline=False):
    ids = selection.get("ids")
    kind = selection.get("kind")
    entity = {"point": "node", "element": "element"}.get(location["association"])
    selection_id = _selection_id(selection)
    reference = _legacy_payload_reference(selection, selection_id) if legacy else None
    if reference is not None or selection_id is not None:
        if ids is not None:
            raise ResultError("OBSERVABLE_MISMATCH", "selection has two ID lists")
        if reference is not None:
            ids = _legacy_selection_payload(store, folder, receipt, selection, selection_id, reference, location, check)
        else:
            ids, digest, _ = _saved_selection(store, receipt, selection, location, check)
            _verify_selection_revision(selection, digest)
    elif ids is not None:
        _verify_selection_revision(selection, _selection_digest(selection, ids), required=not legacy_inline)
    if ids is None and selection:
        raise ResultError("HASH_MISMATCH", "selection does not identify a pinned payload")
    if ids is not None and not isinstance(ids, (list, tuple)):
        raise ResultError("SCHEMA_MISMATCH", "selection IDs must be a list")
    expected = f"{entity}_ids" if entity else None
    if kind not in (None, expected) or ("count" in selection and selection["count"] != len(ids)):
        raise ResultError("OBSERVABLE_MISMATCH", "selection association or count")
    return ids


def _operator(ref):
    op = ref.operator.get("id")
    if (not isinstance(op, str) or ref.operator.get("version", "1") != "1"
            or ref.operator.get("params", {}) or set(ref.operator) - {"id", "version", "params"}):
        raise ResultError("UNSUPPORTED_OBSERVABLE", "unregistered operator or parameters")
    return op


def _validate_semantics(ref, location, basis, measure, shear, units, geometry, binding, item):
    if any(ref.location.get(key, "none" if key == "integration_point" else None) != expected
           for key, expected in location.items()) or ref.geometry_state != geometry:
        raise ResultError("SEMANTIC_MISMATCH", "result location or geometry state")
    for key in ("integration_point", "ip"):
        if key in ref.location and ref.location[key] != location.get("integration_point", "none"):
            raise ResultError("SEMANTIC_MISMATCH", "unsupported integration point")
    if "layer" not in location and ref.location.get("layer") not in (None, "", "none"):
        raise ResultError("SEMANTIC_MISMATCH", "field does not have a shell layer")
    if ref.measure.casefold() != measure.casefold() or ref.shear_convention != shear:
        raise ResultError("MEASURE_UNKNOWN", "result measure or shear convention is not verified")
    if ref.eligibility.get("fit") or ref.eligibility.get("fit_eligible"):
        raise ResultError("SEMANTIC_MISMATCH", "reader profile does not authorize fitting")
    for key in ("dimension", "unit_dimension"):
        if key in ref.units and ref.units[key] != units.get("dimension"):
            raise ResultError("SEMANTIC_MISMATCH", "result unit dimension differs from the field")
    if ref.units.get("symbol", ref.units.get("unit", "unknown")) != units["symbol"]:
        raise ResultError("UNIT_UNKNOWN", "result unit differs from the dataset declaration")
    if "scale_to_si" in ref.units and ref.units["scale_to_si"] != units.get("scale_to_si"):
        raise ResultError("UNIT_UNKNOWN", "result unit scale differs from the dataset declaration")
    requested_basis = ref.basis.get("kind")
    orientation = ref.basis.get("orientation_sha256")
    if "orientation_sha256" in ref.basis and (
            not isinstance(orientation, str) or not re.fullmatch(r"[0-9a-f]{64}", orientation)
            or requested_basis != "element_local" or location["association"] != "element"
            or binding is not None or orientation != item.get("orientation_sha256")):
        raise ResultError("SEMANTIC_MISMATCH", "orientation does not match the field basis")
    if requested_basis != basis and not (basis == "element_local" and requested_basis == "global"):
        raise ResultError("UNSUPPORTED_BASIS", str(requested_basis))
    if binding is not None and ref.component != binding["component"]:
        raise ResultError("OBSERVABLE_MISMATCH", "history column component")


def read_result(store: RunStore, ref: ResultRefV1, *, check_cancelled=None, live_stale=None) -> NumericResultV1:
    """Read the pinned numeric selection with the reference preserved verbatim."""
    check = check_cancelled or no_checkpoint
    if isinstance(ref, ResultRefV1) and not ref.eligibility.get("query", True):
        raise ResultError("SEMANTIC_MISMATCH", "Quantity reference is not query-eligible")
    store = owning_store(store)
    resolved = resolve_result_ref(store, ref, check_cancelled=check, live_stale=live_stale)
    folder, receipt, dataset = (resolved[key] for key in ("operation_dir", "operation", "dataset"))
    profile = receipt["adapter"]["id"]
    if receipt["adapter"]["version"] != "1" or profile not in {*_SPATIAL_READERS, "OC-TH4-1"}:
        raise ResultError("UNSUPPORTED_CAPABILITY", "unregistered numeric reader")
    adapter_sha = receipt["adapter"].get("code_sha256")
    top_sha = receipt.get("adapter_code_sha256")
    expected_reader = receipt.get("reader_code_sha256", receipt.get("adapter_code_sha256", adapter_sha))
    recorded = [value for value in (receipt.get("reader_code_sha256"), top_sha, adapter_sha) if value is not None]
    if expected_reader is None and recorded:
        expected_reader = recorded[0]
    active_reader = converters.reader_code_sha256(profile, check)
    original_reader = resolved["legacy_manifest"] and _original_reader(receipt)
    if (any(value != recorded[0] for value in recorded[1:])
            or not original_reader and active_reader != expected_reader):
        raise ResultError("HASH_MISMATCH", "active reader code differs from the operation receipt")
    item, location, basis, measure, shear, units, geometry, binding = _semantics(
        store, folder, receipt, dataset, ref.frame_id, ref.field_id, check)
    _validate_semantics(ref, location, basis, measure, shear, units, geometry, binding, item)
    op = _operator(ref)
    normalized = "identity" if op == "values" else op
    base_operator = {"id": "curve_extract", "version": "1", "params": {}} if binding else _CANONICAL_OPERATOR
    base_hash = canonical_sha256(ref.with_operator(base_operator).to_dict(), exclude_keys=())
    requested_hash = canonical_sha256(ref.to_dict(), exclude_keys=())
    coordinates = ()
    audit = {"id": normalized, "version": "1", "params": {}, "base_quantity_ref_sha256": base_hash,
             "requested_quantity_ref_sha256": requested_hash, "base_ref_sha256": base_hash,
             "requested_ref_sha256": requested_hash}
    if original_reader:
        audit.update(reader_code_sha256=active_reader, recorded_reader_code_sha256=expected_reader or active_reader)
    eroded = ()
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
        kwargs = {}
        if profile == "OC-ANIM-1":
            kwargs["exact_required_set"] = options.get("exact_required_set")
            kwargs["expected"] = options.get("expected_mesh")
            kwargs["erosion_active_value"] = options.get("erosion_active_value", options.get("active_erosion_status"))
        else:
            kwargs["node_id_policy"] = options.get("node_id_policy")
        grid = _SPATIAL_READERS[profile](source, check_cancelled=check, **kwargs)
        metadata = vtk_fields.sample_metadata(grid)
        if (grid.time != item["time"] and (not original_reader or item["time"] is not None)
                or any(item.get(key) != val for key, val in metadata.items() if key in item or not original_reader)):
            raise ResultError("HASH_MISMATCH", "frame TIME differs from its registered value")
        ids = _selection(store, folder, receipt, ref.selection, location, check,
                         legacy=resolved["legacy_manifest"], legacy_inline=original_reader)
        if location["association"] == "point" and "entity_id" in ref.location:
            entity_id = ref.location["entity_id"]
            if type(entity_id) is not int or entity_id <= 0 or (ids is not None and tuple(ids) != (entity_id,)):
                raise ResultError("SEMANTIC_MISMATCH", "point entity_id differs from the selected IDs")
            ids = (entity_id,)
        values, entity_ids, coverage = vtk_fields.query_field(
            grid, ref.field_id, ids=ids, component=ref.component, operator=op, basis=ref.basis["kind"],
            units={"unit": units["symbol"], "components": ref.units.get("components")}, check_cancelled=check,
            include_coverage=True)
        count = coverage["total"]
        eroded = coverage["eroded_ids"]
        values_ref = None
        audit.update(entity_ids=entity_ids, shape=values.shape, frame_time=grid.time, **metadata)
        if eroded:
            audit["eroded_ids"] = eroded
    if "expected" in ref.coverage and ref.coverage["expected"] != count:
        raise ResultError("COVERAGE_INCOMPLETE", "valid sample count differs from expected coverage")
    check()
    reason_codes = (*(("MEASURE_UNKNOWN",) if measure == "unknown" else ()),
                    *(("ERODED_ELEMENTS",) if eroded else ()))
    return NumericResultV1(ref, tuple(map(float, np.ravel(values))), values_ref, coordinates,
                           {"total": count, "valid": count - len(eroded), "expected": count,
                            "missing": len(eroded), "eroded": len(eroded)},
                           resolved["availability"], reason_codes=reason_codes, operator_provenance=audit)
