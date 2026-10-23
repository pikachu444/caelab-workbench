"""Missing result values and undeclared units must stay visible as unknown."""

import pytest

pytest.importorskip("PySide6")

from apps.workbench_qt.main_window import fmt_force, fmt_length


def test_missing_result_is_not_displayed_as_zero():
    assert fmt_length(None, None) == "미확인"
    assert fmt_force(None, "N") == "미확인"


def test_result_units_are_not_assumed_or_converted_without_declaration():
    assert fmt_length(0.002, "m") == "2.000 mm"
    assert fmt_length(0.002, "mm") == "0.002 mm"
    assert "단위 미확인" in fmt_length(0.002, None)
    assert "단위 미확인" in fmt_force(4.0, None)
    assert fmt_force(0.0, "N") == "0.0 N"


def test_partial_displacement_field_does_not_invent_zero_deformation():
    from types import SimpleNamespace

    from apps.workbench_qt.viewport import Viewport

    shown_models = []
    viewport = SimpleNamespace(
        available=True, _grid=object(), _node_ids=[1, 2],
        show_model=shown_models.append, _base_scale=2.0,
    )
    model = object()
    result = SimpleNamespace(displacements={1: [0.0, 0.0, 0.0]})
    Viewport.show_results(viewport, model, result)
    assert shown_models == [model]
    assert viewport._base_scale is None
    assert viewport._last_result is None
