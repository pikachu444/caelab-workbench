"""Plans and validates registered converter profiles. Process ownership lives in jobs."""

from __future__ import annotations

import gzip
import hashlib
import json
import platform
import re
import shutil
from collections.abc import Mapping
from pathlib import Path

from ..artifact_refs import ArtifactOwner, ArtifactRefV1, DatasetManifestV1, canonical_json_bytes, canonical_sha256
from ..result_readers import ResultError, no_checkpoint
from ..result_readers import opencourant_history, vtk_fields
from .operations import POST_PRODUCER, artifact, artifact_path, read_object, sha256_file, source_fingerprint, source_record, verify_artifact
from .run_store import TERMINAL_STATES

GUNZIP_LIMIT = 5 * 1024**3
_COPY_CHUNK = 1024 * 1024
_PROFILES = {"OC-ANIM-1", "OC-TH4-1", "VTU-1"}
_ANIMATION = re.compile(r".*(A[0-9]{3,})(?:\.gz)?", re.I)
_HISTORY = re.compile(r".*T01", re.I)


def _registered(receipt):
    entries = dict(receipt["snapshot"].get("outputs", {}))
    for relative, digest in receipt["snapshot"].get("artifacts", {}).items():
        if relative in entries and entries[relative] != digest:
            raise ResultError("INVALID_DATASET", f"conflicting registered hashes for {relative}")
        entries[relative] = digest
    return entries


def _source_ref(root, relative, registered, role, format_id, *, check=no_checkpoint):
    if relative not in registered:
        raise ResultError("UNSUPPORTED_OBSERVABLE", f"unregistered source {relative}")
    try:
        ref = artifact(root, root / relative, role, format_id, POST_PRODUCER, check=check)
    except FileNotFoundError as exc:
        raise ResultError("UNSUPPORTED_OBSERVABLE", f"missing source {relative}") from exc
    if ref.sha256 != registered[relative]:
        raise ResultError("SOURCE_CHANGED", relative, availability="corrupt")
    return ref


def _match_source(requested, paths, label):
    matches = [relative for relative, names in paths.items() if requested in names]
    if len(matches) != 1:
        raise ResultError("UNSUPPORTED_OBSERVABLE", f"expected one registered {label} for {requested!r}")
    return matches[0]


def _animation_paths(registered):
    matches = {}
    for relative in registered:
        name = Path(relative).name
        match = _ANIMATION.fullmatch(name)
        if match:
            matches[relative] = {relative, name, match[1].upper()}
    return matches


def _vtu_paths(registered, receipt):
    paths = {name: {name, Path(name).name} for name in registered if Path(name).suffix.lower() == ".vtu"}
    for frame in receipt.get("result", {}).get("frames", ()):
        path = frame.get("vtu_file", frame.get("vtu"))
        if path in paths and type(frame.get("index")) is int:
            paths[path].add(str(frame["index"]))
    return paths


def _history_paths(registered):
    return [relative for relative in registered if _HISTORY.fullmatch(Path(relative).name)]


def _read_source_plan(store, run_id, profile, frame_ids, options, *, check=no_checkpoint):
    root, _, receipt = source_record(store, run_id)
    if receipt.get("state") not in TERMINAL_STATES or receipt.get("parse_state") == "reading":
        raise ResultError("UNSUPPORTED_CAPABILITY", "the source run is still active")
    if profile != "VTU-1" and receipt["solver_id"] not in {"opencourant", "openradioss", "radioss"}:
        raise ResultError("UNSUPPORTED_CAPABILITY", "OpenCourant conversion requires an OpenCourant-format source run")
    registered = _registered(receipt)
    if profile == "OC-TH4-1":
        want = options.get("history_artifact")
        available = _history_paths(registered)
        matches = available if want is None else [name for name in available if want == name]
        if len(matches) != 1 or matches[0] + "_TITLES" not in registered:
            raise ResultError("UNSUPPORTED_OBSERVABLE", "a unique registered T01 and its TITLES are required")
        relative = matches[0]
        artifacts = [
            _source_ref(root, relative, registered, "history_input", "T01", check=check),
            _source_ref(root, relative + "_TITLES", registered, "history_titles", "TITLES", check=check),
        ]
    else:
        paths = _animation_paths(registered) if profile == "OC-ANIM-1" else _vtu_paths(registered, receipt)
        relatives = [_match_source(frame, paths, "frame") for frame in frame_ids]
        if len(relatives) != len(set(relatives)):
            raise ResultError("INVALID_DATASET", "the same frame was selected more than once")
        artifacts = [_source_ref(root, path, registered, "frame_input", profile, check=check) for path in relatives]
    return root, receipt, artifacts


