"""Independent-response surrogate adapters; no solver or legacy numerical imports.

Input features use the frozen codec's numeric coordinates. Scaling is learned on
training rows only; GP variance is returned in squared physical response units.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from importlib.metadata import version
from types import MappingProxyType
from typing import Mapping
import warnings
import numpy as np


def _array(value, ndim=None):
    a = np.array(value, dtype=float, copy=True)
    if (ndim is not None and a.ndim != ndim) or not np.isfinite(a).all():
        raise ValueError("expected finite numeric array of the declared dimension")
    return np.frombuffer(a.tobytes(), dtype=float).reshape(a.shape)


def _positive(value, name, zero=False):
    if not np.isfinite(value) or (value < 0 if zero else value <= 0):
        raise ValueError(f"{name} must be finite and {'nonnegative' if zero else 'positive'}")


@dataclass(frozen=True, slots=True, kw_only=True)
class SurrogateDataset:
    x: np.ndarray
    y: np.ndarray
    point_ids: tuple[str, ...]
    run_ids: tuple[str, ...]
    response_ids: tuple[str, ...]
    units: tuple[str, ...]
    groups: tuple[str, ...] = ()
    feature_order: tuple[str, ...] = ()
    codec_version: str = "1"
    training_revision: str = "1"
    continuous_only: bool = True
    excluded_reasons: tuple[str, ...] = ()

    def __post_init__(self):
        x, y = _array(self.x, 2), _array(self.y, 2)
        n, d = x.shape
        if n == 0 or d == 0 or y.shape[0] != n or y.shape[1] == 0:
            raise ValueError("nonempty matching input/output rows required")
        for name in ("point_ids", "run_ids", "response_ids", "units", "groups", "feature_order", "excluded_reasons"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if len(self.point_ids) != n or len(self.run_ids) != n or len(set(self.point_ids)) != n:
            raise ValueError("unique point IDs and run IDs required for every row")
        if any(not isinstance(i, str) or not i for i in self.point_ids + self.run_ids + self.response_ids):
            raise ValueError("provenance/response IDs must be nonempty")
        if len(self.response_ids) != y.shape[1] or len(self.units) != y.shape[1] or len(set(self.response_ids)) != y.shape[1]:
            raise ValueError("response metadata must match columns")
        if any(not isinstance(u, str) for u in self.units):
            raise ValueError("response units must be explicit strings")
        if self.groups and len(self.groups) != n:
            raise ValueError("groups must match rows")
        if not self.codec_version or not self.training_revision:
            raise ValueError("frozen codec/training revisions required")
        if self.feature_order and len(self.feature_order) != d:
            raise ValueError("feature order must match input columns")
        object.__setattr__(self, "x", x)
        object.__setattr__(self, "y", y)


@dataclass(frozen=True, slots=True, kw_only=True)
class HoldoutSplit:
    train_indices: tuple[int, ...]
    validation_indices: tuple[int, ...]
    method: str = "explicit_grouped_holdout"
    seed: int = 0

    def __post_init__(self):
        for key in ("train_indices", "validation_indices"):
            a = tuple(getattr(self, key))
            if any(type(i) is not int or i < 0 for i in a) or len(set(a)) != len(a):
                raise ValueError("split indices must be distinct nonnegative integers")
            object.__setattr__(self, key, a)
        if not self.train_indices or set(self.train_indices) & set(self.validation_indices):
            raise ValueError("training split must be nonempty and disjoint")


def _components(dataset):
    parent = list(range(len(dataset.x)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen_x, seen_group, seen_run = {}, {}, {}
    for i, row in enumerate(dataset.x):
        for key, seen in ((tuple(row), seen_x), (dataset.run_ids[i], seen_run)):
            if key in seen:
                parent[find(i)] = find(seen[key])
            else:
                seen[key] = i
        if dataset.groups:
            key = dataset.groups[i]
            if key in seen_group:
                parent[find(i)] = find(seen_group[key])
            else:
                seen_group[key] = i
    groups = {}
    for i in range(len(parent)):
        groups.setdefault(find(i), []).append(i)
    return tuple(tuple(v) for v in groups.values())


def grouped_holdout(dataset, *, fraction=0.2, seed=0, chronological=False):
    if not np.isfinite(fraction) or not 0 < fraction < 1:
        raise ValueError("holdout fraction must lie between zero and one")
    groups = list(_components(dataset))
    if len(groups) < 2:
        raise ValueError("holdout needs at least two independent groups")
    if not chronological:
        np.random.default_rng(seed).shuffle(groups)
    count = max(1, min(len(groups) - 1, int(np.ceil(len(groups) * fraction))))
    validation = set(i for group in groups[-count:] for i in group)
    training = set(range(len(dataset.x))) - validation
    if chronological and max(training) >= min(validation):
        raise ValueError("grouped replicates cross chronological cutoff; choose a separate final holdout or explicit purged policy")
    return HoldoutSplit(train_indices=tuple(i for i in range(len(dataset.x)) if i not in validation),
                        validation_indices=tuple(sorted(validation)),
                        method="chronological_grouped_holdout" if chronological else "seeded_grouped_holdout", seed=seed)


@dataclass(frozen=True, slots=True, kw_only=True)
class SurrogateConfig:
    method: str = "gp"
    seed: int = 0
    gp_kernel: str = "matern52"
    length_scale: float = 1.0
    amplitude: float = 1.0
    optimize_hyperparameters: bool = True
    restarts: int = 0
    noise_variance: float = 0.0
    jitter: float = 1e-10
    max_jitter: float = 1e-5
    max_gp_rows: int = 5000
    rbf_kernel: str = "thin_plate_spline"
    smoothing: float = 0.0
    degree: int = 1
    epsilon: float | None = None
    neighbors: int | None = None
    condition_warning: float = 1e8
    condition_reject: float = 1e12
    qualification_rmse: tuple[float, ...] = ()

    def __post_init__(self):
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed must be nonnegative integer")
        if self.method not in ("gp", "rbf", "prs") or self.gp_kernel not in ("matern52", "matern32", "squared_exponential"):
            raise ValueError("unsupported surrogate/kernel")
        for name in ("length_scale", "amplitude", "jitter", "max_jitter", "condition_warning", "condition_reject"):
            _positive(getattr(self, name), name)
        for name in ("noise_variance", "smoothing"):
            _positive(getattr(self, name), name, zero=True)
        if self.max_jitter < self.jitter or self.condition_reject < self.condition_warning:
            raise ValueError("invalid jitter or condition thresholds")
        if type(self.max_gp_rows) is not int or self.max_gp_rows < 1 or type(self.restarts) is not int or self.restarts < 0:
            raise ValueError("invalid GP resource limits")
        if self.rbf_kernel not in ("thin_plate_spline", "linear", "multiquadric"):
            raise ValueError("unsupported RBF kernel")
        if type(self.degree) is not int or self.degree < (1 if self.rbf_kernel == "thin_plate_spline" else 0):
            raise ValueError("RBF polynomial degree below kernel minimum")
        if self.epsilon is not None:
            _positive(self.epsilon, "epsilon")
        if self.method == "rbf" and self.rbf_kernel == "multiquadric" and self.epsilon is None:
            raise ValueError("multiquadric requires explicit epsilon")
        if self.neighbors is not None and (type(self.neighbors) is not int or self.neighbors < 1):
            raise ValueError("neighbors must be a positive integer")
        object.__setattr__(self, "qualification_rmse", tuple(self.qualification_rmse))
        for x in self.qualification_rmse:
            _positive(x, "qualification_rmse", zero=True)


@dataclass(frozen=True, slots=True)
class ValidationMetrics:
    response_id: str
    unit: str
    n: int
    rmse: float
    mae: float
    maxae: float
    r2: float | None


@dataclass(frozen=True, slots=True)
class PredictionBatch:
    mean: np.ndarray
    variance: np.ndarray | None
    variance_kind: str
    response_ids: tuple[str, ...]
    units: tuple[str, ...]
    model_id: str
    training_revision: str
    domain_warning: np.ndarray

    def __post_init__(self):
        mean = _array(self.mean, 2)
        object.__setattr__(self, "mean", mean)
        if self.variance is not None:
            variance = _array(self.variance, 2)
            if variance.shape != mean.shape or np.any(variance < 0):
                raise ValueError("variance must match mean and be nonnegative")
            object.__setattr__(self, "variance", variance)
        warning = np.array(self.domain_warning, dtype=bool, copy=True)
        if warning.shape != (len(mean),):
            raise ValueError("domain warning must match rows")
        warning = np.frombuffer(warning.tobytes(), dtype=bool).reshape(warning.shape)
        object.__setattr__(self, "domain_warning", warning)


@dataclass(frozen=True, slots=True, kw_only=True)
class SurrogateArtifact:
    id: str
    dataset: SurrogateDataset
    config: SurrogateConfig
    split: HoldoutSplit
    x_offset: np.ndarray
    x_scale: np.ndarray
    y_offset: np.ndarray
    y_scale: np.ndarray
    models: tuple = field(repr=False)
    diagnostics: Mapping = field(default_factory=dict)
    metrics: tuple[ValidationMetrics, ...] = ()
    warnings: tuple[str, ...] = ()
    qualified: bool = False

    def __post_init__(self):
        for name in ("x_offset", "x_scale", "y_offset", "y_scale"):
            object.__setattr__(self, name, _array(getattr(self, name), 1))
        object.__setattr__(self, "diagnostics", MappingProxyType(dict(self.diagnostics)))
        object.__setattr__(self, "models", tuple(self.models))


def quadratic_basis(x):
    x = _array(x, 2)
    return np.column_stack([np.ones(len(x)), *x.T, *(x[:, i] ** 2 for i in range(x.shape[1])),
                            *(x[:, i] * x[:, j] for i in range(x.shape[1]) for j in range(i + 1, x.shape[1]))])


def _optional(module, extra):
    import importlib
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(f"{module} is required; install caelab-workbench[mdo] ({extra})") from exc


def _check_split(dataset, split):
    train, val = set(split.train_indices), set(split.validation_indices)
    if train | val != set(range(len(dataset.x))):
        raise ValueError("split must cover exactly the dataset")
    if any(train.intersection(group) and val.intersection(group) for group in _components(dataset)):
        raise ValueError("identical designs, runs or grouped replicates cross holdout boundary")


def fit_surrogate(dataset, config=None, split=None, *, model_id="surrogate"):
    """Fit a surrogate metamodel (Kriging, RBF, or Polynomial) on dataset."""
    config = config or SurrogateConfig()
    split = split or HoldoutSplit(train_indices=tuple(range(len(dataset.x))), validation_indices=())
    _check_split(dataset, split)
    x, y = dataset.x[list(split.train_indices)], dataset.y[list(split.train_indices)]
    if config.method == "prs" and not dataset.continuous_only:
        raise ValueError("full quadratic PRS requires independent continuous features")
    offset, span = x.min(axis=0), np.ptp(x, axis=0)
    # A constant training coordinate is retained and exposed as an out-of-domain warning.
    scale = np.where(span > 0, span, 1.0)
    xx = (x - offset) / scale
    ymean, yscale = y.mean(axis=0), y.std(axis=0)
    yscale = np.where(yscale > 0, yscale, 1.0)
    yy = (y - ymean) / yscale
    notices = ["trained only on valid actual observations; failed-domain behavior is unknown"]
    diag = {"numpy": version("numpy"), "excluded_count": len(dataset.excluded_reasons),
            "training_rows": len(x), "validation_rows": len(split.validation_indices)}
    if np.any(span == 0):
        notices.append("one or more input features have no training variation")
    # Exact duplicate deterministic designs are grouped, with every provenance ID retained in dataset.
    unique, inverse = np.unique(xx, axis=0, return_inverse=True)
    if config.noise_variance == 0:
        for i in range(len(unique)):
            block = yy[inverse == i]
            if not np.all(block == block[0]):
                raise ValueError("conflicting deterministic replicates require explicit observation noise")
        first = [int(np.flatnonzero(inverse == i)[0]) for i in range(len(unique))]
        xx, yy = xx[first], yy[first]
    models = []
    if config.method == "prs":
        basis = quadratic_basis(2 * xx - 1)
        if len(basis) < basis.shape[1]:
            raise ValueError("full quadratic PRS has insufficient independent rows")
        coef, _, rank, singular = np.linalg.lstsq(basis, yy, rcond=None)
        condition = float(singular[0] / singular[-1]) if singular[-1] else float("inf")
        if rank != basis.shape[1] or condition > config.condition_reject:
            raise ValueError("quadratic PRS is rank deficient or ill conditioned")
        if condition > config.condition_warning:
            notices.append("quadratic PRS condition warning")
        if len(basis) == basis.shape[1]:
            notices.append("just-determined PRS cannot estimate residual variance")
        diag.update(rank=int(rank), singular_values=tuple(map(float, singular)), condition_number=condition)
        models = [coef[:, j] for j in range(yy.shape[1])]
        for m in models:
            m.setflags(write=False)
    elif config.method == "rbf":
        rbf = _optional("scipy.interpolate", "SciPy RBFInterpolator")
        for j in range(yy.shape[1]):
            models.append(rbf.RBFInterpolator(xx, yy[:, j], kernel=config.rbf_kernel, smoothing=config.smoothing,
                                             degree=config.degree, epsilon=config.epsilon, neighbors=config.neighbors))
        diag["scipy"] = version("scipy")
    else:
        if len(xx) > config.max_gp_rows:
            raise ValueError("exact GP exceeds configured row budget; no automatic subsampling")
        gp = _optional("sklearn.gaussian_process", "scikit-learn")
        kernels = _optional("sklearn.gaussian_process.kernels", "scikit-learn")
        length = np.full(xx.shape[1], config.length_scale)
        signal = (kernels.RBF(length, (1e-4, 1e4)) if config.gp_kernel == "squared_exponential" else
                  kernels.Matern(length, (1e-4, 1e4), nu=2.5 if config.gp_kernel == "matern52" else 1.5))
        kernel = kernels.ConstantKernel(config.amplitude, (1e-6, 1e6)) * signal
        chosen_jitter, parameters, likelihoods, conditions, bound_hits = [], [], [], [], []
        for j in range(yy.shape[1]):
            jitter = config.jitter
            while True:
                model = gp.GaussianProcessRegressor(kernel=kernel, alpha=config.noise_variance / yscale[j] ** 2 + jitter,
                         optimizer="fmin_l_bfgs_b" if config.optimize_hyperparameters else None,
                         n_restarts_optimizer=config.restarts, normalize_y=False, random_state=config.seed)
                try:
                    with warnings.catch_warnings(record=True) as caught:
                        warnings.simplefilter("always")
                        model.fit(xx, yy[:, j])
                    notices.extend(str(w.message) for w in caught)
                    break
                except np.linalg.LinAlgError:
                    if jitter >= config.max_jitter:
                        raise ValueError("GP covariance not positive definite within jitter budget") from None
                    jitter = min(jitter * 10, config.max_jitter)
            models.append(model)
            chosen_jitter.append(float(jitter))
            parameters.append(str(model.kernel_))
            likelihoods.append(float(model.log_marginal_likelihood_value_))
            conditions.append(float(np.linalg.cond(model.L_) ** 2))
            theta, bounds = model.kernel_.theta, model.kernel_.bounds
            bound_hits.append(bool(np.any(np.isclose(theta, bounds[:, 0])) or np.any(np.isclose(theta, bounds[:, 1]))))
        diag.update(sklearn=version("scikit-learn"), jitter=tuple(chosen_jitter), kernels=tuple(parameters),
                    log_marginal_likelihood=tuple(likelihoods), condition_number=tuple(conditions), bound_hits=tuple(bound_hits),
                    estimated_covariance_bytes=int(len(xx) ** 2 * 8))
    artifact = SurrogateArtifact(id=model_id, dataset=dataset, config=config, split=split, x_offset=offset, x_scale=scale,
                                y_offset=ymean, y_scale=yscale, models=tuple(models), diagnostics=diag, warnings=tuple(notices))
    metrics = ()
    if split.validation_indices:
        from dataclasses import replace
        observed = dataset.y[list(split.validation_indices)]
        predicted = predict(artifact, dataset.x[list(split.validation_indices)]).mean
        rows = []
        for j, response_id in enumerate(dataset.response_ids):
            error = observed[:, j] - predicted[:, j]
            sst = np.sum((observed[:, j] - observed[:, j].mean()) ** 2)
            rows.append(ValidationMetrics(response_id, dataset.units[j], len(error), float(np.sqrt(np.mean(error ** 2))),
                        float(np.mean(np.abs(error))), float(np.max(np.abs(error))),
                        None if sst == 0 or len(error) < 2 else float(1 - np.sum(error ** 2) / sst)))
        metrics = tuple(rows)
        limits = config.qualification_rmse
        if limits and len(limits) != len(metrics):
            raise ValueError("qualification tolerances must match responses")
        artifact = replace(artifact, metrics=metrics, qualified=bool(limits) and all(m.rmse <= t for m, t in zip(metrics, limits)))
    return artifact


def predict(model, points, *, variance_kind="latent", observation_noise_variance=None):
    """Predict mean responses and variance intervals for input points."""
    if variance_kind not in ("latent", "observation"):
        raise ValueError("variance kind must be latent or observation")
    x = _array(points, 2)
    if x.shape[1] != len(model.x_offset):
        raise ValueError("prediction feature count differs from frozen codec")
    xx = (x - model.x_offset) / model.x_scale
    means, variances = [], []
    for j, estimator in enumerate(model.models):
        if model.config.method == "prs":
            mean = quadratic_basis(2 * xx - 1) @ estimator
        elif model.config.method == "rbf":
            mean = estimator(xx)
        else:
            from scipy.linalg import solve_triangular

            mean = estimator.predict(xx)
            cross = estimator.kernel_(xx, estimator.X_train_)
            solved = solve_triangular(estimator.L_, cross.T, lower=True, check_finite=False)
            prior = estimator.kernel_.diag(xx)
            reduction = np.einsum("ij,ij->j", solved, solved)
            raw = prior - reduction
            magnitude = np.maximum(np.abs(prior) + np.abs(reduction), np.finfo(float).tiny)
            threshold = 128 * np.finfo(float).eps * magnitude * max(1, len(estimator.X_train_))
            if np.any(raw < -threshold):
                raise ValueError("materially negative GP variance")
            variances.append(np.maximum(raw, 0) * model.y_scale[j] ** 2)
        means.append(mean * model.y_scale[j] + model.y_offset[j])
    variance = np.column_stack(variances) if variances else None
    if variance is not None and variance_kind == "observation":
        noise = model.config.noise_variance if observation_noise_variance is None else observation_noise_variance
        noise = np.asarray(noise, dtype=float)
        if not np.isfinite(noise).all() or np.any(noise < 0) or noise.shape not in ((), (len(model.models),), variance.shape):
            raise ValueError("query noise variance must be nonnegative physical squared units")
        variance = variance + noise
    train = model.dataset.x[list(model.split.train_indices)]
    warning = np.any((x < train.min(axis=0)) | (x > train.max(axis=0)), axis=1)
    return PredictionBatch(np.column_stack(means), variance, variance_kind, model.dataset.response_ids,
                           model.dataset.units, model.id, model.dataset.training_revision, warning)


def save_surrogate(model, directory):
    """Safe arrays plus strict JSON; supported models reload by deterministic refit."""
    from dataclasses import asdict
    from pathlib import Path
    import hashlib
    import json
    destination = Path(directory)
    destination.mkdir(parents=True, exist_ok=True)
    dataset = model.dataset
    probe = predict(model, dataset.x)
    arrays = destination / "arrays.npz"
    np.savez_compressed(arrays, x=dataset.x, y=dataset.y, mean=probe.mean,
                        variance=np.empty((0, 0)) if probe.variance is None else probe.variance)
    metadata = {"schema_version": 1, "record_type": "SurrogateArtifact", "id": model.id,
                "config": asdict(model.config), "split": asdict(model.split),
                "dataset": {k: getattr(dataset, k) for k in dataset.__dataclass_fields__ if k not in ("x", "y")},
                "software_versions": {k: v for k, v in model.diagnostics.items() if k in ("numpy", "scipy", "sklearn")},
                "array_sha256": hashlib.sha256(arrays.read_bytes()).hexdigest(),
                "restore_method": "deterministic refit with prediction consistency check"}
    (destination / "model.json").write_text(json.dumps(metadata, allow_nan=False, indent=2), encoding="utf-8")


def load_surrogate(directory):
    from pathlib import Path
    import hashlib
    import json
    source = Path(directory)
    metadata = json.loads((source / "model.json").read_text(encoding="utf-8"),
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    if metadata.get("schema_version") != 1 or metadata.get("record_type") != "SurrogateArtifact":
        raise ValueError("unsupported surrogate artifact schema")
    arrays = source / "arrays.npz"
    if hashlib.sha256(arrays.read_bytes()).hexdigest() != metadata["array_sha256"]:
        raise ValueError("surrogate arrays hash mismatch")
    with np.load(arrays, allow_pickle=False) as data:
        dataset = SurrogateDataset(x=data["x"], y=data["y"], **metadata["dataset"])
        artifact = fit_surrogate(dataset, SurrogateConfig(**metadata["config"]), HoldoutSplit(**metadata["split"]), model_id=metadata["id"])
        prediction = predict(artifact, dataset.x)
        if not np.allclose(prediction.mean, data["mean"], rtol=1e-9, atol=1e-12):
            raise ValueError("refit prediction differs from saved artifact")
        stored_variance = data["variance"]
        if prediction.variance is None:
            if stored_variance.size:
                raise ValueError("saved variance semantics mismatch")
        elif not np.allclose(prediction.variance, stored_variance, rtol=1e-7, atol=1e-12):
            raise ValueError("refit variance differs from saved artifact")
    return artifact
