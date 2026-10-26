"""Paired SALib experiments and pairwise-rank Spearman diagnostics.

Sobol/Morris use independent physical-uniform continuous factors. Optimizer log
coordinates are deliberately irrelevant to these probability distributions.
"""
from __future__ import annotations
from dataclasses import dataclass
from importlib.metadata import version
from types import MappingProxyType
from typing import Mapping
import numpy as np


def _readonly(value):
    x = np.array(value, dtype=float, copy=True)
    if not np.isfinite(x).all():
        raise ValueError("experiment values must be finite")
    return np.frombuffer(x.tobytes(), dtype=float).reshape(x.shape)


def _salib(module):
    import importlib
    try:
        return importlib.import_module(f"SALib.{module}")
    except ImportError as exc:
        raise ImportError("SALib is required; install caelab-workbench[mdo] for Sobol/Morris") from exc


@dataclass(frozen=True, slots=True, kw_only=True)
class SensitivityConfig:
    method: str = "sobol"
    base_samples: int = 1024
    trajectories: int = 20
    levels: int = 4
    seed: int = 0
    resamples: int = 1000
    confidence: float = 0.95
    max_evaluations: int = 100000

    def __post_init__(self):
        if self.method not in ("sobol", "morris"):
            raise ValueError("paired method must be sobol or morris")
        for key in ("base_samples", "trajectories", "levels", "resamples", "max_evaluations"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError(f"{key} must be a positive integer")
        if self.base_samples & (self.base_samples - 1):
            raise ValueError("Sobol base samples must be a power of two")
        if self.levels < 4 or self.levels % 2 or self.trajectories < 2 or self.resamples < 2:
            raise ValueError("Morris needs even levels>=4, trajectories>=2; resamples>=2")
        if type(self.seed) is not int or self.seed < 0 or not np.isfinite(self.confidence) or not 0 < self.confidence < 1:
            raise ValueError("invalid seed or confidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class SensitivityDesign:
    method: str
    variable_ids: tuple[str, ...]
    bounds: np.ndarray
    values: np.ndarray
    row_ids: tuple[str, ...]
    config: SensitivityConfig
    sampler_version: str
    analyzer_seed: int
    distribution: str = "independent physical uniform"
    calc_second_order: bool = False

    def __post_init__(self):
        bounds, values = _readonly(self.bounds), _readonly(self.values)
        ids, rows = tuple(self.variable_ids), tuple(self.row_ids)
        d = len(ids)
        if not d or len(set(ids)) != d or any(not x for x in ids):
            raise ValueError("independent factor IDs required")
        if bounds.shape != (d, 2) or np.any(bounds[:, 0] >= bounds[:, 1]):
            raise ValueError("finite increasing physical bounds required")
        expected = (self.config.base_samples * (d + 2) if self.method == "sobol" else self.config.trajectories * (d + 1))
        if self.method != self.config.method or values.shape != (expected, d) or len(rows) != expected or len(set(rows)) != expected:
            raise ValueError("paired experiment layout/IDs do not match method configuration")
        if np.any(values < bounds[:, 0]) or np.any(values > bounds[:, 1]) or self.calc_second_order:
            raise ValueError("invalid physical design or unsupported second-order layout")
        coded = (values - bounds[:, 0]) / (bounds[:, 1] - bounds[:, 0])
        if self.method == "sobol":
            for block in coded.reshape(-1, d + 2, d):
                for j in range(d):
                    hybrid = block[0].copy()
                    hybrid[j] = block[-1, j]
                    if not np.array_equal(block[j + 1], hybrid):
                        raise ValueError("invalid Sobol A/AB_i/B paired block")
        else:
            delta = self.config.levels / (2 * (self.config.levels - 1))
            if not np.allclose(coded * (self.config.levels - 1), np.rint(coded * (self.config.levels - 1)), atol=1e-10, rtol=0):
                raise ValueError("Morris points do not lie on the declared coded grid")
            for trajectory in coded.reshape(-1, d + 1, d):
                differences = np.diff(trajectory, axis=0)
                changed = np.abs(differences) > 1e-10
                if not np.all(changed.sum(axis=1) == 1) or not np.all(changed.sum(axis=0) == 1):
                    raise ValueError("invalid Morris trajectory factor changes")
                if not np.allclose(np.abs(differences[changed]), delta, atol=1e-10, rtol=0):
                    raise ValueError("Morris trajectory step does not match declared coded delta")
        if self.analyzer_seed <= 0:
            raise ValueError("positive local analyzer seed required")
        object.__setattr__(self, "bounds", bounds)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "variable_ids", ids)
        object.__setattr__(self, "row_ids", rows)

    def problem(self):
        return {"num_vars": len(self.variable_ids), "names": list(self.variable_ids), "bounds": self.bounds.tolist()}


@dataclass(frozen=True, slots=True)
class SensitivityObservation:
    value: float | None
    valid: bool = True
    reason: str | None = None
    mode: str = "ANALYTIC"
    source_id: str | None = None

    def __post_init__(self):
        if self.mode not in ("ACTUAL", "SURROGATE", "ANALYTIC") or (self.valid and not self.source_id):
            raise ValueError("valid sensitivity observation requires explicit mode and run/model/analytic source identity")
        if self.valid and (self.value is None or not np.isfinite(self.value)):
            raise ValueError("valid observation requires a finite value")
        if not self.valid and not self.reason:
            raise ValueError("invalid observation requires a reason")


@dataclass(frozen=True, slots=True, kw_only=True)
class SensitivityResult:
    method: str
    variable_ids: tuple[str, ...]
    estimates: Mapping[str, np.ndarray]
    sample_count: int
    output_variance: float
    seed: int
    analyzer_seed: int
    evidence_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    source_mode: str
    metadata: Mapping
    warnings: tuple[str, ...] = ()

    def __post_init__(self):
        frozen = {}
        for name, value in self.estimates.items():
            array = _readonly(value)
            if array.shape != (len(self.variable_ids),):
                raise ValueError("sensitivity estimates must match factors")
            frozen[name] = array
        object.__setattr__(self, "estimates", MappingProxyType(frozen))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def generate_sensitivity_design(space, config=None):
    from .variables import ContinuousVariable
    config = config or SensitivityConfig()
    factors = space.independent_variables
    if any(not isinstance(v, ContinuousVariable) for v in factors):
        raise ValueError("Sobol/Morris require independent continuous factors; categorical/discrete estimators unsupported")
    if not factors:
        raise ValueError("at least one factor required")
    bounds = np.array([(v.lower, v.upper) for v in factors])
    names = tuple(v.id for v in factors)
    problem = {"num_vars": len(names), "names": list(names), "bounds": bounds.tolist()}
    n = config.base_samples * (len(names) + 2) if config.method == "sobol" else config.trajectories * (len(names) + 1)
    if n > config.max_evaluations:
        raise ValueError("paired experiment exceeds evaluation budget")
    sampler = _salib(f"sample.{config.method}")
    if config.method == "sobol":
        values = sampler.sample(problem, config.base_samples, calc_second_order=False, scramble=True, seed=config.seed)
    else:
        values = sampler.sample(problem, config.trajectories, num_levels=config.levels, seed=config.seed)
    # SALib's Sobol analyzer treats seed=0 as no seed in some releases.
    analyzer_seed = int(np.random.default_rng(config.seed).integers(1, 2**31 - 1))
    return SensitivityDesign(method=config.method, variable_ids=names, bounds=bounds, values=values,
               row_ids=tuple(f"{config.method}:{i:09d}" for i in range(n)), config=config,
               sampler_version=version("SALib"), analyzer_seed=analyzer_seed)


def analyze_sensitivity(design, observations, *, output_unit=""):
    """Compute first-order and total-order Sobol indices from design observations."""
    if set(observations) != set(design.row_ids):
        raise ValueError("incomplete experiment: observations must match every paired row ID exactly")
    records = [observations[i] for i in design.row_ids]
    if any(not isinstance(v, SensitivityObservation) for v in records):
        raise TypeError("explicit SensitivityObservation records required")
    failed = [i for i, v in zip(design.row_ids, records) if not v.valid or v.value is None or not np.isfinite(v.value)]
    if failed:
        raise ValueError(f"incomplete experiment: invalid rows {failed}; no rows may be dropped")
    modes = {v.mode for v in records}
    if len(modes) != 1:
        raise ValueError("actual, analytic and surrogate sensitivity experiments must be analyzed separately")
    y = np.array([v.value for v in records], dtype=float)
    variance = float(np.var(y))
    if variance == 0:
        raise ValueError("constant output: sensitivity is undefined")
    analyzer = _salib(f"analyze.{design.method}")
    c = design.config
    metadata = {"distribution": design.distribution, "sampler_version": design.sampler_version,
                "analyzer_version": version("SALib"), "confidence": c.confidence, "resamples": c.resamples,
                "interval_method": "bootstrap normal half-width", "output_unit": output_unit}
    notices = []
    if design.method == "sobol":
        result = analyzer.analyze(design.problem(), y, calc_second_order=False, num_resamples=c.resamples,
                                  conf_level=c.confidence, print_to_console=False, seed=design.analyzer_seed)
        estimates = {name: result[name] for name in ("S1", "ST", "S1_conf", "ST_conf")}
        metadata.update(base_samples=c.base_samples, calc_second_order=False,
                        convergence_diagnostic="single base size; repeat larger paired experiments to establish convergence")
        if np.any(result["S1"] < 0) or np.any(result["ST"] > 1):
            notices.append("finite-sample estimates outside [0,1] retained without clipping")
    else:
        result = analyzer.analyze(design.problem(), design.values, y, num_levels=c.levels,
                                  num_resamples=c.resamples, conf_level=c.confidence,
                                  print_to_console=False, seed=design.analyzer_seed)
        estimates = {name: result[name] for name in ("mu", "mu_star", "sigma", "mu_star_conf")}
        metadata.update(levels=c.levels, delta=c.levels / (2 * (c.levels - 1)), trajectories=c.trajectories,
                        effect_unit=f"{output_unit} per normalized-factor step", trajectory_selection="SALib random grid trajectories")
    return SensitivityResult(method=design.method, variable_ids=design.variable_ids, estimates=estimates,
               sample_count=len(y), output_variance=variance, seed=c.seed, analyzer_seed=design.analyzer_seed,
               evidence_ids=design.row_ids, observation_ids=tuple(v.source_id for v in records),
               source_mode=records[0].mode, metadata=metadata, warnings=tuple(notices))


@dataclass(frozen=True, slots=True)
class SpearmanPair:
    variable_id: str
    response_id: str
    rho: float | None
    pvalue: float | None
    sample_count: int
    missing_count: int
    row_indices: tuple[int, ...]
    reason: str | None = None


def spearman(x, y, *, variable_ids=None, response_ids=None):
    """Pairwise finite masks; ties use average ranks; constants are undefined."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    if y.ndim == 1:
        y = y[:, None]
    if x.ndim != 2 or y.ndim != 2 or len(x) != len(y):
        raise ValueError("matching input/output rows required")
    variable_ids = tuple(variable_ids) if variable_ids is not None else tuple(f"x{i}" for i in range(x.shape[1]))
    response_ids = tuple(response_ids) if response_ids is not None else tuple(f"y{i}" for i in range(y.shape[1]))
    if len(variable_ids) != x.shape[1] or len(response_ids) != y.shape[1]:
        raise ValueError("IDs must match columns")
    from scipy.stats import spearmanr
    result = []
    for i, variable in enumerate(variable_ids):
        for j, response in enumerate(response_ids):
            mask = np.isfinite(x[:, i]) & np.isfinite(y[:, j])
            indices = tuple(map(int, np.flatnonzero(mask)))
            n = len(indices)
            reason = None
            rho = pvalue = None
            if n < 2:
                reason = "insufficient paired observations"
            elif np.ptp(x[mask, i]) == 0 or np.ptp(y[mask, j]) == 0:
                reason = "constant variable or output"
            else:
                statistic = spearmanr(x[mask, i], y[mask, j])
                rho = float(statistic.statistic)
                pvalue = float(statistic.pvalue) if np.isfinite(statistic.pvalue) else None
            result.append(SpearmanPair(variable, response, rho, pvalue, n, len(x) - n, indices, reason))
    return tuple(result)
