"""Collect OpenRadioss listings and publish frame VTUs from decoded animations."""
from __future__ import annotations

from collections import Counter
from itertools import accumulate, count
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from ..result_readers import ResultError, vtk_fields
from .results import _array, _write_xml


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
_ENERGY_LABELS = {
    "kinetic_energy": r"KINETIC(?:\s+ENERGY)?",
    "internal_energy": r"INTERNAL(?:\s+ENERGY)?",
    "hourglass_energy": r"HOURGLASS(?:\s+ENERGY)?",
    "total_energy": r"TOTAL(?:\s+ENERGY)?",
    "energy_error": r"(?:TOTAL\s+)?ENERGY\s+ERROR",
}
_FAILURES = (
    (re.compile(r"\bNEGATIVE\s+VOLUME\b", re.I), "negative volume"),
    (re.compile(r"\bRUNAWAY\s+(?:ENERGY|ERROR)\b", re.I), "runaway energy/error"),
    (re.compile(r"\b(?:ERROR|ABNORMAL)\s+TERMINATION\b", re.I), "error termination"),
    (re.compile(r"\bFATAL\s+(?:ERROR|MESSAGE)\b", re.I), "fatal error"),
)


def parse_radioss_starter(path: Path) -> dict:
    """Return the keyword blocks present in a Radioss Starter deck."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    blocks: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("/") or stripped.startswith("//"):
            continue
        keyword = stripped.split()[0].rstrip("/").upper()
        if keyword and keyword not in blocks:
            blocks.append(keyword)
    return {"path": str(Path(path)), "blocks": blocks, "text": text}


def _parse_cycle_table_header(header_line: str) -> list[str]:
    raw_tokens = re.findall(r"\S+", header_line.upper())
    cols: list[str] = []
    i = 0
    while i < len(raw_tokens):
        tok = raw_tokens[i]
        if tok == "K-ENERGY" and i + 1 < len(raw_tokens) and raw_tokens[i + 1] in ("T", "R"):
            cols.append(f"K-ENERGY_{raw_tokens[i + 1]}")
            i += 2
        elif tok == "TOTAL" and i + 1 < len(raw_tokens) and raw_tokens[i + 1] == "MASS":
            cols.append("TOTAL_MASS")
            i += 2
        elif tok == "MASS" and i + 1 < len(raw_tokens) and raw_tokens[i + 1] == "ADDED":
            cols.append("MASS_ADDED")
            i += 2
        else:
            cols.append(tok)
            i += 1
    return cols


def parse_radioss_output(path: Path) -> dict[str, float]:
    """Extract the last explicitly labelled global-energy values or cycle-table KPIs from an Engine listing."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    values: dict[str, float] = {}
    has_explicit_energy = False
    for key, label in _ENERGY_LABELS.items():
        pattern = re.compile(rf"{label}(?:\s*\([^)]*\))?\s*(?:=|:)?\s*({_NUMBER})\s*%?", re.I)
        matches = pattern.findall(text)
        if matches:
            value = float(matches[-1].replace("D", "E").replace("d", "e"))
            if math.isfinite(value):
                values[key] = value
                has_explicit_energy = True

    # Parse OpenRadioss standard Engine cycle table lines:
    # CYCLE TIME TIME-STEP ELEMENT ERROR I-ENERGY K-ENERGY T K-ENERGY R EXT-WORK [MAS.ERR]
    # Enforce preceding header validation and defense against corrupted/incomplete trailing rows.
    in_cycle_table = False
    cycle_header_cols: list[str] = []
    last_valid_cycle: dict[str, float] | None = None
    cycle_table_corrupted = False

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Header detection
        if (
            re.search(r"\bCYCLE\b", stripped, re.I)
            and re.search(r"\bTIME\b", stripped, re.I)
            and re.search(r"\b(?:I-ENERGY|ERROR|K-ENERGY)\b", stripped, re.I)
        ):
            in_cycle_table = True
            cycle_table_corrupted = False
            cycle_header_cols = _parse_cycle_table_header(stripped)
            continue

        # Section end detection
        if (
            re.search(r"\b(?:NORMAL|ERROR)\s+TERMINATION\b", stripped, re.I)
            or re.search(r"\bELAPSED\s+TIME\b", stripped, re.I)
            or stripped.startswith("---")
            or stripped.startswith("===")
            or re.search(r"\bTOTAL NUMBER OF CYCLES\b", stripped, re.I)
        ):
            in_cycle_table = False
            continue

        if in_cycle_table:
            row_tokens = re.findall(r"\S+", stripped)
            if not row_tokens:
                continue

            # Check if line begins with a cycle number (digits)
            if re.match(r"^\d+$", row_tokens[0]):
                # Detect corrupted values: NaN, Inf, ***
                if any(re.search(r"(?:NaN|Inf|\*{3,})", tok, re.I) for tok in row_tokens):
                    last_valid_cycle = None
                    cycle_table_corrupted = True
                    continue

                # Truncated row check: if row has fewer tokens than expected columns
                if len(row_tokens) < len(cycle_header_cols):
                    last_valid_cycle = None
                    cycle_table_corrupted = True
                    continue

                try:
                    elem_idx = cycle_header_cols.index("ELEMENT") if "ELEMENT" in cycle_header_cols else None
                    col_values: dict[str, str] = {}
                    if elem_idx is not None:
                        num_after = len(cycle_header_cols) - 1 - elem_idx
                        if len(row_tokens) < elem_idx + num_after + 1:
                            last_valid_cycle = None
                            cycle_table_corrupted = True
                            continue
                        for c, tok in zip(cycle_header_cols[:elem_idx], row_tokens[:elem_idx]):
                            col_values[c] = tok
                        col_values["ELEMENT"] = " ".join(row_tokens[elem_idx : len(row_tokens) - num_after])
                        for c, tok in zip(cycle_header_cols[elem_idx + 1 :], row_tokens[len(row_tokens) - num_after :]):
                            col_values[c] = tok
                    else:
                        for c, tok in zip(cycle_header_cols, row_tokens):
                            col_values[c] = tok

                    c_val = float(col_values["CYCLE"])
                    t_val = float(col_values["TIME"].replace("D", "E").replace("d", "e"))

                    entry: dict[str, float] = {
                        "printed_cycle": c_val,
                        "printed_time": t_val,
                    }

                    if "ERROR" in col_values:
                        err_str = col_values["ERROR"].rstrip("%").replace("D", "E").replace("d", "e")
                        entry["energy_error"] = float(err_str)

                    if "I-ENERGY" in col_values:
                        entry["internal_energy"] = float(col_values["I-ENERGY"].replace("D", "E").replace("d", "e"))

                    kt = None
                    kr = None
                    if "K-ENERGY_T" in col_values:
                        kt = float(col_values["K-ENERGY_T"].replace("D", "E").replace("d", "e"))
                        entry["kinetic_energy_t"] = kt
                    if "K-ENERGY_R" in col_values:
                        kr = float(col_values["K-ENERGY_R"].replace("D", "E").replace("d", "e"))
                        entry["kinetic_energy_r"] = kr

                    if kt is not None and kr is not None:
                        entry["kinetic_energy"] = kt + kr
                    elif kt is not None:
                        entry["kinetic_energy"] = kt
                    elif "K-ENERGY" in col_values:
                        entry["kinetic_energy"] = float(col_values["K-ENERGY"].replace("D", "E").replace("d", "e"))

                    if "EXT-WORK" in col_values:
                        entry["external_work"] = float(col_values["EXT-WORK"].replace("D", "E").replace("d", "e"))

                    if "MAS.ERR" in col_values:
                        entry["mass_error"] = float(col_values["MAS.ERR"].replace("D", "E").replace("d", "e"))

                    if all(math.isfinite(v) for v in entry.values()):
                        last_valid_cycle = entry
                        cycle_table_corrupted = False
                    else:
                        last_valid_cycle = None
                        cycle_table_corrupted = True
                except (ValueError, TypeError, KeyError):
                    last_valid_cycle = None
                    cycle_table_corrupted = True

    if last_valid_cycle and not cycle_table_corrupted:
        values["printed_cycle"] = last_valid_cycle["printed_cycle"]
        values["printed_time"] = last_valid_cycle["printed_time"]

        # Only populate energy metrics from cycle table if no explicit energy scalar labels were present
        if not has_explicit_energy:
            for k in ("internal_energy", "kinetic_energy", "kinetic_energy_t", "kinetic_energy_r", "energy_error"):
                if k in last_valid_cycle:
                    values[k] = last_valid_cycle[k]

        for diag_key in ("external_work", "mass_error"):
            if diag_key in last_valid_cycle and diag_key not in values:
                values[diag_key] = last_valid_cycle[diag_key]

        if "cycle" not in values:
            values["cycle"] = last_valid_cycle["printed_cycle"]
        if "time" not in values:
            values["time"] = last_valid_cycle["printed_time"]

    total_cycles_match = re.search(r"TOTAL NUMBER OF CYCLES\s*:\s*(\d+)", text, re.I)
    if total_cycles_match:
        try:
            values["total_cycles"] = float(total_cycles_match.group(1))
        except (ValueError, TypeError):
            pass

    return values


