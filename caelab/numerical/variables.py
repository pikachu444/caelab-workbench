"""Variable declarations for the numerical engine.

Ported from the original repository's ``caelab/variable_domains.py``
``normalize()`` contract, reduced to what the new repository can
honestly support today: independent ``continuous`` and ``integer``
variables, ``free`` or ``fixed``, with an optional ``log`` transform
for positive continuous variables.

Discrete/categorical/dependent variables are *rejected with a clear
message* instead of being silently reinterpreted: they need the
dimensioned-expression subsystem, which the redesign has not ported.
A declaration is a plain dict::

    {"id": "E", "unit": "Pa", "lower": 1e11, "upper": 3e11,
     "value": 2.1e11}          # value/initial optional; midpoint default

``lower_bound``/``upper_bound`` and ``parameter_id`` are accepted as
aliases, exactly as in the original.
"""
from __future__ import annotations

import math

_ALLOWED = {"id", "parameter_id", "unit", "kind", "mode", "lower", "upper",
            "lower_bound", "upper_bound", "value", "initial", "transform"}


def _number(value, label):
    if isinstance(value, (bool,)) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    return value


def _alias(row, primary, secondary, default, label):
    if primary in row and secondary in row:
        raise ValueError(f"{label}: use {primary} or {secondary}, not both")
    return row.get(primary, row.get(secondary, default))


def normalize_variables(variables):
    """Validate declarations and return the normalized list (new dicts)."""
    if not isinstance(variables, list) or not 1 <= len(variables) <= 64:
        raise ValueError("Declare 1..64 numerical variables")
    result, names = [], set()
    for i, row in enumerate(variables):
        path = f"variables[{i}]"
        if not isinstance(row, dict) or set(row) - _ALLOWED:
            raise ValueError(f"{path}: unknown variable fields {sorted(set(row) - _ALLOWED)}")
        name = _alias(row, "id", "parameter_id", None, path)
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValueError(f"{path}.id: a nonempty variable ID is required")
        if name in names:
            raise ValueError(f"{path}.id: variable IDs must be distinct: {name}")
        names.add(name)
        unit = row.get("unit")
        if not isinstance(unit, str) or not unit.strip() or len(unit) > 128:
            raise ValueError(f"{path}.unit: declare the unit ('1' for dimensionless)")
        kind = row.get("kind", "continuous")
        mode = row.get("mode", "free")
        if kind not in ("continuous", "integer"):
            raise ValueError(
                f"{path}.kind: only continuous/integer are supported in this port; "
                f"got {kind!r} (discrete/categorical need the unported expression subsystem)")
        if mode not in ("free", "fixed"):
            raise ValueError(
                f"{path}.mode: only free/fixed are supported in this port; got {mode!r}")
        transform = row.get("transform", "linear")
        if transform not in ("linear", "log") or (
                transform == "log" and (kind != "continuous" or mode != "free")):
            raise ValueError(f"{path}.transform: log needs a positive free continuous variable")
        low = _number(_alias(row, "lower", "lower_bound", None, path), f"{path}.lower")
        high = _number(_alias(row, "upper", "upper_bound", None, path), f"{path}.upper")
        if low > high or (low == high and kind == "continuous" and mode == "free"):
            raise ValueError(f"{path}: bounds must increase (fixed/singleton integer excepted)")
        if not math.isfinite(high - low) or not math.isfinite(high + low):
            raise ValueError(f"{path}: bounds must have a finite range")
        if kind == "integer" and math.ceil(low) > math.floor(high):
            raise ValueError(f"{path}: no integer lies in these bounds")
        if transform == "log" and low <= 0:
            raise ValueError(f"{path}.transform: log sampling requires positive bounds")
        default = (math.ceil(low) + math.floor(high)) // 2 if kind == "integer" else (low + high) / 2
        value = _alias(row, "value", "initial", default, path)
        value = _number(value, f"{path}.value")
        if kind == "integer" and not float(value).is_integer():
            raise ValueError(f"{path}.value: integer variable value must be integral")
        if not low <= value <= high:
            raise ValueError(f"{path}.value: value lies outside declared bounds")
        result.append({"id": name, "unit": unit, "kind": kind, "mode": mode,
                       "lower": low, "upper": high, "value": value,
                       "transform": transform})
    return result


def free_variables(variables):
    return [v for v in variables if v["mode"] == "free"]


def check_values(variables, values, *, label="values"):
    """Require exactly the free variables, each finite and in bounds."""
    free = free_variables(variables)
    if not isinstance(values, dict) or set(values) != {v["id"] for v in free}:
        raise ValueError(f"{label}: must contain exactly the free variables "
                         f"{[v['id'] for v in free]}")
    out = {}
    for v in free:
        value = _number(values[v["id"]], f"{label}.{v['id']}")
        if v["kind"] == "integer" and not float(value).is_integer():
            raise ValueError(f"{label}.{v['id']}: integer variable must be integral")
        if not v["lower"] <= value <= v["upper"]:
            raise ValueError(f"{label}.{v['id']}: value outside declared bounds")
        out[v["id"]] = int(value) if v["kind"] == "integer" else float(value)
    return out


def full_values(variables, free_values):
    """Free values plus the declared values of fixed variables."""
    out = dict(free_values)
    for v in variables:
        if v["mode"] == "fixed":
            out[v["id"]] = v["value"]
    return out


def encode(variables, free_values):
    """Physical free values -> sampler coordinates (log where declared)."""
    checked = check_values(variables, free_values)
    out = {}
    for v in free_variables(variables):
        value = checked[v["id"]]
        out[v["id"]] = math.log(value) if v["transform"] == "log" else float(value)
    return out


def decode(variables, encoded):
    """Sampler coordinates -> physical values (exact at the bounds)."""
    free = free_variables(variables)
    if not isinstance(encoded, dict) or set(encoded) != {v["id"] for v in free}:
        raise ValueError("encoded coordinates must match the free variable IDs")
    out = {}
    for v in free:
        raw = _number(encoded[v["id"]], f"encoded.{v['id']}")
        if v["transform"] == "log":
            endpoints = {math.log(v["lower"]): v["lower"], math.log(v["upper"]): v["upper"]}
            value = endpoints[raw] if raw in endpoints else math.exp(raw)
        elif v["kind"] == "integer":
            if not float(raw).is_integer():
                raise ValueError(f"encoded.{v['id']}: integer coordinate must be integral")
            value = int(raw)
        else:
            value = float(raw)
        out[v["id"]] = value
    return check_values(variables, out, label="decoded")


def encoded_bounds(variables):
    """(names, [(low, high), ...], integrality) in encoded coordinates."""
    names, bounds, integrality = [], [], []
    for v in free_variables(variables):
        names.append(v["id"])
        if v["transform"] == "log":
            bounds.append((math.log(v["lower"]), math.log(v["upper"])))
        elif v["kind"] == "integer":
            bounds.append((math.ceil(v["lower"]), math.floor(v["upper"])))
        else:
            bounds.append((v["lower"], v["upper"]))
        integrality.append(1 if v["kind"] == "integer" else 0)
    return names, bounds, integrality
