"""Synthetic analytic optimizer fixtures; no solver is launched."""
from dataclasses import replace
import importlib.util
import threading
import numpy as np
import pytest
from caelab.mdo.variables import VariableSpace, ContinuousVariable, DiscreteVariable, CategoricalVariable
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.optimize import (OptimizationProblem, OptimizationConfig, EvaluationObservation, optimize)


def continuous_space():
    return VariableSpace(variables=(ContinuousVariable(id="x", name="X", unit="", lower=0, upper=2, initial=1.5),))


class AnalyticEvaluator:
    def __init__(self, function, *, mode="ACTUAL", cancel=None):
        self.function, self.mode, self.cancel = function, mode, cancel
        self.requests = []
    def evaluate_batch(self, requests):
        self.requests.extend(requests)
        records = {}
        for r in reversed(requests):
            responses = self.function(r.values)
            records[r.id] = EvaluationObservation(request_id=r.id, point_id=r.point_id, responses=responses,
                    mode=self.mode, source_id="synthetic analytic fixture" if self.mode == "ACTUAL" else "model:v1")
        if self.cancel is not None:
            self.cancel.set()
        return records


def problem(**kwargs):
    defaults = dict(space=continuous_space(), objectives=(ObjectiveSpec(id="f", response_id="f"),), differentiable=True)
    defaults.update(kwargs)
    return OptimizationProblem(**defaults)


def test_slsqp_constrained_known_solution_and_scaled_tolerance_sign():
    # Minimize x² subject to x>=1 with 0.1 acceptance tolerance => optimum x=.9.
    p = problem(constraints=(ConstraintSpec(id="bound", response_id="x", kind="LOWER", limit=1, scale=7, tolerance=.1),))
    evaluator = AnalyticEvaluator(lambda v: {"f": v["x"]**2, "x": v["x"]})
    result = optimize(p, evaluator, OptimizationConfig(algorithm="slsqp", max_evaluations=200))
    assert result.termination_reason == "CONVERGED"
    chosen = next(a for a in result.actual_archive if a.point_id == result.selected_ids[0])
    assert chosen.values["x"] == pytest.approx(.9, abs=1e-5)
    assert chosen.feasible and chosen.total_violation == 0
    assert result.budget_usage.dispatched_requests == len(evaluator.requests)
    assert result.budget_usage.cache_hits > 0
    assert len({tuple(r.values.items()) for r in evaluator.requests}) == len(evaluator.requests)
    assert not chosen.objectives.flags.writeable


@pytest.mark.parametrize("tolerance", [0., .1])
def test_slsqp_equality_has_declared_acceptance_interval(tolerance):
    p = problem(constraints=(ConstraintSpec(id="e", response_id="x", kind="EQUALITY", limit=1, tolerance=tolerance, scale=4),))
    result = optimize(p, AnalyticEvaluator(lambda v: {"f": (v["x"]-.2)**2, "x": v["x"]}),
                      OptimizationConfig(algorithm="slsqp", max_evaluations=200))
    accepted = [a for a in result.actual_archive if a.point_id in result.selected_ids]
    assert accepted
    assert accepted[0].values["x"] == pytest.approx(1-tolerance, abs=1e-5)


def test_budget_and_cancel_keep_finished_valid_observations():
    evaluator = AnalyticEvaluator(lambda v: {"f": v["x"]**2})
    result = optimize(problem(), evaluator, OptimizationConfig(algorithm="slsqp", max_evaluations=1))
    assert result.termination_reason == "BUDGET_EXHAUSTED"
    assert len(result.actual_archive) == 1 and len(evaluator.requests) == 1
    cancel = threading.Event()
    result = optimize(problem(), AnalyticEvaluator(lambda v: {"f": v["x"]**2}, cancel=cancel),
                      OptimizationConfig(algorithm="slsqp"), cancel)
    assert result.termination_reason == "CANCELLED" and len(result.actual_archive) == 1
    cancelled = threading.Event(); cancelled.set()
    result = optimize(problem(), evaluator, OptimizationConfig(algorithm="slsqp"), cancelled)
    assert result.termination_reason == "CANCELLED" and not result.actual_archive