def detect_radioss_failure(text: str, *, return_code: int | None = None) -> str | None:
    if return_code not in (None, 0):
        return f"OpenRadioss exited with return code {return_code}"
    for pattern, description in _FAILURES:
        if pattern.search(text):
            return description
    return None


def _read_ascii_animation(path: Path, checkpoint) -> tuple[dict[int, tuple[float, float, float]], dict[int, tuple[int, ...]]]:
    nodes: dict[int, tuple[float, float, float]] = {}
    cells: dict[int, tuple[int, ...]] = {}
    next_id = 1
    with path.open("rb") as stream:
        if not re.search(rb"(?im)^\s*(?:NODE|TETRA|TET4)[\s,]", stream.read(4096)):
            return {}, {}
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            checkpoint()
            fields = re.split(r"[\s,]+", line.strip())
            try:
                if fields[0].upper() == "NODE" and len(fields) == 5:
                    nodes[int(fields[1])] = tuple(float(value) for value in fields[2:5])
                elif fields[0].upper() in {"TETRA", "TET4"} and len(fields) in {5, 6}:
                    cell_id = int(fields[1]) if len(fields) == 6 else next_id
                    cells[cell_id] = tuple(int(value) for value in fields[-4:])
                    next_id = max(next_id, cell_id + 1)
            except ValueError:
                continue
    if any(not all(math.isfinite(v) for v in xyz) for xyz in nodes.values()):
        return {}, {}
    if any(node_id not in nodes for cell in cells.values() for node_id in cell):
        return {}, {}
    return nodes, cells


