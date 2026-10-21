"""PDE line (FEniCSx) — keeps its own format by design (DESIGN §3.3, §5).

Not part of the common structural model schema and not a solver
converter under ``caelab/solvers``: a PDE problem is the JSON-shaped
dict documented in `caelab.pde.runner`, solved by an isolated
FEniCSx worker subprocess.
"""

from .runner import LIMITATIONS, find_fenicsx_python, poisson_settings, run_pde
from .worker import PDEInputError, validate_settings

__all__ = [
    "LIMITATIONS",
    "PDEInputError",
    "find_fenicsx_python",
    "poisson_settings",
    "run_pde",
    "validate_settings",
]
