"""Read native field arrays without changing their IDs, basis, or sampling."""

from __future__ import annotations

import base64
import math
import re
from dataclasses import dataclass, field
from itertools import islice
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np

from . import ResultError, no_checkpoint

_WIDTHS = {1: 1, 3: 2, 5: 3, 8: 4, 9: 4, 10: 4, 11: 8, 12: 8, 13: 6,
           14: 5, 21: 3, 22: 6, 23: 8, 24: 10, 25: 20, 26: 15, 27: 13}
_SHELLS = {5, 9}
_SOLIDS = {10, 11, 12, 13, 14}
_TENSOR = re.compile(r"3DELEM_(?:Stress|Strain)|2DELEM_(?:Stress|Strain)_\((?:upper|lower|layer__[1-9][0-9]*)\)_?")
_INTEGER = re.compile(r"(?:u?int(?:8|16|32|64)?|(?:unsigned_)?(?:char|short|int|long)(?:_long)?|vtktypeint64)", re.I)
_XML_TYPES = {f"{prefix}{bits}": f"{kind}{bits // 8}"
              for prefix, kind in (("Float", "f"), ("Int", "i"), ("UInt", "u"))
              for bits in ((32, 64) if prefix == "Float" else (8, 16, 32, 64))}


class _VTKEncoding(ResultError):
    def __init__(self, mode):
        super().__init__("UNSUPPORTED_CAPABILITY", f"VTU encoding {mode} requires the optional VTK reader")


@dataclass
class FieldGrid:
    points: np.ndarray
    cells: tuple[tuple[int, ...], ...]
    cell_types: np.ndarray
    point_data: dict
    cell_data: dict
    field_data: dict
    profile: str
    time: float | None = None
    node_ids: np.ndarray | None = None
    element_ids: np.ndarray | None = None
    part_ids: np.ndarray | None = None
    component_names: dict = field(default_factory=dict)

    @property
    def reference_points(self) -> np.ndarray:
        if self.profile != "OC-ANIM-1":
            raise ResultError("UNSUPPORTED_BASIS", "undeformed animation geometry is unavailable")
        return self.points - self.point_data["Displacement"]

    @property
    def family_masks(self) -> dict:
        return {"2DELEM": np.isin(self.cell_types, list(_SHELLS)),
                "3DELEM": np.isin(self.cell_types, list(_SOLIDS))}


def _put(arrays, name, array):
    if not name or name in arrays:
        raise ResultError("INVALID_DATASET", f"missing or duplicate array name {name!r}")
    arrays[name] = array


def _legacy(path, check):
    with path.open(encoding="ascii") as stream:
        if (stream.readline().strip() != "# vtk DataFile Version 3.0" or
                not stream.readline() or stream.readline().strip() != "ASCII"):
            raise ResultError("INVALID_DATASET", "expected legacy VTK 3.0 ASCII")

        def words():
            for line in stream:
                check()
                yield from line.split()

        tokens = iter(words())

        def word(expected=None):
            value = next(tokens)
            if expected is not None and value != expected:
                raise ValueError(f"expected {expected}, found {value}")
            return value

        def count():
            value = int(word())
            if value < 0:
                raise ValueError("negative VTK array size")
            return value

        def array(rows, components, datatype):
            if components < 1:
                raise ValueError("missing array components")
            size = rows * components
            integer = bool(_INTEGER.fullmatch(datatype))
            if not integer and datatype not in {"float", "double"}:
                raise ValueError(f"unsupported data type {datatype}")
            out = np.fromiter(map(int if integer else float, islice(tokens, size)),
                              dtype=np.int64 if integer else np.float64)
            return out.reshape(rows, components)

        word("DATASET")
        word("UNSTRUCTURED_GRID")
        points = types = cells = None
        positional, elemental, metadata = {}, {}, {}
        arrays, rows = metadata, None
        for key in tokens:
            if key == "POINTS" and points is None:
                points = array(count(), 3, word())
            elif key == "CELLS" and cells is None:
                n, size = count(), count()
                flat = array(size, 1, "int").ravel()
                cells_list = []
                offset = 0
                for _ in range(n):
                    check()
                    width = int(flat[offset])
                    end = offset + width + 1
                    if width < 1 or end > size:
                        raise ValueError("invalid legacy connectivity width")
                    cells_list.append(tuple(map(int, flat[offset + 1:end])))
                    offset = end
                if offset != size:
                    raise ValueError("invalid legacy connectivity size")
                cells = tuple(cells_list)
            elif key == "CELL_TYPES" and types is None:
                types = array(count(), 1, "int").ravel()
            elif key in {"POINT_DATA", "CELL_DATA"}:
                arrays, rows = (positional if key == "POINT_DATA" else elemental), count()
                tuples = points if key == "POINT_DATA" else cells
                if tuples is None or rows != len(tuples):
                    raise ValueError(f"{key} tuple count disagrees with geometry")
            elif key == "FIELD":
                word()
                for _ in range(count()):
                    name, width, length, dtype = word(), count(), count(), word()
                    if rows is not None and length != rows:
                        raise ValueError(f"{name} tuple count disagrees with association")
                    _put(arrays, name, array(length, width, dtype))
            elif key in {"SCALARS", "VECTORS", "TENSORS"} and rows is not None:
                name, dtype = word(), word()
                if key == "SCALARS":
                    token = word()
                    width = 1 if token == "LOOKUP_TABLE" else int(token)
                    if token != "LOOKUP_TABLE":
                        word("LOOKUP_TABLE")
                    word()
                else:
                    width = 3 if key == "VECTORS" else 9
                _put(arrays, name, array(rows, width, dtype))
            else:
                raise ValueError(f"unexpected legacy VTK section {key!r}")
        if points is None or cells is None or types is None:
            raise ValueError("missing VTK topology")
        return FieldGrid(points, cells, types, positional, elemental, metadata, "OC-ANIM-1")