def _seal(plan):
    content = {key: value for key, value in plan.items() if key not in {"plan_hash", "cache_key", "executable"}}
    executable = plan["executable"]
    content["executable"] = None if executable is None else {k: v for k, v in executable.items() if k != "path_audit"}
    plan_hash = canonical_sha256(content, exclude_keys=())
    return plan_hash, canonical_sha256({"conversion_plan": plan_hash}, exclude_keys=())


def _plan(store, source_run_id, profile, executable, frame_ids, options, *, check=no_checkpoint):
    if not isinstance(frame_ids, (list, tuple)) or any(not isinstance(frame, str) or not frame for frame in frame_ids):
        raise ResultError("INVALID_DATASET", "frame_ids must be a sequence of nonempty strings")
    if profile != "OC-TH4-1" and not frame_ids:
        raise ResultError("UNSUPPORTED_OBSERVABLE", "select at least one exact frame")
    if options is not None and not isinstance(options, Mapping):
        raise ResultError("INVALID_DATASET", "converter options must be an object")
    options = json.loads(canonical_json_bytes(options or {}))
    for name, default in (("gunzip_limit_bytes", GUNZIP_LIMIT), ("output_limit_bytes", GUNZIP_LIMIT)):
        value = options.setdefault(name, default)
        if type(value) is not int or value <= 0:
            raise ResultError("INVALID_DATASET", f"{name} must be a positive integer")
    if "exact_required_set" in options:
        names = options["exact_required_set"]
        if (not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names)
                or len(set(names)) != len(names)):
            raise ResultError("INVALID_DATASET", "exact_required_set must contain distinct array names")
        options["exact_required_set"] = sorted(names)
    _, receipt, refs = _read_source_plan(store, source_run_id, profile, frame_ids, options, check=check)
    program = None
    if profile != "VTU-1":
        if not executable:
            raise ResultError("PROGRAM_UNAVAILABLE", profile)
        try:
            path = Path(executable).resolve(strict=True)
            if not path.is_file():
                raise OSError("expected a converter executable file")
            program = {"path_audit": str(path), "sha256": sha256_file(path, check),
                       "version": options.get("tool_version", "unknown"), "host_os": platform.system()}
        except OSError as exc:
            raise ResultError("PROGRAM_UNAVAILABLE", str(executable)) from exc
    plan = {
        "schema_version": 1, "profile": profile, "reader_version": "1", "source_run_id": source_run_id,
        "source_node_id": receipt["node_id"], "source_input_revision": receipt.get("input_revision"),
        "source_fingerprint": source_fingerprint(receipt), "source_artifacts": [ref.to_dict() for ref in refs],
        "frame_ids": list(frame_ids), "options": options, "units": receipt.get("units") or {},
        "executable": program, "estimated_input_bytes": sum(ref.size for ref in refs),
    }
    reader_module = opencourant_history if profile == "OC-TH4-1" else vtk_fields
    reader_file = Path(reader_module.__file__)
    plan["reader_sha256"] = sha256_file(reader_file, check) if reader_file.is_file() else None
    plan["plan_hash"], plan["cache_key"] = _seal(plan)
    return plan


def plan_oc_animation(store, source_run_id, converter_exe, frame_ids, options=None):
    return _plan(store, source_run_id, "OC-ANIM-1", converter_exe, frame_ids, options)


def plan_oc_history(store, source_run_id, converter_exe, options=None):
    return _plan(store, source_run_id, "OC-TH4-1", converter_exe, (), options)


