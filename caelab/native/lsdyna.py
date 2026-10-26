"""Small, dependency-free LS-DYNA result handoff.

The native files remain the source of truth.  This module only extracts the
ASCII global-energy values and the last NODOUT displacement state; it does not
pretend to decode the proprietary d3plot binary format.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"
_ENERGY_LABELS = {
    "kinetic_energy": ("kinetic energy",),
    "internal_energy": ("internal energy",),
    "hourglass_energy": ("hourglass energy",),
    "sliding_energy": ("sliding interface energy", "sliding energy"),
    "total_energy": ("total energy",),
    "energy_ratio": ("energy ratio",),
}
_FATAL_ERRORS = (
    re.compile(r"\berror termination\b", re.I),
    re.compile(r"\bout[- ]of[- ]core\b", re.I),
)


def _checkpoint(check_cancelled):
    if check_cancelled is not None:
        check_cancelled()


def parse_include_references(path: str | Path) -> list[str]:
    """Return LS-DYNA ``*INCLUDE`` filenames in deck order.

    Both the customary filename-on-the-next-data-line form and
    ``*INCLUDE, file`` are accepted.  Resolution and snapshotting remain the
    caller's responsibility.
    """
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    references: list[str] = []
    waiting = False
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("$"):
            continue
        if waiting:
            if line.startswith("*"):
                waiting = False
            else:
                references.append(line.strip('"\''))
                waiting = False
                continue
        keyword = line.split(",", 1)[0].split(None, 1)[0].upper()
        if keyword == "*INCLUDE":
            rest = line[len("*INCLUDE"):].lstrip(" ,=")
            if rest:
                references.append(rest.strip().strip('"\''))
            else:
                waiting = True
    return references


def parse_glstat(path: str | Path) -> dict[str, float]:
    """Extract the final reported global-energy state from an ASCII GLSTAT."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    starts = list(re.finditer(rf"(?im)^\s*time\s*=\s*({_NUMBER})", text))
    blocks = [text] if not starts else [text[match.start():(starts[i + 1].start() if i + 1 < len(starts) else None)] for i, match in enumerate(starts)]
    parsed: list[dict[str, float]] = []
    for block in blocks:
        values: dict[str, float] = {}
        valid = True
        for key, labels in _ENERGY_LABELS.items():
            matches: list[str] = []
            for label in labels:
                pattern = rf"(?im)^\s*{re.escape(label)}\s*[:=]\s*({_NUMBER})"
                matches.extend(re.findall(pattern, block))
            if len(matches) > 1:
                valid = False
                break
            if matches:
                value = float(matches[0].replace("D", "E").replace("d", "e"))
                if not math.isfinite(value):
                    valid = False
                    break
                values[key] = value
        if valid and values:
            parsed.append(values)
    complete = [values for values in parsed if all(key in values for key in _ENERGY_LABELS)]
    return complete[-1] if complete else (parsed[-1] if parsed else {})


def _split_fields(line: str) -> list[str]:
    return [item for item in re.split(r"[\s,]+", line.strip()) if item]


def _read_deck_mesh(path: Path, checkpoint, visited=None) -> tuple[dict[int, tuple[float, float, float]], list[tuple[int, list[int]]]]:
    visited = set() if visited is None else visited
    path = path.resolve()
    if path in visited:
        return {}, []
    visited.add(path)
    nodes: dict[int, tuple[float, float, float]] = {}
    cells: list[tuple[int, list[int]]] = []
    section = ""
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if number % 512 == 1:
            checkpoint()
        line = raw.strip()
        if not line or line.startswith("$"):
            continue
        if line.startswith("*"):
            section = line.upper().split(",", 1)[0]
            continue
        fields = _split_fields(line)
        try:
            if section == "*NODE" and len(fields) >= 4:
                nodes[int(fields[0])] = tuple(float(value.replace("D", "E")) for value in fields[1:4])
            elif section == "*ELEMENT_SOLID" and len(fields) >= 10:
                cells.append((int(fields[0]), [int(value) for value in fields[2:10]]))
        except ValueError:
            continue
    for reference in parse_include_references(path):
        included = (path.parent / reference).resolve()
        if included.is_file():
            child_nodes, child_cells = _read_deck_mesh(included, checkpoint, visited)
            nodes.update(child_nodes)
            cells.extend(child_cells)
    return nodes, cells


def _read_last_nodout(path: Path, checkpoint) -> tuple[dict[int, tuple[float, float, float]], float | None]:
    states: list[dict[int, tuple[float, float, float]]] = []
    current: dict[int, tuple[float, float, float]] = {}
    time_value = None
    for number, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if number % 512 == 1:
            checkpoint()
        match = re.search(rf"\btime\s*=\s*({_NUMBER})", raw, re.I)
        if match:
            if current:
                states.append(current)
                current = {}
            time_value = float(match.group(1).replace("D", "E").replace("d", "e"))
            continue
        fields = _split_fields(raw)
        if len(fields) < 4:
            continue
        try:
            current[int(fields[0])] = tuple(float(value.replace("D", "E").replace("d", "e")) for value in fields[1:4])
        except ValueError:
            continue
    if current:
        states.append(current)
    return (states[-1] if states else {}), time_value