def _xml_grid(path, check):
    parser = ET.XMLParser()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            check()
            parser.feed(chunk)
    root = parser.close()
    grid = root.find("UnstructuredGrid")
    if root.tag != "VTKFile" or root.get("type") != "UnstructuredGrid" or grid is None:
        raise ValueError("expected VTU UnstructuredGrid")
    pieces = grid.findall("Piece")
    if len(pieces) != 1:
        raise ResultError("UNSUPPORTED_CAPABILITY", "VTU requires a single piece")
    piece = pieces[0]
    order = {"LittleEndian": "<", "BigEndian": ">"}[root.get("byte_order", "LittleEndian")]

    def array(element, rows):
        check()
        components = int(element.get("NumberOfComponents", "1"))
        if components < 1:
            raise ValueError("invalid VTU component count")
        dtype = np.dtype(order + _XML_TYPES[element.attrib["type"]])
        mode = element.get("format", "ascii")
        if mode == "ascii":
            convert = float if dtype.kind == "f" else int
            values = []
            for line in (element.text or "").splitlines():
                check()
                values.extend(map(convert, line.split()))
            # Read IDs without rounding or narrowing to the producer's declared width.
            data = np.array(values, dtype=np.float64 if dtype.kind == "f" else np.int64)
        elif mode == "binary" and not root.get("compressor"):
            raw = base64.b64decode("".join((element.text or "").split()), validate=True)
            header = np.dtype(order + {"UInt32": "u4", "UInt64": "u8"}[root.get("header_type", "UInt32")])
            payload = raw[header.itemsize:]
            if len(raw) < header.itemsize or np.frombuffer(raw, header, count=1)[0] != len(payload):
                raise ValueError("invalid VTU binary payload length")
            data = np.frombuffer(payload, dtype=dtype)
        else:
            raise _VTKEncoding(mode)
        count = rows if rows is not None else int(element.get("NumberOfTuples", "-1"))
        return data.reshape(count, components)

    def arrays(parent, tag, rows):
        result = {}
        for element in parent.findall(f"{tag}/DataArray"):
            _put(result, element.get("Name"), array(element, rows))
        return result

    n, m = int(piece.attrib["NumberOfPoints"]), int(piece.attrib["NumberOfCells"])
    if min(n, m) <= 0:
        raise ValueError("empty VTU geometry")
    point_coords = piece.findall("Points/DataArray")
    if len(point_coords) != 1:
        raise ValueError("expected one VTU coordinate array")
    points = array(point_coords[0], n)
    cell_data = arrays(piece, "Cells", None)
    flat, offsets, types = (cell_data[name] for name in ("connectivity", "offsets", "types"))
    if any(data.shape[1] != 1 or data.dtype.kind not in "iu" for data in (flat, offsets, types)):
        raise ValueError("invalid VTU topology arrays")
    if len(offsets) != m or len(types) != m or offsets[-1, 0] != len(flat):
        raise ValueError("invalid VTU connectivity size")
    cells = []
    start = 0
    for end in offsets[:, 0]:
        check()
        if end <= start or end > len(flat):
            raise ValueError("invalid VTU cell offset")
        cells.append(tuple(map(int, flat[start:end, 0])))
        start = end
    metadata = arrays(grid, "FieldData", None)
    for name, value in arrays(piece, "FieldData", None).items():
        _put(metadata, name, value)
    component_names = {
        a.get("Name"): tuple(a.get(f"ComponentName{i}") for i in range(int(a.get("NumberOfComponents", "1"))))
        for a in piece.findall("PointData/DataArray")
    }
    return FieldGrid(points, tuple(cells), types.ravel(), arrays(piece, "PointData", n),
                     arrays(piece, "CellData", m), metadata, "VTU-1", component_names=component_names)


