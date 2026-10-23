"""Read native ASCII FRD output without interpreting or rewriting its input model.

The old project converter required FreeCADCmd, assigned units and lost frame/ID
information. Only its format investigation is reused: no vendor code is copied.
FRD layout: https://www.dhondt.de/ccx_2.21.src.tar.bz2 (frd.c, frdheader.c).
Nodal records: https://www.dhondt.de/cgx_2.21.htm.tar.bz2 (doc/cgx/node225.html).
Topology: FreeCAD FRDReader::mapCcxTypeNodes/mapCcxToVtk/fillCell:
https://github.com/FreeCAD/FreeCAD/blob/dc1ab881ca0bbea9735e92938a660acee339679e/src/Mod/Fem/App/FemVTKTools.cpp
VTU: https://docs.vtk.org/en/latest/vtk_file_formats/vtkxml_file_format.html
"""
from __future__ import annotations

import base64
import math
import os
import re
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET


# FRD connectivity order, not INP order. Only these two topologies need a permutation.
_CELLS = {1: (8, 12), 2: (6, 13), 3: (4, 10), 4: (20, 25), 5: (15, 26),
          6: (10, 24), 7: (3, 5), 8: (6, 22), 9: (4, 9), 10: (8, 23),
          11: (2, 3), 12: (3, 21)}
_PERMUTATIONS = {4: (0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 16, 17, 18, 19, 12, 13, 14, 15),
                 5: (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14, 9, 10, 11)}


class _Unsupported(ValueError):
    pass


class _Abort(BaseException):
    def __init__(self, error):
        self.error = error


def _checked(values, checkpoint):
    for index, value in enumerate(values):
        if index % 4096 == 0:
            checkpoint()
        yield value


def _number(text: str) -> float:
    return float(text.replace("D", "E").replace("d", "e"))


def _chunks(text: str, width: int):
    text = text.rstrip()
    return [text[start:start + width] for start in range(0, len(text), width)]


def _digits(indicator: str) -> int:
    if indicator.strip() not in {"0", "1"}:
        raise _Unsupported(f"FRD 형식 {indicator.strip()!r}: ASCII short/long만 판독합니다. 원본은 보존됩니다.")
    return 10 if indicator.strip() == "1" else 5


