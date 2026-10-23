"""Binding the numerical engine to the common structural model.

Redesign stage 4 (DESIGN.md §3.1/§12): the numerical engine is kept,
and connected to the common model's variables and responses. Any
numeric leaf of a ``StructuralModel`` can be a variable, and any
scalar of a solver ``ResultSet`` (or of a plain result dict) can be a
response — no solver or model field is special-cased.

Paths are dotted, with ``[key]`` segments for lists/dicts/objects::

    "materials[0].params.youngs_modulus"
    "materials[STEEL].params.youngs_modulus"   # list entry matched by .name
    "loads[TIP_FORCE].values.fy"
    "sections[0].options.thickness"

A variable spec is a normal numerical variable declaration plus
``path``; its current value is read from the model when ``value`` is
not given::

    {"id": "E", "unit": "Pa", "path": "materials[0].params.youngs_modulus",
     "lower": 1.0e11, "upper": 3.0e11}

A response spec is ``{"response": name, "unit": unit, "path": ...}``
where the path resolves inside the run result, e.g.
``"max_displacement"`` or ``"reaction_totals.fy"`` on a ``ResultSet``.

The binding never mutates the caller's model: each candidate works on
a deep copy, and the run function receives that copy.
"""
from __future__ import annotations

import math
import re
from copy import deepcopy

from .core import _normalize_evaluation
from .variables import normalize_variables

_SEGMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(?:\[([^\]]+)\])?$")


def _parse_path(path):
    if not isinstance(path, str) or not path.strip():
        raise ValueError("binding path must be a nonempty string")
    parts = []
    for raw in path.split("."):
        match = _SEGMENT.match(raw)
        if not match:
            raise ValueError(f"binding path segment is not valid: {raw!r} in {path!r}")
        parts.append((match.group(1), match.group(2)))
    return parts


def _child(container, name, key, path):
    if isinstance(container, dict):
        if name not in container:
            raise ValueError(f"binding path {path!r}: key {name!r} not found")
        value = container[name]
    else:
        if not hasattr(container, name):
            raise ValueError(f"binding path {path!r}: attribute {name!r} not found")
        value = getattr(container, name)
    if key is None:
        return value
    return _index(value, key, path)


def _index(value, key, path):
    if isinstance(value, dict):
        # Mesh dicts are keyed by int node/element IDs in the model.
        for candidate in (key, int(key) if key.lstrip("-").isdigit() else key):
            if candidate in value:
                return value[candidate]
        raise ValueError(f"binding path {path!r}: key {key!r} not found")
    if isinstance(value, (list, tuple)):
        if key.lstrip("-").isdigit():
            return value[int(key)]
        for item in value:  # named entries: materials/loads/bcs/sections/steps
            if getattr(item, "name", None) == key:
                return item
        raise ValueError(f"binding path {path!r}: no list entry named {key!r}")
    raise ValueError(f"binding path {path!r}: cannot index into "
                     f"{type(value).__name__}")


def get_path(container, path):
    value = container
    for name, key in _parse_path(path):
        value = _child(value, name, key, path)
    return value


def set_path(container, path, new_value):
    parts = _parse_path(path)
    parent = container
    for name, key in parts[:-1]:
        parent = _child(parent, name, key, path)
    name, key = parts[-1]
    if key is None:
        if isinstance(parent, dict):
            if name not in parent:
                raise ValueError(f"binding path {path!r}: key {name!r} not found")
            parent[name] = new_value
        else:
            if not hasattr(parent, name):
                raise ValueError(f"binding path {path!r}: attribute {name!r} not found")
            setattr(parent, name, new_value)
        return
    target = parent[name] if isinstance(parent, dict) else getattr(parent, name)
    if isinstance(target, dict):
        for candidate in (key, int(key) if key.lstrip("-").isdigit() else key):
            if candidate in target:
                target[candidate] = new_value
                return
        raise ValueError(f"binding path {path!r}: key {key!r} not found")
    if isinstance(target, list):
        if key.lstrip("-").isdigit():
            target[int(key)] = new_value
            return
        for index, item in enumerate(target):
            if getattr(item, "name", None) == key:
                target[index] = new_value
                return
        raise ValueError(f"binding path {path!r}: no list entry named {key!r}")
    raise ValueError(f"binding path {path!r}: cannot index into "
                     f"{type(target).__name__}")