def _vtk_xml_grid(path, check):
    from vtkmodules.util.numpy_support import vtk_to_numpy
    from vtkmodules.vtkIOXML import vtkXMLUnstructuredGridReader

    reader = vtkXMLUnstructuredGridReader()
    reader.SetFileName(str(path))
    cancelled = []

    def progress(caller, _):
        try:
            check()
        except Exception as exc:
            cancelled.append(exc)
            caller.SetAbortExecute(True)

    reader.AddObserver("ProgressEvent", progress)
    check()
    reader.Update()
    if cancelled:
        raise cancelled[0]
    check()
    grid = reader.GetOutput()
    if reader.GetErrorCode() or grid.GetPoints() is None:
        raise ValueError("VTK could not read the XML dataset")
    names = {}

    def arrays(attributes, *, point=False):
        data = {}
        for i in range(attributes.GetNumberOfArrays()):
            check()
            values = attributes.GetArray(i)
            if values is None:
                continue
            width = values.GetNumberOfComponents()
            name = values.GetName()
            _put(data, name, vtk_to_numpy(values).reshape(-1, width).copy())
            if point:
                names[name] = tuple(values.GetComponentName(j) for j in range(width))
        return data

    cells, types = [], []
    for index in range(grid.GetNumberOfCells()):
        check()
        cell = grid.GetCell(index)
        cells.append(tuple(cell.GetPointId(i) for i in range(cell.GetNumberOfPoints())))
        types.append(grid.GetCellType(index))
    return FieldGrid(vtk_to_numpy(grid.GetPoints().GetData()).copy(), tuple(cells), np.array(types),
                     arrays(grid.GetPointData(), point=True), arrays(grid.GetCellData()),
                     arrays(grid.GetFieldData()), "VTU-1", component_names=names)


def _read(path, parser, check):
    try:
        return parser(Path(path), check)
    except ResultError:
        raise
    except (ValueError, OSError, KeyError, IndexError, StopIteration, OverflowError, ET.ParseError) as exc:
        raise ResultError("INVALID_DATASET", f"{path}: {exc}") from exc


def _ids(arrays, name, rows, *, unique=True):
    data = arrays.get(name)
    if data is None:
        raise ResultError("MISSING_ID", name)
    if (data.shape != (rows, 1) or data.dtype.kind not in "iu" or np.any(data <= 0)
            or (unique and np.unique(data).size != rows)):
        raise ResultError("INVALID_DATASET", f"invalid {name}")
    return data.ravel()


def _geometry(grid, check):
    n, m = len(grid.points), len(grid.cells)
    if not n or not m or grid.points.shape != (n, 3) or not np.isfinite(grid.points).all() or len(grid.cell_types) != m:
        raise ResultError("INVALID_DATASET", "empty or invalid geometry")
    for cell, cell_type in zip(grid.cells, grid.cell_types):
        check()
        if (_WIDTHS.get(cell_type) != len(cell) or len(set(cell)) != len(cell)
                or min(cell) < 0 or max(cell) >= n):
            raise ResultError("INVALID_DATASET", f"invalid connectivity for VTK cell type {cell_type}")