@dataclass
class _Field:
    name: str
    declared_count: int
    components: list[dict] = field(default_factory=list)
    values: dict[int, list[float]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    dataset_counter: int | None = None
    result_type: int = 1

    @property
    def stored(self):
        # CGX IEXIST=2 stores values with an additional display marker; only 1
        # denotes a predefined component without a value in the nodal record.
        return [component for component in self.components if component["exist"] in (0, 2)]


@dataclass
class _Frame:
    metadata: dict
    fields: list[_Field] = field(default_factory=list)


class _Reader:
    def __init__(self, stream, checkpoint):
        self.checkpoint = checkpoint
        self.lines = enumerate(stream, 1)
        self.line_number = 0
        self.nodes: dict[int, list[float]] = {}
        self.elements: dict[int, tuple] = {}
        self.frames: list[_Frame] = []
        self.warnings: list[str] = []
        self.step = self.increment = self.dataset_counter = None
        self.parameters = {}
        self.ended = False

    def _next(self):
        if self.line_number % 1024 == 0:
            self.checkpoint()
        self.line_number, line = next(self.lines)
        if "\x00" in line:
            raise _Unsupported("바이너리 또는 NUL이 있는 FRD는 판독하지 않습니다.")
        return line.rstrip("\r\n")

    def _block(self):
        while True:
            try:
                line = self._next()
            except StopIteration:
                raise ValueError("닫히지 않은 FRD 블록입니다 (-3 누락).") from None
            if line.startswith(" -3"):
                return
            yield line

    def _nodes(self, header):
        if self.nodes:
            raise _Unsupported("여러 geometry 블록은 아직 교환하지 않습니다.")
        count, digits = int(header[24:36]), _digits(header[73:74])
        for line in self._block():
            if not line.startswith(" -1"):
                raise ValueError("FRD 절점 레코드 형식이 잘못되었습니다.")
            identifier = int(line[3:3 + digits])
            coordinates = [_number(part) for part in _chunks(line[3 + digits:], 12)]
            if identifier <= 0 or identifier in self.nodes or len(coordinates) != 3 or not all(map(math.isfinite, coordinates)):
                raise ValueError("FRD 절점 ID·좌표가 손상되었거나 중복됩니다.")
            self.nodes[identifier] = coordinates
        if count <= 0 or len(self.nodes) != count:
            raise ValueError("FRD geometry 절점 수가 헤더와 다릅니다.")

    def _elements(self, header):
        if self.elements:
            raise _Unsupported("여러 element geometry 블록은 아직 교환하지 않습니다.")
        count, digits = int(header[24:36]), _digits(header[73:74])
        current, connectivity = None, []

        def finish():
            if current is None:
                return
            identifier, topology, group, material = current
            if len(connectivity) != _CELLS[topology][0] or any(node not in self.nodes for node in connectivity):
                raise ValueError(f"FRD 요소 {identifier}: 연결 절점 수·ID가 geometry와 다릅니다.")
            if identifier <= 0 or identifier in self.elements:
                raise ValueError("FRD 요소 ID가 잘못되었거나 중복됩니다.")
            self.elements[identifier] = (topology, group, material, list(connectivity))

        for line in self._block():
            if line.startswith(" -1"):
                finish()
                identifier = int(line[3:3 + digits])
                info = [int(part) for part in _chunks(line[3 + digits:], 5)]
                if len(info) != 3:
                    raise ValueError("FRD 요소 헤더가 손상되었습니다.")
                if info[0] not in _CELLS:
                    raise _Unsupported(f"FRD topology {info[0]}의 출력 교환은 아직 지원하지 않습니다.")
                current, connectivity = (identifier, *info), []
            elif line.startswith(" -2") and current is not None:
                connectivity.extend(int(part) for part in _chunks(line[3:], digits))
            else:
                raise ValueError("FRD 요소 연결 레코드가 손상되었습니다.")
        finish()
        if count < 0 or len(self.elements) != count:
            raise ValueError("FRD geometry 요소 수가 헤더와 다릅니다.")

    def _frame(self, line):
        metadata = {"step": self.step, "increment": self.increment,
                    "value": _number(line[12:24]), "analysis_type": int(line[56:58]),
                    "kode": int(line[58:63]), "description": line[36:48].strip(),
                    "text": line[63:73].strip(), "parameters": dict(self.parameters)}
        if not math.isfinite(metadata["value"]) or int(line[24:36]) < 0:
            raise ValueError("FRD 100C 헤더의 값·레코드 수가 잘못되었습니다.")
        if not self.frames or self.frames[-1].metadata != metadata:
            self.frames.append(_Frame(metadata))
        if self.step is None or self.increment is None:
            message = "PSTEP이 없어 원래 step/increment를 확인할 수 없습니다."
            if message not in self.warnings:
                self.warnings.append(message)
        return int(line[24:36]), _digits(line[73:75])

    def _field(self, header, count, digits):
        line = self._next()
        if not line.startswith(" -4"):
            raise ValueError("100C 다음의 필드 헤더(-4)가 없습니다.")
        name, component_count = line[5:13].strip(), int(line[13:18])
        if not name or component_count <= 0:
            raise ValueError("FRD 필드 이름·성분 수가 잘못되었습니다.")
        if any(result.name == name for result in self.frames[-1].fields):
            self.frames.append(_Frame(dict(self.frames[-1].metadata)))
            self.warnings.append(f"같은 프레임의 {name}가 반복돼 별도 프레임으로 보존했습니다.")
        result = _Field(name, count, dataset_counter=self.dataset_counter, result_type=int(line[18:23]))
        self.frames[-1].fields.append(result)
        for _ in range(component_count):
            line = self._next()
            if not line.startswith(" -5"):
                raise ValueError(f"{name}: 성분 선언(-5)이 부족합니다.")
            result.components.append({"name": line[5:13].strip(), "menu": int(line[13:18]),
                                      "type": int(line[18:23]), "row": int(line[23:28]),
                                      "column": int(line[28:33]), "exist": int(line[33:38].strip() or "0"),
                                      "definition": line[38:].strip()})
        if result.result_type != 1 or not result.stored or any(c["exist"] not in (0, 1, 2) for c in result.components):
            result.warnings.append(f"{name}: nodal 결과 또는 저장 성분 배치가 지원되지 않습니다.")
            for _ in self._block():
                pass
            return
        node, values, malformed = None, [], False

        def finish():
            if node is None:
                return
            if malformed or len(values) != len(result.stored) or node in result.values or node <= 0:
                result.warnings.append(f"{name}: 절점 {node}의 값 수·형식·ID가 잘못되었거나 중복됩니다.")
                return
            result.values[node] = list(values)
            if not all(map(math.isfinite, values)):
                result.warnings.append(f"{name}: 절점 {node}에 유한하지 않은 값이 있습니다.")

        try:
            for line in self._block():
                if line.startswith(" -1"):
                    finish()
                    node, values, malformed = int(line[3:3 + digits]), [], False
                    raw = line[3 + digits:]
                elif line.startswith(" -2") and node is not None:
                    # Result continuations reserve the node-ID column, unlike
                    # element connectivity -2 records. Its width follows FORMAT.
                    if line[3:3 + digits].strip():
                        raise ValueError(f"{name}: 결과 연속 레코드의 절점 칸이 비어 있지 않습니다.")
                    raw = line[3 + digits:]
                else:
                    raise ValueError(f"{name}: 결과 레코드 형식이 잘못되었습니다.")
                try:
                    values.extend(_number(part) for part in _chunks(raw, 12))
                except ValueError:
                    malformed = True
        finally:
            finish()

    def read(self):
        while True:
            try:
                line = self._next()
            except StopIteration:
                break
            if not line.strip():
                continue
            if self.ended:
                raise ValueError("FRD 종료 표식 뒤에 데이터가 있습니다.")
            if line.startswith("    2C"):
                self._nodes(line)
            elif line.startswith("    3C"):
                self._elements(line)
            elif line.startswith("    1PSTEP"):
                self.dataset_counter = int(line[24:36])
                self.increment, self.step = int(line[36:48]), int(line[48:60])
                self.parameters = {}
            elif line.startswith("    1P"):
                name = re.match(r"\s+1P(\S+)", line)
                if name:
                    self.parameters[name[1]] = line[24:].strip()
            elif line.startswith("  100C"):
                count, digits = self._frame(line)
                self._field(line, count, digits)
            elif line.strip() == "9999":
                self.ended = True
            elif line.startswith((" -1", " -2", " -3", " -4", " -5")):
                raise ValueError("FRD 블록 밖에 결과·geometry 레코드가 있습니다.")
        if not self.ended:
            raise ValueError("FRD 종료 표식(9999)이 없습니다.")


def _field_metadata(result: _Field, nodes: dict, checkpoint) -> dict:
    unknown = sorted(result.values.keys() - nodes.keys())
    missing = sorted(nodes.keys() - result.values.keys())
    nonfinite = [node for node in _checked(nodes, checkpoint) if node in result.values and not all(map(math.isfinite, result.values[node]))]
    warnings = list(result.warnings)
    if len(result.values) != result.declared_count:
        warnings.append(f"{result.name}: 레코드 수 {len(result.values)}가 선언 수 {result.declared_count}와 다릅니다.")
    if missing or unknown or nonfinite:
        warnings.append(f"{result.name}: 일부 geometry 절점의 결과가 없거나 대응·유한성을 확인할 수 없습니다.")
    ranges = []
    for i, component in enumerate(result.stored):
        values = [result.values[node][i] for node in _checked(nodes, checkpoint) if node in result.values and math.isfinite(result.values[node][i])]
        ranges.append({"name": component["name"], "minimum": min(values) if values else None,
                       "maximum": max(values) if values else None, "finite_count": len(values),
                       "missing_count": len(nodes) - len(values)})
    return {"name": result.name, "components": result.components,
            "dataset_counter": result.dataset_counter, "result_type": result.result_type,
            "coverage": {"declared_count": result.declared_count, "record_count": len(result.values),
                         "mesh_node_count": len(nodes), "missing_node_ids": missing,
                         "unknown_node_ids": unknown, "nonfinite_node_ids": nonfinite},
            "summary": {"components": ranges}, "status": "partial" if warnings else "readable",
            "warnings": warnings}


def _summary(frame: _Frame, nodes: dict, checkpoint) -> dict:
    displacement = next((f for f in frame.fields if f.name == "DISP"), None)
    maximum, count, overflow = None, 0, 0
    if displacement is not None:
        components = displacement.stored
        indices = [next((i for i, c in enumerate(components) if c["name"] == name and c["type"] == 2
                         and c["row"] == row and c["column"] == 0), None)
                   for row, name in enumerate(("D1", "D2", "D3"), 1)]
        if all(index is not None for index in indices):
            norms = [math.hypot(*(row[i] for i in indices)) for node, row in _checked(displacement.values.items(), checkpoint)
                     if node in nodes and all(math.isfinite(row[i]) for i in indices)]
            finite = [value for value in norms if math.isfinite(value)]
            maximum, count, overflow = max(finite, default=None), len(finite), len(norms) - len(finite)
    return {"max_displacement": maximum, "displacement_node_count": count,
            "displacement_norm_overflow_count": overflow, "mesh_node_count": len(nodes),
            "units": {}, "reaction_totals": None}


def _array(parent, name, values, components=1, *, integer=False, component_names=(), checkpoint=lambda: None):
    values = list(_checked(values, checkpoint))
    attributes = {"type": "Int64" if integer else "Float64", "Name": name,
                  "NumberOfComponents": str(components), "format": "ascii"}
    # VTK ASCII numeric extraction is not portable for NaN. Inline uncompressed
    # Float64 arrays use the XML UInt32 length header, retaining every missing value.
    if not integer and any(not math.isfinite(value) for value in values):
        payload = struct.pack("<" + str(len(values)) + "d", *values)
        text = base64.b64encode(struct.pack("<I", len(payload)) + payload).decode("ascii")
        attributes["format"] = "binary"
    else:
        text = " ".join(str(value) if integer else format(value, ".17g") for value in _checked(values, checkpoint))
    for i, component in enumerate(component_names):
        attributes[f"ComponentName{i}"] = component
    checkpoint()
    array = ET.SubElement(parent, "DataArray", attributes)
    array.text = text
    return array


def _write_xml(root, path: Path):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".frd-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            ET.ElementTree(root).write(stream, encoding="utf-8", xml_declaration=True)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_vtu(reader: _Reader, frame: _Frame, index: int, path: Path):
    from functools import partial

    array = partial(_array, checkpoint=reader.checkpoint)
    root = ET.Element("VTKFile", type="UnstructuredGrid", version="0.1", byte_order="LittleEndian", header_type="UInt32")
    grid = ET.SubElement(root, "UnstructuredGrid")
    data = ET.SubElement(grid, "FieldData")
    for key in ("step", "increment", "value", "analysis_type", "kode"):
        if frame.metadata[key] is not None:
            array(data, "Native_" + key, [frame.metadata[key]], integer=key != "value").set("NumberOfTuples", "1")
    array(data, "FrameIndex", [index], integer=True).set("NumberOfTuples", "1")
    piece = ET.SubElement(grid, "Piece", NumberOfPoints=str(len(reader.nodes)), NumberOfCells=str(len(reader.elements)))
    points = ET.SubElement(piece, "Points")
    array(points, "Coordinates", (v for row in reader.nodes.values() for v in row), 3)
    cells = ET.SubElement(piece, "Cells")
    indices = {node: i for i, node in enumerate(reader.nodes)}
    connections, offsets, types = [], [], []
    for topology, group, material, nodes in _checked(reader.elements.values(), reader.checkpoint):
        connections.extend(indices[nodes[i]] for i in _PERMUTATIONS.get(topology, range(len(nodes))))
        offsets.append(len(connections))
        types.append(_CELLS[topology][1])
    array(cells, "connectivity", connections, integer=True)
    array(cells, "offsets", offsets, integer=True)
    array(cells, "types", types, integer=True)
    point_data = ET.SubElement(piece, "PointData")
    array(point_data, "NativeNodeId", reader.nodes, integer=True)
    for result in frame.fields:
        if not result.values:
            continue
        array(point_data, result.name,
               (v for node in reader.nodes for v in result.values.get(node, [math.nan] * len(result.stored))),
               len(result.stored), component_names=[c["name"] for c in result.stored])
        array(point_data, "Coverage_" + result.name,
               (int(node in result.values and all(map(math.isfinite, result.values[node]))) for node in reader.nodes), integer=True)
    cell_data = ET.SubElement(piece, "CellData")
    array(cell_data, "NativeElementId", reader.elements, integer=True)
    for offset, name in enumerate(("NativeElementType", "NativeGroup", "NativeMaterial")):
        array(cell_data, name, (row[offset] for row in reader.elements.values()), integer=True)
    reader.checkpoint()
    _write_xml(root, path)
    reader.checkpoint()


def collect_results(run_dir: Path, *, check_cancelled=None) -> dict:
    """Collect model.frd into run-local exchange files; never alter solver status.

    Original FRD/DAT files are read-only evidence. This reader assigns no units,
    assumes no displacement request and never interprets FORC as reactions.
    """
    def checkpoint():
        if check_cancelled is not None:
            try:
                check_cancelled()
            except BaseException as error:
                raise _Abort(error) from error
    try:
        return _collect_results(Path(run_dir).resolve(), checkpoint)
    except _Abort as abort:
        raise abort.error
    except OSError as error:
        return {"status": "failed", "frames": [], "exchange_files": [], "summary": {},
                "warnings": [f"원래 결과 파일을 읽지 못했습니다: {error}"]}


def _collect_dat(path, checkpoint):
    from ..solvers.calculix import parse_dat

    answer = {"status": "missing", "frame": None, "selection": "final_frame",
              "sets": {}, "warnings": [], "max_displacement": None,
              "displacement_node_count": 0, "units": {}}
    if not path.is_file() or path.stat().st_size == 0:
        return answer
    checkpoint()
    try:
        parsed = parse_dat(path, check_cancelled=checkpoint)
    except (OSError, ValueError) as error:
        answer["status"], answer["warnings"] = "failed", [f"DAT 판독 제한: {error}"]
        return answer
    checkpoint()
    answer["warnings"] = list(parsed.errors)
    if parsed.frame is not None:
        answer["frame"] = dict(zip(("step", "increment", "value"), parsed.frame))
    for values, key in ((parsed.displacements_by_set, "displacements"),
                        (parsed.forces_by_set, "nodal_forces"),
                        (parsed.totals_by_set, "total_force"),
                        (parsed.displacement_totals_by_set, "total_displacement")):
        for name, block in _checked(values.items(), checkpoint):
            answer["sets"].setdefault(name, {})[key] = block
    if answer["sets"]:
        answer["status"] = "partial" if parsed.errors else "readable"
        answer["max_displacement"] = parsed.max_displacement
        answer["displacement_node_count"] = len(parsed.displacements)
        if parsed.max_displacement is not None and not math.isfinite(parsed.max_displacement):
            answer["status"] = "partial"
            answer["max_displacement"] = None
            answer["warnings"].append("DAT 변위 크기가 부동소수 표현 범위를 넘습니다.")
    else:
        answer["status"] = "failed" if parsed.errors else "unsupported"
        answer["warnings"].append("DAT의 알려진 전역 절점 변위·힘·합계만 판독합니다. 다른 필드는 원본에서 확인하세요.")
    return answer


def _collect_results(run_dir, checkpoint):
    checkpoint()
    answer = {"status": "missing", "frames": [], "exchange_files": [], "warnings": [], "summary": {}}
    answer["dat"] = _collect_dat(run_dir / "model.dat", checkpoint)

    def dat_only():
        dat = answer["dat"]
        answer["warnings"].extend(dat["warnings"])
        if dat["status"] in {"readable", "partial"}:
            answer["status"] = dat["status"]
            answer["summary"] = {"source": "model.dat", "selection": "final_DAT_frame", "frame": dat["frame"],
                                 "max_displacement": dat["max_displacement"], "displacement_node_count": dat["displacement_node_count"],
                                 "mesh_node_count": None, "reaction_totals": None, "units": {}}
            answer["warnings"].append("DAT의 알려진 집합 결과만 판독했습니다. geometry 교환 파일은 없습니다.")
        elif dat["status"] != "missing":
            answer["status"] = dat["status"]
        return answer

    source = run_dir / "model.frd"
    if not source.is_file() or source.stat().st_size == 0:
        answer["warnings"].append("model.frd가 없거나 비어 있습니다. DAT 등 원래 출력 파일은 그대로 보존됩니다.")
        return dat_only()
    reader, failure, unsupported = None, None, False
    try:
        with source.open(encoding="utf-8") as stream:
            reader = _Reader(stream, checkpoint)
            reader.read()
    except (ValueError, OSError, StopIteration) as error:
        failure = f"FRD 판독 제한 (행 {reader.line_number if reader else '?'}): {error}"
        unsupported = isinstance(error, (_Unsupported, UnicodeError))
    if reader is None or not reader.nodes or not reader.elements or not reader.frames:
        if reader is not None and not reader.frames and reader.ended and failure is None:
            answer["warnings"].append("FRD에 결과 프레임이 없습니다.")
            return dat_only()
        answer["status"] = "unsupported" if unsupported else "failed"
        answer["warnings"] = [failure or "FRD geometry 또는 결과 프레임이 없습니다."]
        return answer
    if unsupported:
        # A geometry/encoding limitation cannot be exported as a reduced mesh.
        answer["status"] = "unsupported"
        answer["warnings"] = [failure]
        return answer
    answer["warnings"] = list(reader.warnings) + ([failure] if failure else [])
    answer["status"] = "partial" if answer["warnings"] else "readable"
    native_values = [frame.metadata["value"] for frame in reader.frames]
    ordinal = any(b <= a for a, b in zip(native_values, native_values[1:]))
    if ordinal:
        answer["warnings"].append("PVD의 좌표는 프레임 순번입니다. 원래 값·step·increment는 각 프레임에 보존됩니다.")
    output = run_dir / "results"
    try:
        output.mkdir(exist_ok=True)
        if not output.resolve().is_relative_to(run_dir):
            raise OSError("결과 교환 경로가 실행 폴더 밖을 가리킵니다.")
        collection = ET.Element("VTKFile", type="Collection", version="0.1", byte_order="LittleEndian")
        datasets = ET.SubElement(collection, "Collection")
        for index, frame in enumerate(reader.frames):
            checkpoint()
            fields = [_field_metadata(result, reader.nodes, checkpoint) for result in frame.fields]
            warnings = [message for result in fields for message in result["warnings"]]
            if warnings or not fields:
                answer["status"] = "partial"
                answer["warnings"].extend(warnings or [f"프레임 {index}: 판독된 필드가 없습니다."])
            filename = f"frame-{index:06d}.vtu"
            metadata = {**frame.metadata, "index": index, "fields": fields,
                        "summary": _summary(frame, reader.nodes, checkpoint), "vtu_file": f"results/{filename}",
                        "pvd_timestep": index if ordinal else frame.metadata["value"]}
            if metadata["summary"]["displacement_norm_overflow_count"]:
                answer["status"] = "partial"
                answer["warnings"].append(f"프레임 {index}: 일부 변위 크기가 부동소수 표현 범위를 넘습니다.")
            _write_vtu(reader, frame, index, output / filename)
            answer["frames"].append(metadata)
            answer["exchange_files"].append(metadata["vtu_file"])
            ET.SubElement(datasets, "DataSet", timestep=str(metadata["pvd_timestep"]), group="", part="0", file=filename)
        if len(reader.frames) > 1:
            checkpoint()
            _write_xml(collection, output / "results.pvd")
            checkpoint()
            answer["exchange_files"].insert(0, "results/results.pvd")
        final = answer["frames"][-1]
        answer["summary"] = {**final["summary"], "selection": "last_frame_in_file", "frame_index": final["index"],
                             **{key: final[key] for key in ("step", "increment", "value", "analysis_type")},
                             "pvd_coordinate": "frame_index" if ordinal else "native_value"}
    except (OSError, ValueError, struct.error) as error:
        answer["status"] = "failed"
        answer["exchange_files"] = []
        answer["warnings"].append(f"결과 교환 파일을 쓰지 못했습니다: {error}")
    return answer
