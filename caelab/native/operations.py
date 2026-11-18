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
TERMINAL_OPERATIONS = {"completed", "failed", "cancelled"}
_LEGACY_TERMINAL_OPERATIONS = {"timed_out", "interrupted"}
INPUT_SNAPSHOT = "input/snapshot.json"
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


def decode_json(raw):
    def object_pairs(pairs):
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError("duplicate JSON key")
        return value

    def reject_constant(value):
        raise ValueError(f"invalid JSON constant {value}")

    return json.loads(raw, object_pairs_hook=object_pairs, parse_constant=reject_constant)


def read_object(path):
    data = decode_json(path.read_bytes())
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
        return directory, path, with_input_snapshot(data, artifact_path(directory, INPUT_SNAPSHOT))
    except FileNotFoundError as exc:
        raise ResultError("SOURCE_CHANGED", f"missing source run {run_id}", availability="source_unavailable") from exc
    except ResultError:
        raise
    except ValueError as exc:
        raise ResultError("INVALID_DATASET", f"invalid source receipt for {run_id}", availability="corrupt") from exc


def with_input_snapshot(data, path):
    if not path.is_file():
        return data
    raw = path.read_bytes()
    sidecar = decode_json(raw)
    if not isinstance(sidecar, dict) or not isinstance(sidecar.get("files"), list):
        raise ResultError("SOURCE_CHANGED", "invalid or changing input snapshot", availability="corrupt")
    snapshot = data["snapshot"]
    if snapshot.get("files") and list(_deck_entries(snapshot)) != list(_deck_entries(sidecar)):
        raise ResultError("SOURCE_CHANGED", "job and input snapshot deck identities disagree", availability="corrupt")
    return {**data, "snapshot": {**snapshot, **sidecar, "input_snapshot_sha256": hashlib.sha256(raw).hexdigest()}}


def source_fingerprint(data):
    snapshot = data["snapshot"]
    content = {
        "identity": {key: data.get(key) for key in ("id", "node_id", "solver_id", "input_revision", "units", "unit_source")},
        "inputs": list(_deck_entries(snapshot)),
        "outputs": snapshot.get("outputs", {}),
        "artifacts": snapshot.get("artifacts", {}),
    }
    for key in ("mesh_revision", "expected_mesh", "input_snapshot_sha256"):
        if key in snapshot:
            content[key] = snapshot[key]
    return canonical_sha256(content, exclude_keys=())


def _original_sha(item):
    return item.get("original_sha256") or item.get("source_sha256") or item.get("sha256")


def _deck_entries(snapshot):
    for item in snapshot.get("files", ()):
        if not isinstance(item, dict):
            raise ValueError("invalid input snapshot file entry")
        execution = item.get("execution_path") or item.get("executed_path") or item.get("path")
        original = item.get("source_copy") or item.get("copied_path") or item.get("snapshot_path") or execution
        execution = execution or original
        digest = _original_sha(item)
        execution_digest = item.get("execution_sha256") or item.get("executed_sha256") or item.get("sha256")
        if execution == original:
            execution_digest = execution_digest or digest
        yield {"source_copy": original, "source_sha256": digest, "execution_path": execution,
               "execution_sha256": execution_digest}


def verify_original_decks(source, record, check=no_checkpoint, *, code="HASH_MISMATCH"):
    for item in _deck_entries(record["snapshot"]):
        original = (item["source_copy"], item["source_sha256"])
        executed = (item["execution_path"], item["execution_sha256"])
        for relative, digest in dict.fromkeys((original, executed)):
            if (relative, digest) == (None, None) and original != (None, None):
                continue
            if not relative or not digest:
                raise ResultError(code, "input deck copy has no recorded path or digest", availability="corrupt")
            path = artifact_path(source, relative)
            try:
                if sha256_file(path, check) == digest:
                    continue
            except FileNotFoundError as exc:
                raise ResultError(code, f"missing input deck copy {relative}", availability="source_unavailable") from exc
            raise ResultError(code, f"input deck copy changed: {relative}", availability="corrupt")


def snapshot_deck_paths(source, record):
    for item in _deck_entries(record["snapshot"]):
        relative = item["execution_path"] or item["source_copy"]
        if relative and Path(relative).suffix.lower() in {".rad", ".inc", ".dat"}:
            yield artifact_path(source, relative)


