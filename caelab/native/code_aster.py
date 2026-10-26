"""Small, dependency-free Code_Aster result adapter.

Real RMED files are MED/HDF5 and require a MED-capable reader.  For deterministic
tests this module accepts a JSON fixture in an ``.rmed`` file; the fixture must
provide geometry and connectivity, so the adapter never invents a mesh from a
text-only ``.resu`` file.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET


_COMM_KEYWORDS = ("DEBUT", "LIRE_MAILLAGE", "AFFE_MATERIAU", "MECA_STATIQUE", "FIN")
_FATAL = re.compile(
    r"(?:NO[_ ]CONVERGENCE|CONVERGENCE\s+(?:ERROR|FAIL)|ERROR\s+TERMINATION|"
    r"ARRET\s+PAR\s+MANQUE\s+DE\s+CONVERGENCE|<F>\s+ERREUR)",
    re.IGNORECASE,
)
_VTK_TYPES = {
    "line": (2, 3),
    "triangle": (3, 5),
    "quad": (4, 9),
    "tetra": (4, 10),
    "hexahedron": (8, 12),
}
_FIXTURE_FORMAT = "caelab-codeaster-rmed-fixture-v1"


def parse_comm_keywords(source: str | Path) -> list[str]:
    """Return supported Code_Aster commands in source order."""
    path = Path(source) if isinstance(source, Path) else None
    text = path.read_text(encoding="utf-8") if path is not None else source
    text = re.sub(r"#.*", "", text)
    pattern = re.compile(r"\b(" + "|".join(_COMM_KEYWORDS) + r")\s*\(", re.IGNORECASE)
    return [match.group(1).upper() for match in pattern.finditer(text)]


def parse_codeaster_comm(source: str | Path) -> list[str]:
    """Compatibility spelling for callers that name the solver in the parser."""
    return parse_comm_keywords(source)


def _checkpoint(check_cancelled):
    if check_cancelled is not None:
        check_cancelled()


def _failed(message: str) -> dict:
    return {
        "status": "failed",
        "frames": [],
        "exchange_files": [],
        "summary": {"solver_id": "codeaster"},
        "warnings": [message],
    }


def _numbers(values, count: int, label: str) -> list[float]:
    if not isinstance(values, (list, tuple)) or len(values) != count:
        raise ValueError(f"{label}: {count}개 성분이 필요합니다.")
    result = [float(value) for value in values]
    if not all(math.isfinite(value) for value in result):
        raise ValueError(f"{label}: 유한한 수치만 허용됩니다.")
    return result


def _load_fixture(path: Path, checkpoint) -> dict:
    checkpoint()
    if path.read_bytes()[:8] == b"\x89HDF\r\n\x1a\n":
        raise ValueError("실제 MED/HDF5 RMED 판독에는 MED 지원 라이브러리가 필요합니다.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("지원하는 JSON RMED 테스트 형식이 아닙니다.") from error
    if not isinstance(data, dict):
        raise ValueError("RMED 테스트 자료는 JSON 객체여야 합니다.")
    if data.get("format") != _FIXTURE_FORMAT:
        raise ValueError(f"RMED JSON 테스트 자료에는 format={_FIXTURE_FORMAT!r} 표시가 필요합니다.")

    raw_nodes = data.get("nodes")
    raw_elements = data.get("elements")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("RMED 테스트 자료에 nodes가 없습니다.")
    if not isinstance(raw_elements, list) or not raw_elements:
        raise ValueError("RMED 테스트 자료에 elements가 없습니다.")
    nodes = [_numbers(row, 3, f"nodes[{index}]") for index, row in enumerate(raw_nodes)]
    elements = []
    for index, row in enumerate(raw_elements):
        checkpoint()
        if not isinstance(row, dict) or row.get("type") not in _VTK_TYPES:
            raise ValueError(f"elements[{index}]: 지원하는 명시적 type이 필요합니다.")
        expected, vtk_type = _VTK_TYPES[row["type"]]
        raw_connectivity = row.get("nodes")
        if not isinstance(raw_connectivity, (list, tuple)) or len(raw_connectivity) != expected:
            raise ValueError(f"elements[{index}]: {row['type']}에는 {expected}개 절점이 필요합니다.")
        connectivity = [int(value) for value in raw_connectivity]
        if any(value < 0 or value >= len(nodes) for value in connectivity):
            raise ValueError(f"elements[{index}]: 절점 인덱스가 범위를 벗어났습니다.")
        elements.append((connectivity, vtk_type))

    displacement = data.get("displacement")
    if displacement is not None:
        if not isinstance(displacement, list) or len(displacement) != len(nodes):
            raise ValueError("displacement는 절점 수와 같은 행 수여야 합니다.")
        displacement = [_numbers(row, 3, f"displacement[{index}]") for index, row in enumerate(displacement)]
    stress = data.get("stress")
    if stress is not None:
        if not isinstance(stress, list) or len(stress) != len(nodes):
            raise ValueError("stress는 이 교환 형식에서 절점 수와 같은 행 수여야 합니다.")
        stress = [_numbers(row, 6, f"stress[{index}]") for index, row in enumerate(stress)]
    time = float(data.get("time", 0.0))
    if not math.isfinite(time):
        raise ValueError("time은 유한한 수치여야 합니다.")
    return {"nodes": nodes, "elements": elements, "displacement": displacement, "stress": stress, "time": time}


def _array(parent, *, name: str | None, values, components: int = 1, value_type="Float64"):
    attributes = {"type": value_type, "format": "ascii"}
    if name is not None:
        attributes["Name"] = name
    if components != 1:
        attributes["NumberOfComponents"] = str(components)
    element = ET.SubElement(parent, "DataArray", attributes)
    element.text = " ".join(str(value) for value in values)


def _von_mises(row):
    xx, yy, zz, xy, xz, yz = row
    return math.sqrt(0.5 * ((xx - yy) ** 2 + (yy - zz) ** 2 + (zz - xx) ** 2) + 3 * (xy**2 + xz**2 + yz**2))


def _write_vtu(path: Path, data: dict):
    root = ET.Element("VTKFile", type="UnstructuredGrid", version="0.1", byte_order="LittleEndian")
    grid = ET.SubElement(root, "UnstructuredGrid")
    piece = ET.SubElement(grid, "Piece", NumberOfPoints=str(len(data["nodes"])), NumberOfCells=str(len(data["elements"])))
    points = ET.SubElement(piece, "Points")
    _array(points, name=None, values=(value for row in data["nodes"] for value in row), components=3)
    cells = ET.SubElement(piece, "Cells")
    _array(cells, name="connectivity", values=(value for row, _ in data["elements"] for value in row), value_type="Int64")
    offsets, offset = [], 0
    for row, _ in data["elements"]:
        offset += len(row)
        offsets.append(offset)
    _array(cells, name="offsets", values=offsets, value_type="Int64")
    _array(cells, name="types", values=(vtk_type for _, vtk_type in data["elements"]), value_type="UInt8")
    point_data = ET.SubElement(piece, "PointData")
    if data["displacement"] is not None:
        _array(point_data, name="DISP", values=(value for row in data["displacement"] for value in row), components=3)
        _array(point_data, name="DISP_Magnitude", values=(math.sqrt(sum(value * value for value in row)) for row in data["displacement"]))
    if data["stress"] is not None:
        _array(point_data, name="STRESS", values=(value for row in data["stress"] for value in row), components=6)
        _array(point_data, name="VON_MISES", values=(_von_mises(row) for row in data["stress"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            ET.ElementTree(root).write(stream, encoding="utf-8", xml_declaration=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def collect_codeaster_results(workdir: Path, *, check_cancelled=None) -> dict:
    """Collect one deterministic Code_Aster exchange frame from ``workdir``."""
    workdir = Path(workdir).resolve()
    target = workdir / "results" / "frame-000000.vtu"
    target.unlink(missing_ok=True)
    _checkpoint(check_cancelled)
    diagnostics = sorted((*workdir.glob("*.resu"), *workdir.glob("*.mess"))) if workdir.is_dir() else []
    try:
        for path in diagnostics:
            _checkpoint(check_cancelled)
            text = path.read_text(encoding="utf-8", errors="replace")
            if _FATAL.search(text):
                return _failed(f"Code_Aster 수렴 또는 치명 오류가 보고되었습니다: {path.name}")
        candidates = sorted(workdir.glob("*.rmed")) if workdir.is_dir() else []
        if not candidates:
            candidates = [path for path in diagnostics if path.suffix.lower() == ".resu"]
        if not candidates:
            return _failed("Code_Aster 결과 파일(.rmed 또는 .resu)을 찾지 못했습니다.")
        data = _load_fixture(candidates[0], lambda: _checkpoint(check_cancelled))
        _checkpoint(check_cancelled)
        _write_vtu(target, data)
    except (OSError, TypeError, ValueError) as error:
        return _failed(f"Code_Aster 결과를 판독하지 못했습니다: {error}")

    fields = {}
    displacement = data["displacement"]
    stress = data["stress"]
    if displacement is not None:
        fields["DISP"] = {"name": "DISP", "components": ["DX", "DY", "DZ", "Magnitude"], "type": "vector"}
    if stress is not None:
        fields["STRESS"] = {"name": "STRESS", "components": ["SIXX", "SIYY", "SIZZ", "SIXY", "SIXZ", "SIYZ", "VON_MISES"], "type": "tensor"}
    max_displacement = max((math.sqrt(sum(value * value for value in row)) for row in displacement), default=None) if displacement is not None else None
    max_stress = max((_von_mises(row) for row in stress), default=None) if stress is not None else None
    warnings = [] if fields else ["메시 형상은 읽었지만 지원하는 절점 결과 필드가 없습니다."]
    status = "readable" if fields else "partial"
    return {
        "status": status,
        "frames": [{"index": 0, "time": data["time"], "name": "Frame 0", "vtu": "results/frame-000000.vtu", "fields": fields}],
        "exchange_files": ["results/frame-000000.vtu"],
        "summary": {
            "max_displacement": max_displacement,
            "max_stress": max_stress,
            "num_nodes": len(data["nodes"]),
            "num_elements": len(data["elements"]),
            "solver_id": "codeaster",
        },
        "warnings": warnings,
    }
