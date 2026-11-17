"""Unit specifications and C6-compliant unit conversions without eval or _UNITS mutation."""
from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Any

import numpy as np

from caelab.mdo.variables import _UNITS as _MDO_UNITS, parse_unit as _mdo_parse_unit

__all__ = [
    "QuantityUnitSpec",
    "convert_frequency_angular",
    "convert_values",
    "parse_material_unit",
    "validate_unit_string",
]

_ZERO_6D = (Fraction(0),) * 6
_FREQ_DIM = (Fraction(0), Fraction(0), Fraction(-1), Fraction(0), Fraction(0), Fraction(0))
_ANGULAR_FREQ_DIM = (Fraction(0), Fraction(0), Fraction(-1), Fraction(0), Fraction(0), Fraction(1))

_DIMENSIONLESS_KINDS = frozenset({
    "dimensionless",
    "ratio",
    "strain",
    "stretch",
    "damage",
    "poisson_ratio",
    "scalar",
    "loss_factor",
})

# Dimensions: (Length[m], Mass[kg], Time[s], TempDiff[K], Amount[mol], Angle[rad])
_EXTENDED_UNITS: dict[str, tuple[Fraction, tuple[int, int, int, int, int, int]]] = {
    "Mg": (Fraction(1000), (0, 1, 0, 0, 0, 0)),
    "mg": (Fraction(1, 1_000_000), (0, 1, 0, 0, 0, 0)),
    "us": (Fraction(1, 1_000_000), (0, 0, 1, 0, 0, 0)),
    "min": (Fraction(60), (0, 0, 1, 0, 0, 0)),
    "mN": (Fraction(1, 1000), (1, 1, -2, 0, 0, 0)),
    "MN": (Fraction(1_000_000), (1, 1, -2, 0, 0, 0)),
    "mJ": (Fraction(1, 1000), (2, 1, -2, 0, 0, 0)),
    "kJ": (Fraction(1000), (2, 1, -2, 0, 0, 0)),
    "MJ": (Fraction(1_000_000), (2, 1, -2, 0, 0, 0)),
    "mW": (Fraction(1, 1000), (2, 1, -3, 0, 0, 0)),
    "kW": (Fraction(1000), (2, 1, -3, 0, 0, 0)),
    "MW": (Fraction(1_000_000), (2, 1, -3, 0, 0, 0)),
    "kHz": (Fraction(1000), (0, 0, -1, 0, 0, 0)),
    "MHz": (Fraction(1_000_000), (0, 0, -1, 0, 0, 0)),
    "GHz": (Fraction(1_000_000_000), (0, 0, -1, 0, 0, 0)),
    "mol": (Fraction(1), (0, 0, 0, 0, 1, 0)),
    "mmol": (Fraction(1, 1000), (0, 0, 0, 0, 1, 0)),
    "K": (Fraction(1), (0, 0, 0, 1, 0, 0)),
}

_KELVIN_OFFSET = 273.15


@dataclass(frozen=True)
class QuantityUnitSpec:
    """Explicit pairing of a unit expression and physical quantity kind."""

    unit: str
    quantity_kind: str

    def __post_init__(self) -> None:
        if not isinstance(self.unit, str) or "\x00" in self.unit:
            raise ValueError("QuantityUnitSpec.unit must be a valid string")
        if not isinstance(self.quantity_kind, str) or "\x00" in self.quantity_kind or not self.quantity_kind.strip():
            raise ValueError("QuantityUnitSpec.quantity_kind must be a nonempty string")


def _is_dimensionless_kind(quantity_kind: str) -> bool:
    q = quantity_kind.strip().lower()
    return q in _DIMENSIONLESS_KINDS or any(
        token in q for token in ("strain", "stretch", "ratio", "dimensionless", "poisson", "damage")
    )


def _rational_constant(node: ast.AST) -> Fraction:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        val = float(node.value)
        if not math.isfinite(val):
            raise ValueError("Unit exponent must be finite")
        return Fraction(str(node.value))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        inner = _rational_constant(node.operand)
        return -inner if isinstance(node.op, ast.USub) else inner
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        right = _rational_constant(node.right)
        if right == 0:
            raise ValueError("Division by zero in unit exponent")
        return _rational_constant(node.left) / right
    raise ValueError("Unit exponents require finite constant rational numbers")


