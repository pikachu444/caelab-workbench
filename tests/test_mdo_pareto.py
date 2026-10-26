"""Independent mathematical oracles for exact fronts, crowding and rectangle unions."""
import itertools
from types import SimpleNamespace
import numpy as np
import pytest
from caelab.mdo.pareto import dominates, non_dominated_sort, pareto_front, crowding_distance, hypervolume, knee_point, feasible_front


def test_dominance_duplicates_and_fronts_against_bruteforce():
    rng = np.random.default_rng(7)
    x = rng.integers(0, 6, size=(35, 3)).astype(float)
    ids = tuple(f"p{i:03d}" for i in range(len(x)))
    remaining = set(range(len(x)))
    expected = []
    while remaining:
        front = sorted((i for i in remaining if not any(np.all(x[j] <= x[i]) and np.any(x[j] < x[i]) for j in remaining)), key=lambda i: ids[i])
        expected.append(tuple(front))
        remaining.difference_update(front)
    assert non_dominated_sort(x, ids) == tuple(expected)
    assert not dominates([1, 2], [1, 2])
    assert dominates([1, 2], [1, 3])
    assert pareto_front(np.empty((0, 2))) == ()
    with pytest.raises(ValueError):
        dominates([np.nan], [0])


def test_crowding_constants_singleton_and_stable_ties():
    assert np.array_equal(crowding_distance([[2, 2], [2, 2]]), [0, 0])
    assert np.array_equal(crowding_distance([[2, 2]]), [0])
    d = crowding_distance([[0, 3], [1, 2], [2, 1], [3, 0]])
    assert np.isinf(d[[0, 3]]).all()
    np.testing.assert_allclose(d[1:3], [4/3, 4/3])
    assert not d.flags.writeable


def rectangle_oracle(points, reference):
    total = 0.
    for count in range(1, len(points) + 1):
        for subset in itertools.combinations(points, count):
            total += (-1) ** (count + 1) * np.prod(reference - np.max(subset, axis=0))
    return total


@pytest.mark.parametrize("dimensions", [2, 3])
def test_hypervolume_matches_inclusion_exclusion(dimensions):
    rng = np.random.default_rng(123)
    points = rng.uniform(0, 3, (6, dimensions))
    r = np.full(dimensions, 4.)
    assert hypervolume(points, r).value == pytest.approx(rectangle_oracle(points, r))
    assert hypervolume(np.empty((0, dimensions)), r).value == 0
    assert hypervolume([[1, 3], [2, 2]], [4, 4]).value == 5
    result = hypervolume([[1, 3], [9, 2]], [4, 4], ["ok", "outside"])
    assert result.included_ids == ("ok",) and result.excluded_ids == ("outside",)
    with pytest.raises(ValueError):
        hypervolume(np.ones((1, 4)), np.ones(4)*2)


def test_knee_chord_sign_scaling_linear_and_degenerate():
    points = [[0, 4], [1, 1], [4, 0]]
    assert knee_point(points, ["a", "bend", "c"], scales=[4, 4]) == "bend"
    assert knee_point(np.array(points) * [100, .1], ["a", "bend", "c"], scales=[400, .4]) == "bend"
    assert knee_point([[0, 4], [2, 2], [4, 0]], ["a", "b", "c"], scales=[4, 4]) is None
    assert knee_point([[0, 4], [3, 3], [4, 0]], ["a", "b", "c"], scales=[4, 4]) is None
    assert knee_point(np.ones((3, 2)), ["a", "b", "c"], scales=[1, 1]) is None
    with pytest.raises(ValueError):
        knee_point([[1, 2, 3]], ["a"], scales=[1, 1, 1])


def test_scientific_front_valid_actual_feasible_compatible_only():
    def point(id, vector, **kw):
        return SimpleNamespace(point_id=id, objectives=vector, mode=kw.get("mode", "ACTUAL"),
            valid=kw.get("valid", True), feasible=kw.get("feasible", True),
            problem_revision=kw.get("revision", "r1"), criteria_revision="c1")
    points = [point("actual", [2, 2]), point("predicted", [0, 0], mode="SURROGATE"),
              point("marginal", [1, 1], feasible=False), point("invalid", [0, 0], valid=False)]
    assert [p.point_id for p in feasible_front(points)] == ["actual"]
    with pytest.raises(ValueError, match="revisions"):
        feasible_front(points + [point("changed", [1, 1], revision="r2")])