def tensor_fields(grid):
    return {name for name in grid.cell_data if _TENSOR.fullmatch(name)}


def _time(grid, *, required):
    data = grid.field_data.get("TIME")
    if data is None and required:
        raise ResultError("MISSING_TIME", "FIELD TIME is required")
    if data is not None:
        if data.shape != (1, 1) or not np.isfinite(data[0, 0]):
            raise ResultError("INVALID_DATASET", "TIME must be a finite scalar")
        grid.time = float(data[0, 0])


def _validate_identity(grid, expected):
    if "node_coordinates" in expected:
        oracle = {int(key): value for key, value in expected["node_coordinates"].items()}
        if set(oracle) != set(grid.node_ids):
            raise ResultError("OBSERVABLE_MISMATCH", "NODE_ID set")
        for node, xyz in zip(grid.node_ids, grid.reference_points):
            target = np.asarray(oracle[node])
            if target.shape != (3,) or not np.allclose(xyz, target, rtol=1e-6, atol=1e-8):
                raise ResultError("OBSERVABLE_MISMATCH", f"reference coordinates for node {node}")
    if "elements" in expected:
        oracle = {int(key): value for key, value in expected["elements"].items()}
        if set(oracle) != set(grid.element_ids):
            raise ResultError("OBSERVABLE_MISMATCH", "ELEMENT_ID set")
        for element, part, kind, cell in zip(grid.element_ids, grid.part_ids, grid.cell_types, grid.cells):
            actual = {"part_id": int(part), "cell_type": int(kind),
                      "node_ids": [int(grid.node_ids[i]) for i in cell]}
            if actual != oracle[element]:
                raise ResultError("OBSERVABLE_MISMATCH", f"topology or part for element {element}")


def read_vtk(path, *, exact_required_set=None, expected=None, check_cancelled=None) -> FieldGrid:
    """Validate OC-ANIM-1 and bind optional source geometry by native ID."""
    check = check_cancelled or no_checkpoint
    grid = _read(path, _legacy, check)
    # anim_to_vtk emits a repeated fourth connectivity index for triangles.
    if len(grid.cells) == len(grid.cell_types):
        grid.cells = tuple(tuple(dict.fromkeys(cell)) if kind == 5 and len(cell) == 4 else cell
                           for kind, cell in zip(grid.cell_types, grid.cells))
    _geometry(grid, check)
    _time(grid, required=True)
    n, m = len(grid.points), len(grid.cells)
    grid.node_ids = _ids(grid.point_data, "NODE_ID", n)
    grid.element_ids = _ids(grid.cell_data, "ELEMENT_ID", m)
    grid.part_ids = _ids(grid.cell_data, "PART_ID", m, unique=False)
    for name in ("Displacement", "Velocity"):
        if name not in grid.point_data or grid.point_data[name].shape != (n, 3):
            raise ResultError("PARTIAL_REQUIRED_FIELD", name)
    masks = grid.family_masks
    tensors = tensor_fields(grid)
    required = set(exact_required_set or ())
    if exact_required_set is None:
        if masks["2DELEM"].any():
            required.update(("2DELEM_Stress_(upper)", "2DELEM_Stress_(lower)", "2DELEM_Stress_(layer__1)"))
        if masks["3DELEM"].any():
            required.add("3DELEM_Stress")
    available = grid.point_data.keys() | grid.cell_data.keys() | grid.field_data.keys()
    missing = {name for name in required if _TENSOR.fullmatch(name) and name not in tensors and name + "_" not in tensors}
    missing.update(name for name in required if not _TENSOR.fullmatch(name) and name not in available)
    if missing:
        raise ResultError("PARTIAL_REQUIRED_FIELD", ", ".join(sorted(missing)))
    for arrays in (grid.point_data, grid.cell_data, grid.field_data):
        for name, data in arrays.items():
            check()
            # Coverage masks are decoded separately from physical values.
            if not np.isfinite(data).all():
                raise ResultError("INVALID_DATASET", f"non-finite values in {name}")
            if name.startswith("Coverage_"):
                covered = name[9:]
                if covered in required or covered.rstrip("_") in required or covered in {"Displacement", "Velocity"}:
                    mask = masks[covered[:6]] if covered in tensors else slice(None)
                    if data.shape[1] != 1 or np.any(data[mask] != 1):
                        raise ResultError("PARTIAL_REQUIRED_FIELD", name)
    for family, mask in masks.items():
        explicit = grid.cell_data.get(family)
        if explicit is not None and (explicit.shape != (m, 1) or not np.array_equal(explicit[:, 0], mask)):
            raise ResultError("OBSERVABLE_MISMATCH", f"{family} mask disagrees with cell topology")
    for name in tensors:
        data = grid.cell_data[name]
        if data.shape != (m, 9):
            raise ResultError("INVALID_DATASET", f"{name} must have nine components")
        mask = masks[name[:6]]
        if np.any(data[~mask]):
            raise ResultError("OBSERVABLE_MISMATCH", f"{name} outside its element family")
        tensor = data[mask].reshape(-1, 3, 3)
        transpose = tensor.transpose(0, 2, 1)
        if np.any(np.abs(tensor - transpose) > 1e-8 + 1e-6 * np.maximum(np.abs(tensor), np.abs(transpose))):
            raise ResultError("INVALID_DATASET", f"{name} is not symmetric")
    if not np.isfinite(grid.reference_points).all():
        raise ResultError("INVALID_DATASET", "non-finite reference coordinates")
    _validate_identity(grid, expected or {})
    return grid