def _pow_scale(base: Fraction | float, exp: Fraction) -> Fraction | float:
    if isinstance(base, Fraction) and exp.denominator == 1:
        return base ** int(exp)
    result = float(base) ** float(exp)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Invalid unit scale")
    return result


def _mul_scale(a: Fraction | float, b: Fraction | float, sign: int) -> Fraction | float:
    if isinstance(a, Fraction) and isinstance(b, Fraction):
        return a * b if sign > 0 else a / b
    return float(a) * (float(b) if sign > 0 else 1.0 / float(b))


def _parse_multiplicative_unit(unit: str) -> tuple[Fraction | float, tuple[Fraction, ...]]:
    """Parse multiplicative SI/material units via AST, delegating known MDO symbols to parse_unit."""
    if not isinstance(unit, str) or "\x00" in unit:
        raise ValueError("Unit must be a valid string")
    if unit in ("", "1"):
        scale, (dl, dm, dt) = _mdo_parse_unit(unit)
        return Fraction(str(scale)), (dl, dm, dt, Fraction(0), Fraction(0), Fraction(0))
    if not unit.strip() or unit != unit.strip():
        raise ValueError(f"Invalid unit formatting: {unit!r}")

    def visit(node: ast.AST) -> tuple[Fraction | float, tuple[Fraction, ...]]:
        if isinstance(node, ast.Name):
            if node.id in _MDO_UNITS:
                scale, (dl, dm, dt) = _mdo_parse_unit(node.id)
                angle_exp = Fraction(1) if node.id == "rad" else Fraction(0)
                return Fraction(str(scale)), (dl, dm, dt, Fraction(0), Fraction(0), angle_exp)
            if node.id in _EXTENDED_UNITS:
                ext_scale, dim_ints = _EXTENDED_UNITS[node.id]
                return ext_scale, tuple(Fraction(x) for x in dim_ints)
            raise ValueError(f"Unsupported or unknown unit symbol: {node.id!r}")
        if isinstance(node, ast.Constant) and type(node.value) is int and node.value == 1:
            return Fraction(1), _ZERO_6D
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Div)):
            ls, ld = visit(node.left)
            rs, rd = visit(node.right)
            sign = -1 if isinstance(node.op, ast.Div) else 1
            if sign < 0 and float(rs) == 0.0:
                raise ValueError("Division by zero in unit")
            return _mul_scale(ls, rs, sign), tuple(x + sign * y for x, y in zip(ld, rd))
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            ls, ld = visit(node.left)
            exp = _rational_constant(node.right)
            if abs(exp) > 100:
                raise ValueError("Unit exponent too large")
            return _pow_scale(ls, exp), tuple(x * exp for x in ld)
        raise ValueError(f"Unsupported unit expression: {unit!r}")

    try:
        tree = ast.parse(unit, mode="eval")
        scale, dim = visit(tree.body)
    except (SyntaxError, ArithmeticError) as exc:
        raise ValueError(f"Invalid unit expression {unit!r}") from exc

    fscale = float(scale)
    if not math.isfinite(fscale) or fscale <= 0:
        raise ValueError(f"Invalid unit scale for {unit!r}")
    return scale, dim