def test_missing_objective_invalid_and_marginal_never_accepted():
    result = optimize(problem(), AnalyticEvaluator(lambda v: {}), OptimizationConfig(algorithm="slsqp"))
    assert result.termination_reason == "FAILED" and not result.actual_archive and result.failures
    p = problem(constraints=(ConstraintSpec(id="c", response_id="c", kind="UPPER", limit=0, marginal_band=2),))
    result = optimize(p, AnalyticEvaluator(lambda v: {"f": v["x"]**2, "c": 1}), OptimizationConfig(algorithm="slsqp", max_iterations=3))
    assert result.termination_reason == "NO_FEASIBLE_DESIGN"
    assert result.actual_archive and not result.selected_ids
    assert all(a.feasibility == "MARGINAL" for a in result.actual_archive)


def test_predictions_separate_and_evaluator_identity_contract():
    result = optimize(problem(mode="SURROGATE"), AnalyticEvaluator(lambda v: {"f": (v["x"]-.5)**2}, mode="SURROGATE"),
                      OptimizationConfig(algorithm="slsqp", max_evaluations=80))
    assert not result.actual_archive and result.predicted_archive
    assert result.budget_usage.predictions == len(result.predicted_archive)
    with pytest.raises(ValueError, match="mode mismatch"):
        optimize(problem(), AnalyticEvaluator(lambda v: {"f": 0}, mode="SURROGATE"), OptimizationConfig(algorithm="slsqp"))
    class BadEvaluator:
        def evaluate_batch(self, requests):
            return {"wrong": EvaluationObservation(request_id="wrong", source_id="test", responses={"f": 0})}
    with pytest.raises(ValueError, match="exactly"):
        optimize(problem(), BadEvaluator(), OptimizationConfig(algorithm="slsqp"))


def test_de_nonuniform_discrete_levels_reproducible_and_categories_rejected():
    space = VariableSpace(variables=(DiscreteVariable(id="d", name="D", unit="", values=(1., 3., 20.), initial=1.),))
    p = problem(space=space)
    config = OptimizationConfig(algorithm="de", seed=10, population_size=8, max_iterations=8, max_evaluations=40)
    function = lambda v: {"f": (v["d"]-3.)**2}
    first, second = AnalyticEvaluator(function), AnalyticEvaluator(function)
    a, b = optimize(p, first, config), optimize(p, second, config)
    assert [dict(r.values) for r in first.requests] == [dict(r.values) for r in second.requests]
    assert len(first.requests) <= 3
    assert a.actual_archive and a.selected_ids == b.selected_ids
    chosen = next(v for v in a.actual_archive if v.point_id == a.selected_ids[0])
    assert chosen.values["d"] == 3
    categories = VariableSpace(variables=(CategoricalVariable(id="c", name="C", choices=("a", "b"), initial="a"),))
    with pytest.raises(ValueError, match="categories"):
        optimize(problem(space=categories), first, config)
    with pytest.raises(ValueError, match="continuous"):
        optimize(p, first, OptimizationConfig(algorithm="slsqp"))


def test_de_constraint_sign_and_scalarization_gate():
    p = problem(constraints=(ConstraintSpec(id="c", response_id="x", kind="LOWER", limit=1, tolerance=.1, scale=10),))
    evaluator = AnalyticEvaluator(lambda v: {"f": v["x"]**2, "x": v["x"]})
    result = optimize(p, evaluator, OptimizationConfig(algorithm="de", seed=1, max_iterations=50, max_evaluations=500))
    chosen = next(v for v in result.actual_archive if v.point_id == result.selected_ids[0])
    assert chosen.values["x"] == pytest.approx(.9, abs=.002)
    multi = problem(objectives=(ObjectiveSpec(id="a",response_id="a"), ObjectiveSpec(id="b",response_id="b")))
    with pytest.raises(ValueError, match="weighted_sum"):
        optimize(multi, evaluator, OptimizationConfig(algorithm="de"))