def read_vtu(path, *, check_cancelled=None) -> FieldGrid:
    """Read the registered ASCII and inline binary VTU formats used by Workbench."""
    check = check_cancelled or no_checkpoint
    try:
        grid = _read(path, _xml_grid, check)
    except _VTKEncoding as exc:
        try:
            grid = _read(path, _vtk_xml_grid, check)
        except ImportError:
            raise exc from None
    _geometry(grid, check)
    _time(grid, required=False)
    def id_name(arrays, producer, standard):
        if producer in arrays and standard in arrays:
            if not np.array_equal(arrays[producer], arrays[standard]):
                raise ResultError("OBSERVABLE_MISMATCH", f"{producer} conflicts with {standard}")
        return producer if producer in arrays else standard

    grid.node_ids = _ids(grid.point_data, id_name(grid.point_data, "NativeNodeId", "NODE_ID"), len(grid.points))
    grid.element_ids = _ids(grid.cell_data, id_name(grid.cell_data, "NativeElementId", "ELEMENT_ID"), len(grid.cells))
    return grid


def shell_basis(points) -> np.ndarray:
    """Return the current element axes as rows, per the triangle/quad convention."""
    p = np.asarray(points, dtype=float)
    if p.shape not in {(3, 3), (4, 3)} or not np.isfinite(p).all():
        raise ResultError("UNSUPPORTED_BASIS", "expected three or four finite shell coordinates")
    span = np.linalg.norm(p - np.roll(p, -1, axis=0), axis=1).max()
    if not math.isfinite(span) or span == 0:
        raise ResultError("UNSUPPORTED_BASIS", "degenerate shell")
    p = (p - p[0]) / span

    def unit(vector):
        norm = np.linalg.norm(vector)
        if not math.isfinite(norm) or norm < 1e-12:
            raise ResultError("UNSUPPORTED_BASIS", "degenerate shell direction")
        return vector / norm

    if len(p) == 3:
        x = unit(p[1])
        n = unit(np.cross(p[1], p[2]))
    else:
        diagonal = p[3] - p[1]
        n = unit(np.cross(p[2], diagonal))
        x = unit(p[2]) - unit(diagonal)
        x = unit(x - np.dot(x, n) * n)
    return np.array([x, np.cross(n, x), n])


def von_mises(tensor):
    t = np.asarray(tensor, dtype=float)
    xx, yy, zz = (t[..., i, i] for i in range(3))
    xy, yz, xz = (t[..., i, j] for i, j in ((0, 1), (1, 2), (0, 2)))
    return np.sqrt(.5 * ((xx - yy)**2 + (yy - zz)**2 + (zz - xx)**2) + 3 * (xy**2 + yz**2 + xz**2))