def _find_anim_to_vtk(installation=None) -> Path | None:
    from ..connections import _registered_path, _registry

    names = ("anim_to_vtk", "anim_to_vtk_win64.exe", "anim_to_vtk.exe", "anim_to_vtk_linux64_gf", "anim_to_vtk_linux64")
    candidates = [installation, os.environ.get("CAELAB_OPENCOURANT")]
    registry, _ = _registry()
    entry = registry.get("programs", {}).get("opencourant")
    registered = registry.get("env", {}).get("CAELAB_OPENCOURANT") or (entry.get("path") if isinstance(entry, dict) else None)
    if isinstance(registered, str):
        try:
            candidates.append(_registered_path(registered, registry))
        except ValueError:
            pass
    for value in filter(None, candidates):
        path = Path(value).expanduser()
        roots = (path, path / "exec", path / "bin") if path.is_dir() else (path.parent, path.parent.parent / "exec")
        for folder in roots:
            for name in names:
                if found := shutil.which(str(folder / name)):
                    return Path(found).resolve()
    return next((Path(found).resolve() for name in names if (found := shutil.which(name))), None)


def _frame_ids(arrays, names, size, original_name):
    source = next((arrays[name] for name in names if name in arrays), None)
    if source is None:
        return list(range(1, size + 1)), None
    if source.shape != (size, 1) or source.dtype.kind not in "iu":
        raise ResultError("INVALID_DATASET", f"invalid ID array {names[0]}")
    native = list(map(int, source.ravel()))
    frequencies = Counter(native)
    # Auxiliary entities may have no source ID or share a producer ID.
    # Retain unique IDs and use unused positive IDs for the remaining rows.
    assigned = (i for i in count(1) if i not in frequencies)
    ids = [i if i > 0 and frequencies[i] == 1 else next(assigned) for i in native]
    if ids != native:
        return ids, dict(zip(ids, native))
    original = arrays.get(original_name)
    if original is not None and original.shape == (size, 1) and original.dtype.kind in "iu":
        return ids, dict(zip(ids, map(int, original.ravel())))
    return ids, None