@pytest.mark.skipif(importlib.util.find_spec("pymoo") is None, reason="pymoo not installed; real NSGA-II backend remains unverified")
def test_real_mixed_nsga2_rank_crowding_and_valid_decoding_seeded():
    space = VariableSpace(variables=(ContinuousVariable(id="x",name="X",unit="",lower=0,upper=1,initial=.5),
        DiscreteVariable(id="d",name="D",unit="",values=(1.,3.,20.),initial=3.),
        CategoricalVariable(id="c",name="C",choices=("steel","aluminum"),initial="steel")))
    p = problem(space=space, objectives=(ObjectiveSpec(id="f",response_id="f"),ObjectiveSpec(id="g",response_id="g")),
                constraints=(ConstraintSpec(id="bound",response_id="x",kind="LOWER",limit=.2),))
    f = lambda v: {"f": v["x"]**2+v["d"]*.01, "g": (1-v["x"])**2+(v["c"]=="steel")*.1, "x":v["x"]}
    config = OptimizationConfig(algorithm="nsga2",population_size=12,max_iterations=5,max_evaluations=100,seed=5)
    first, second = AnalyticEvaluator(f), AnalyticEvaluator(f)
    a,b=optimize(p,first,config),optimize(p,second,config)
    assert [dict(r.values) for r in first.requests] == [dict(r.values) for r in second.requests]
    assert a.selected_ids == b.selected_ids and len(a.actual_archive)>12
    assert all(r.values["d"] in (1.,3.,20.) and r.values["c"] in ("steel","aluminum") for r in first.requests)
    assert all(p.feasible for p in a.actual_archive if p.point_id in a.selected_ids)


def test_missing_nsga2_dependency_has_install_guidance(monkeypatch):
    import builtins
    original = builtins.__import__
    def blocked(name, *args, **kwargs):
        if name.startswith("pymoo"):
            raise ImportError("blocked test backend")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(ImportError, match=r"install caelab-workbench\[mdo\]"):
        optimize(problem(), AnalyticEvaluator(lambda v: {"f": 0}), OptimizationConfig(algorithm="nsga2"))


def test_transient_invalid_outcome_is_retried_across_batches_without_cache_hit():
    from caelab.mdo.optimize import _Ledger
    class Transient:
        def __init__(self):
            self.calls = []
        def evaluate_batch(self, requests):
            self.calls.append(requests)
            if len(self.calls) == 1:
                return {r.id: EvaluationObservation(request_id=r.id, valid=False, reason="transient extraction failure") for r in requests}
            return {r.id: EvaluationObservation(request_id=r.id, responses={"f": 1.}, source_id="analytic") for r in requests}
    evaluator = Transient()
    ledger = _Ledger(problem(), evaluator, OptimizationConfig(algorithm="de", max_failures=5), None)
    assert ledger.batch([[.5], [.5]]) == [None, None]
    assert ledger.hits == 0 and ledger.duplicates == 1 and len(ledger.failures) == 1
    point = ledger.batch([[.5]])[0]
    assert point is not None and len(evaluator.calls) == 2
    assert evaluator.calls[0][0].id != evaluator.calls[1][0].id
    assert ledger.hits == 0
    assert ledger.batch([[.5]])[0] is point and ledger.hits == 1


def test_failure_scores_cannot_beat_valid_large_finite_values():
    from caelab.mdo.optimize import _nsga_scores
    from types import SimpleNamespace
    f, g = _nsga_scores([None, SimpleNamespace(objectives=np.array([1e40]), total_violation=1e40)], 1)
    assert np.isinf(g[0,0]) and g[1,0] < g[0,0]
    space = VariableSpace(variables=(DiscreteVariable(id="d",name="D",unit="",values=(0.,1.),initial=1.),))
    class FailOrLarge:
        def evaluate_batch(self, requests):
            return {r.id: (EvaluationObservation(request_id=r.id,valid=False,reason="failed") if r.values["d"]==0 else
                EvaluationObservation(request_id=r.id,responses={"f":1e40},source_id="large valid analytic")) for r in requests}
    result=optimize(problem(space=space),FailOrLarge(),OptimizationConfig(algorithm="de",max_failures=100,max_iterations=4,seed=2))
    assert result.selected_ids
    assert len(result.actual_archive)==1 and result.actual_archive[0].values["d"]==1
    assert result.actual_archive[0].objectives[0]==1e40 and result.failures


