"""Pure MDO numerical contracts and algorithms.

Commercial-grade Multidisciplinary Design Optimization (MDO) core for caelab-workbench:
- variables: ContinuousVariable, DiscreteVariable, CategoricalVariable, VariableSpace
- objectives: ObjectiveSpec, evaluate_criteria
- constraints: ConstraintSpec, ConstraintAssessment, assess_constraints, Feasibility
- inp_binder: TemplateDeck, BindingSpec
- responses: ResponseSpec, extract_scalar, extract_all_responses
- study_manager: StudyManager, DesignPointRecord
- pareto: pareto_front, knee_point, hypervolume, crowding_distance
- surrogate: SurrogateDataset, SurrogateConfig, fit_surrogate, predict
- sensitivity: SensitivityConfig, analyze_sensitivity
- optimize: OptimizationProblem, OptimizationConfig, optimize
"""

from caelab.mdo.variables import (
    VariableSpace,
    ContinuousVariable,
    DiscreteVariable,
    CategoricalVariable,
    LinkedVariable,
)
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import (
    ConstraintSpec,
    ConstraintAssessment,
    Feasibility,
    assess_constraints,
)
from caelab.mdo.inp_binder import TemplateDeck, BindingSpec
from caelab.mdo.responses import ResponseSpec, extract_scalar, extract_all_responses
from caelab.mdo.study_manager import StudyManager, DesignPointRecord
from caelab.mdo.pareto import pareto_front, knee_point, hypervolume, crowding_distance
from caelab.mdo.surrogate import SurrogateDataset, SurrogateConfig, fit_surrogate, predict
from caelab.mdo.sensitivity import SensitivityConfig, analyze_sensitivity
from caelab.mdo.optimize import OptimizationProblem, OptimizationConfig, optimize
from caelab.mdo.calculix_evaluator import CalculiXEvaluator, extract_calculix_responses

__all__ = [
    "VariableSpace",
    "ContinuousVariable",
    "DiscreteVariable",
    "CategoricalVariable",
    "LinkedVariable",
    "ObjectiveSpec",
    "ConstraintSpec",
    "ConstraintAssessment",
    "Feasibility",
    "assess_constraints",
    "TemplateDeck",
    "BindingSpec",
    "ResponseSpec",
    "extract_scalar",
    "extract_all_responses",
    "StudyManager",
    "DesignPointRecord",
    "pareto_front",
    "knee_point",
    "hypervolume",
    "crowding_distance",
    "SurrogateDataset",
    "SurrogateConfig",
    "fit_surrogate",
    "predict",
    "SensitivityConfig",
    "analyze_sensitivity",
    "OptimizationProblem",
    "OptimizationConfig",
    "optimize",
    "CalculiXEvaluator",
    "extract_calculix_responses",
]
