"""Analytic sensitivity experiments with genuine SALib when installed."""
from dataclasses import replace
import importlib.util
import numpy as np
import pytest
from caelab.mdo.variables import VariableSpace, ContinuousVariable, CategoricalVariable
from caelab.mdo.sensitivity import (SensitivityConfig, SensitivityDesign, SensitivityObservation,
    generate_sensitivity_design, analyze_sensitivity, spearman)


SALIB = importlib.util.find_spec("SALib") is not None
requires_salib = pytest.mark.skipif(not SALIB, reason="SALib not installed; real Sobol/Morris backend remains unverified")


def space(bounds=((0, 1), (0, 1)), log=False):
    return VariableSpace(variables=tuple(ContinuousVariable(id=f"x{i}", name=f"X{i}", unit="", lower=lo, upper=hi,
                 initial=(lo+hi)/2, transform="log" if log else "linear") for i, (lo, hi) in enumerate(bounds)))


def observations(design, function):
    values = function(design.values)
    return {id: SensitivityObservation(value=float(y), source_id=f"analytic:{id}") for id, y in reversed(tuple(zip(design.row_ids, values)))}


@requires_salib
def test_sobol_additive_indices_and_seed_zero_repeatability():
    config = SensitivityConfig(base_samples=2048, seed=0, resamples=100)
    design = generate_sensitivity_design(space(), config)
    assert len(design.values) == 2048*4
    assert design.analyzer_seed > 0
    assert not design.values.flags.writeable
    obs = observations(design, lambda x: x[:, 0] + 2*x[:, 1])
    result = analyze_sensitivity(design, obs, output_unit="N")
    again = analyze_sensitivity(design, obs, output_unit="N")
    np.testing.assert_allclose(result.estimates["S1"], [.2, .8], atol=.005)
    np.testing.assert_allclose(result.estimates["ST"], [.2, .8], atol=.005)
    np.testing.assert_array_equal(result.estimates["S1_conf"], again.estimates["S1_conf"])
    assert result.source_mode == "ANALYTIC"
    assert result.observation_ids[0] == f"analytic:{design.row_ids[0]}"
    assert result.metadata["interval_method"] == "bootstrap normal half-width"


@requires_salib
def test_sobol_interaction_and_invalid_row_never_dropped():
    design = generate_sensitivity_design(space(((-1, 1), (-1, 1))), SensitivityConfig(base_samples=4096, resamples=100, seed=4))
    obs = observations(design, lambda x: x[:, 0]*x[:, 1])
    result = analyze_sensitivity(design, obs)
    np.testing.assert_allclose(result.estimates["S1"], [0, 0], atol=.015)
    np.testing.assert_allclose(result.estimates["ST"], [1, 1], atol=.015)
    obs[design.row_ids[3]] = SensitivityObservation(value=None, valid=False, reason="solver failed", mode="ACTUAL")
    with pytest.raises(ValueError, match="invalid rows"):
        analyze_sensitivity(design, obs)
    del obs[design.row_ids[3]]
    with pytest.raises(ValueError, match="match every paired row"):
        analyze_sensitivity(design, obs)
    with pytest.raises(ValueError, match="constant"):
        analyze_sensitivity(design, observations(design, lambda x: np.ones(len(x))))


@requires_salib
def test_morris_linear_physical_ranges_coded_effect_units():
    design = generate_sensitivity_design(space(((2, 5), (10, 20))), SensitivityConfig(method="morris", trajectories=30, resamples=100, seed=1))
    assert len(design.values) == 30*3
    result = analyze_sensitivity(design, observations(design, lambda x: x[:, 0] - 2*x[:, 1]), output_unit="N")
    np.testing.assert_allclose(result.estimates["mu"], [3., -20.], atol=1e-12)
    np.testing.assert_allclose(result.estimates["mu_star"], [3., 20.], atol=1e-12)
    np.testing.assert_allclose(result.estimates["sigma"], 0., atol=1e-12)
    assert result.metadata["effect_unit"] == "N per normalized-factor step"
    assert result.metadata["delta"] == pytest.approx(2/3)


@requires_salib
def test_physical_uniform_sampling_ignores_log_optimizer_transform():
    config = SensitivityConfig(base_samples=1024)
    linear = generate_sensitivity_design(space(((1, 100), (1, 100))), config)
    logarithmic = generate_sensitivity_design(space(((1, 100), (1, 100)), log=True), config)
    np.testing.assert_array_equal(linear.values, logarithmic.values)
    np.testing.assert_allclose(logarithmic.values.mean(axis=0), [50.5, 50.5], atol=.2)


@requires_salib
@pytest.mark.parametrize("method", ["sobol", "morris"])
def test_constructor_rejects_random_matrix_and_mixed_provenance(method):
    config = SensitivityConfig(method=method, base_samples=8, trajectories=4, resamples=10)
    design = generate_sensitivity_design(space(), config)
    bad = np.random.default_rng(2).uniform(0, 1, design.values.shape)
    with pytest.raises(ValueError, match="paired block|grid|trajectory"):
        replace(design, values=bad)
    obs = observations(design, lambda x: x[:, 0]+x[:, 1])
    obs[design.row_ids[0]] = SensitivityObservation(value=2., mode="SURROGATE", source_id="model:v1")
    with pytest.raises(ValueError, match="separately"):
        analyze_sensitivity(design, obs)


def test_method_applicability_budget_and_missing_dependency(monkeypatch):
    with pytest.raises(ValueError, match="power of two"):
        SensitivityConfig(base_samples=10)
    with pytest.raises(ValueError, match="budget"):
        generate_sensitivity_design(space(), SensitivityConfig(max_evaluations=2))
    category = VariableSpace(variables=(CategoricalVariable(id="c", name="C", choices=("a", "b"), initial="a"),))
    with pytest.raises(ValueError, match="continuous"):
        generate_sensitivity_design(category)
    import caelab.mdo.sensitivity as module
    import importlib
    original = importlib.import_module
    def missing(name):
        if name.startswith("SALib"):
            raise ImportError("blocked test backend")
        return original(name)
    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(ImportError, match=r"install caelab-workbench\[mdo\]"):
        module.generate_sensitivity_design(space())


def test_spearman_average_ties_constants_pair_masks_and_sign():
    x = np.array([[1, 1], [2, 1], [2, 1], [4, 1], [np.nan, 1]])
    y = np.array([4, 3, 3, 1, np.nan])
    result = spearman(x, y, variable_ids=("varying", "constant"), response_ids=("y",))
    assert result[0].rho == pytest.approx(-1)
    assert result[0].sample_count == 4 and result[0].missing_count == 1
    assert result[0].row_indices == (0, 1, 2, 3)
    assert result[1].rho is None and "constant" in result[1].reason
    assert spearman([np.nan], [1])[0].reason == "insufficient paired observations"
