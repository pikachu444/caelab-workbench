"""Numerical engine: DOE, fitting, optimization, UQ, sensitivity.

Kept from the original repository (DESIGN.md §3.1) and connected to
the common model in redesign stage 4: any numeric leaf of a
``StructuralModel`` can be a variable and any scalar of a run result
can be a response (see ``model_binding``).

Port scope, stated plainly instead of silently dropped:

- Ported: ``run_doe`` (SciPy LHS), ``optimize`` (differential
  evolution), ``fit_model`` (least squares, scalar/series, holdout
  separated), ``fit_surrogate``/``Surrogate`` (SciPy RBF),
  ``analyze_candidates`` (statistics, normalized-regression
  sensitivity, Pareto), ``run_uq`` (independent + correlated-normal),
  ``sample_sensitivity``/``analyze_sensitivity`` (SALib Sobol),
  ``compare_curves``, ``detect_event``, ``compare_events``.
- Not ported, because their subsystems are not in this repository:
  process/batch parallel execution, the ``slsqp_adjoint`` optimizer,
  joint-distribution UQ, discrete/categorical/dependent variables
  (dimensioned expressions), file-batch export/import, and the
  original's saved-study/record machinery. Each raises a clear error
  where the original accepted it, rather than faking the result.

Requires NumPy and SciPy; Sobol additionally requires SALib.
"""
from .analysis import analyze_candidates
from .core import (EvaluationBudgetExceeded, EvaluationFailed,
                   evaluate_once, optimize, run_doe, scalar_value)
from .events import compare_events, detect_event
from .fit import Surrogate, compare_curves, fit_model, fit_surrogate
from .model_binding import (ModelEvaluator, apply_values, get_path,
                            set_path, variables_from_model)
from .sensitivity import analyze_sensitivity, sample_sensitivity
from .uq import run_uq
from .variables import normalize_variables

__all__ = [
    "EvaluationBudgetExceeded",
    "EvaluationFailed",
    "ModelEvaluator",
    "Surrogate",
    "analyze_candidates",
    "analyze_sensitivity",
    "apply_values",
    "compare_curves",
    "compare_events",
    "detect_event",
    "evaluate_once",
    "fit_model",
    "fit_surrogate",
    "get_path",
    "normalize_variables",
    "optimize",
    "run_doe",
    "run_uq",
    "sample_sensitivity",
    "scalar_value",
    "set_path",
    "variables_from_model",
]