def parse_material_unit(unit: str, *, quantity_kind: str = "dimensionless") -> tuple[Fraction | float, tuple[Fraction, ...]]:
    """Parse and validate a unit string against a declared quantity_kind."""
    if not isinstance(unit, str) or "\x00" in unit:
        raise ValueError("Unit must be a valid string")
    if not isinstance(quantity_kind, str) or not quantity_kind.strip():
        raise ValueError("quantity_kind must be a nonempty string")

    if unit == "":
        if not _is_dimensionless_kind(quantity_kind):
            raise ValueError(f"Empty unit '' is invalid for quantity_kind={quantity_kind!r}")
        return _parse_multiplicative_unit("")

    if quantity_kind in ("absolute_temperature", "temperature_difference"):
        if unit not in ("K", "degC"):
            raise ValueError(f"{quantity_kind} requires 'K' or 'degC', got {unit!r}")
        return Fraction(1), (Fraction(0), Fraction(0), Fraction(0), Fraction(1), Fraction(0), Fraction(0))

    if unit == "degC":
        raise ValueError(
            f"'degC' requires explicit quantity_kind 'absolute_temperature' or 'temperature_difference', got {quantity_kind!r}"
        )

    scale, dim = _parse_multiplicative_unit(unit)
    if quantity_kind == "frequency" and dim != _FREQ_DIM:
        raise ValueError(f"Unit {unit!r} does not have pure frequency dimension (use convert_frequency_angular for rad/s)")
    if quantity_kind == "angular_frequency" and dim != _ANGULAR_FREQ_DIM:
        raise ValueError(f"Unit {unit!r} does not have angular frequency dimension (rad/s)")
    return scale, dim


def validate_unit_string(unit: str, *, allow_empty: bool = True) -> str:
    """Validate standalone unit strings in parameter/observable descriptors (including degC, K, Mg, J/mol)."""
    if not isinstance(unit, str) or "\x00" in unit:
        raise ValueError("Unit must be a valid string")
    if unit == "":
        if not allow_empty:
            raise ValueError("Empty unit is not allowed here")
        return ""
    if unit == "degC":
        return unit
    _parse_multiplicative_unit(unit)
    return unit


def _require_finite_scalar(val: Any) -> float:
    if isinstance(val, (bool, np.bool_)) or not isinstance(val, (int, float, np.integer, np.floating)):
        raise ValueError("Unit conversion requires finite numeric values (booleans rejected)")
    fval = float(val)
    if not math.isfinite(fval):
        raise ValueError("Unit conversion requires finite numeric values")
    return fval


def _apply_numeric_transform(values: Any, scalar_fn: Callable[[float], float], array_fn: Callable[[np.ndarray], np.ndarray]) -> Any:
    if isinstance(values, np.ndarray):
        if values.dtype.kind not in ("i", "u", "f"):
            raise ValueError(f"Unsupported ndarray dtype {values.dtype!r} for unit conversion")
        arr = np.asarray(values, dtype=np.float64)
        if not np.isfinite(arr).all():
            raise ValueError("Unit conversion ndarray contains non-finite values")
        out = np.asarray(array_fn(arr), dtype=np.float64)
        if not np.isfinite(out).all():
            raise ValueError("Unit conversion produced non-finite array values")
        return out
    if isinstance(values, list):
        return [_apply_numeric_transform(item, scalar_fn, array_fn) for item in values]
    if isinstance(values, tuple):
        return tuple(_apply_numeric_transform(item, scalar_fn, array_fn) for item in values)
    if isinstance(values, (str, bytes)):
        raise ValueError("Unit conversion does not accept string or bytes values")
    fval = _require_finite_scalar(values)
    out_val = float(scalar_fn(fval))
    if not math.isfinite(out_val):
        raise ValueError("Unit conversion produced non-finite value")
    return out_val


def _convert_absolute_temperature(values: Any, source_unit: str, target_unit: str) -> Any:
    def _to_kelvin_scalar(x: float) -> float:
        kelvin = x if source_unit == "K" else x + _KELVIN_OFFSET
        if not math.isfinite(kelvin) or kelvin <= 0.0:
            raise ValueError(f"Absolute temperature must be > 0 K (got {x} {source_unit})")
        return kelvin

    def scalar_fn(x: float) -> float:
        kelvin = _to_kelvin_scalar(x)
        if source_unit == target_unit:
            return x
        out = kelvin if target_unit == "K" else kelvin - _KELVIN_OFFSET
        check_k = out if target_unit == "K" else out + _KELVIN_OFFSET
        if not math.isfinite(check_k) or check_k <= 0.0:
            raise ValueError(f"Converted absolute temperature must be > 0 K (got {check_k} K)")
        return out

    def array_fn(arr: np.ndarray) -> np.ndarray:
        kelvin = arr if source_unit == "K" else arr + _KELVIN_OFFSET
        if not np.isfinite(kelvin).all() or not (kelvin > 0.0).all():
            raise ValueError("Absolute temperature array must have all entries > 0 K")
        if source_unit == target_unit:
            return arr.copy()
        out = kelvin if target_unit == "K" else kelvin - _KELVIN_OFFSET
        check_k = out if target_unit == "K" else out + _KELVIN_OFFSET
        if not np.isfinite(check_k).all() or not (check_k > 0.0).all():
            raise ValueError("Converted absolute temperature array must have all entries > 0 K")
        return out

    return _apply_numeric_transform(values, scalar_fn, array_fn)