def plan_vtu(store, source_run_id, frame_ids, options=None):
    return _plan(store, source_run_id, "VTU-1", None, frame_ids, options)


def verify_plan(store, plan, check):
    try:
        profile = plan["profile"]
        if profile not in _PROFILES or type(plan["schema_version"]) is not int or plan["schema_version"] != 1:
            raise ValueError("unknown conversion plan profile or schema")
        program = plan["executable"]
        refreshed = _plan(store, plan["source_run_id"], profile, program["path_audit"] if program else None,
                          plan["frame_ids"], plan["options"], check=check)
        if refreshed != plan:
            raise ValueError("plan no longer matches the registered source and executable")
    except (ValueError, TypeError, KeyError) as exc:
        raise ResultError("STALE_PLAN", str(exc)) from exc


def _stage_file(source, target, check, *, gzip_limit=None):
    digest = hashlib.sha256()
    total = 0
    temporary = target.with_name(target.name + ".part")
    reader = gzip.open if gzip_limit is not None else Path.open
    try:
        with reader(source, "rb") as stream, temporary.open("xb") as output:
            for chunk in iter(lambda: stream.read(_COPY_CHUNK), b""):
                check()
                total += len(chunk)
                if gzip_limit is not None and total > gzip_limit:
                    raise ResultError("INVALID_DATASET", f"gunzip size limit exceeded for {source.name}")
                digest.update(chunk)
                output.write(chunk)
        check()
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return digest.hexdigest()


def prepare_conversion(store, folder, plan, check):
    verify_plan(store, plan, check)
    source, receipt_path, _ = source_record(store, plan["source_run_id"])
    for name in ("inputs", "outputs"):
        artifact_path(folder, name).mkdir()
    frozen = folder / "inputs/source-receipt.json"
    _stage_file(receipt_path, frozen, check)
    if source_fingerprint(read_object(frozen)) != plan["source_fingerprint"]:
        raise ResultError("STALE_PLAN", "source receipt changed during preparation")
    receipt_ref = artifact(folder, frozen, "source_receipt", "json", POST_PRODUCER, check=check)
    staged = []
    jobs = []
    program = plan["executable"]
    profile = plan["profile"]
    for index, item in enumerate(plan["source_artifacts"], 1):
        ref = ArtifactRefV1.from_dict(item)
        src = artifact_path(source, ref.path)
        if profile == "OC-ANIM-1":
            input_name = f"inA{index:03d}"
        elif profile == "OC-TH4-1":
            input_name = "inT01" if index == 1 else "inT01_TITLES"
        else:
            input_name = f"in{index:03d}.vtu"
        compressed = profile == "OC-ANIM-1" and src.name.lower().endswith(".gz")
        copy_path = folder / "inputs" / (input_name + ".gz" if compressed else input_name)
        if _stage_file(src, copy_path, check) != ref.sha256:
            raise ResultError("SOURCE_CHANGED", ref.path)
        staged.append(artifact(folder, copy_path, "staged_input", ref.format_id, POST_PRODUCER, check=check))
        if compressed:
            unpacked = folder / "inputs" / input_name
            try:
                _stage_file(copy_path, unpacked, check, gzip_limit=plan["options"]["gunzip_limit_bytes"])
            except (OSError, EOFError) as exc:
                raise ResultError("INVALID_DATASET", f"invalid gzip source {ref.path}") from exc
            staged.append(artifact(folder, unpacked, "expanded_input", "A###", POST_PRODUCER, check=check))
        if profile == "OC-ANIM-1":
            jobs.append({"argv": [program["path_audit"], input_name], "cwd_relative": "inputs",
                         "stdout_target": f"outputs/frame-{index:03d}.vtk.part", "stderr_target": "stderr.log"})
    if profile == "OC-TH4-1":
        jobs.append({"argv": [program["path_audit"], "inT01", "history"], "cwd_relative": "inputs",
                     "stdout_target": "operation.log", "stderr_target": "stderr.log"})
    return receipt_ref, staged, jobs