def _live_input_changed(data, check):
    files = [(item.get("source_path"), _original_sha(item)) for item in data["snapshot"].get("files", ())]
    if not any(path for path, _ in files):
        files.append((data.get("input_path") or data.get("source_path"), data.get("input_revision")))
    for path, digest in files:
        if not isinstance(path, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        target = Path(path)
        if not target.is_absolute():
            base = data["snapshot"].get("solver_cwd")
            input_path = data.get("input_path")
            if not base and isinstance(input_path, str) and Path(input_path).is_absolute():
                base = Path(input_path).parent
            if not base or not Path(base).is_absolute():
                continue
            target = Path(base) / target
        try:
            if sha256_file(target, check) != digest:
                return True
        except OSError:
            return True
    return False


def _completed_manifest(receipt):
    result = receipt.get("result")
    for record in (result, receipt):
        if not isinstance(record, dict):
            continue
        digest = record.get("dataset_manifest_sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        try:
            ref = ArtifactRefV1.from_dict(record.get("dataset_manifest_ref") or record.get("dataset_ref"))
        except (ValueError, TypeError):
            continue
        if ref.path == "dataset.json":
            return ref, digest
    raise ResultError("CORRUPT_OPERATION", "completed operation lacks a valid dataset manifest reference",
                      availability="corrupt")


def _read_receipt(folder, source_run_id, operation_id):
    try:
        data = read_object(artifact_path(folder, "operation.json"))
    except FileNotFoundError:
        if folder.is_dir():
            raise ResultError("CORRUPT_OPERATION", f"missing operation.json in {operation_id}", availability="corrupt")
        return None
    except ResultError:
        raise
    except (ValueError, OSError) as exc:
        raise ResultError("CORRUPT_OPERATION", "operation receipt", availability="corrupt") from exc
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ResultError("UNSUPPORTED_CAPABILITY", "unknown operation receipt schema")
    state = data.get("state")
    if (data.get("id") != operation_id or data.get("source_run_id") != source_run_id
            or not isinstance(state, str) or state not in ACTIVE_OPERATIONS | TERMINAL_OPERATIONS | _LEGACY_TERMINAL_OPERATIONS):
        raise ResultError("CORRUPT_OPERATION", "operation identity or state", availability="corrupt")
    if state == "completed":
        _completed_manifest(data)
    if state in {"failed", "cancelled"}:
        error = data.get("error")
        if not isinstance(error, dict) or any(not isinstance(error.get(key), str) or not error[key]
                                              for key in ("code", "message")):
            raise ResultError("CORRUPT_OPERATION", "terminal operation is missing its error", availability="corrupt")
    if state != "completed" and state not in ACTIVE_OPERATIONS and (folder / "dataset.json").exists():
        raise ResultError("CORRUPT_OPERATION", "dataset has no completed operation", availability="corrupt")
    return data


def _interrupt(folder, receipt):
    _quarantine_parts(folder)
    artifact_path(folder, "dataset.json").unlink(missing_ok=True)
    message = "operation owner stopped before publication"
    receipt.update(state="failed", stage="interrupted", parse_state="failed", finished_at=utc_now(),
                   error={"code": "INTERRUPTED", "message": message, "detail": message},
                   result=None, dataset_ref=None, dataset_manifest_sha256=None)
    receipt.setdefault("plan_sha256", receipt.get("plan_hash"))
    if not isinstance(receipt.get("progress"), dict):
        receipt["progress"] = {"completed": 0, "total": 1, "unit": "steps"}
    atomic_json(folder / "operation.json", receipt)
    return receipt


def _quarantine_parts(folder):
    for path in folder.rglob("*.part"):
        checked = artifact_path(folder, path.relative_to(folder).as_posix())
        target = artifact_path(folder, path.with_suffix(".interrupted").relative_to(folder).as_posix())
        checked.replace(target)


def _operation_receipts(store, *, strict=False, recover_orphans=False):
    base = artifact_path(store.root, ".derived")
    if not base.is_dir():
        return
    for source in base.iterdir():
        if not source.is_dir():
            continue
        try:
            artifact_path(base, source.name)
        except ResultError:
            if strict:
                raise
            continue
        for path in source.iterdir():
            if not path.is_dir():
                continue
            try:
                folder = operation_dir(store, source.name, path.name)
                receipt = _read_receipt(folder, source.name, path.name)
            except ResultError as exc:
                if strict:
                    raise
                if recover_orphans and exc.code == "CORRUPT_OPERATION" and _ID.fullmatch(source.name) and _ID.fullmatch(path.name):
                    _quarantine_parts(folder)
                    dataset = artifact_path(folder, "dataset.json")
                    if dataset.is_file():
                        dataset.replace(artifact_path(folder, "dataset.json.orphaned"))
                continue
            if receipt is not None:
                yield folder, receipt


def recover_derived_operations(store):
    """Recover orphaned operations while the caller holds the RunStore lease."""
    for folder, receipt in _operation_receipts(store, recover_orphans=True):
        if receipt["state"] in ACTIVE_OPERATIONS:
            _interrupt(folder, receipt)


def find_active_duplicate(store, plan):
    for folder, receipt in _operation_receipts(store):
        if (receipt["state"] in ACTIVE_OPERATIONS and receipt["source_run_id"] == plan["source_run_id"]
                and receipt.get("cache_key") == plan["cache_key"]
                and receipt.get("plan_sha256", receipt.get("plan_hash")) == plan["plan_hash"]):
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
    return _read_receipt(folder, source_run_id, operation_id)


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


def load_completed_operation(store, source_run_id, operation_id, *, check=no_checkpoint, live_stale=None):
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
        manifest, expected_digest = _completed_manifest(receipt)
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
        snapshot_copy = artifact_path(folder, "inputs/input-snapshot.json")
        if snapshot_copy.is_file() and not any(ref.path == "inputs/input-snapshot.json" for ref in staged):
            raise ResultError("HASH_MISMATCH", "unregistered input snapshot copy", availability="corrupt")
        fixed_record = with_input_snapshot(read_object(folder / pinned.path), snapshot_copy)
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
        result = receipt.get("result") or {}
        if isinstance(result, dict) and result.get("dataset_id", dataset.dataset_id) != dataset.dataset_id:
            raise ResultError("CORRUPT_OPERATION", "completed result does not identify its dataset", availability="corrupt")
        digest = canonical_sha256(dataset.to_dict(), exclude_keys=())
        if expected_digest != digest:
            raise ResultError("HASH_MISMATCH", "canonical dataset manifest", availability="corrupt")
        refs_by_id = {ref.id: ref for ref in outputs}
        nested = [ArtifactRefV1.from_dict(frame["artifact_ref"]) for frame in dataset.frames]
        for series in dataset.series:
            nested.extend((series.coordinate_ref, series.source_row_index_ref))
        if any(refs_by_id.get(ref.id) != ref for ref in nested):
            raise ResultError("HASH_MISMATCH", "dataset references an unregistered artifact", availability="corrupt")
        context = store.live_stale
        project_stale = context(live) if context is not None else False
        working_stale = _live_input_changed(live, check)
        caller_stale = live_stale(live) if callable(live_stale) else live_stale
        return folder, receipt, dataset, bool(live.get("stale") or project_stale or working_stale or caller_stale)
    except ResultError:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise ResultError("INVALID_DATASET", str(exc), availability="corrupt") from exc


def resolve_result_ref(store: RunStore, ref: ResultRefV1, *, check_cancelled=None, live_stale=None) -> dict:
    """Resolve a fixed ResultRef without substituting a newer run or dataset."""
    if not isinstance(ref, ResultRefV1):
        raise ResultError("INVALID_DATASET", "a ResultRefV1 is required")
    check = check_cancelled or no_checkpoint
    store = owning_store(store)
    folder, receipt, dataset, stale = load_completed_operation(
        store, ref.source_run_id, ref.operation_id, check=check, live_stale=live_stale)
    manifest_digest = canonical_sha256(dataset.to_dict(), exclude_keys=())
    if (receipt["source_node_id"] != ref.source_node_id or dataset.dataset_id != ref.dataset_id
            or receipt["source_receipt_sha256"] != ref.source_receipt_sha256
            or ArtifactRefV1.from_dict(receipt["source_receipt_ref"]) != ref.source_receipt_ref
            or tuple(ArtifactRefV1.from_dict(item) for item in receipt["source_artifacts"]) != ref.source_artifacts
            or canonical_sha256(receipt, exclude_keys=()) != ref.operation_manifest_sha256
            or ref.dataset_manifest_sha256 != manifest_digest):
        raise ResultError("HASH_MISMATCH", "result reference does not match the fixed operation bundle",
                          availability="corrupt")
    return {"operation_dir": folder, "operation": receipt, "dataset": dataset,
            "availability": "stale" if stale else "available"}


POST_PRODUCER = ProducerIdentity("caelab.result_conversion", "1")