def convert_values(
    values: Any,
    source: QuantityUnitSpec,
    target: QuantityUnitSpec,
    policy_revision: str = "v1",
) -> Any:
    """Convert scalar, list/tuple, or ndarray values between compatible QuantityUnitSpecs under C6 policy v1."""
    if policy_revision != "v1":
        raise ValueError(f"Unsupported unit policy_revision {policy_revision!r}; expected 'v1'")
    if not isinstance(source, QuantityUnitSpec) or not isinstance(target, QuantityUnitSpec):
        raise ValueError("source and target must be QuantityUnitSpec instances")
    if source.quantity_kind != target.quantity_kind:
        raise ValueError(
            f"Incompatible quantity_kind for implicit conversion: {source.quantity_kind!r} vs {target.quantity_kind!r}"
        )

    kind = source.quantity_kind
    src_scale, src_dim = parse_material_unit(source.unit, quantity_kind=kind)
    tgt_scale, tgt_dim = parse_material_unit(target.unit, quantity_kind=kind)
    if src_dim != tgt_dim:
        raise ValueError(f"Physical dimension mismatch between {source.unit!r} and {target.unit!r}")

    if kind == "absolute_temperature":
        return _convert_absolute_temperature(values, source.unit, target.unit)

    if kind == "temperature_difference":
        return _apply_numeric_transform(values, lambda x: x, lambda arr: arr.copy())

    factor = float(src_scale / tgt_scale) if (isinstance(src_scale, Fraction) and isinstance(tgt_scale, Fraction)) else float(src_scale) / float(tgt_scale)
    if not math.isfinite(factor) or factor <= 0.0:
        raise ValueError("Invalid unit conversion factor")
    if source.unit == target.unit:
        return _apply_numeric_transform(values, lambda x: x, lambda arr: arr.copy())
    return _apply_numeric_transform(values, lambda x: x * factor, lambda arr: arr * factor)


def convert_frequency_angular(
    values: Any,
    source: QuantityUnitSpec,
    target: QuantityUnitSpec,
    *,
    policy_revision: str = "v1",
) -> Any:
    """Explicit 2*pi conversion between frequency (Hz/kHz/MHz) and angular_frequency (rad/s)."""
    if policy_revision != "v1":
        raise ValueError(f"Unsupported unit policy_revision {policy_revision!r}; expected 'v1'")
    if not isinstance(source, QuantityUnitSpec) or not isinstance(target, QuantityUnitSpec):
        raise ValueError("source and target must be QuantityUnitSpec instances")

    allowed = {"frequency", "angular_frequency"}
    if source.quantity_kind not in allowed or target.quantity_kind not in allowed:
        raise ValueError("convert_frequency_angular only supports 'frequency' and 'angular_frequency'")

    if source.quantity_kind == target.quantity_kind:
        return convert_values(values, source, target, policy_revision=policy_revision)

    src_scale, _ = parse_material_unit(source.unit, quantity_kind=source.quantity_kind)
    tgt_scale, _ = parse_material_unit(target.unit, quantity_kind=target.quantity_kind)
    base_ratio = float(src_scale / tgt_scale) if (isinstance(src_scale, Fraction) and isinstance(tgt_scale, Fraction)) else float(src_scale) / float(tgt_scale)
    if source.quantity_kind == "frequency" and target.quantity_kind == "angular_frequency":
        factor = base_ratio * (2.0 * math.pi)
    else:
        factor = base_ratio / (2.0 * math.pi)
    return _apply_numeric_transform(values, lambda x: x * factor, lambda arr: arr * factor)
