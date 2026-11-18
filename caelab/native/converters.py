"""Plans and validates registered converter profiles. Process ownership lives in jobs."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import platform
import re
import shutil
from collections.abc import Mapping
from pathlib import Path

from ..artifact_refs import ArtifactOwner, ArtifactRefV1, DatasetManifestV1, canonical_json_bytes, canonical_sha256
from ..result_readers import ResultError, no_checkpoint
from ..result_readers import opencourant_history, vtk_fields
from .operations import (INPUT_SNAPSHOT, POST_PRODUCER, artifact, artifact_path, read_object, sha256_file,
                         snapshot_deck_paths, source_fingerprint, source_record, verify_artifact,
                         verify_original_decks, with_input_snapshot)
from .run_store import TERMINAL_STATES

GUNZIP_LIMIT = 5 * 1024**3
_COPY_CHUNK = 1024 * 1024
_PROFILES = {"OC-ANIM-1", "OC-TH4-1", "VTU-1"}
_ANIMATION = re.compile(r".*(A[0-9]{3,})(?:\.gz)?", re.I)
_HISTORY = re.compile(r".*T01", re.I)
_LOG_LIMIT = 64 * 1024**2


def reader_code_sha256(profile, check=no_checkpoint):
    module = opencourant_history if profile == "OC-TH4-1" else vtk_fields
    source = Path(module.__file__)
    if source.is_file():
        return sha256_file(source, check)
    check()
    # Frozen installations may only expose the code object through their loader.
    import marshal
    code = module.__loader__.get_code(module.__name__)
    return hashlib.sha256(marshal.dumps(code)).hexdigest()


def _install_root(options, executable, profile):
    candidate = options.get("install_root") or os.environ.get("CAELAB_OPENCOURANT")
    root = Path(candidate).resolve() if candidate else None
    if root and root.is_file() and not options.get("install_root") and root.name.startswith("starter_"):
        root = root.parent.parent
    if root is not None and root.is_dir() and executable.is_relative_to(root) and executable != root:
        name = "anim_to_vtk" if profile == "OC-ANIM-1" else "th_to_csv"
        filenames = {name, name + ".exe", name + "_win64.exe", name + "_linux64", name + "_linux64_gf"}
        directories = {"exec", "bin", f"output_converters/{name}", f"output_converters/{name}/bin"}
        if executable.name.lower() in filenames and executable.parent.relative_to(root).as_posix().lower() in directories:
            return root
    if options.get("allow_custom_executable") is True:
        return None
    raise ResultError("UNTRUSTED_CONVERTER_EXECUTABLE", str(executable))


def converter_environment(plan, environment):
    program = plan["executable"]
    root = program and program.get("install_root")
    if os.name != "nt" or not root:
        return environment
    home = Path(root)
    directories = ("exec", "extlib/hm_reader/win64", "extlib/intelOneAPI_runtime/win64",
                   "hm_reader/win64", "intelOneAPI_runtime/win64")
    external_path = next((v for k, v in environment.items() if k.upper() == "PATH"), "")
    env = {k: v for k, v in environment.items() if k.upper() not in {"PATH", "RAD_CFG_PATH", "KMP_STACKSIZE"}}
    env.update(PATH=os.pathsep.join([*(str(home / directory) for directory in directories), external_path]).rstrip(os.pathsep),
               RAD_CFG_PATH=str(home / "hm_cfg_files"), KMP_STACKSIZE="400m")
    return env


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


def _rad_cards(paths, check):
    for path in paths:
        card = None
        rows = []
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                check()
                text = line.lstrip()
                if text.startswith("//") and text.upper().startswith("//SUBMODEL"):
                    raise ResultError("SOURCE_MISMATCH", "transformed RAD submodels are unsupported")
                if text.startswith("/") and not text.startswith("//"):
                    if card:
                        yield card, rows
                    card, rows = text.strip().upper().strip("/").split("/"), []
                elif card and not text.startswith(("#", "$", "//")):
                    rows.append(line.rstrip("\r\n"))
        if card:
            yield card, rows


def _rad_fields(row, widths, convert):
    offset = 0
    cells = []
    for width in widths:
        cells.append(row[offset:offset + width])
        offset += width
    try:
        return list(map(convert, cells))
    except ValueError:
        return list(map(convert, row.split()[:len(widths)]))


def _rad_snapshot_metadata(root, receipt, check, *, animation):
    paths = tuple(dict.fromkeys(snapshot_deck_paths(root, receipt)))
    if not paths:
        return None, [], {}
    verify_original_decks(root, receipt, check, code="SOURCE_CHANGED")
    nodes, elements, parts, shell_properties, solid_properties = {}, {}, {}, {}, {}
    history = []
    topology = {"SHELL": (9, 4), "SH3N": (5, 3), "BRICK": (12, 8), "TETRA4": (10, 4), "TETRA": (10, 4)}

    def add(table, key, value):
        if key in table or int(key) <= 0:
            raise ValueError(f"duplicate or invalid RAD identifier {key}")
        table[key] = value

    try:
        for card, rows in _rad_cards(paths, check):
            head = card[0]
            if head == "NODE" and animation:
                if len(card) > 1 and int(card[1]) != 0:
                    raise ValueError("local RAD node units are unsupported")
                for row in rows:
                    if not row.strip():
                        continue
                    node, x, y, z = _rad_fields(row, (10, 20, 20, 20), lambda v: float(v.upper().replace("D", "E")))
                    if not node.is_integer() or not all(map(math.isfinite, (x, y, z))):
                        raise ValueError("invalid RAD node")
                    add(nodes, str(int(node)), [x, y, z])
            elif head in topology:
                cell_type, arity = topology[head]
                part = int(card[1])
                for row in rows:
                    if not row.strip():
                        continue
                    eid, *conn = _rad_fields(row, (10,) * (arity + 1), int)
                    if len(conn) != arity:
                        raise ValueError(f"invalid {head} connectivity")
                    if head == "SHELL" and conn[2] == conn[3]:
                        conn = conn[:3]
                        kind = 5
                    else:
                        kind = cell_type
                    if len(set(conn)) != len(conn):
                        raise ValueError(f"degenerate {head} connectivity is unsupported")
                    add(elements, str(eid), {"part_id": part, "cell_type": kind, "node_ids": conn})
            elif head == "PART":
                add(parts, int(card[1]), int(rows[1][:10].strip() or rows[1].split()[0]))
            elif head == "PROP" and len(card) >= 3:
                if card[1] in {"SHELL", "TYPE1"} and animation:
                    add(shell_properties, int(card[2]), int(rows[3][:10].strip() or "0"))
                elif card[1] in {"SOLID", "TYPE14"}:
                    add(solid_properties, int(card[2]), int(rows[1][:10].strip() or "0"))
            elif not animation and head == "TH" and len(card) >= 3 and card[1] in {"BRIC", "NODE"}:
                title = " ".join(rows[0].split())
                codes = []
                for row in rows[1:]:
                    if not row.strip():
                        continue
                    if not row[:10].strip().isdigit():
                        codes.extend(row.split())
                        continue
                    entity = int(row[:10])
                    name = " ".join(row[20:].split())
                    if not title or not name or not codes or (card[1] == "NODE" and row[10:20].strip() not in {"", "0"}):
                        raise ValueError(f"unverifiable /TH/{card[1]} entity {entity}")
                    history.extend({"group_title": title, "entity_id": entity, "entity_title": name,
                                    "output_type": "Solid" if card[1] == "BRIC" else "Node", "variable_code": code}
                                   for code in codes)
        shells = {eid: element for eid, element in elements.items() if element["cell_type"] in {5, 9}}
        solids = {eid: element for eid, element in elements.items() if element["cell_type"] in {10, 12}}
        formulations = {eid: "hex8_1pt" for eid, element in solids.items() if element["cell_type"] == 12
                        and solid_properties.get(parts.get(element["part_id"])) == 1}
        mesh = None
        if animation:
            node_ids = set(map(int, nodes))
            if not nodes or not elements or any(set(element["node_ids"]) - node_ids for element in elements.values()):
                raise ValueError("saved RAD deck is missing supported node or element definitions")
            mesh = {"node_coordinates": nodes, "elements": elements, "shell_ids": sorted(map(int, shells)),
                    "solid_ids": sorted(map(int, solids)),
                    "shell_nip": {eid: shell_properties[parts[element["part_id"]]] for eid, element in shells.items()}}
        return mesh, history, formulations
    except (OSError, ValueError, IndexError, KeyError) as exc:
        if isinstance(exc, ResultError):
            raise
        raise ResultError("SOURCE_MISMATCH", f"cannot establish saved RAD definitions: {exc}") from exc


def _confirm_mesh(expected, authoritative):
    if expected is not None:
        if not isinstance(expected, Mapping):
            raise ResultError("SOURCE_MISMATCH", "invalid caller source mesh")
        for key, value in expected.items():
            native_key = "node_coordinates" if key == "node_coords" else key
            native_value = authoritative.get(native_key)
            if native_key in {"shell_ids", "solid_ids"} and isinstance(value, (list, tuple)) and native_value is not None:
                value = sorted(value)
            if native_key == "shell_nip" and type(value) is int and isinstance(native_value, dict):
                value = dict.fromkeys(native_value, value)
            if native_key in authoritative and value != native_value:
                raise ResultError("SOURCE_MISMATCH", f"caller {key} differs from the saved RAD snapshot")
    return authoritative


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
    content["executable"] = None if executable is None else {
        k: v for k, v in executable.items() if k not in {"path_audit", "install_root"}}
    content["options"] = {k: v for k, v in plan["options"].items() if k != "install_root"}
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
    for name, default in (("gunzip_limit_bytes", GUNZIP_LIMIT), ("output_limit_bytes", GUNZIP_LIMIT),
                          ("max_log_bytes", _LOG_LIMIT)):
        value = options.setdefault(name, default)
        if type(value) is not int or value <= 0:
            raise ResultError("INVALID_DATASET", f"{name} must be a positive integer")
    if "exact_required_set" in options:
        names = options["exact_required_set"]
        if (not isinstance(names, list) or any(not isinstance(name, str) or not name for name in names)
                or len(set(names)) != len(names)):
            raise ResultError("INVALID_DATASET", "exact_required_set must contain distinct array names")
        options["exact_required_set"] = sorted(names)
    source, receipt, refs = _read_source_plan(store, source_run_id, profile, frame_ids, options, check=check)
    program = None
    if profile != "VTU-1":
        if not executable:
            raise ResultError("PROGRAM_UNAVAILABLE", profile)
        try:
            path = Path(executable).resolve(strict=True)
            if not path.is_file():
                raise OSError("expected a converter executable file")
            root = _install_root(options, path, profile)
            if root is not None:
                options["install_root"] = str(root)
            program = {"path_audit": str(path), "sha256": sha256_file(path, check),
                       "version": options.get("tool_version", "unknown"), "host_os": platform.system(),
                       "install_root": str(root) if root is not None else None}
        except OSError as exc:
            raise ResultError("PROGRAM_UNAVAILABLE", str(executable)) from exc
    if profile == "OC-ANIM-1":
        recovered, _, _ = _rad_snapshot_metadata(source, receipt, check, animation=True)
        recorded_mesh = receipt["snapshot"].get("expected_mesh")
        if recovered is not None:
            _confirm_mesh(recorded_mesh, recovered)
        authoritative = recovered or recorded_mesh
        mesh = (_confirm_mesh(options.get("expected_mesh"), authoritative) if authoritative is not None
                else options.get("expected_mesh"))
        if isinstance(mesh, Mapping) and "node_coords" in mesh and "node_coordinates" not in mesh:
            mesh = {**mesh, "node_coordinates": mesh["node_coords"]}
        if not isinstance(mesh, Mapping) or not mesh.get("node_coordinates") or not mesh.get("elements"):
            raise ResultError("SOURCE_MISMATCH", "OC-ANIM-1 requires verified source node and element maps")
        options["expected_mesh"] = mesh
        erosion = options.get("erosion_active_value", options.pop("active_erosion_status", None))
        if erosion is not None and (type(erosion) is not int or erosion not in {0, 1}):
            raise ResultError("INVALID_DATASET", "erosion_active_value must be 0 or 1")
        options["erosion_active_value"] = erosion
    if profile == "OC-TH4-1":
        _, bindings, formulations = _rad_snapshot_metadata(source, receipt, check, animation=False)
        if bindings:
            options["source_bindings"] = bindings
            options["solid_formulation"] = formulations
    plan = {
        "schema_version": 1, "profile": profile, "reader_version": "1", "source_run_id": source_run_id,
        "source_node_id": receipt["node_id"], "source_input_revision": receipt.get("input_revision"),
        "source_mesh_revision": receipt["snapshot"].get("mesh_revision"),
        "source_fingerprint": source_fingerprint(receipt), "source_artifacts": [ref.to_dict() for ref in refs],
        "frame_ids": list(frame_ids), "options": options, "units": receipt.get("units") or {},
        "executable": program, "estimated_input_bytes": sum(ref.size for ref in refs),
    }
    plan["reader_sha256"] = reader_code_sha256(profile, check)
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
    except ResultError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as exc:
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
    source, receipt_path, receipt = source_record(store, plan["source_run_id"])
    verify_original_decks(source, receipt, check, code="SOURCE_CHANGED")
    for name in ("inputs", "outputs"):
        artifact_path(folder, name).mkdir()
    frozen = folder / "inputs/source-receipt.json"
    _stage_file(receipt_path, frozen, check)
    staged = []
    snapshot = artifact_path(source, INPUT_SNAPSHOT)
    copy = folder / "inputs/input-snapshot.json"
    if snapshot.is_file():
        _stage_file(snapshot, copy, check)
        staged.append(artifact(folder, copy, "source_input_snapshot", "json", POST_PRODUCER, check=check))
    if source_fingerprint(with_input_snapshot(read_object(frozen), copy)) != plan["source_fingerprint"]:
        raise ResultError("STALE_PLAN", "source receipt changed during preparation")
    receipt_ref = artifact(folder, frozen, "source_receipt", "json", POST_PRODUCER, check=check)
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
    verify_original_decks(directory, current, check, code="SOURCE_CHANGED")
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
                                           expected=plan["options"]["expected_mesh"],
                                           erosion_active_value=plan["options"]["erosion_active_value"],
                                           check_cancelled=check)
                target = pending.with_suffix("")
                pending.replace(target)
            elif profile == "VTU-1":
                target = artifact_path(folder, f"inputs/in{index:03d}.vtu")
                _nonempty(target, limit)
                grid = vtk_fields.read_vtu(target, exact_required_set=plan["options"].get("exact_required_set"),
                                           node_id_policy=plan["options"].get("node_id_policy"), check_cancelled=check)
                if not vtk_fields.nodal_fields(grid):
                    raise ResultError("SCHEMA_MISMATCH", "VTU has no queryable nodal physics fields")
            else:
                raise ResultError("UNSUPPORTED_CAPABILITY", profile)
            ref = artifact(folder, target, "frame_output", "vtk-legacy" if profile == "OC-ANIM-1" else "vtu",
                           POST_PRODUCER, check=check)
            outputs.append(ref)
            element_fields = vtk_fields.tensor_fields(grid) if profile == "OC-ANIM-1" else set()
            if profile == "OC-ANIM-1" and (erosion := vtk_fields.erosion_field(grid)) is not None:
                element_fields.add(erosion)
            frames.append({"frame_id": frame_id, "time": grid.time, "artifact_ref": ref.to_dict(),
                           **vtk_fields.sample_metadata(grid), "fields": sorted(vtk_fields.nodal_fields(grid)),
                           "element_fields": sorted(element_fields),
                           "geometry_state": "deformed" if profile == "OC-ANIM-1" else "reference"})
        dataset = DatasetManifestV1(plan["cache_key"], "field", frames=tuple(frames))
    manifest = dataset.to_dict()
    return {"dataset": manifest, "dataset_manifest_sha256": canonical_sha256(manifest, exclude_keys=()),
            "outputs": outputs}


def receipt_owners(source_id, operation_id, source_artifacts, operation_artifacts):
    run = ArtifactOwner("run", source_id).to_dict()
    operation = ArtifactOwner("operation", operation_id, source_id).to_dict()
    return {**{ref["id"]: run for ref in source_artifacts},
            **{ref.id: operation for ref in operation_artifacts}}
