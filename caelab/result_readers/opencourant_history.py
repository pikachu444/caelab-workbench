"""Read OC-TH4-1 time histories using TITLES and physical sample coordinates."""

from __future__ import annotations

import csv
import hashlib
import math
import re
from pathlib import Path

import numpy as np

from ..artifact_refs import ArtifactRefV1, DatasetManifestV1, ProducerIdentity, SeriesManifestV1, write_safe_npy_v2
from ..native.run_store import atomic_json
from . import ResultError, no_checkpoint

_PRODUCER = ProducerIdentity("OC-TH4-1", "1")
_STRESS = dict(zip(("SX", "SY", "SZ", "SXY", "SYZ", "SXZ"), ("xx", "yy", "zz", "xy", "yz", "xz")))
_STRAIN = dict(zip(("EPSX", "EPSY", "EPSZ", "EPSXY", "EPSYZ", "EPSXZ"), ("xx", "yy", "zz", "xy", "yz", "xz")))
_STRAIN.update(EPSXX="xx", EPSYY="yy", EPSZZ="zz", EPSYX="xy", EPSZY="yz", EPSZX="xz")
_SOLID = {"DENS": ("density", "density", "current_density"), "VOL": ("volume", "volume", "current_volume"),
          "MASS": ("mass", "mass", "mass"), "ENER": ("energy", "energy", "element_energy"),
          "IE": ("internal_energy_density", "energy_density", "per_initial_volume"),
          "BULK": ("bulk_viscosity", "stress", "native_bulk_viscosity")}
_CODE = re.compile(r"[A-Za-z0-9_*+|/-]+")
_BINDING_KEYS = (
    "schema_version", "column_index", "raw_header", "normalized_header", "group_title", "output_type",
    "entity_id", "entity_title", "variable_code", "source_titles_pair_index", "quantity", "component",
    "measure", "basis", "unit", "operator",
)


def _normalized(value):
    return " ".join(value.split())


def _entity_header(header, group_titles):
    for title in sorted(group_titles, key=lambda name: (-len(name), name)):
        prefix = title + " "
        if header.startswith(prefix):
            match = re.fullmatch(r"([1-9][0-9]*) (.+)", header[len(prefix):])
            if match:
                return title, int(match[1]), match[2]
    return None


def read_titles(titles_path: Path, *, check_cancelled=None) -> dict:
    """Read fixed-width TITLES pairs, retaining bytes and the file digest."""
    check = check_cancelled or no_checkpoint
    try:
        with Path(titles_path).open("rb") as stream:
            lines = []
            for line in stream:
                check()
                lines.append(line)
    except FileNotFoundError as exc:
        raise ResultError("UNSUPPORTED_OBSERVABLE", "T01 TITLES are required") from exc
    raw = b"".join(lines)
    pairs = []
    try:
        lines = iter(line.rstrip(b"\r\n").decode("ascii") for line in lines)
        for line in lines:
            check()
            if len(line) < 10:
                raise ValueError("TITLES type requires ten columns")
            output_type = int(line[:10])
            tail = line[10:]
            if not tail.strip():
                tail = next(lines)
            if len(tail) < 10:
                raise ValueError("TITLES variable code requires ten columns")
            code = tail[:10].strip()
            if not _CODE.fullmatch(code):
                raise ValueError(f"invalid TITLES code {code!r}")
            pairs.append({"output_type": output_type, "variable_code": code, "title": _normalized(tail[10:])})
    except (ValueError, StopIteration) as exc:
        raise ResultError("INVALID_DATASET", f"{titles_path}: {exc}") from exc
    if not pairs:
        raise ResultError("INVALID_DATASET", "empty TITLES")
    return {"pairs": tuple(pairs), "raw_bytes": raw, "sha256": hashlib.sha256(raw).hexdigest()}


def _headers(raw):
    normalized = tuple(map(_normalized, raw))
    if not normalized or normalized[0] != "time":
        raise ResultError("MISSING_TIME", "first CSV column must be time")
    if any(not header for header in normalized) or len(set(normalized)) != len(normalized):
        raise ResultError("INVALID_DATASET", "empty or duplicate normalized CSV header")
    return normalized


def _times(coordinates, *, minimum=1):
    if (coordinates.ndim != 1 or len(coordinates) < minimum or not np.isfinite(coordinates).all()
            or np.any(coordinates[1:] <= coordinates[:-1])):
        raise ResultError("INVALID_DATASET", "sample times must be finite and strictly increasing")


