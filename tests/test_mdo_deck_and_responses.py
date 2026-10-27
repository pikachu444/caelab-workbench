"""Tests for TemplateDeck token parsing/binding and Response extraction reductions."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from pathlib import Path
from caelab.mdo.inp_binder import TemplateDeck
from caelab.mdo.responses import ResponseSpec, extract_scalar


def test_template_deck_tokens_and_binding(tmp_path: Path):
    deck_text = """*HEADING
** Parametric model
*BEAM SECTION, SECTION=RECT, MATERIAL=STEEL
<WIDTH>, @@HEIGHT@@
*SHELL SECTION
{{THICKNESS}}
"""
    inp_file = tmp_path / "model.inp"
    inp_file.write_text(deck_text, encoding="utf-8")

    deck = TemplateDeck.from_file(inp_file)
    assert set(deck.token_names) == {"WIDTH", "HEIGHT", "THICKNESS"}

    bound = deck.bind({"WIDTH": 50.0, "HEIGHT": 100.0, "THICKNESS": 4.5})
    assert "50, 100" in bound or "50.0, 100.0" in bound
    assert "4.5" in bound
    assert "<WIDTH>" not in bound
    assert "@@HEIGHT@@" not in bound
    assert "{{THICKNESS}}" not in bound


def test_extract_scalar_reductions(tmp_path: Path):
    dat_content = """
    INCREMENT 1: MAX VON MISES = 350.5 MPa
    INCREMENT 2: MAX VON MISES = 520.0 MPa
    INCREMENT 3: MAX VON MISES = 480.0 MPa
    INCREMENT 4: MAX VON MISES = 610.2 MPa
    INCREMENT 5: MAX VON MISES = 410.0 MPa
    """
    dat_file = tmp_path / "job.dat"
    dat_file.write_text(dat_content, encoding="utf-8")

    # Reduction: max
    spec_max = ResponseSpec(
        id="resp_max",
        name="max_stress",
        source_file="job.dat",
        kind="regex",
        pattern=r"MAX VON MISES =\s*([\d\.]+)",
        reduction="max",
    )
    assert extract_scalar(spec_max, tmp_path) == 610.2

    # Reduction: min
    spec_min = ResponseSpec(
        id="resp_min",
        name="min_stress",
        source_file="job.dat",
        kind="regex",
        pattern=r"MAX VON MISES =\s*([\d\.]+)",
        reduction="min",
    )
    assert extract_scalar(spec_min, tmp_path) == 350.5

    # Reduction: last
    spec_last = ResponseSpec(
        id="resp_last",
        name="last_stress",
        source_file="job.dat",
        kind="regex",
        pattern=r"MAX VON MISES =\s*([\d\.]+)",
        reduction="last",
    )
    assert extract_scalar(spec_last, tmp_path) == 410.0

    # Reduction: sum
    spec_sum = ResponseSpec(
        id="resp_sum",
        name="sum_stress",
        source_file="job.dat",
        kind="regex",
        pattern=r"MAX VON MISES =\s*([\d\.]+)",
        reduction="sum",
    )
    assert pytest.approx(extract_scalar(spec_sum, tmp_path), 0.01) == 2371.2


def test_mdo_studio_apply_as_current_with_backup(tmp_path: Path, monkeypatch):
    import apps.workbench_qt.mdo_studio.window as win_mod
    from PySide6.QtWidgets import QApplication
    from caelab.mdo.study_manager import DesignPointRecord

    class MockMessageBox:
        @staticmethod
        def information(*args, **kwargs):
            return None
        @staticmethod
        def warning(*args, **kwargs):
            return None
        @staticmethod
        def critical(*args, **kwargs):
            return None

    monkeypatch.setattr(win_mod, "QMessageBox", MockMessageBox)

    instance = QApplication.instance() or QApplication([])

    deck_content = "*MATERIAL\nE = @@YOUNG@@, NU = @@POISSON@@\n"
    inp_file = tmp_path / "test_active.inp"
    inp_file.write_text(deck_content, encoding="utf-8")

    win = win_mod.MDOStudioWindow()
    win._on_load_deck(inp_file)

    assert win.template_deck is not None
    assert win.current_deck_path == inp_file
    assert set(win.template_deck.token_names) == {"YOUNG", "POISSON"}

    # Add a design record to the study
    rec = DesignPointRecord(
        point_id="DP-001",
        index=1,
        name="Optimal Point",
        physical_values={"YOUNG": 210000.0, "POISSON": 0.3},
    )
    win.study.records.append(rec)

    # Apply design point DP-001
    win._on_apply_as_current("DP-001")

    # Verify backup file created
    bak_file = tmp_path / "test_active.inp.bak"
    assert bak_file.is_file()
    assert bak_file.read_text(encoding="utf-8") == deck_content

    # Verify input file updated with new parameter values
    updated_content = inp_file.read_text(encoding="utf-8")
    assert "E = 210000" in updated_content
    assert "NU = 0.3" in updated_content

    win.close()