def _write_vtu(path: Path, nodes, cells, *, frame_index=0, time_value=None, displacements=None,
               cell_types=None, source_node_ids=None, source_element_ids=None, checkpoint=lambda: None) -> None:
    from functools import partial

    array = partial(_array, checkpoint=checkpoint)
    root = ET.Element("VTKFile", type="UnstructuredGrid", version="0.1", byte_order="LittleEndian")
    grid = ET.SubElement(root, "UnstructuredGrid")
    field_data = ET.SubElement(grid, "FieldData")
    array(field_data, "FrameIndex", [frame_index], integer=True).set("NumberOfTuples", "1")
    if time_value is not None:
        array(field_data, "Native_value", [time_value]).set("NumberOfTuples", "1")
        array(field_data, "Native_analysis_type", [1], integer=True).set("NumberOfTuples", "1")
    piece = ET.SubElement(grid, "Piece", NumberOfPoints=str(len(nodes)), NumberOfCells=str(len(cells)))
    ordered = sorted(nodes)
    point_data = ET.SubElement(piece, "PointData")
    array(point_data, "NativeNodeId", ordered, integer=True)
    if source_node_ids is not None:
        array(point_data, "RadiossNodeId", (source_node_ids[node] for node in ordered), integer=True)
    if displacements is not None:
        array(point_data, "DISP", (value for node in ordered for value in displacements[node]),
              3, component_names=("D1", "D2", "D3"))
    cell_data = ET.SubElement(piece, "CellData")
    array(cell_data, "NativeElementId", cells, integer=True)
    if source_element_ids is not None:
        array(cell_data, "RadiossElementId", (source_element_ids[cell] for cell in cells), integer=True)
    array(ET.SubElement(piece, "Points"), "Coordinates", (value for node in ordered for value in nodes[node]), 3)
    index = {node_id: offset for offset, node_id in enumerate(ordered)}
    cell_node = ET.SubElement(piece, "Cells")
    array(cell_node, "connectivity", (index[node] for cell in cells.values() for node in cell), integer=True)
    array(cell_node, "offsets", accumulate(map(len, cells.values())), integer=True)
    array(cell_node, "types", (10 for _ in cells) if cell_types is None else cell_types, integer=True)
    ET.indent(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_xml(root, path)


def _read_exchange_frame(path: Path, checkpoint):
    legacy = path.suffix.lower() == ".vtk"
    grid = (vtk_fields.read_animation_geometry(path, check_cancelled=checkpoint) if legacy
            else vtk_fields.read_vtu(path, node_id_policy="one_based", check_cancelled=checkpoint))
    node_ids, source_node_ids = _frame_ids(
        grid.point_data, ("NativeNodeId", "NODE_ID", "node_id"), len(grid.points), "RadiossNodeId")
    element_ids, source_element_ids = _frame_ids(
        grid.cell_data, ("NativeElementId", "ELEMENT_ID"), len(grid.cells), "RadiossElementId")
    displacement = grid.point_data.get("Displacement" if legacy else "DISP")
    points = grid.points
    geometry_state = "deformed" if legacy else "stored"
    if displacement is not None:
        if displacement.shape != points.shape or not np.isfinite(displacement).all():
            raise ResultError("INVALID_DATASET", "invalid displacement vectors")
        if legacy:
            points = grid.reference_points
            if not np.isfinite(points).all():
                raise ResultError("INVALID_DATASET", "non-finite reference geometry")
        geometry_state = "reference"
    return (dict(zip(node_ids, points)),
            {element_id: tuple(node_ids[index] for index in cell) for element_id, cell in zip(element_ids, grid.cells)},
            {"time_value": grid.time, "displacements": None if displacement is None else dict(zip(node_ids, displacement)),
             "cell_types": grid.cell_types, "source_node_ids": source_node_ids, "source_element_ids": source_element_ids,
             "geometry_state": geometry_state})


def _frame_fields(displacements, node_count):
    summary = {"max_displacement": None, "displacement_node_count": 0, "mesh_node_count": node_count}
    if displacements is None:
        return [], summary
    values = list(displacements.values())
    norms = [math.hypot(*value) for value in values]
    finite = [value for value in norms if math.isfinite(value)]
    summary.update(max_displacement=max(finite, default=None), displacement_node_count=len(finite))
    components = [{"name": name, "minimum": float(min(column)), "maximum": float(max(column))}
                  for name, column in zip(("D1", "D2", "D3"), zip(*values))]
    return [{"name": "DISP", "components": [{"name": item["name"], "exist": 0} for item in components],
             "coverage": {"record_count": len(values), "mesh_node_count": node_count},
             "summary": {"components": components}}], summary


def _decode_animation(animation, sidecars, converter, run_converter, checkpoint):
    error = None
    for path in sidecars:
        try:
            return _read_exchange_frame(path, checkpoint), None
        except ResultError as exc:
            error = exc.code
    if converter is not None:
        try:
            with tempfile.TemporaryDirectory(prefix=".rad-convert-", dir=animation.parent) as folder:
                output = Path(folder) / "animation.vtk"
                run_converter(converter, animation, output, check_cancelled=checkpoint)
                return _read_exchange_frame(output, checkpoint), None
        except ResultError as exc:
            error = exc.code
    return None, error


def _frame_name(path):
    return path.stem if path.suffix.lower() in {".vtk", ".vtu"} else path.name


def _frame_order(path):
    name = _frame_name(path)
    return name.casefold().rstrip("0123456789"), int(re.search(r"\d+$", name)[0])


def _frame_key(path):
    name = _frame_name(path).casefold()
    indexed = re.fullmatch(r"frame-(\d+)", name)
    return f"frame-{int(indexed[1]):06d}" if indexed else name


def collect_radioss_results(workdir: Path, *, check_cancelled=None, converter_installation=None, run_converter=None) -> dict:
    workdir = Path(workdir).resolve()
    checkpoint = check_cancelled or (lambda: None)
    checkpoint()
    listings = sorted(workdir.glob("*_0001.out")) or sorted(workdir.glob("*.out"))
    animations = sorted((path for path in workdir.iterdir()
                         if path.is_file() and re.fullmatch(r".*A\d{3,}", path.name, re.I)), key=_frame_order)
    rendered = sorted(path for folder in (workdir, workdir / "results") for path in folder.glob("*")
                      if path.suffix.lower() in {".vtk", ".vtu"} and path.is_file())
    sidecars = {}
    for path in rendered:
        sidecars.setdefault(_frame_key(path), []).append(path)
    inputs = (animations or sorted((paths[0] for name, paths in sidecars.items()
                                   if re.fullmatch(r".*a\d{3,}|frame-\d+", name)), key=_frame_order))
    listing_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in listings)
    failure = detect_radioss_failure(listing_text)
    energies = parse_radioss_output(listings[-1]) if listings else {}
    internal = energies.get("internal_energy")
    hourglass = energies.get("hourglass_energy")
    if internal is not None and hourglass is not None:
        # Product KPI required by spec.  This is HG/internal, not the distinct
        # HG/total diagnostic used by some Radioss guidance.
        energies["hourglass_ratio"] = hourglass / internal if internal != 0.0 else None
    summary = {**energies, "solver_id": "radioss"}
    warnings: list[str] = []
    frames: list[dict] = []
    exchange_files: list[str] = []
    geometry_only = False

    if failure:
        warnings.append(f"OpenRadioss failure detected: {failure}")
    converter = _find_anim_to_vtk(converter_installation) if animations else None
    if converter is not None and run_converter is None:
        from ..jobs import JobManager

        run_converter = JobManager.run_animation_converter
    used_indices = {int(path.stem[6:]) for path in inputs if not animations and path.stem.lower().startswith("frame-")}
    available_indices = (index for index in count() if index not in used_indices)
    for animation in inputs:
        checkpoint()
        frame_index = (int(animation.stem[6:]) if not animations and animation.stem.lower().startswith("frame-")
                       else next(available_indices))
        relative = f"results/frame-{frame_index:06d}.vtu"
        geometry_state = "reference"
        nodes, cells = _read_ascii_animation(animation, checkpoint) if animations else ({}, {})
        if not nodes or not cells:
            matching = sidecars.get(_frame_key(animation), [])
            decoded, error = _decode_animation(animation, matching, converter, run_converter, checkpoint)
            if decoded is None and animations and workdir / relative in rendered:
                decoded, error = _decode_animation(animation, [workdir / relative], None, None, checkpoint)
            if decoded is None:
                reason = f" ({error})" if error else ""
                warnings.append(f"{animation.name}: native animation payload was not decoded{reason}; use a compatible external postprocessor.")
                continue
            nodes, cells, options = decoded
            geometry_state = options.pop("geometry_state")
            if options["displacements"] is None:
                warnings.append(f"{animation.name}: animation geometry is available; no displacement vectors were output.")
                geometry_only = True
        else:
            options = {}
        if not animations or workdir / relative in rendered:
            # A supplied VTU may carry additional fields kept only in the original.
            relative = f"results/decoded/frame-{frame_index:06d}.vtu"
        _write_vtu(workdir / relative, nodes, cells, frame_index=frame_index, checkpoint=checkpoint, **options)
        fields, frame_summary = _frame_fields(options.get("displacements"), len(nodes))
        time = options.get("time_value")
        frames.append({"index": frame_index, "time": time, "value": time, "name": animation.name,
                       "is_final": animation == inputs[-1], "geometry_state": geometry_state,
                       "vtu_file": relative, "vtu": relative, "fields": fields, "summary": frame_summary})
        exchange_files.append(relative)

    if not inputs:
        warnings.append("OpenRadioss animation output A### was not found.")
    if not listings:
        warnings.append("OpenRadioss Engine listing was not found; energy KPIs are unavailable.")
    if internal == 0.0 and hourglass is not None:
        warnings.append("Hourglass ratio is undefined because internal energy is zero.")
    status = "failed" if failure or (not inputs and not listings) else "readable"
    if status == "readable" and (not inputs or not listings or not energies or geometry_only or len(frames) != len(inputs)):
        status = "partial"
    primary = inputs[0].relative_to(workdir).as_posix() if inputs else listings[-1].name if listings else None
    return {"status": status, "frames": frames, "exchange_files": exchange_files,
            "native_files": [path.name for path in listings + inputs],
            "primary_native_output": primary,
            "summary": summary, "warnings": warnings}
