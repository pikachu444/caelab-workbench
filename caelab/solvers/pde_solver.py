"""ResultSet compatibility bridge for the independent scalar PDE API.

This is not a StructuralModel SolverConverter and is intentionally not registered
in CONVERTERS. NativeResultView consumes recorded VTU/frame evidence, not this
object. Scalar/flux slots must not be sent to structural deformation or Von Mises
tools: u occupies Ux and qx/qy occupy Sxx/Syy only for data compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .base import ResultSet
from ..pde.scipy_solver import BoundaryData, ScalarField, solve_poisson_2d


@dataclass
class PDEResultSet(ResultSet):
    """Scalar-aware summary; reaction_totals stores unweighted nodal flux sums.

    fx/fy/fz are compatibility keys, not mechanical reactions or boundary heat
    balance. Their values depend on mesh density. Actual scalar FEM reactions
    are available from the core PoissonSolution, not these structural slots.
    """

    def summary(self) -> str:
        state = "성공" if self.success else "실패"
        maximum = "미확인" if self.max_displacement is None else f"{self.max_displacement:.6g}"
        sums = " ".join(f"{axis}={value:.6g}" for axis, value in self.reaction_totals.items())
        return (f"{self.solver_id} 실행 {state}: max |u|={maximum}, "
                f"절점 flux 합 (메시 의존) {sums} ({self.verification})")


@dataclass
class SciPyPDESolver:
    id: str = "scipy_pde"

    def solve_poisson_2d(self, nx=32, ny=32, lx=1.0, ly=1.0, diffusion=1.0,
                         rhs_func: ScalarField | None = None,
                         dirichlet_bc: BoundaryData = None, *, neumann_bc=None,
                         units: dict[str, str] | None = None) -> ResultSet:
        """Return one-based nodal u/flux data using existing ResultSet shapes.

        units is copied from the caller, without conversion or an assumed mm/K
        declaration. rhs_func=None means zero source. Invalid inputs and numerical
        failures raise; arbitrary inputs are not declared MMS-verified at runtime.
        """
        solution = solve_poisson_2d(nx, ny, lx, ly, diffusion, rhs_func,
                                    dirichlet_bc, neumann_bc=neumann_bc)
        sums = np.sum(solution.flux, axis=0)
        if not np.all(np.isfinite(sums)):
            raise RuntimeError("Nodal flux sums overflowed")
        return PDEResultSet(
            solver_id=self.id,
            success=True,
            displacements={i: [float(value), 0.0, 0.0]
                           for i, value in enumerate(solution.values, 1)},
            stresses={i: [float(q[0]), float(q[1]), 0.0, 0.0, 0.0, 0.0]
                      for i, q in enumerate(solution.flux, 1)},
            max_displacement=float(np.max(np.abs(solution.values))),
            reaction_totals={"fx": float(sums[0]), "fy": float(sums[1]), "fz": 0.0},
            verification="T3 MMS 회귀 기준: L2 < 0.003 (32×32), 2차 수렴; 현재 입력 오차는 미평가",
            units={} if units is None else dict(units),
            log_tail=(f"Scalar PDE: Ux=u; Sxx=qx, Syy=qy; q=-k grad(u). "
                      f"fx/fy are unweighted nodal flux sums (mesh-dependent), not reactions. "
                      f"Free-DOF relative residual={solution.relative_residual:.6g}."),
        )
