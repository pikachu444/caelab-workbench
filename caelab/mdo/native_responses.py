"""Explicit CalculiX nodal FRD and same-frame DAT EMAS scalar selectors."""
from __future__ import annotations

import math
import re
from pathlib import Path

from .variables import parse_unit


def default_selector(name, unit):
    if name == "disp":
        return dict(source="FRD", field="DISP", component="magnitude", frame="last", set="ALL", reduction="max", unit=unit)
    if name == "stress":
        return dict(source="FRD", field="STRESS", component="von_mises", frame="last", set="ALL", reduction="max", unit=unit)
    if name.startswith("mass_"):
        return dict(source="DAT", field="EMAS", component="value", frame="last", set=name[5:].upper(), reduction="sum", unit=unit)
    raise ValueError(f"{name}: declare an explicit native response selector")


def validate_selector(selector, unit):
    s = dict(selector)
    parse_unit(unit)
    if not unit or s.get("unit", unit) != unit:
        raise ValueError("Selector and declared response units must agree")
    s["unit"] = unit
    s.setdefault("frame", "last"); s.setdefault("set", "ALL")
    s.setdefault("reduction", "max"); s.setdefault("component", "value")
    if s["frame"] != "last" and (type(s["frame"]) is not int or s["frame"] < 0):
        raise ValueError("Frame must be last or a nonnegative FRD frame index")
    if s["reduction"] not in ("max", "min", "mean", "sum"):
        raise ValueError("Unsupported response reduction")
    if s.get("source") == "FRD":
        allowed = {"DISP": ("magnitude", "D1", "D2", "D3"),
                   "STRESS": ("von_mises", "SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX")}
        if s.get("field") not in allowed or s["component"] not in allowed[s["field"]]:
            raise ValueError("Unsupported nodal FRD field/component")
        if s["set"] != "ALL":
            raise ValueError("FRD selectors require ALL plus optional explicit node_ids")
        if "node_ids" in s and (not s["node_ids"] or any(type(i) is not int or i <= 0 for i in s["node_ids"])):
            raise ValueError("Node IDs must be positive integers")
    elif s.get("source") != "DAT" or s.get("field") != "EMAS" or s["component"] != "value" or not re.fullmatch(r"[A-Za-z0-9_]+", s["set"]):
        raise ValueError("DAT response supports named-set EMAS totals only")
    return s


def extract_native_responses(workdir: Path, selectors):
    from caelab.native.results import _Reader
    with (Path(workdir) / "model.frd").open(encoding="utf-8", errors="strict") as stream:
        reader = _Reader(stream, lambda: None)
        reader.read()
    if not reader.frames or not reader.ended:
        raise ValueError("No readable FRD frame for requested native responses")
    from .native_endpoint import validate_native_endpoint
    endpoint = validate_native_endpoint(workdir,reader)
    if any(s.get("field") == "STRESS" for s in selectors.values()):
        if endpoint["section_forces"]:
            raise ValueError("SECTION FORCES replaces tensor stress; nodal tensor/von Mises responses are unavailable")
    answer = {}
    for name, s in selectors.items():
        index = -1 if s["frame"] == "last" else s["frame"]
        try:
            frame = reader.frames[index]
        except IndexError:
            raise ValueError(f"{name}: requested FRD frame is missing") from None
        if s["source"] == "DAT":
            text = (Path(workdir) / "model.dat").read_text(encoding="utf-8", errors="strict")
            pattern = r"total mass for set\s+(\S+)\s+and time\s+([+\-\d.eEdD]+)\s*\n\s*([+\-\d.eEdD]+)"
            values = [float(value.replace("D", "E").replace("d", "e"))
                      for group, stamp, value in re.findall(pattern, text, re.I)
                      if group.upper() == s["set"].upper() and math.isclose(
                          float(stamp.replace("D", "E").replace("d", "e")), frame.metadata["value"], rel_tol=1e-6, abs_tol=1e-12)]
            if len(values) != 1 or values[0] < 0:
                raise ValueError(f"{name}: exactly one nonnegative DAT EMAS total at selected FRD time is required")
        else:
            field = next((f for f in frame.fields if f.name == s["field"]), None)
            if field is None or field.warnings or set(field.values) != set(reader.nodes):
                raise ValueError(f"{name}: requested complete nodal FRD field is unavailable")
            nodes = s.get("node_ids", tuple(reader.nodes))
            if not set(nodes) <= field.values.keys():
                raise ValueError(f"{name}: requested node IDs are missing")
            components = [c["name"].upper() for c in field.stored]
            values = []
            for node in nodes:
                row = field.values[node]
                def component(key):
                    if key not in components:
                        raise ValueError(f"{name}: missing component {key}")
                    return row[components.index(key)]
                if s["component"] == "magnitude":
                    value = math.hypot(*(component(k) for k in ("D1", "D2", "D3")))
                elif s["component"] == "von_mises":
                    xx, yy, zz, xy, yz, zx = (component(k) for k in ("SXX", "SYY", "SZZ", "SXY", "SYZ", "SZX"))
                    value = math.sqrt(.5*((xx-yy)**2+(yy-zz)**2+(zz-xx)**2)+3*(xy*xy+yz*yz+zx*zx))
                else:
                    value = component(s["component"])
                values.append(value)
        if not values or not all(math.isfinite(v) for v in values):
            raise ValueError(f"{name}: finite native values are required")
        answer[name] = {"max": max, "min": min, "mean": lambda v: sum(v)/len(v), "sum": sum}[s["reduction"]](values)
    return answer
