"""RunStore-relative provenance and public derived-operation entry points."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

from ..artifact_refs import (ArtifactOwner, ArtifactRefV1, DatasetManifestV1, ProducerIdentity, ResultRefV1,
                             canonical_sha256, validate_relative_artifact_path)
from ..result_readers import ResultError, no_checkpoint
from .run_store import ACTIVE_STATES, RunStore, atomic_json, utc_now

ACTIVE_OPERATIONS = ACTIVE_STATES | {"validating"}
TERMINAL_OPERATIONS = {"completed", "failed", "cancelled", "timed_out", "interrupted"}
_ID = re.compile(r"[0-9a-f]{32}")
_CHUNK = 1024 * 1024


def artifact_path(root: Path, relative: str) -> Path:
    try:
        validate_relative_artifact_path(relative, root)
        # Windows also interprets colons as NTFS alternate stream separators.
        if any(":" in segment or segment.endswith((".", " ")) for segment in relative.split("/")):
            raise ValueError(relative)
    except ValueError as exc:
        raise ResultError("PATH_ESCAPE", str(relative), availability="corrupt") from exc
    return root / relative


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ResultError("PATH_ESCAPE", f"invalid run or operation ID {value!r}", availability="corrupt")
    return value


def operation_dir(store, source_run_id, operation_id):
    return artifact_path(store.root, f".derived/{_id(source_run_id)}/{_id(operation_id)}")


def sha256_file(path, check=no_checkpoint):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK), b""):
            check()
            digest.update(chunk)
    check()
    return digest.hexdigest()


def artifact(root, path, role, format_id, producer, *, check=no_checkpoint):
    relative = path.relative_to(root).as_posix()
    path = artifact_path(root, relative)
    return ArtifactRefV1(f"{role}:{relative}", role, relative, sha256_file(path, check),
                         path.stat().st_size, format_id, producer)


def verify_artifact(root, ref, *, check=no_checkpoint, code="HASH_MISMATCH"):
    path = artifact_path(root, ref.path)
    try:
        if path.stat().st_size == ref.size and sha256_file(path, check) == ref.sha256:
            return path
    except FileNotFoundError as exc:
        raise ResultError(code, f"missing artifact {ref.path}", availability="source_unavailable") from exc
    raise ResultError(code, ref.path, availability="corrupt")


def read_object(path):
    def object_pairs(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate JSON key")
        return value

    def reject_constant(value):
        raise ValueError(f"invalid JSON constant {value}")

    data = json.loads(path.read_bytes(), object_pairs_hook=object_pairs, parse_constant=reject_constant)
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object in {path.name}")
    return data


def source_record(store, run_id):
    directory = artifact_path(store.root, _id(run_id))
    path = artifact_path(directory, "job.json")
    try:
        data = read_object(path)
        if (data.get("id") != run_id or not isinstance(data.get("node_id"), str) or not data["node_id"]
                or not isinstance(data.get("solver_id"), str) or not isinstance(data.get("snapshot"), dict)):
            raise ValueError("invalid run identity")
        return directory, path, data
    except FileNotFoundError as exc:
        raise ResultError("SOURCE_CHANGED", f"missing source run {run_id}", availability="source_unavailable") from exc
    except ValueError as exc:
        raise ResultError("INVALID_DATASET", f"invalid source receipt for {run_id}", availability="corrupt") from exc


def source_fingerprint(data):
    snapshot = data["snapshot"]
    return canonical_sha256({
        "identity": {key: data.get(key) for key in ("id", "node_id", "solver_id", "input_revision", "units", "unit_source")},
        "inputs": [{key: item.get(key) for key in ("source_copy", "source_sha256", "execution_path", "execution_sha256")}
                   for item in snapshot.get("files", ())],
        "outputs": snapshot.get("outputs", {}),
        "artifacts": snapshot.get("artifacts", {}),
    }, exclude_keys=())


def _read_receipt(folder, source_run_id, operation_id):
    try:
        data = read_object(artifact_path(folder, "operation.json"))
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise ResultError("INVALID_DATASET", "operation receipt", availability="corrupt") from exc
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ResultError("UNSUPPORTED_CAPABILITY", "unknown operation receipt schema")
    if (data.get("id") != operation_id or data.get("source_run_id") != source_run_id
            or data.get("state") not in ACTIVE_OPERATIONS | TERMINAL_OPERATIONS):
        raise ResultError("INVALID_DATASET", "operation identity or state", availability="corrupt")
    return data


def _interrupt(folder, receipt):
    for path in folder.rglob("*.part"):
        checked = artifact_path(folder, path.relative_to(folder).as_posix())
        checked.replace(checked.with_suffix(".interrupted"))
    artifact_path(folder, "dataset.json").unlink(missing_ok=True)
    receipt.update(state="interrupted", stage="interrupted", parse_state="failed", finished_at=utc_now(),
                   error={"code": "INTERRUPTED", "detail": "operation owner stopped before publication"},
                   dataset_ref=None)
    atomic_json(folder / "operation.json", receipt)
    return receipt


def recover_operations_unlocked(store):
    base = artifact_path(store.root, ".derived")
    for path in base.glob("*/*/operation.json"):
        source, operation = path.parent.parent.name, path.parent.name
        if not (_ID.fullmatch(source) and _ID.fullmatch(operation)):
            continue
        folder = operation_dir(store, source, operation)
        try:
            receipt = _read_receipt(folder, source, operation)
        except ResultError as exc:
            if exc.code != "UNSUPPORTED_CAPABILITY":
                raise
            continue
        if receipt is not None and receipt["state"] in ACTIVE_OPERATIONS:
            _interrupt(folder, receipt)


def stored_snapshot(store: RunStore, source_run_id: str, operation_id: str):
    folder = operation_dir(store, source_run_id, operation_id)
    receipt = _read_receipt(folder, source_run_id, operation_id)
    if receipt is None or receipt["state"] not in ACTIVE_OPERATIONS:
        return receipt
    try:
        lease = store.lease()
    except ValueError:
        return receipt
    try:
        receipt = _read_receipt(folder, source_run_id, operation_id)
        if receipt is not None and receipt["state"] in ACTIVE_OPERATIONS:
            return _interrupt(folder, receipt)
        return receipt
    finally:
        lease.release()


def plan_conversion(store: RunStore, source_run_id: str, converter_id: str, converter_exe=None,
                    frame_ids=(), options=None) -> dict:
    from .converters import plan_oc_animation, plan_oc_history, plan_vtu

    if converter_id == "OC-ANIM-1":
        return plan_oc_animation(store, source_run_id, converter_exe, frame_ids, options)
    if converter_id == "OC-TH4-1":
        if frame_ids:
            raise ResultError("UNSUPPORTED_OBSERVABLE", "history has a series rather than spatial frames")
        return plan_oc_history(store, source_run_id, converter_exe, options)
    if converter_id == "VTU-1" and converter_exe is None:
        return plan_vtu(store, source_run_id, frame_ids, options)
    raise ResultError("UNSUPPORTED_CAPABILITY", converter_id)


def submit_conversion(manager, store: RunStore, plan: Mapping, *, timeout=120.0, executor=None):
    return manager.submit_result_conversion(store, plan, timeout=timeout, executor=executor)


def operation_snapshot(manager, store: RunStore, source_run_id: str, operation_id: str):
    return manager.operation_snapshot(store, source_run_id, operation_id)


def cancel_operation(manager, operation_id: str):
    return manager.cancel_operation(operation_id)


def load_completed_operation(store, source_run_id, operation_id, *, check=no_checkpoint):
    """Return the receipt and manifest after verifying the owner-scoped bundle."""
    folder = operation_dir(store, source_run_id, operation_id)
    receipt = _read_receipt(folder, source_run_id, operation_id)
    if receipt is None:
        raise ResultError("SOURCE_CHANGED", "operation receipt is missing", availability="source_unavailable")
    if receipt["state"] != "completed":
        raise ResultError("INVALID_DATASET", f"operation is {receipt['state']}")
    source, _, live = source_record(store, source_run_id)
    try:
        pinned = ArtifactRefV1.from_dict(receipt["source_receipt_ref"])
        inputs = tuple(ArtifactRefV1.from_dict(ref) for ref in receipt["source_artifacts"])
        staged = tuple(ArtifactRefV1.from_dict(ref) for ref in receipt["staged_artifacts"])
        manifest = ArtifactRefV1.from_dict(receipt["dataset_ref"])
        outputs = tuple(ArtifactRefV1.from_dict(ref) for ref in receipt["outputs"])
        refs = (pinned, manifest, *staged, *outputs, *inputs)
        owners = {key: ArtifactOwner.from_dict(value) for key, value in receipt["artifact_owners"].items()}
        if len({ref.id for ref in refs}) != len(refs) or set(owners) != {ref.id for ref in refs}:
            raise ValueError("artifact owner index disagrees with the receipt")
        for ref in (pinned, manifest, *staged, *outputs):
            if owners[ref.id] != ArtifactOwner("operation", operation_id, source_run_id):
                raise ValueError(f"wrong operation owner for {ref.id}")
            verify_artifact(folder, ref, check=check)
        if pinned.path != "inputs/source-receipt.json" or manifest.path != "dataset.json":
            raise ValueError("incorrect receipt or manifest path")
        fixed_record = read_object(folder / pinned.path)
        if (fixed_record.get("id") != source_run_id or fixed_record.get("node_id") != receipt["source_node_id"]
                or pinned.sha256 != receipt["source_receipt_sha256"]
                or source_fingerprint(fixed_record) != receipt["source_fingerprint"]):
            raise ValueError("original receipt identity mismatch")
        if source_fingerprint(live) != receipt["source_fingerprint"]:
            raise ResultError("SOURCE_CHANGED", "immutable source run identity has changed", availability="stale")
        for ref in inputs:
            if owners[ref.id] != ArtifactOwner("run", source_run_id):
                raise ValueError(f"wrong source owner for {ref.id}")
            verify_artifact(source, ref, check=check)
        dataset = DatasetManifestV1.from_dict(read_object(folder / manifest.path))
        return folder, receipt, dataset, bool(live.get("stale"))
    except ResultError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ResultError("INVALID_DATASET", str(exc), availability="corrupt") from exc


def resolve_result_ref(store: RunStore, ref: ResultRefV1, *, check_cancelled=None) -> dict:
    """Resolve a fixed ResultRef without substituting a newer run or dataset."""
    if not isinstance(ref, ResultRefV1):
        raise ResultError("INVALID_DATASET", "a ResultRefV1 is required")
    check = check_cancelled or no_checkpoint
    folder, receipt, dataset, stale = load_completed_operation(store, ref.source_run_id, ref.operation_id, check=check)
    if (receipt["source_node_id"] != ref.source_node_id or dataset.dataset_id != ref.dataset_id
            or receipt["source_receipt_sha256"] != ref.source_receipt_sha256
            or ArtifactRefV1.from_dict(receipt["source_receipt_ref"]) != ref.source_receipt_ref
            or tuple(ArtifactRefV1.from_dict(item) for item in receipt["source_artifacts"]) != ref.source_artifacts
            or canonical_sha256(receipt, exclude_keys=()) != ref.operation_manifest_sha256
            or receipt["dataset_ref"]["sha256"] != ref.dataset_manifest_sha256):
        raise ResultError("HASH_MISMATCH", "result reference does not match the fixed operation bundle",
                          availability="corrupt")
    return {"operation_dir": folder, "operation": receipt, "dataset": dataset,
            "availability": "stale" if stale else "available"}


POST_PRODUCER = ProducerIdentity("caelab.result_conversion", "1")