def query_field(grid, field_id, *, ids=None, component=None, operator="values", basis="global",
                units=None, check_cancelled=None):
    """Query native rows in the requested ID order and return numeric values and shape."""
    check = check_cancelled or no_checkpoint
    if basis not in {"global", "element_local"}:
        raise ResultError("UNSUPPORTED_BASIS", str(basis))
    point = (field_id in grid.point_data and not field_id.startswith("Coverage_")
             and field_id not in {"NODE_ID", "NativeNodeId"})
    if point:
        entity_ids, data, allowed = grid.node_ids, grid.point_data[field_id], np.ones(len(grid.points), dtype=bool)
        if basis != "global":
            raise ResultError("UNSUPPORTED_BASIS", f"{field_id} has a global nodal basis")
    elif grid.profile == "OC-ANIM-1" and _TENSOR.fullmatch(field_id) and field_id in grid.cell_data:
        entity_ids, data = grid.element_ids, grid.cell_data[field_id]
        allowed = grid.family_masks[field_id[:6]]
    else:
        raise ResultError("UNSUPPORTED_FIELD", field_id)
    id_rows = {int(entity): index for index, entity in enumerate(entity_ids)}
    if ids is None:
        indices = np.flatnonzero(allowed)
    else:
        if (not isinstance(ids, (tuple, list)) or any(type(entity) is not int for entity in ids)
                or len(ids) != len(set(ids)) or any(entity not in id_rows for entity in ids)):
            raise ResultError("OBSERVABLE_MISMATCH", "unknown, duplicated, or invalid selection ID")
        indices = np.array([id_rows[entity] for entity in ids], dtype=np.intp)
    if not len(indices) or not allowed[indices].all():
        raise ResultError("OBSERVABLE_MISMATCH", "selection crosses the field's association or element family")
    coverage = (grid.point_data if point else grid.cell_data).get("Coverage_" + field_id)
    if coverage is not None and (coverage.shape[1] != 1 or np.any(coverage[indices, 0] != 1)):
        raise ResultError("COVERAGE_INCOMPLETE", field_id)
    values = data[indices]
    if not np.isfinite(values).all():
        raise ResultError("COVERAGE_INCOMPLETE", f"non-finite selected {field_id}")
    if not point:
        values = values.reshape(-1, 3, 3)
        if field_id.startswith("2DELEM") and basis == "global":
            rotation = []
            for index in indices:
                check()
                rotation.append(shell_basis(grid.points[list(grid.cells[index])]))
            rotation = np.asarray(rotation)
            values = rotation.transpose(0, 2, 1) @ values @ rotation
        elif field_id.startswith("3DELEM") and basis != "global":
            raise ResultError("UNSUPPORTED_BASIS", "solid tensors are stored in the global basis")
        if component and component.lower() == "vm" and "Stress" in field_id:
            values = von_mises(values)
        elif component:
            if len(component) != 2 or any(axis not in "xyz" for axis in component.lower()):
                raise ResultError("UNSUPPORTED_FIELD", f"unknown tensor component {component}")
            row, col = map("xyz".index, component.lower())
            values = values[:, row, col]
    elif component is not None:
        labels = grid.component_names.get(field_id) or ()
        label = next((i for i, name in enumerate(labels) if name and component.casefold() == name.casefold()), None)
        if label is None and values.shape[1] == 3 and component.lower() in ("x", "y", "z"):
            label = "xyz".index(component.lower())
        if label is None:
            raise ResultError("UNSUPPORTED_FIELD", f"{field_id}.{component}")
        values = values[:, label]

    if operator == "norm":
        if not point or values.ndim != 2 or values.shape[1] != 3:
            raise ResultError("UNSUPPORTED_OBSERVABLE", "norm requires a three-component nodal vector")
        declared = (units or {}).get("components")
        if (not units or units.get("unit") in (None, "", "unknown") or
                (declared is not None and tuple(declared) != (units["unit"],) * 3)):
            raise ResultError("UNIT_UNKNOWN", "norm requires matching declared component units")
        values = np.hypot(np.hypot(values[:, 0], values[:, 1]), values[:, 2])
    elif operator in {"min", "max", "mean"}:
        function = {"min": np.min, "max": np.max, "mean": np.mean}[operator]
        values = np.asarray(function(values, axis=0))
    elif operator not in {"identity", "values"}:
        raise ResultError("UNSUPPORTED_OBSERVABLE", operator)
    check()
    if not np.isfinite(values).all():
        raise ResultError("INVALID_DATASET", "numeric operator produced a non-finite result")
    return values, tuple(map(int, entity_ids[indices]))
