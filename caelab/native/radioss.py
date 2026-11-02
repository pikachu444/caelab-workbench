"""Small, dependency-free OpenRadioss result collector.

The native ``A###`` animation format is not reimplemented here.  For automated
connection tests we accept a deliberately small ASCII fixture (``NODE`` and
``TETRA`` records); an opaque native animation is preserved and represented by
an empty VTU with an explicit warning instead of invented field values.
"""
from __future__ import annotations

import math
import re
from pathlib import Path
from xml.etree import ElementTree as ET


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


def _read_ascii_animation(path: Path) -> tuple[dict[int, tuple[float, float, float]], list[tuple[int, ...]]]:
    nodes: dict[int, tuple[float, float, float]] = {}
    cells: list[tuple[int, ...]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        fields = re.split(r"[\s,]+", line.strip())
        if not fields:
            continue
        try:
            if fields[0].upper() == "NODE" and len(fields) == 5:
                nodes[int(fields[1])] = tuple(float(value) for value in fields[2:5])
            elif fields[0].upper() in {"TETRA", "TET4"} and len(fields) in {5, 6}:
                ids = fields[-4:]
                cells.append(tuple(int(value) for value in ids))
        except ValueError:
            continue
    if any(not all(math.isfinite(v) for v in xyz) for xyz in nodes.values()):
        return {}, []
    if any(node_id not in nodes for cell in cells for node_id in cell):
        return {}, []
    return nodes, cells


def _write_vtu(path: Path, nodes: dict[int, tuple[float, float, float]], cells: list[tuple[int, ...]]) -> None:
    root = ET.Element("VTKFile", type="UnstructuredGrid", version="0.1", byte_order="LittleEndian")
    grid = ET.SubElement(root, "UnstructuredGrid")
    piece = ET.SubElement(grid, "Piece", NumberOfPoints=str(len(nodes)), NumberOfCells=str(len(cells)))
    points = ET.SubElement(piece, "Points")
    point_data = ET.SubElement(points, "DataArray", type="Float64", NumberOfComponents="3", format="ascii")
    ordered = sorted(nodes)
    point_data.text = " ".join(str(value) for node_id in ordered for value in nodes[node_id])
    index = {node_id: offset for offset, node_id in enumerate(ordered)}
    cell_node = ET.SubElement(piece, "Cells")
    connectivity = ET.SubElement(cell_node, "DataArray", type="Int64", Name="connectivity", format="ascii")
    connectivity.text = " ".join(str(index[node]) for cell in cells for node in cell)
    offsets = ET.SubElement(cell_node, "DataArray", type="Int64", Name="offsets", format="ascii")
    offsets.text = " ".join(str(4 * (offset + 1)) for offset in range(len(cells)))
    types = ET.SubElement(cell_node, "DataArray", type="UInt8", Name="types", format="ascii")
    types.text = " ".join("10" for _ in cells)  # VTK_TETRA
    ET.indent(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def collect_radioss_results(workdir: Path, *, check_cancelled=None) -> dict:
    workdir = Path(workdir).resolve()
    checkpoint = check_cancelled or (lambda: None)
    checkpoint()
    listings = sorted(workdir.glob("*_0001.out")) or sorted(workdir.glob("*.out"))
    animations = sorted(path for path in workdir.iterdir() if path.is_file() and re.fullmatch(r".*A\d{3,}", path.name, re.I))
    listing_text = "\n".join(path.read_text(encoding="utf-8", errors="replace") for path in listings)
    failure = detect_radioss_failure(listing_text)
    energies = parse_radioss_output(listings[-1]) if listings else {}
    internal = energies.get("internal_energy")
    hourglass = energies.get("hourglass_energy")
    decoded_frames = 0
    if internal is not None and hourglass is not None:
        # Product KPI required by spec.  This is HG/internal, not the distinct
        # HG/total diagnostic used by some Radioss guidance.
        energies["hourglass_ratio"] = hourglass / internal if internal != 0.0 else None
    summary = {**energies, "solver_id": "radioss"}
    warnings: list[str] = []
    frames: list[dict] = []
    exchange_files: list[str] = []

    if failure:
        warnings.append(f"OpenRadioss failure detected: {failure}")
    for frame_index, animation in enumerate(animations):
        checkpoint()
        nodes, cells = _read_ascii_animation(animation)
        if not nodes:
            warnings.append(f"{animation.name}: native animation payload was not decoded; use a compatible external postprocessor.")
            continue
        else:
            decoded_frames += 1
        relative = f"results/frame-{frame_index:06d}.vtu"
        _write_vtu(workdir / relative, nodes, cells)
        frames.append({"index": frame_index, "time": float(frame_index), "name": animation.name,
                       "vtu": relative, "fields": {}})
        exchange_files.append(relative)

    if not animations:
        warnings.append("OpenRadioss animation output A### was not found.")
    if not listings:
        warnings.append("OpenRadioss Engine listing was not found; energy KPIs are unavailable.")
    if internal == 0.0 and hourglass is not None:
        warnings.append("Hourglass ratio is undefined because internal energy is zero.")
    status = "failed" if failure or (not animations and not listings) else "readable"
    if status == "readable" and (not animations or not listings or not energies or decoded_frames != len(animations)):
        status = "partial"
    return {"status": status, "frames": frames, "exchange_files": exchange_files,
            "native_files": [path.name for path in listings + animations],
            "primary_native_output": animations[0].name if animations else listings[-1].name if listings else None,
            "summary": summary, "warnings": warnings}