def variables_from_model(model, specs):
    """Numerical variable declarations whose values come from the model.

    Each spec needs ``id``, ``unit``, ``path``, ``lower`` and ``upper``;
    ``value`` defaults to the model's current number at that path.
    """
    declarations = []
    for i, spec in enumerate(specs):
        if not isinstance(spec, dict) or "path" not in spec:
            raise ValueError(f"variable spec [{i}] requires a model path")
        declaration = {k: v for k, v in spec.items() if k != "path"}
        if "value" not in declaration and "initial" not in declaration:
            current = get_path(model, spec["path"])
            if isinstance(current, bool) or not isinstance(current, (int, float)) \
                    or not math.isfinite(float(current)):
                raise ValueError(f"model path {spec['path']!r} does not hold a "
                                 "finite number to use as the variable value")
            declaration["value"] = float(current)
        declarations.append(declaration)
    return normalize_variables(declarations)


def apply_values(model, specs, values):
    """A deep copy of ``model`` with each variable's value written at its path."""
    bound = {spec["id"] if "id" in spec else spec.get("parameter_id"): spec
             for spec in specs}
    unknown = set(values) - set(bound)
    if unknown:
        raise ValueError(f"values for unbound variables: {sorted(unknown)}")
    new_model = deepcopy(model)
    for name, value in values.items():
        set_path(new_model, bound[name]["path"], value)
    return new_model


class ModelEvaluator:
    """Callable evaluator for the numerical engine, backed by a model run.

    ``run_fn(model)`` is whatever the caller uses to run one model —
    typically a solver converter's run — returning a ``ResultSet`` or a
    plain dict. ``response_specs`` declare which scalars become the
    evaluation's responses; each is extracted from the result by path,
    so predicted/invented numbers never enter: a missing path is a
    FAILED evaluation with that reason.
    """

    def __init__(self, model, specs, run_fn, response_specs):
        if not callable(run_fn):
            raise ValueError("run_fn must be callable")
        self.model = model
        self.specs = list(specs)
        self.variables = variables_from_model(model, specs)
        self.run_fn = run_fn
        self.response_specs = []
        for spec in response_specs:
            if "response" not in spec or "path" not in spec:
                raise ValueError("each response spec requires response and path")
            self.response_specs.append(dict(spec))
        self._paths = {spec.get("id", spec.get("parameter_id")): spec["path"]
                       for spec in self.specs}

    def __call__(self, values):
        free_plus_fixed = dict(values)
        candidate_model = deepcopy(self.model)
        for name, value in free_plus_fixed.items():
            if name in self._paths:
                set_path(candidate_model, self._paths[name], value)
        result = self.run_fn(candidate_model)
        status = (result.get("execution_status", "SUCCEEDED")
                  if isinstance(result, dict) else "SUCCEEDED")
        success = (result.get("success", True) if isinstance(result, dict)
                   else getattr(result, "success", True))
        if not success and status == "SUCCEEDED":
            status = "FAILED"
        if status != "SUCCEEDED":
            diagnostics = deepcopy(result.get("diagnostics", {})) \
                if isinstance(result, dict) else {}
            diagnostics.setdefault("failure_reason", diagnostics.get(
                "reason", "run_fn reported " + str(status)))
            return _normalize_evaluation({
                "execution_status": status, "responses": {},
                "checks": deepcopy(result.get("checks", []))
                if isinstance(result, dict) else [], "diagnostics": diagnostics})
        responses = {}
        for spec in self.response_specs:
            try:
                raw = get_path(result, spec["path"])
            except ValueError as error:
                return {"execution_status": "FAILED", "responses": {},
                        "checks": [], "diagnostics": {"failure_reason": str(error)}}
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) \
                    or not math.isfinite(float(raw)):
                return {"execution_status": "FAILED", "responses": {}, "checks": [],
                        "diagnostics": {"failure_reason":
                                        f"response path {spec['path']!r} is not a "
                                        "finite number"}}
            responses[spec["response"]] = {
                "kind": "scalar", "value": float(raw), "unit": spec.get("unit", "1"),
                **{k: spec[k] for k in ("component", "location", "reduction")
                   if k in spec}}
        return _normalize_evaluation({
            "execution_status": "SUCCEEDED",
            "responses": responses, "checks": [],
            "diagnostics": {}})
