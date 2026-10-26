"""Exact minimization comparisons and explicitly selected geometric heuristics."""
from __future__ import annotations
from dataclasses import dataclass
import numpy as np


def _matrix(values):
    a = np.array(values, dtype=float, copy=True)
    if a.ndim != 2 or a.shape[1] == 0 or not np.isfinite(a).all():
        raise ValueError("expected a finite (points, objectives) matrix")
    return a


def _ids(n, point_ids):
    ids = tuple(str(i) for i in range(n)) if point_ids is None else tuple(point_ids)
    if len(ids) != n or len(set(ids)) != n or any(not isinstance(x, str) or not x for x in ids):
        raise ValueError("point IDs must be unique nonempty strings")
    return ids


def dominates(a, b):
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if a.ndim != 1 or a.size == 0 or a.shape != b.shape or not np.isfinite([a, b]).all():
        raise ValueError("dominance requires equal finite vectors")
    return bool(np.all(a <= b) and np.any(a < b))


def non_dominated_sort(values, point_ids=None):
    """Deb sorting: O(M N²) time and worst-case O(N²) bookkeeping."""
    a = _matrix(values)
    ids = _ids(len(a), point_ids)
    dominated = [[] for _ in a]
    counts = np.zeros(len(a), dtype=int)
    for i in range(len(a)):
        for j in range(i + 1, len(a)):
            if dominates(a[i], a[j]):
                dominated[i].append(j)
                counts[j] += 1
            elif dominates(a[j], a[i]):
                dominated[j].append(i)
                counts[i] += 1
    current = [i for i in range(len(a)) if counts[i] == 0]
    fronts = []
    while current:
        current.sort(key=lambda i: ids[i])
        fronts.append(tuple(current))
        following = []
        for i in current:
            for j in dominated[i]:
                counts[j] -= 1
                if counts[j] == 0:
                    following.append(j)
        current = following
    return tuple(fronts)


def pareto_front(values, point_ids=None):
    """Extract the first non-dominated front indices."""
    fronts = non_dominated_sort(values, point_ids)
    return fronts[0] if fronts else ()


def crowding_distance(values, point_ids=None):
    """Compute NSGA-II crowding distances along non-dominated front."""
    a = _matrix(values)
    ids = _ids(len(a), point_ids)
    distances = np.zeros(len(a))
    for j in range(a.shape[1]):
        order = sorted(range(len(a)), key=lambda i: (a[i, j], ids[i]))
        if not order or a[order[-1], j] == a[order[0], j]:
            continue
        distances[order[0]] = distances[order[-1]] = np.inf
        span = a[order[-1], j] - a[order[0], j]
        for k in range(1, len(order) - 1):
            distances[order[k]] += (a[order[k + 1], j] - a[order[k - 1], j]) / span
    return np.frombuffer(distances.tobytes(), dtype=float)


@dataclass(frozen=True, slots=True)
class HypervolumeResult:
    value: float
    included_ids: tuple[str, ...]
    excluded_ids: tuple[str, ...]
    method: str = "exact_slicing"


def hypervolume(values, reference, point_ids=None):
    """Exact 1D/2D/3D rectangle union; exclude/report out-of-reference rows."""
    a = _matrix(values)
    ids = _ids(len(a), point_ids)
    r = np.asarray(reference, dtype=float)
    if r.shape != (a.shape[1],) or not np.isfinite(r).all():
        raise ValueError("reference must be a finite objective vector")
    if a.shape[1] > 3:
        raise ValueError("exact hypervolume supports up to three objectives")
    mask = np.all(a <= r, axis=1)
    selected = a[mask]
    def volume(points, ref):
        if len(points) == 0:
            return 0.0
        if len(ref) == 1:
            return float(ref[0] - points[:, 0].min())
        edges = sorted(set(points[:, 0]) | {float(ref[0])})
        return sum((hi - lo) * volume(points[points[:, 0] <= lo, 1:], ref[1:])
                   for lo, hi in zip(edges, edges[1:]))
    return HypervolumeResult(float(volume(selected, r)), tuple(i for i, ok in zip(ids, mask) if ok),
                             tuple(i for i, ok in zip(ids, mask) if not ok))


def feasible_front(points):
    """Only actual, valid, accepted FEASIBLE records with objective vectors."""
    points = tuple(points)
    if len({(p.problem_revision, p.criteria_revision) for p in points}) > 1:
        raise ValueError("cannot mix study or criteria revisions in a scientific frontier")
    accepted = tuple(p for p in points if p.mode == "ACTUAL" and p.valid and p.feasible is True)
    if not accepted:
        return ()
    indices = pareto_front([p.objectives for p in accepted], [p.point_id for p in accepted])
    return tuple(accepted[i] for i in indices)


def knee_point(values, point_ids, *, scales, tolerance=1e-12):
    """2D chord-distance knee heuristic; no distinct positive bend returns None.

    Caller supplies frozen positive coordinate scales. Selection neither qualifies
    predictions nor promotes a design to an engineering optimum.
    """
    a = _matrix(values)
    ids = _ids(len(a), point_ids)
    scale = np.asarray(scales, dtype=float)
    if a.shape[1] != 2 or scale.shape != (2,) or not np.isfinite(scale).all() or np.any(scale <= 0):
        raise ValueError("2D knee requires two fixed positive scales")
    if not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be finite and nonnegative")
    front = pareto_front(a, ids)
    if len(front) < 3:
        return None
    order = sorted(front, key=lambda i: (a[i, 0], a[i, 1], ids[i]))
    z = a / scale
    left, right = z[order[0]], z[order[-1]]
    chord = right - left
    if np.any(np.ptp(z[order], axis=0) <= tolerance):
        return None
    length = np.linalg.norm(chord)
    distances = [(float((chord[1] * (z[i, 0] - left[0]) - chord[0] * (z[i, 1] - left[1])) / length), i)
                 for i in order[1:-1]]
    best, index = min(distances, key=lambda p: (-p[0], ids[p[1]]))
    return ids[index] if best > tolerance else None