def _write_vtu(path: Path, nodes, cells, displacements, time_value, checkpoint) -> None:
    ids = list(nodes)
    index = {node_id: offset for offset, node_id in enumerate(ids)}
    valid_cells = [(cell_id, connectivity) for cell_id, connectivity in cells if all(node in index for node in connectivity)]
    root = ET.Element("VTKFile", type="UnstructuredGrid", version="0.1", byte_order="LittleEndian")
    grid = ET.SubElement(root, "UnstructuredGrid")
    if time_value is not None:
        field = ET.SubElement(grid, "FieldData")
        ET.SubElement(field, "DataArray", type="Float64", Name="TimeValue", NumberOfTuples="1", format="ascii").text = format(time_value, ".17g")
    piece = ET.SubElement(grid, "Piece", NumberOfPoints=str(len(ids)), NumberOfCells=str(len(valid_cells)))
    points = ET.SubElement(piece, "Points")
    ET.SubElement(points, "DataArray", type="Float64", NumberOfComponents="3", format="ascii").text = " ".join(format(v, ".17g") for node in ids for v in nodes[node])
    cell_data = ET.SubElement(piece, "Cells")
    flat = [index[node] for _, connectivity in valid_cells for node in connectivity]
    offsets = [8 * (i + 1) for i in range(len(valid_cells))]
    for name, values in (("connectivity", flat), ("offsets", offsets), ("types", [12] * len(valid_cells))):
        ET.SubElement(cell_data, "DataArray", type="Int64", Name=name, format="ascii").text = " ".join(map(str, values))
    point_data = ET.SubElement(piece, "PointData")
    ET.SubElement(point_data, "DataArray", type="Int64", Name="NativeNodeId", format="ascii").text = " ".join(map(str, ids))
    if displacements:
        values = [value for node in ids for value in displacements.get(node, (math.nan,) * 3)]
        ET.SubElement(point_data, "DataArray", type="Float64", Name="Displacement", NumberOfComponents="3", format="ascii").text = " ".join(format(v, ".17g") for v in values)
    checkpoint()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".lsdyna-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            ET.ElementTree(root).write(stream, encoding="utf-8", xml_declaration=True)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _find_error(workdir: Path, checkpoint) -> str | None:
    candidates = ("d3hsp", "messag", "message", "stdout", "stderr", "lsdyna.log", "job.log")
    paths = [workdir / name for name in candidates]
    paths.extend(sorted(workdir.glob("mes*")))
    for path in dict.fromkeys(paths):
        if not path.is_file() or path.stat().st_size > 20_000_000:
            continue
        checkpoint()
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for pattern in _FATAL_ERRORS:
            match = pattern.search(text)
            if match:
                return f"{path.name}: {match.group(0)}"
        negative = re.search(r"\bnegative volume\b.*\bsolid\b", text, re.I)
        completed = re.search(r"\bnormal termination\b|\bn o r m a l\s+t e r m i n a t i o n\b", text, re.I)
        if negative and not completed:
            return f"{path.name}: {negative.group(0)}"
    return None


def collect_lsdyna_results(workdir: str | Path, *, check_cancelled=None) -> dict:
    """Collect LS-DYNA ASCII results and write a ParaView-readable VTU frame."""
    root = Path(workdir).resolve()
    checkpoint = lambda: _checkpoint(check_cancelled)
    checkpoint()
    failure = _find_error(root, checkpoint)
    empty_summary = {key: None for key in _ENERGY_LABELS}
    empty_summary.update({"max_displacement": None, "solver_id": "lsdyna"})
    base = {"status": "failed", "frames": [], "exchange_files": [], "summary": empty_summary, "warnings": []}
    if failure:
        base["warnings"].append(f"LS-DYNA 오류 종료가 감지되었습니다: {failure}")
        return base

    glstat = root / "glstat"
    nodout = root / "nodout"
    d3plots = sorted(path for path in root.glob("d3plot*") if path.is_file())
    decks = sorted((*root.glob("*.k"), *root.glob("*.key")))
    energies = parse_glstat(glstat) if glstat.is_file() else {}
    displacements, time_value = _read_last_nodout(nodout, checkpoint) if nodout.is_file() else ({}, None)
    nodes, cells = _read_deck_mesh(decks[0], checkpoint) if decks else ({}, [])
    if not (energies or displacements or d3plots):
        base["warnings"].append("판독 가능한 d3plot, glstat 또는 nodout 결과가 없습니다.")
        return base

    maximum = max((math.sqrt(sum(value * value for value in row)) for row in displacements.values()), default=None)
    summary = {key: energies.get(key) for key in _ENERGY_LABELS}
    summary.update({"max_displacement": maximum, "solver_id": "lsdyna"})
    warnings: list[str] = []
    if d3plots:
        warnings.append("d3plot 바이너리는 직접 변환하지 않았으며 deck/nodout ASCII로 교환 프레임을 만들었습니다.")
    if not energies:
        warnings.append("glstat 전역 에너지 값을 찾지 못했습니다.")
    if not nodes:
        warnings.append("교환 프레임을 만들 LS-DYNA deck 절점 형상을 찾지 못했습니다.")
        return {"status": "partial", "frames": [], "exchange_files": [], "summary": summary, "warnings": warnings}
    frame_path = root / "results" / "frame-000000.vtu"
    _write_vtu(frame_path, nodes, cells, displacements, time_value, checkpoint)
    return {
        "status": "readable" if (energies and (displacements or nodes)) else "partial",
        "frames": [{"index": 0, "time": time_value, "vtu": "results/frame-000000.vtu"}],
        "exchange_files": ["results/frame-000000.vtu"],
        "summary": summary,
        "warnings": warnings,
    }
