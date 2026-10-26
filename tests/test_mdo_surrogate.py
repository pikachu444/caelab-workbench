"""Synthetic analytic responses; no native CAE run is implied."""
from dataclasses import replace
import numpy as np
import pytest
from caelab.mdo.surrogate import (SurrogateDataset, SurrogateConfig, HoldoutSplit, grouped_holdout,
    fit_surrogate, predict, quadratic_basis, save_surrogate, load_surrogate)


def dataset(x, y, **kw):
    x = np.asarray(x, float)
    if x.ndim == 1:
        x = x[:, None]
    y = np.asarray(y, float)
    if y.ndim == 1:
        y = y[:, None]
    return SurrogateDataset(x=x, y=y, point_ids=tuple(f"p{i}" for i in range(len(x))),
        run_ids=tuple(f"run{i}" for i in range(len(x))), response_ids=tuple(f"r{i}" for i in range(y.shape[1])),
        units=tuple("N" for _ in range(y.shape[1])), **kw)


def test_prs_recovers_full_quadratic_independent_polynomial():
    x = np.array(list(__import__('itertools').product([-2., 0., 2.], [-3., 0., 3.])))
    function = lambda x: 3 + 2*x[:, 0] - 5*x[:, 1] + .7*x[:, 0]**2 + 1.2*x[:, 1]**2 + 4*x[:, 0]*x[:, 1]
    data = dataset(x, function(x))
    model = fit_surrogate(data, SurrogateConfig(method="prs"))
    probe = np.random.default_rng(12).uniform([-2, -3], [2, 3], (20, 2))
    prediction = predict(model, probe)
    np.testing.assert_allclose(prediction.mean[:, 0], function(probe), atol=1e-12)
    assert prediction.variance is None and model.diagnostics["rank"] == 6
    assert not model.qualified
    assert quadratic_basis(probe).shape == (20, 6)
    with pytest.raises(ValueError, match="rank deficient"):
        fit_surrogate(dataset(np.column_stack([np.arange(10), np.arange(10)]), np.arange(10)), SurrogateConfig(method="prs"))
    with pytest.raises(ValueError, match="continuous"):
        fit_surrogate(replace(data, continuous_only=False), SurrogateConfig(method="prs"))


def test_gp_fixed_kernel_matches_independent_covariance_solve_and_noise_units():
    data = dataset([0., 1.], [10., 14.])
    config = SurrogateConfig(gp_kernel="squared_exponential", length_scale=.4, amplitude=1.7,
        optimize_hyperparameters=False, noise_variance=.3, jitter=1e-8)
    model = fit_surrogate(data, config)
    probe = np.array([[.25], [.7]])
    kernel = lambda a, b: 1.7 * np.exp(-.5 * ((a[:, None] - b[None, :])/.4)**2)
    k = kernel(probe[:, 0], np.array([0., 1.]))
    covariance = kernel(np.array([0., 1.]), np.array([0., 1.])) + np.eye(2)*(.3/4+1e-8)
    expected_mean = 12 + k @ np.linalg.solve(covariance, [-1., 1.])*2
    expected_variance = (1.7 - np.sum(k * np.linalg.solve(covariance, k.T).T, axis=1))*4
    prediction = predict(model, probe)
    np.testing.assert_allclose(prediction.mean[:, 0], expected_mean, atol=1e-12)
    np.testing.assert_allclose(prediction.variance[:, 0], expected_variance, atol=1e-12)
    observation = predict(model, probe, variance_kind="observation", observation_noise_variance=.8)
    np.testing.assert_allclose(observation.variance - prediction.variance, .8, atol=1e-14)
    scaled = fit_surrogate(dataset([0., 1.], np.array([10., 14.])*7+100), replace(config, noise_variance=.3*49))
    np.testing.assert_allclose(predict(scaled, probe).variance, prediction.variance*49, rtol=1e-12)
    np.testing.assert_allclose(predict(scaled, probe).mean, prediction.mean*7+100, rtol=1e-12)
    with pytest.raises(ValueError, match="budget"):
        fit_surrogate(data, replace(config, max_gp_rows=1))


