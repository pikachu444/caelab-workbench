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


def parse_radioss_output(path: Path) -> dict[str, float]:
    """Extract the last explicitly labelled global-energy values from an Engine listing."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    values: dict[str, float] = {}
    for key, label in _ENERGY_LABELS.items():
        pattern = re.compile(rf"{label}(?:\s*\([^)]*\))?\s*(?:=|:)?\s*({_NUMBER})\s*%?", re.I)
        matches = pattern.findall(text)
        if matches:
            value = float(matches[-1].replace("D", "E").replace("d", "e"))
            if math.isfinite(value):
                values[key] = value

    # Do not guess columns from native compact headings such as I-ENERGY and
    # K-ENERGY: their layout/version must be established before values can be
    # mapped safely.  Explicitly labelled exported values remain unambiguous.
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
    animations = sorted(path for path in workdir.iterdir() if re.fullmatch(r"A\d{3}", path.name, re.I))
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
            warnings.append(f"{animation.name}: native animation payload was not decoded; empty exchange geometry written.")
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
            "summary": summary, "warnings": warnings}