def test_slsqp_initialization_uses_overflow_safe_public_codec():
    space=VariableSpace(variables=(ContinuousVariable(id="x",name="X",unit="",lower=-1e308,upper=1e308,initial=0.),))
    evaluator=AnalyticEvaluator(lambda values: {"f": (values["x"]/1e308)**2})
    result=optimize(problem(space=space),evaluator,OptimizationConfig(algorithm="slsqp",max_evaluations=5))
    assert evaluator.requests[0].values["x"]==0 and result.selected_ids


@pytest.mark.parametrize("extra", [np.nan, np.inf, -np.inf, {"mutable":1}, [1.], "number", True])
def test_observation_and_archive_reject_nonfinite_or_mutable_extra_responses(extra):
    with pytest.raises(ValueError,match="finite numeric"):
        optimize(problem(),AnalyticEvaluator(lambda v:{"f":v["x"]**2,"unused":extra}),OptimizationConfig(algorithm="slsqp"))
    result=optimize(problem(),AnalyticEvaluator(lambda v:{"f":v["x"]**2}),OptimizationConfig(algorithm="slsqp",max_evaluations=1))
    with pytest.raises(ValueError,match="finite numeric"):
        replace(result.actual_archive[0],responses={"f":1.,"unused":extra})


def test_extra_nullable_response_preserved_but_required_none_unassessed():
    result=optimize(problem(),AnalyticEvaluator(lambda v:{"f":v["x"]**2,"unused":None}),OptimizationConfig(algorithm="slsqp",max_evaluations=1))
    assert result.actual_archive[0].responses["unused"] is None
    result=optimize(problem(),AnalyticEvaluator(lambda v:{"f":None}),OptimizationConfig(algorithm="slsqp"))
    assert not result.actual_archive and result.failures


def archive_record(**changes):
    from caelab.mdo.optimize import ArchivePoint
    fields=dict(point_id="p",request_id="r",values={"x":1.},responses={"f":1.},objectives=[1.],
        normalized_residuals=[0.],raw_residuals=[.1],total_violation=0.,feasible=True,feasibility="FEASIBLE",
        mode="ACTUAL",problem_revision="1",criteria_revision="1",source_id="analytic")
    fields.update(changes)
    return ArchivePoint(**fields)


@pytest.mark.parametrize("changes", [
    {"normalized_residuals":[1.],"total_violation":1.},
    {"normalized_residuals":[-1.],"total_violation":1.},
    {"normalized_residuals":[1.],"total_violation":0.},
    {"feasible":False,"feasibility":"INFEASIBLE"},
    {"feasible":False,"feasibility":"MARGINAL","normalized_residuals":[1.],"total_violation":0.},
    {"feasible":False,"feasibility":"INFEASIBLE","normalized_residuals":[0.],"total_violation":1.},
    {"feasibility":"UNKNOWN"}, {"feasible":1}, {"objectives":[]}, {"objectives":[[1.]]},
    {"normalized_residuals":[[0.]]}, {"raw_residuals":[[0.]]}, {"raw_residuals":[0.,0.]},
    {"point_id":""}, {"request_id":" "}, {"problem_revision":""}, {"criteria_revision":None},
    {"source_id":None}, {"source_id":""}, {"run_ids":("",)},
    {"mode":"SURROGATE","source_id":None,"run_ids":("run",)},
])
def test_archive_constructor_rejects_contradictory_state_or_missing_provenance(changes):
    with pytest.raises(ValueError):
        archive_record(**changes)


def test_archive_acceptance_uses_adjusted_residuals_without_inventing_weights():
    from caelab.mdo.pareto import feasible_front
    accepted=archive_record()
    assert feasible_front([accepted]) == (accepted,)
    for state in ("MARGINAL","INFEASIBLE"):
        violated=archive_record(feasible=False,feasibility=state,normalized_residuals=[.1],total_violation=5.)
        assert not feasible_front([violated])
    assert archive_record(source_id=None,run_ids=["run"]).run_ids == ("run",)
    assert archive_record(normalized_residuals=[],raw_residuals=[]).feasible
    assert archive_record(mode="SURROGATE",source_id="model:v1").mode == "SURROGATE"
