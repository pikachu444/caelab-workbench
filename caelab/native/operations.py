"""RunStore-relative provenance and public derived-operation entry points."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path

from filelock import FileLock, Timeout

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


def owning_store(store):
    root = store.root
    if _ID.fullmatch(root.name):
        if _ID.fullmatch(root.parent.name) and root.parent.parent.name == ".derived":
            return RunStore(root.parent.parent.parent, expected=True)
        if root.parent.name == ".derived":
            return RunStore(root.parent.parent, expected=True)
        if (root / "job.json").is_file():
            return RunStore(root.parent, expected=True)
    return store


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
    content = {
        "identity": {key: data.get(key) for key in ("id", "node_id", "solver_id", "input_revision", "units", "unit_source")},
        "inputs": [{"source_copy": item.get("source_copy"), "source_sha256": _original_sha(item),
                    "execution_path": item.get("execution_path"), "execution_sha256": item.get("execution_sha256")}
                   for item in snapshot.get("files", ())],
        "outputs": snapshot.get("outputs", {}),
        "artifacts": snapshot.get("artifacts", {}),
    }
    for key in ("mesh_revision", "expected_mesh", "input_snapshot"):
        if key in snapshot:
            content[key] = snapshot[key]
    return canonical_sha256(content, exclude_keys=())


def _original_sha(item):
    return item.get("original_sha256") or item.get("source_sha256") or item.get("sha256")


def verify_original_decks(source, record, check=no_checkpoint, *, code="HASH_MISMATCH"):
    snapshot = record["snapshot"]
    if (sidecar := snapshot.get("input_snapshot")) is not None:
        if not isinstance(sidecar, dict) or sidecar.get("path") != "input/snapshot.json":
            raise ResultError(code, "invalid immutable input snapshot reference", availability="corrupt")
        path = artifact_path(source, sidecar["path"])
        try:
            if sha256_file(path, check) != sidecar.get("sha256"):
                raise ResultError(code, "immutable input snapshot changed", availability="corrupt")
            frozen = read_object(path)
        except FileNotFoundError as exc:
            raise ResultError(code, "immutable input snapshot is missing", availability="source_unavailable") from exc
        if any(frozen.get(key) != snapshot.get(key) for key in ("input_revision", "execution_input", "files")):
            raise ResultError(code, "immutable input snapshot disagrees with the run receipt", availability="corrupt")
    for item in record["snapshot"].get("files", ()):
        relative, digest = item.get("source_copy"), _original_sha(item)
        if not relative or not digest:
            raise ResultError(code, "original deck copy has no recorded path or digest", availability="corrupt")
        path = artifact_path(source, relative)
        try:
            if sha256_file(path, check) == digest:
                continue
        except FileNotFoundError as exc:
            raise ResultError(code, f"missing original deck copy {relative}", availability="source_unavailable") from exc
        raise ResultError(code, f"original deck copy changed: {relative}", availability="corrupt")


def _read_receipt(folder, source_run_id, operation_id):
    try:
        data = read_object(artifact_path(folder, "operation.json"))
    except FileNotFoundError:
        return None
    except ResultError:
        raise
    except (ValueError, OSError) as exc:
        raise ResultError("INVALID_DATASET", "operation receipt", availability="corrupt") from exc
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ResultError("UNSUPPORTED_CAPABILITY", "unknown operation receipt schema")
    state = data.get("state")
    if (data.get("id") != operation_id or data.get("source_run_id") != source_run_id
            or not isinstance(state, str) or state not in ACTIVE_OPERATIONS | TERMINAL_OPERATIONS):
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


def _operation_receipts(store, *, strict=False):
    base = artifact_path(store.root, ".derived")
    for path in base.glob("*/*/operation.json"):
        source, operation = path.parent.parent.name, path.parent.name
        if not (_ID.fullmatch(source) and _ID.fullmatch(operation)):
            continue
        try:
            folder = operation_dir(store, source, operation)
            receipt = _read_receipt(folder, source, operation)
        except ResultError:
            if strict:
                raise
            continue
        if receipt is not None:
            yield folder, receipt


def recover_derived_operations(store):
    """Recover orphaned operations while the caller holds the RunStore lease."""
    for folder, receipt in _operation_receipts(store):
        if receipt["state"] in ACTIVE_OPERATIONS:
            _interrupt(folder, receipt)


def find_active_duplicate(store, plan):
    for folder, receipt in _operation_receipts(store):
        if (receipt["state"] in ACTIVE_OPERATIONS and receipt["source_run_id"] == plan["source_run_id"]
                and receipt.get("cache_key") == plan["cache_key"] and receipt.get("plan_hash") == plan["plan_hash"]):
            lock_path = artifact_path(folder, "operation.lock")
            if lock_path.is_file():
                try:
                    with FileLock(lock_path, timeout=0):
                        continue
                except Timeout:
                    return receipt
    return None


def verify_derived_datasets(store):
    for _, receipt in _operation_receipts(store, strict=True):
        if receipt["state"] == "completed":
            load_completed_operation(store, receipt["source_run_id"], receipt["id"])


def stored_snapshot(store: RunStore, source_run_id: str, operation_id: str):
    folder = operation_dir(store, source_run_id, operation_id)
    receipt = _read_receipt(folder, source_run_id, operation_id)
    if receipt is None or receipt["state"] not in ACTIVE_OPERATIONS:
        return receipt
    try:
        lease = store.lease()
    except ValueError as exc:
        if isinstance(exc.__cause__, Timeout):
            return receipt
        raise
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


def load_completed_operation(store, source_run_id, operation_id, *, check=no_checkpoint, live_stale=False):
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
        verify_original_decks(source, fixed_record, check)
        for ref in inputs:
            if owners[ref.id] != ArtifactOwner("run", source_run_id):
                raise ValueError(f"wrong source owner for {ref.id}")
            verify_artifact(source, ref, check=check)
        dataset = DatasetManifestV1.from_dict(read_object(folder / manifest.path))
        digest = canonical_sha256(dataset.to_dict(), exclude_keys=())
        if receipt.get("dataset_manifest_sha256", digest) != digest:
            raise ResultError("HASH_MISMATCH", "canonical dataset manifest", availability="corrupt")
        refs_by_id = {ref.id: ref for ref in outputs}
        nested = [ArtifactRefV1.from_dict(frame["artifact_ref"]) for frame in dataset.frames]
        for series in dataset.series:
            nested.extend((series.coordinate_ref, series.source_row_index_ref))
        if any(refs_by_id.get(ref.id) != ref for ref in nested):
            raise ResultError("HASH_MISMATCH", "dataset references an unregistered artifact", availability="corrupt")
        stale = live_stale(live) if callable(live_stale) else live_stale
        return folder, receipt, dataset, bool(live.get("stale") or stale)
    except ResultError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ResultError("INVALID_DATASET", str(exc), availability="corrupt") from exc


def resolve_result_ref(store: RunStore, ref: ResultRefV1, *, check_cancelled=None, live_stale=False) -> dict:
    """Resolve a fixed ResultRef without substituting a newer run or dataset."""
    if not isinstance(ref, ResultRefV1):
        raise ResultError("INVALID_DATASET", "a ResultRefV1 is required")
    check = check_cancelled or no_checkpoint
    store = owning_store(store)
    folder, receipt, dataset, stale = load_completed_operation(
        store, ref.source_run_id, ref.operation_id, check=check, live_stale=live_stale)
    manifest_digests = {canonical_sha256(dataset.to_dict(), exclude_keys=()), receipt["dataset_ref"]["sha256"]}
    if (receipt["source_node_id"] != ref.source_node_id or dataset.dataset_id != ref.dataset_id
            or receipt["source_receipt_sha256"] != ref.source_receipt_sha256
            or ArtifactRefV1.from_dict(receipt["source_receipt_ref"]) != ref.source_receipt_ref
            or tuple(ArtifactRefV1.from_dict(item) for item in receipt["source_artifacts"]) != ref.source_artifacts
            or canonical_sha256(receipt, exclude_keys=()) != ref.operation_manifest_sha256
            or ref.dataset_manifest_sha256 not in manifest_digests):
        raise ResultError("HASH_MISMATCH", "result reference does not match the fixed operation bundle",
                          availability="corrupt")
    return {"operation_dir": folder, "operation": receipt, "dataset": dataset,
            "availability": "stale" if stale else "available"}


POST_PRODUCER = ProducerIdentity("caelab.result_conversion", "1")
