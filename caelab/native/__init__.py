"""Native solver file handoff, separate from auxiliary model conversion."""

from .adapter import SolverAdapter, get_solver_adapter, supported_solver_ids
from .run_store import NativeRun, RunStore
from .results import collect_results
from .code_aster import collect_codeaster_results
from .radioss import collect_radioss_results
from .lsdyna import collect_lsdyna_results

__all__ = [
    "SolverAdapter",
    "get_solver_adapter",
    "supported_solver_ids",
    "NativeRun",
    "RunStore",
    "collect_results",
    "collect_codeaster_results",
    "collect_radioss_results",
    "collect_lsdyna_results",
]