def test_gp_variance_negative_gate_respects_covariance_scale():
    from types import SimpleNamespace
    data = dataset([0.], [1.])
    model = fit_surrogate(data, SurrogateConfig(optimize_hyperparameters=False))
    class Kernel:
        def __init__(self, scale, defect):
            self.scale, self.defect = scale, defect
        def __call__(self, x, y):
            return np.full((len(x), len(y)), np.sqrt(self.scale*(1+self.defect)))
        def diag(self, x):
            return np.full(len(x), self.scale)
    for scale in (1., 1e-6):
        for defect, reject in ((1e-15, False), (1e-6, True)):
            estimator = SimpleNamespace(kernel_=Kernel(scale, defect), X_train_=np.zeros((1,1)), L_=np.eye(1), predict=lambda x: np.zeros(len(x)))
            artifact = replace(model, models=(estimator,))
            if reject:
                with pytest.raises(ValueError, match="negative GP variance"):
                    predict(artifact, [[0.]])
            else:
                assert predict(artifact, [[0.]]).variance[0,0] == 0


def test_grouped_holdout_merges_identical_designs_runs_and_groups():
    data = dataset([0., 0., 1., 2., 3., 4.], [0., 0., 1., 2., 3., 4.], groups=("g0", "other", "g1", "g2", "g3", "g4"))
    split = grouped_holdout(data, seed=5)
    assert (0 in split.train_indices) == (1 in split.train_indices)
    with pytest.raises(ValueError, match="cross holdout"):
        fit_surrogate(data, SurrogateConfig(method="rbf", rbf_kernel="linear", degree=0),
                      HoldoutSplit(train_indices=(0, 2, 3, 4, 5), validation_indices=(1,)))
    conflicting = dataset([0., 0., 1.], [1., 2., 3.])
    with pytest.raises(ValueError, match="conflicting"):
        fit_surrogate(conflicting, SurrogateConfig(optimize_hyperparameters=False))
    noisy = fit_surrogate(conflicting, SurrogateConfig(noise_variance=.1, optimize_hyperparameters=False))
    assert len(noisy.models[0].X_train_) == 3


def test_holdout_scaling_metrics_constant_r2_and_qualification():
    data = dataset([0., 1., 2., 100., 101.], [5., 5., 5., 5., 5.])
    split = HoldoutSplit(train_indices=(0, 1, 2), validation_indices=(3, 4))
    model = fit_surrogate(data, SurrogateConfig(method="prs", qualification_rmse=(.01,)), split)
    assert model.x_scale[0] == 2
    assert model.metrics[0].r2 is None
    assert model.metrics[0].mae == 0 and model.qualified
    assert predict(model, [[100.]]).domain_warning[0]
    with pytest.raises(ValueError):
        data.x[0, 0] = 2
    with pytest.raises(ValueError):
        data.x.setflags(write=True)
    assert not predict(model, [[1.]]).mean.flags.writeable


@pytest.mark.parametrize("kernel,degree,epsilon", [("thin_plate_spline",1,None), ("linear",0,None), ("multiquadric",0,1.)])
def test_real_scipy_rbf_interpolates_without_fabricated_variance(kernel, degree, epsilon):
    data = dataset(np.linspace(0, 1, 8), np.sin(np.linspace(0, 1, 8)))
    model = fit_surrogate(data, SurrogateConfig(method="rbf", rbf_kernel=kernel, degree=degree, epsilon=epsilon))
    prediction = predict(model, data.x)
    np.testing.assert_allclose(prediction.mean[:, 0], data.y[:, 0], atol=1e-10)
    assert prediction.variance is None


def test_safe_refit_roundtrip_and_hash_rejects_modified_arrays(tmp_path):
    data = dataset(np.linspace(-1, 1, 8), np.linspace(-1, 1, 8)**2)
    model = fit_surrogate(data, SurrogateConfig(method="prs"))
    save_surrogate(model, tmp_path)
    restored = load_surrogate(tmp_path)
    np.testing.assert_allclose(predict(model, [[.3]]).mean, predict(restored, [[.3]]).mean)
    with (tmp_path / "arrays.npz").open("ab") as stream:
        stream.write(b"corrupted")
    with pytest.raises(ValueError, match="hash"):
        load_surrogate(tmp_path)


def test_chronological_holdout_rejects_interleaved_future_group_leakage():
    data = dataset([0., 1., 2., 3., 4., 5.], [0., 1., 2., 3., 4., 5.], groups=("repeat","one","two","three","four","repeat"))
    with pytest.raises(ValueError,match="chronological cutoff"):
        grouped_holdout(data,fraction=.25,chronological=True)
    clean = dataset(np.arange(10.),np.arange(10.))
    split = grouped_holdout(clean,fraction=.2,chronological=True)
    assert max(split.train_indices)<min(split.validation_indices)