def read_th_csv(csv_path: Path, *, check_cancelled=None) -> dict:
    """Read exact header cells and finite data rows without synthesizing time."""
    check = check_cancelled or no_checkpoint
    try:
        with Path(csv_path).open(encoding="ascii", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            raw_header = tuple(next(reader, ()))
            normalized = _headers(raw_header)
            rows = []
            for index, row in enumerate(reader):
                check()
                if len(row) != len(raw_header):
                    raise ResultError("INVALID_DATASET", f"CSV row {index} has the wrong width")
                rows.append(tuple(map(float, row)))
    except ResultError:
        raise
    except (OSError, ValueError, csv.Error) as exc:
        raise ResultError("INVALID_DATASET", f"{csv_path}: {exc}") from exc
    if not rows:
        raise ResultError("EMPTY_OUTPUT", "CSV contains no history samples")
    values = np.asarray(rows)
    if not np.isfinite(values).all():
        raise ResultError("INVALID_DATASET", "CSV contains a non-finite sample")
    _times(values[:, 0])
    return {"raw_header": raw_header, "normalized_headers": normalized, "times": values[:, 0], "values": values}


def _quantity(kind, code):
    if kind == 1:
        if code in _STRESS:
            return "stress", _STRESS[code], "stress", "Cauchy"
        if code in _STRAIN:
            return "strain", _STRAIN[code], "strain", "unknown"
        if code in _SOLID:
            quantity, dimension, measure = _SOLID[code]
            return quantity, None, dimension, measure
    if kind == 0 and code in {"DX", "DY", "DZ", "VX", "VY", "VZ"}:
        quantity, dimension = ("displacement", "length") if code[0] == "D" else ("velocity", "velocity")
        return quantity, code[1].lower(), dimension, quantity
    return None


def bind_history_columns(titles_meta, raw_headers, *, expected_columns=None, exact_required_set=None,
                         expected_bindings=None, source_bindings=None, solid_formulation=None, units=None) -> tuple[dict, ...]:
    """Match entity columns against the positional TITLES pairs and fixed requests."""
    columns = _bind_history_columns(titles_meta, raw_headers, expected_columns=expected_columns,
                                    exact_required_set=exact_required_set, expected_bindings=expected_bindings,
                                    source_bindings=source_bindings, solid_formulation=solid_formulation, units=units)
    return tuple({key: column[key] for key in _BINDING_KEYS} for column in columns)


def _bind_history_columns(titles_meta, raw_headers, *, expected_columns, exact_required_set,
                          expected_bindings, source_bindings, solid_formulation, units):
    normalized = _headers(raw_headers)
    pairs = titles_meta["pairs"]
    first = len(normalized) - len(pairs)
    if first < 1:
        raise ResultError("INVALID_DATASET", "TITLES count exceeds CSV column count")
    bindings = []
    entity_columns = []
    identities = set()
    requests = [request for request in (expected_bindings, source_bindings) if request is not None]
    keys = {"entity_id", "group_title", "variable_code"}
    for request in requests:
        if (not isinstance(request, (list, tuple)) or any(
                not isinstance(item, dict) or not keys <= item.keys() or
                not item.keys() <= keys | {"entity_title", "output_type"} or type(item["entity_id"]) is not int
                or not isinstance(item["group_title"], str) or not item["group_title"]
                for item in request)):
            raise ResultError("SOURCE_MISMATCH", "invalid source history requests")
    group_titles = {"BRIC", "NODE"} | {item["group_title"] for request in requests for item in request}
    archived_headers = {}
    for request in requests:
        for item in request:
            if item.get("entity_title"):
                group_title, entity_id = item["group_title"], item["entity_id"]
                title_and_code = f"{item['entity_title']} {item['variable_code']}"
                header = _normalized(f"{group_title} {entity_id} {title_and_code}")
                archived_headers.setdefault(header, set()).add((group_title, entity_id, title_and_code))
    for index, header in enumerate(normalized[1:], 1):
        archived = archived_headers.get(header)
        if archived and len(archived) != 1:
            raise ResultError("INVALID_DATASET", f"ambiguous archived history header: {header!r}")
        group = next(iter(archived)) if archived else _entity_header(header, group_titles)
        if index < first:
            if group:
                raise ResultError("INVALID_DATASET", "entity column is missing a TITLES pair")
            entry = _GLOBAL.get(header)
            if entry is None:
                continue
            quantity, dimension = entry
            output_type, entity_id, entity, group_title, pair_index = "Global", None, None, None, None
            code, component, measure, basis = header, None, quantity, "unknown"
        else:
            pair_index = index - first
            pair = pairs[pair_index]
            kind, code, title = pair["output_type"], pair["variable_code"], pair["title"]
            group_kind = {0: "NODE", 1: "BRIC"}.get(kind)
            suffix = " " + code
            if group is None and requests:
                raise ResultError("SOURCE_MISMATCH", f"unexpected history group: {header!r}")
            if (group is None or (not requests and group_kind is not None and group[0] != group_kind)
                    or not group[2].endswith(suffix)):
                raise ResultError("INVALID_DATASET", f"TITLES pair {pair_index} does not bind {header!r}")
            group_title, entity_id, variable_title = group
            entity = variable_title[:-len(suffix)]
            if not entity or (title and title not in {entity, header}):
                raise ResultError("INVALID_DATASET", f"TITLES title does not bind {header!r}")
            identity = (kind, group_title, entity_id, code)
            if identity in identities:
                raise ResultError("INVALID_DATASET", f"duplicate entity-variable binding {identity}")
            identities.add(identity)
            output_type = {0: "Node", 1: "Solid"}.get(kind, "Unsupported")
            entity_columns.append({"entity_id": entity_id, "group_title": group_title, "entity_title": entity,
                                   "output_type": output_type, "variable_code": code, "header": header})
            quantity_info = _quantity(kind, code)
            if quantity_info is None:
                continue
            quantity, component, dimension, measure = quantity_info
            basis = "global" if quantity in {"stress", "strain", "displacement", "velocity"} else "unknown"
        declared_units = units or {}
        declaration = declared_units.get(dimension, declared_units.get("pressure", "unknown")
                                         if dimension == "stress" else "unknown")
        if isinstance(declaration, dict):
            declaration = declaration.get("symbol", declaration.get("unit", "unknown"))
        bindings.append({
            "schema_version": 1, "column_index": index, "raw_header": raw_headers[index],
            "normalized_header": header, "group_title": group_title, "output_type": output_type,
            "entity_id": entity_id, "entity_title": entity, "variable_code": code, "source_titles_pair_index": pair_index,
            "quantity": quantity, "component": component, "measure": measure,
            "basis": basis, "unit": declaration, "unit_dimension": "pressure" if dimension == "stress" else dimension,
            "operator": "curve_extract",
        })
    if expected_columns is not None:
        if not isinstance(expected_columns, (list, tuple)) or any(not isinstance(h, str) for h in expected_columns):
            raise ResultError("INVALID_DATASET", "expected_columns must list complete CSV headers")
        missing = set(map(_normalized, expected_columns)) - {b["normalized_header"] for b in bindings}
        if missing:
            raise ResultError("COVERAGE_INCOMPLETE", ", ".join(sorted(missing)))
        requested_headers = set(map(_normalized, expected_columns))
        if not requests and any(column["header"] in requested_headers for column in entity_columns):
            extra = {column["header"] for column in entity_columns} - requested_headers
            if extra:
                raise ResultError("SOURCE_MISMATCH", f"unexpected history columns: {', '.join(sorted(extra))}")
    by_header = {binding["normalized_header"]: binding for binding in bindings}
    if exact_required_set is not None:
        if not isinstance(exact_required_set, (list, tuple)) or any(not isinstance(h, str) for h in exact_required_set):
            raise ResultError("SCHEMA_MISMATCH", "exact_required_set must list complete history headers")
        missing = set(map(_normalized, exact_required_set)) - by_header.keys()
        if missing:
            raise ResultError("SCHEMA_MISMATCH", f"missing or unsupported history fields: {', '.join(sorted(missing))}")
    def matches(column, expected):
        return all(column.get(key) == value for key, value in expected.items())

    for request in requests:
        for expected in request:
            if not any(matches(binding, expected) for binding in bindings):
                raise ResultError("SOURCE_MISMATCH", f"unmatched source history request: {expected}")
        for column in entity_columns:
            if not any(matches(column, expected) for expected in request):
                raise ResultError("SOURCE_MISMATCH", f"unexpected history column: {column['header']}")
    for binding in bindings:
        if binding["output_type"] != "Solid":
            continue
        formulation = (solid_formulation.get(str(binding["entity_id"])) if isinstance(solid_formulation, dict)
                       else solid_formulation)
        if binding["quantity"] in {"stress", "strain"} and formulation != "hex8_1pt":
            raise ResultError("SOURCE_MISMATCH", f"1-point HEX8 required for {binding['normalized_header']}")
        if binding["quantity"] in {"stress", "strain"}:
            binding["integration_point"] = "ip1"
    return tuple(bindings)


def _npy(root, target, values, role):
    digest = write_safe_npy_v2(target, values, root=root)
    path = target.relative_to(root).as_posix()
    return ArtifactRefV1(path, role, path, digest, target.stat().st_size, "npy-v2", _PRODUCER)


def extract_history(csv_path: Path, titles_path: Path, request_spec) -> DatasetManifestV1:
    """Publish a series descriptor and an indexed set of immutable NPY files."""
    csv_path = Path(csv_path)
    root = Path(request_spec.get("operation_dir", csv_path.parent)).resolve()
    output = root / "outputs"
    output.mkdir(exist_ok=True)
    check = request_spec.get("check_cancelled") or no_checkpoint
    titles = read_titles(titles_path, check_cancelled=check)
    table = read_th_csv(csv_path, check_cancelled=check)
    bindings = _bind_history_columns(titles, table["raw_header"], **{
        key: request_spec.get(key) for key in ("expected_columns", "exact_required_set", "expected_bindings", "source_bindings",
                                              "solid_formulation", "units")})
    if not bindings:
        raise ResultError("UNSUPPORTED_OBSERVABLE", "no supported history columns")
    coordinates = _npy(root, output / "history-time.npy", table["times"], "sample_coordinates")
    check()
    rows = _npy(root, output / "history-rows.npy", np.arange(len(table["times"])), "source_row_index")
    fields = {}
    for binding in bindings:
        check()
        index = binding["column_index"]
        field_id = binding["normalized_header"]
        ref = _npy(root, output / f"history-column-{index}.npy", table["values"][:, index], "history_values")
        frozen_binding = {key: binding[key] for key in _BINDING_KEYS}
        semantics = {key: binding[key] for key in ("measure", "basis", "unit", "unit_dimension",
                                                    "integration_point") if key in binding}
        fields[field_id] = {"binding": frozen_binding, "semantics": semantics, "values_ref": ref.to_dict()}
    atomic_json(output / "history-columns.json", {"schema_version": 1, "titles_sha256": titles["sha256"],
                                                  "raw_header": table["raw_header"], "fields": fields})
    series = SeriesManifestV1("history:all", coordinates, len(table["times"]), rows, tuple(fields))
    return DatasetManifestV1(request_spec.get("dataset_id", "OC-TH4-1"), "history", series=(series,))


def require_coverage(times, interval):
    if interval is None:
        return
    if (not isinstance(interval, (list, tuple)) or len(interval) != 2
            or any(type(t) not in (int, float) or not math.isfinite(t) for t in interval)
            or interval[0] > interval[1] or interval[0] < times[0] or interval[1] > times[-1]):
        raise ResultError("COVERAGE_INCOMPLETE", "requested interval is outside the original sample brackets")


def official_impulse_secant(times, impulse, *, converter_sha256=None):
    """Apply the converter's endpoint and central secants to raw impulse samples."""
    coordinates = np.asarray(times, dtype=float)
    values = np.asarray(impulse, dtype=float)
    _times(coordinates, minimum=3)
    if values.shape != coordinates.shape or not np.isfinite(values).all():
        raise ResultError("INVALID_DATASET", "impulse and time samples must have the same finite shape")
    lower = np.maximum(np.arange(len(coordinates)) - 1, 0)
    upper = np.minimum(np.arange(len(coordinates)) + 1, len(coordinates) - 1)
    force = (values[upper] - values[lower]) / (coordinates[upper] - coordinates[lower])
    if not np.isfinite(force).all():
        raise ResultError("INVALID_DATASET", "impulse secant produced a non-finite result")
    if converter_sha256 is not None:
        if not isinstance(converter_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", converter_sha256):
            raise ResultError("INVALID_DATASET", "invalid converter SHA256 for impulse secant provenance")
        return force, {"id": "official_impulse_secant", "version": converter_sha256,
                       "params": {"endpoint": "one_sided", "interior": "central_secant", "time_source": "original_samples"}}
    return force


_GLOBAL = {
    header: (header.lower().replace(" ", "_").replace("-", "_"), dimension)
    for dimension, headers in {
        "energy": ("INTERNAL ENERGY", "KINETIC ENERGY", "HOURGLASS ENERGY", "ROTATION ENERGY", "EXTERNAL WORK",
                   "SPRING ENERGY", "CONTACT ENERGY", "ELASTIC CONTACT ENERGY", "FRICTIONAL CONTACT ENERGY",
                   "DAMPING CONTACT ENERGY", "PLASTIC WORK", "INLET ENERGY", "OUTLET ENERGY"),
        "mass": ("MASS", "ADDED MASS", "INLET MASS", "OUTLET MASS"),
        "momentum": ("X-MOMENTUM", "Y-MOMENTUM", "Z-MOMENTUM"),
        "time": ("TIME STEP",),
        "percent": ("PERCENTAGE ADDED MASS",),
    }.items()
    for header in headers
}