def verify_source(store, plan, check):
    directory, _, current = source_record(store, plan["source_run_id"])
    if source_fingerprint(current) != plan["source_fingerprint"]:
        raise ResultError("SOURCE_CHANGED", "source receipt changed during conversion")
    for item in plan["source_artifacts"]:
        verify_artifact(directory, ArtifactRefV1.from_dict(item), check=check, code="SOURCE_CHANGED")


def _nonempty(path, limit):
    if not path.is_file() or path.stat().st_size == 0:
        raise ResultError("EMPTY_OUTPUT", path.name)
    if path.stat().st_size > limit:
        raise ResultError("INVALID_DATASET", f"{path.name} exceeds the output size limit")


def validate_oc_outputs(operation_dir: Path, plan: Mapping, *, check_cancelled=None) -> dict:
    """Validate exact producer outputs and assemble the dataset and artifact records."""
    check = check_cancelled or no_checkpoint
    folder = Path(operation_dir)
    limit = plan["options"]["output_limit_bytes"]
    profile = plan["profile"]
    outputs = []
    if profile == "OC-TH4-1":
        pending = artifact_path(folder, "inputs/history.csv")
        _nonempty(pending, limit)
        csv_path = artifact_path(folder, "outputs/history.csv")
        pending.replace(csv_path)
        dataset = opencourant_history.extract_history(csv_path, folder / "inputs/inT01_TITLES",
            {**plan["options"], "units": plan["units"], "operation_dir": folder, "check_cancelled": check,
             "dataset_id": plan["cache_key"]})
        metadata = folder / "outputs/history-columns.json"
        series = dataset.series[0]
        outputs.extend([series.coordinate_ref, series.source_row_index_ref])
        outputs.extend(ArtifactRefV1.from_dict(value["values_ref"])
                       for value in read_object(metadata)["fields"].values())
        outputs.extend([artifact(folder, metadata, "history_columns", "json", POST_PRODUCER, check=check),
                        artifact(folder, csv_path, "derived_output", "csv", POST_PRODUCER, check=check)])
    else:
        frames = []
        for index, frame_id in enumerate(plan["frame_ids"], 1):
            check()
            if profile == "OC-ANIM-1":
                pending = artifact_path(folder, f"outputs/frame-{index:03d}.vtk.part")
                _nonempty(pending, limit)
                grid = vtk_fields.read_vtk(pending, exact_required_set=plan["options"].get("exact_required_set"),
                                           expected=plan["options"].get("expected_mesh"), check_cancelled=check)
                target = pending.with_suffix("")
                pending.replace(target)
            elif profile == "VTU-1":
                target = artifact_path(folder, f"inputs/in{index:03d}.vtu")
                _nonempty(target, limit)
                grid = vtk_fields.read_vtu(target, check_cancelled=check)
            else:
                raise ResultError("UNSUPPORTED_CAPABILITY", profile)
            ref = artifact(folder, target, "frame_output", "vtk-legacy" if profile == "OC-ANIM-1" else "vtu",
                           POST_PRODUCER, check=check)
            outputs.append(ref)
            frames.append({"frame_id": frame_id, "time": grid.time, "artifact_ref": ref.to_dict(),
                           "fields": sorted(name for name in grid.point_data
                                            if not name.startswith("Coverage_") and name not in {"NODE_ID", "NativeNodeId"}),
                           "element_fields": sorted(vtk_fields.tensor_fields(grid)) if profile == "OC-ANIM-1" else [],
                           "geometry_state": "deformed" if profile == "OC-ANIM-1" else "reference"})
        dataset = DatasetManifestV1(plan["cache_key"], "field", frames=tuple(frames))
    return {"dataset": dataset.to_dict(), "outputs": outputs}


def receipt_owners(source_id, operation_id, source_artifacts, operation_artifacts):
    run = ArtifactOwner("run", source_id).to_dict()
    operation = ArtifactOwner("operation", operation_id, source_id).to_dict()
    return {**{ref["id"]: run for ref in source_artifacts},
            **{ref.id: operation for ref in operation_artifacts}}
