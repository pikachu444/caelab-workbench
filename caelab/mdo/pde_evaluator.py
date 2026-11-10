"""Headless MDO evaluations using the embedded scalar SciPy FEM solver."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

import numpy as np

from caelab.mdo.optimize import EvaluationObservation, EvaluationRequest
from caelab.pde.scipy_solver import BoundaryData, ScalarField, solve_poisson_2d


_SIDES = ("left", "right", "bottom", "top")
_TARGETS = {
    "diffusion", "k", "rhs", "rhs_func", "source", "q",
    "dirichlet", "dirichlet_bc", "T0", "lx", "ly", "nx", "ny",
    *(f"dirichlet_{side}" for side in _SIDES),
    *(f"neumann_{side}" for side in _SIDES),
}


class SciPyPDEEvaluator:
    """Evaluates MDO design vectors using the embedded native SciPy 2D Poisson solver.

    Responses use caller-consistent units; mean_temp is an unweighted nodal mean.
    Neumann data is g=k du/dn, whereas solution flux is -k grad(u).
    A mapped Dirichlet side promotes None to a named-side mapping, leaving other
    sides natural. Scalar/callable defaults remain prescribed on all four sides.
    """

    def __init__(
        self,
        *,
        variable_mapping: Mapping[str, str],
        nx: int = 32,
        ny: int = 32,
        lx: float = 1.0,
        ly: float = 1.0,
        default_diffusion: float = 1.0,
        default_rhs: ScalarField = 0.0,
        dirichlet_bc: BoundaryData = None,
        neumann_bc: Mapping[str, ScalarField] | None = None,
        source_id: str = "scipy_pde:poisson_2d",
    ) -> None:
        if not variable_mapping:
            raise ValueError("variable_mapping must not be empty")
        self.variable_mapping = dict(variable_mapping)
        for target in self.variable_mapping.values():
            if target not in _TARGETS:
                raise ValueError(f"Unsupported PDE target: {target}")
        for name, count in (("nx", nx), ("ny", ny)):
            if isinstance(count, bool) or not isinstance(count, (int, np.integer)) or count < 1:
                raise ValueError(f"{name} must be a positive integer")
        self.nx, self.ny = nx, ny
        for name, value in (("lx", lx), ("ly", ly), ("default_diffusion", default_diffusion)):
            try:
                value = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{name} must be finite and positive") from exc
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
            setattr(self, name, value)
        self.default_rhs = default_rhs
        self.dirichlet_bc = dict(dirichlet_bc) if isinstance(dirichlet_bc, Mapping) else dirichlet_bc
        self.neumann_bc = dict(neumann_bc) if neumann_bc is not None else None
        self.source_id = source_id

    def __call__(self, physical_values: Mapping[str, Any]) -> dict[str, float]:
        for key in physical_values:
            if key not in self.variable_mapping:
                raise ValueError(
                    f"Unknown variable '{key}'; expected one of {list(self.variable_mapping)}"
                )
        for key in self.variable_mapping:
            if key not in physical_values:
                raise ValueError(f"Missing required design variable '{key}' in physical_values")

        nx, ny, lx, ly = self.nx, self.ny, self.lx, self.ly
        diffusion, rhs = self.default_diffusion, self.default_rhs
        dirichlet_bc = self.dirichlet_bc
        neumann_bc = dict(self.neumann_bc) if self.neumann_bc is not None else {}
        dirichlet_sides = {}
        for key, value in physical_values.items():
            target = self.variable_mapping[key]
            if target in ("diffusion", "k"):
                diffusion = float(value)
            elif target in ("rhs", "rhs_func", "source", "q"):
                rhs = value if callable(value) else float(value)
            elif target in ("dirichlet", "dirichlet_bc", "T0"):
                dirichlet_bc = float(value)
            elif target.startswith("dirichlet_"):
                dirichlet_sides[target.removeprefix("dirichlet_")] = float(value)
            elif target.startswith("neumann_"):
                neumann_bc[target.removeprefix("neumann_")] = float(value)
            elif target == "lx":
                lx = float(value)
            elif target == "ly":
                ly = float(value)
            elif target == "nx":
                nx = int(value)
            elif target == "ny":
                ny = int(value)

        if dirichlet_sides:
            if dirichlet_bc is None:
                dirichlet_bc = {}
            elif isinstance(dirichlet_bc, Mapping):
                dirichlet_bc = dict(dirichlet_bc)
            else:
                dirichlet_bc = dict.fromkeys(_SIDES, dirichlet_bc)
            dirichlet_bc.update(dirichlet_sides)
        if not math.isfinite(diffusion) or diffusion <= 0:
            raise ValueError(f"Diffusion k must be finite and positive, got {diffusion}")
        if not all(math.isfinite(v) and v > 0 for v in (lx, ly)):
            raise ValueError("lx and ly must be finite and positive")
        if nx < 1 or ny < 1:
            raise ValueError("nx and ny must be positive integers")

        solution = solve_poisson_2d(
            nx=nx, ny=ny, lx=lx, ly=ly, diffusion=diffusion,
            rhs_func=rhs, dirichlet_bc=dirichlet_bc, neumann_bc=neumann_bc,
        )
        max_temp = float(np.max(solution.values))
        min_temp = float(np.min(solution.values))
        mean_temp = float(np.mean(solution.values))
        flux_magnitudes = np.hypot(solution.flux[:, 0], solution.flux[:, 1])
        max_flux = float(np.max(flux_magnitudes))
        responses = {
            "max_temp": max_temp,
            "min_temp": min_temp,
            "mean_temp": mean_temp,
            "max_flux": max_flux,
            "max_u": max_temp,
            "min_u": min_temp,
            "mean_u": mean_temp,
        }
        if not all(math.isfinite(value) for value in responses.values()):
            raise ValueError("Non-finite response value computed")
        return responses

    def evaluate_batch(
        self, requests: tuple[EvaluationRequest, ...]
    ) -> dict[str, EvaluationObservation]:
        observations = {}
        for req in requests:
            try:
                responses = self(req.values)
                observations[req.id] = EvaluationObservation(
                    request_id=req.id, point_id=req.point_id, responses=responses,
                    valid=True, reason=None, mode=req.mode, source_id=self.source_id,
                )
            except Exception as exc:
                observations[req.id] = EvaluationObservation(
                    request_id=req.id, point_id=req.point_id, responses={},
                    valid=False, reason=str(exc) or "Evaluation failed",
                    mode=req.mode, source_id=self.source_id,
                )
        return observations
