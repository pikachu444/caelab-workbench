"""GUI regression tests for MDO Studio Korean engineering mode, live language/theme toggles,
dark-mode Matplotlib toolbar icon tinting, 1440x900 dock readability, and ProjectWindow handoff.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import zipfile
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import matplotlib
from matplotlib import font_manager
from matplotlib.colors import to_rgba
from matplotlib.ft2font import FT2Font
import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSize, Qt
from PySide6.QtGui import QColor, QFontDatabase, QFontInfo, QFontMetrics
from PySide6.QtWidgets import QApplication, QMessageBox

from apps.workbench_qt.mdo_studio.design_points_model import DesignPointsTableModel
from apps.workbench_qt.mdo_studio.views.pareto_view import PARETO_LAYERS, PARETO_LAYERS_KO
from apps.workbench_qt.mdo_studio.window import MDOStudioWindow
from apps.workbench_qt.project_window import ProjectWindow
from apps.workbench_qt.theme import (
    current_theme_mode,
    current_ui_language,
    resolved_ui_language,
    set_theme_mode,
    set_ui_language,
)
from caelab.connections import ProgramStatus, PROGRAMS
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.study_manager import StudyManager
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.workbench import Workbench


@pytest.fixture(autouse=True)
def _isolated_ui_and_font_state(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("CAELAB_UI_SETTINGS_PATH", str(tmp_path / "ui.ini"))
    monkeypatch.setenv("CAELAB_MDO_SETTINGS_PATH", str(tmp_path / "mdo_layout.ini"))
    monkeypatch.delenv("CAELAB_UI_LANGUAGE", raising=False)
    monkeypatch.delenv("CAELAB_THEME_MODE", raising=False)

    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families():
        for name in ("segoeui.ttf", "malgun.ttf"):
            font_path = Path("C:/Windows/Fonts") / name
            if font_path.is_file():
                QFontDatabase.addApplicationFont(str(font_path))

    saved_font = app.font()
    saved_style = app.style().objectName()
    saved_rc = matplotlib.rcParams.copy()
    try:
        yield app
    finally:
        matplotlib.rcParams.update(saved_rc)
        app.setFont(saved_font)
        if app.style().objectName().lower() != saved_style.lower():
            app.setStyle(saved_style)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def _close_window(win, app: QApplication) -> None:
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def _build_multipart_mdo_service() -> object:
    wb = Workbench()
    space = VariableSpace(
        variables=(
            ContinuousVariable(id="flange_t", name="플랜지 두께", unit="mm", lower=4.0, upper=12.0, initial=6.0),
            ContinuousVariable(id="rib_t", name="보강 리브 두께", unit="mm", lower=2.5, upper=8.0, initial=4.0),
            ContinuousVariable(id="bolt_preload", name="볼트 예압 하중", unit="N", lower=8000.0, upper=24000.0, initial=15000.0),
            ContinuousVariable(id="bracket_rho", name="브래킷 밀도", unit="Mg/mm**3", lower=2.4e-9, upper=7.8e-9, initial=2.7e-9),
        )
    )
    objectives = [
        ObjectiveSpec(id="mass_obj", response_id="mass_eall", sense="MIN"),
        ObjectiveSpec(id="disp_obj", response_id="disp", sense="MIN"),
    ]
    constraints = [
        ConstraintSpec(id="vm_limit", response_id="stress", kind="UPPER", limit=240.0, unit="MPa"),
    ]
    study = StudyManager("다중 파트 체결 브래킷 최적설계", space=space, objectives=objectives, constraints=constraints)
    samples = [
        ("초기 기준 어셈블리", {"flange_t": 6.0, "rib_t": 4.0, "bolt_preload": 15000.0, "bracket_rho": 2.7e-9},
         {"mass_eall": 0.00142, "disp": 0.184, "stress": 192.0}, "COMPLETED", "FEASIBLE", "ACTUAL", True, 0.0),
        ("경량 리브 후보", {"flange_t": 5.0, "rib_t": 3.0, "bolt_preload": 14000.0, "bracket_rho": 2.55e-9},
         {"mass_eall": 0.00118, "disp": 0.236, "stress": 228.0}, "COMPLETED", "FEASIBLE", "ACTUAL", True, 0.0),
        ("고강성 후판 후보", {"flange_t": 9.5, "rib_t": 6.5, "bolt_preload": 18000.0, "bracket_rho": 2.7e-9},
         {"mass_eall": 0.00205, "disp": 0.112, "stress": 148.0}, "COMPLETED", "FEASIBLE", "ACTUAL", True, 0.0),
        ("경계 응력 후보", {"flange_t": 4.6, "rib_t": 2.8, "bolt_preload": 19500.0, "bracket_rho": 2.65e-9},
         {"mass_eall": 0.00115, "disp": 0.251, "stress": 240.0}, "COMPLETED", "MARGINAL", "ACTUAL", False, 0.0),
        ("과응력 위반 후보", {"flange_t": 4.1, "rib_t": 2.5, "bolt_preload": 23000.0, "bracket_rho": 2.5e-9},
         {"mass_eall": 0.00104, "disp": 0.312, "stress": 289.0}, "COMPLETED", "INFEASIBLE", "ACTUAL", False, 49.0),
        ("대리모델 탐색 후보", {"flange_t": 6.8, "rib_t": 4.5, "bolt_preload": 15200.0, "bracket_rho": 2.58e-9},
         {"mass_eall": 0.00149, "disp": 0.158, "stress": 176.0}, "PREDICTED", "FEASIBLE", "SURROGATE", False, 0.0),
        ("대리모델 제약위반 후보", {"flange_t": 4.2, "rib_t": 2.6, "bolt_preload": 22500.0, "bracket_rho": 2.52e-9},
         {"mass_eall": 0.00108, "disp": 0.298, "stress": 274.0}, "PREDICTED", "INFEASIBLE", "SURROGATE", False, 34.0),
        ("미실행 대기 후보", {"flange_t": 7.2, "rib_t": 5.0, "bolt_preload": 16000.0, "bracket_rho": 2.7e-9},
         {}, "PENDING", "NOT_ASSESSED", "ACTUAL", False, 0.0),
    ]
    for name, params, obs, status, feas, mode, is_pareto, viol in samples:
        rec = study.add_point(params, name=name)
        rec.revision = "0"
        rec.status = status
        rec.feasibility = feas
        rec.mode = mode
        rec.observations = dict(obs)
        rec.is_pareto = is_pareto
        rec.total_violation = viol
        if mode == "ACTUAL" and status == "COMPLETED":
            rec.attempts = ({"run_id": f"run-{rec.index}", "status": "COMPLETED", "feasibility": feas, "duration_sec": 1.2},)

    service = wb.create_mdo_study(study)
    service.response_units = {"mass_eall": "Mg", "disp": "mm", "stress": "MPa"}
    service.current_point_id = study.records[0].point_id
    return service


def _dominant_icon_rgb(icon, size: QSize) -> tuple[int, int, int]:
    image = icon.pixmap(size).toImage()
    assert not image.isNull()
    rs, gs, bs = [], [], []
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() >= 64:
                rs.append(pixel.red())
                gs.append(pixel.green())
                bs.append(pixel.blue())
    assert rs, "Expected non-transparent pixels in Matplotlib toolbar icon"
    return round(sum(rs) / len(rs)), round(sum(gs) / len(gs)), round(sum(bs) / len(bs))


def test_ui_language_precedence_and_bare_mdo_english_default(_isolated_ui_and_font_state, monkeypatch):
    app = _isolated_ui_and_font_state
    # 1. Unset env and unset QSettings: direct MDO defaults to 'en', Workbench launcher defaults to 'ko'.
    assert resolved_ui_language() == "en"
    assert current_ui_language("ko") == "ko"

    win_en = MDOStudioWindow()
    try:
        assert win_en.language == "en"
        assert win_en.windowTitle() == "CAELab MDO Studio"
        assert [a.text() for a in win_en.menuBar().actions()] == ["&File", "&Study", "&Run", "&View"]
        assert win_en.tree_dock.windowTitle() == "Study"
        assert win_en.setup_dock.windowTitle() == "Setup"
        assert win_en.setup_widget.badge_box.title() == "Status && Feasibility"
        assert win_en.tdp_model.headers[:9] == [
            "Point", "Name", "Revision", "Status", "Feasibility", "Pareto", "Data", "Attempts", "Current",
        ]
    finally:
        _close_window(win_en, app)

    # 2. Stored QSettings < CAELAB_UI_LANGUAGE / CAELAB_THEME_MODE env < explicit,
    #    and live set_ui_language / set_theme_mode syncs active process env when present.
    assert set_ui_language("ko") == "ko"
    assert set_theme_mode("dark") == "dark"
    assert resolved_ui_language() == "ko"
    assert current_theme_mode() == "dark"

    monkeypatch.setenv("CAELAB_UI_LANGUAGE", "en")
    monkeypatch.setenv("CAELAB_THEME_MODE", "light")
    assert resolved_ui_language() == "en"
    assert resolved_ui_language("ko") == "ko"
    assert current_theme_mode() == "light"

    # Live toggle inside a child process with CAELAB_UI_LANGUAGE / CAELAB_THEME_MODE set
    assert set_ui_language("ko") == "ko"
    assert os.environ["CAELAB_UI_LANGUAGE"] == "ko"
    assert current_ui_language() == "ko" and resolved_ui_language() == "ko"

    assert set_theme_mode("dark") == "dark"
    assert os.environ["CAELAB_THEME_MODE"] == "dark"
    assert current_theme_mode() == "dark"


def test_mdo_korean_engineering_mode_zero_missing_glyphs_and_1440x900_readability(_isolated_ui_and_font_state):
    app = _isolated_ui_and_font_state
    service = _build_multipart_mdo_service()
    win = MDOStudioWindow(service=service, language="ko")
    try:
        win.resize(1440, 900)
        win.show()
        for _ in range(5):
            app.processEvents()

        assert win.language == "ko"
        assert win.windowTitle() == "CAELab MDO 최적설계 스튜디오"
        assert [a.text() for a in win.menuBar().actions()] == ["&파일", "&연구", "&실행", "&보기"]

        # Verify key engineering Korean actions
        assert win.act_new.text() == "새 연구"
        assert win.act_open.text() == "연구 열기…"
        assert win.act_input.text() == "네이티브 입력 열기…"
        assert win.act_save.text() == "저장"
        assert win.act_save_as.text() == "다른 이름으로 저장…"
        assert win.act_export.text() == "필터된 표 내보내기…"
        assert win.act_parameterize.text() == "스칼라 토큰 생성…"
        assert win.act_configure.text() == "연구 구성"
        assert win.act_generate.text() == "실험계획(DOE) 생성…"
        assert win.act_train.text() == "대리모델 학습 · 검증…"
        assert win.act_optimize.text() == "제약 기반 대리모델 탐색…"
        assert win.act_run.text() == "선택 · 대기점 실행"
        assert win.act_all.text() == "대기점 일괄 실행"
        assert win.act_confirm.text() == "네이티브 재검증"
        assert win.act_clone.text() == "후보로 복제"
        assert win.act_stop.text() == "스케줄링 중지"
        assert win.act_cancel.text() == "실행 중 캠페인 취소"
        assert win.act_run_settings.text() == "실행 설정…"
        assert win.act_current.text() == "현재 설계점으로 적용"
        assert win.act_undo.text() == "현재 설계 적용 되돌리기"
        assert win.act_rerun.text() == "현재 설계 재해석"
        assert win.act_result.text() == "ParaView에서 선택 결과 열기"
        assert win.act_current_result.text() == "ParaView에서 현재 재해석 열기"
        assert win.act_attempts.text() == "실행 이력 · 로그 보기"
        assert win.act_default_layout.text() == "기본 레이아웃 복원"
        assert win.act_save_layout.text() == "레이아웃 저장"
        assert win.act_compact_rows.text() == "행 높이 좁게"

        # Docks, filters, tabs, headers, and localized cells (with SortRole preserved)
        assert win.tree_dock.windowTitle() == "연구 탐색"
        assert win.setup_dock.windowTitle() == "상세 설정"
        assert win.tree_dock.minimumWidth() >= 180
        assert win.setup_dock.minimumWidth() >= 250
        assert win.tree_dock.width() >= 255
        assert win.setup_dock.width() >= 285
        assert win.filter_label.isVisible() and win.filter_label.text() == "설계점 필터"
        assert win.revision_label.text() == "연구 리비전"
        assert win.clear_filters_btn.text() == "필터 초기화"
        assert win.search.placeholderText() == "설계점 이름 / ID 필터"
        assert win.tdp_lbl.text().startswith("표시 설계점:")
        assert win.plot_filter_label.text().startswith("표시 설계점:")
        assert [win.viz_tabs.tabText(i) for i in range(win.viz_tabs.count())] == [
            "Pareto 프런트", "평행 좌표", "반응표면", "민감도", "수렴 이력",
        ]

        assert win.tdp_model.headers[:9] == [
            "설계점", "이름", "리비전", "상태", "적합성", "Pareto", "구분", "시도", "현재",
        ]
        assert win.tdp_model.headers[-1] == "위반량"
        assert win.tdp_model.data(win.tdp_model.index(0, 3)) == "완료"
        assert win.tdp_model.data(win.tdp_model.index(0, 4)) == "적합"
        assert win.tdp_model.data(win.tdp_model.index(0, 6)) == "실제 해석"
        assert win.tdp_model.data(win.tdp_model.index(5, 6)) == "대리모델"
        assert win.tdp_model.data(win.tdp_model.index(0, 3), DesignPointsTableModel.SortRole) == "COMPLETED"
        assert win.tdp_model.data(win.tdp_model.index(0, 0), DesignPointsTableModel.SortRole) == 1

        # Filter combo boxes localize display labels while preserving exact filter semantics
        infeasible_idx = win.feasibility_filter.findData("INFEASIBLE")
        assert infeasible_idx > 0 and win.feasibility_filter.itemText(infeasible_idx) == "부적합"
        win.feasibility_filter.setCurrentIndex(infeasible_idx)
        assert win.tdp_proxy.rowCount() == 2
        win._clear_filters()
        assert win.tdp_proxy.rowCount() == 8

        # Study tree Korean labels & readability at 1440x900
        root = win.study_tree.tree.topLevelItem(0)
        top_labels = [root.child(i).text(0) for i in range(root.childCount())]
        assert "입력 덱 / 스칼라 토큰" in top_labels
        assert "설계 변수 (4)" in top_labels
        assert "목적 · 제약 응답" in top_labels
        assert "실험계획 (DOE Plans)" in top_labels
        assert "네이티브 해석 평가 (5개 완료)" in top_labels
        assert "대리모델 — 학습 필요" in top_labels
        assert "최적화 실행 (2개 대리모델 예측)" in top_labels
        assert "민감도 분석" in top_labels
        assert "시각화 보기" in top_labels
        assert any(lbl.startswith("저장된 선택 / 현재 설계") for lbl in top_labels)

        tree_fm = QFontMetrics(win.study_tree.tree.font())
        viewport_w = win.study_tree.tree.viewport().width()
        for lbl in top_labels:
            assert tree_fm.horizontalAdvance(lbl) + 28 <= viewport_w + 12, f"Tree label clipped at 1440x900: {lbl}"

        # Setup dock Korean labels
        assert win.setup_widget.context_tabs.tabText(0) == "설계점 상세"
        assert win.setup_widget.lbl_title.text() == "선택된 설계점: 없음"
        assert win.setup_widget.badge_box.title() == "실행 상태 · 적합성"
        assert win.setup_widget.param_box.title() == "설계 변수"
        assert win.setup_widget.resp_box.title() == "해석 응답"
        assert win.setup_widget.act_box.title() == "공학 작업"
        assert win.setup_widget.btn_apply_current.text() == "현재 설계점으로 적용"
        assert win.setup_widget.btn_confirm.text() == "네이티브 재검증"
        assert win.setup_widget.btn_attempts.text() == "실행 이력 · 로그"
        assert win.setup_widget.btn_result.text() == "ParaView에서 결과 열기"

        win.select_point(service.study.records[0].point_id)
        assert "실제 해석" in win.setup_widget.lbl_guide.toPlainText()
        assert "적합" in win.setup_widget.lbl_feasibility.text()

        # WT2-1: Exact layer predicate parity between PARETO_LAYERS (EN) and PARETO_LAYERS_KO (KO),
        # ensuring infeasible surrogate points stay in '대리모델 예측' and never conflate with actual solver runs.
        for rec in service.study.records:
            en_matches = [idx for idx, (*_, pred) in enumerate(PARETO_LAYERS) if pred(rec)]
            ko_matches = [idx for idx, (*_, pred) in enumerate(PARETO_LAYERS_KO) if pred(rec)]
            assert en_matches == ko_matches == [en_matches[0]]

        # Pareto Korean labels and zero missing glyphs ('□') in both Qt and Matplotlib FreeType
        win.pareto_view.canvas.draw()
        assert win.pareto_view.ax.get_title() == "표본 Pareto 투영"
        legend_texts = [t.get_text() for t in win.pareto_view.ax.get_legend().get_texts()]
        assert "실제 실행 (허용)" in legend_texts
        assert "대리모델 예측" in legend_texts
        assert "제약 위반 / 미평가" in legend_texts
        assert "전체 목적 기준 Pareto 프런트" in legend_texts
        assert "선택됨" in legend_texts

        # Verify both surrogate points (feasible + infeasible) belong to the '대리모델 예측' artist, not '제약 위반 / 미평가'
        artist_by_label = {coll.get_label(): coll for coll in win.pareto_view.ax.collections}
        surrogate_ids = {r.point_id for r in service.study.records if r.mode == "SURROGATE"}
        assert set(win.pareto_view._picks[artist_by_label["대리모델 예측"]]) == surrogate_ids
        assert not (set(win.pareto_view._picks[artist_by_label["제약 위반 / 미평가"]]) & surrogate_ids)

        all_korean_strings = [
            win.windowTitle(),
            *top_labels,
            *win.tdp_model.headers,
            *[win.viz_tabs.tabText(i) for i in range(win.viz_tabs.count())],
            win.pareto_view.ax.get_title(),
            win.pareto_view.ax.get_xlabel(),
            win.pareto_view.ax.get_ylabel(),
            *legend_texts,
        ]
        assert all("□" not in s for s in all_korean_strings)

        if Path("C:/Windows/Fonts/malgun.ttf").is_file():
            resolved_qt_family = QFontInfo(win.font()).family()
            assert "malgun" in resolved_qt_family.lower() or "맑은" in resolved_qt_family
            assert matplotlib.rcParams["font.family"][0] == "Malgun Gothic"

        qt_fm = QFontMetrics(win.font())
        for text in all_korean_strings:
            assert qt_fm.horizontalAdvance(text) > 0
            for ch in text:
                if not ch.isspace():
                    assert qt_fm.inFontUcs4(ord(ch)), f"Missing Qt font glyph for {ch!r} in {text!r}"

        resolved_mpl_font = font_manager.findfont(win.pareto_view.ax.title.get_fontproperties())
        ft_font = FT2Font(resolved_mpl_font)
        for text in all_korean_strings:
            for ch in text:
                if not ch.isspace():
                    assert ft_font.get_char_index(ord(ch)) != 0, (
                        f"Missing Matplotlib FreeType glyph ({resolved_mpl_font}) for {ch!r} in {text!r}"
                    )

        assert not win.grab().isNull()
    finally:
        service.workbench.close()
        _close_window(win, app)


def test_live_theme_and_language_toggles_and_dark_toolbar_icons(_isolated_ui_and_font_state, monkeypatch):
    app = _isolated_ui_and_font_state
    monkeypatch.setenv("CAELAB_THEME_MODE", "dark")
    monkeypatch.setenv("CAELAB_UI_LANGUAGE", "ko")
    service = _build_multipart_mdo_service()
    win = MDOStudioWindow(service=service)
    try:
        win.show()
        win.select_point(service.study.records[0].point_id)
        app.processEvents()

        toolbar = win.pareto_view.mpl_toolbar
        icon_actions = [a for a in toolbar.actions() if not a.icon().isNull()]
        assert len(icon_actions) >= 5

        # Dark mode: toolbar icons are tinted light (#e2e8f0) and '선택됨' ring is #ffffff
        dark_rgb = _dominant_icon_rgb(icon_actions[0].icon(), toolbar.iconSize())
        expected_dark = QColor("#e2e8f0")
        assert abs(dark_rgb[0] - expected_dark.red()) <= 8
        assert abs(dark_rgb[1] - expected_dark.green()) <= 8
        assert abs(dark_rgb[2] - expected_dark.blue()) <= 8
        sel_dark = next(c for c in win.pareto_view.ax.collections if c.get_label() == "선택됨")
        np.testing.assert_allclose(sel_dark.get_edgecolors()[0], to_rgba("#ffffff"), atol=1e-2)

        # WT2-3: Toggle to Light mode — toolbar icons AND '선택됨' selection ring immediately switch to #0f172a
        win._on_toggle_theme()
        app.processEvents()
        assert not win.is_dark and current_theme_mode() == "light"
        assert os.environ["CAELAB_THEME_MODE"] == "light"
        light_rgb = _dominant_icon_rgb(icon_actions[0].icon(), toolbar.iconSize())
        expected_light = QColor("#0f172a")
        assert abs(light_rgb[0] - expected_light.red()) <= 8
        assert abs(light_rgb[1] - expected_light.green()) <= 8
        assert abs(light_rgb[2] - expected_light.blue()) <= 8
        sel_light = next(c for c in win.pareto_view.ax.collections if c.get_label() == "선택됨")
        np.testing.assert_allclose(sel_light.get_edgecolors()[0], to_rgba("#0f172a"), atol=1e-2)

        # WT2-2: Live toggle Korean -> English -> Korean updates active CAELAB_UI_LANGUAGE env as well
        win.act_language.trigger()
        app.processEvents()
        assert win.language == "en"
        assert os.environ["CAELAB_UI_LANGUAGE"] == "en"
        assert current_ui_language("ko") == "en" and resolved_ui_language() == "en"
        assert win.windowTitle() == "CAELab MDO Studio"
        assert win.tree_dock.windowTitle() == "Study"
        assert win.setup_dock.windowTitle() == "Setup"
        assert win.viz_tabs.tabText(0) == "Pareto"
        assert win.tdp_model.headers[0] == "Point"
        assert win.pareto_view.ax.get_title() == "Sampled Pareto projection"

        win.act_language.trigger()
        app.processEvents()
        assert win.language == "ko"
        assert os.environ["CAELAB_UI_LANGUAGE"] == "ko"
        assert current_ui_language("en") == "ko" and resolved_ui_language() == "ko"
        assert win.windowTitle() == "CAELab MDO 최적설계 스튜디오"
        assert win.tree_dock.windowTitle() == "연구 탐색"
        assert win.setup_dock.windowTitle() == "상세 설정"
        assert win.viz_tabs.tabText(0) == "Pareto 프런트"
        assert win.tdp_model.headers[0] == "설계점"
        assert win.pareto_view.ax.get_title() == "표본 Pareto 투영"
    finally:
        service.workbench.close()
        _close_window(win, app)


def test_project_window_passes_theme_and_korean_language_to_mdo_and_prep(_isolated_ui_and_font_state, tmp_path, monkeypatch):
    app = _isolated_ui_and_font_state
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Ok)
    set_theme_mode("dark")

    fake_prep = tmp_path / ("caelab-prep.exe" if os.name == "nt" else "caelab-prep")
    fake_prep.write_text("stub", encoding="utf-8")
    fake_prep.chmod(0o755)
    prep_spec = next(spec for spec in PROGRAMS if spec.id == "caelab_prep")

    wb = Workbench()
    orig_programs = wb.programs
    monkeypatch.setattr(
        wb,
        "programs",
        lambda: [
            ProgramStatus(prep_spec, str(fake_prep), source="test") if s.spec.id == "caelab_prep" else s
            for s in orig_programs()
        ],
    )

    launched_requests = []
    monkeypatch.setattr(
        wb.jobs,
        "launch",
        lambda request: launched_requests.append(request) or SimpleNamespace(state="opened", error=""),
    )

    window = ProjectWindow(wb)
    try:
        # 1. Launching MDO Studio from ProjectWindow passes CAELAB_UI_LANGUAGE='ko' & CAELAB_THEME_MODE='dark'
        window.open_optimization_panel()
        assert len(launched_requests) == 1
        mdo_req = launched_requests[-1]
        assert mdo_req.program_id == "mdo_studio"
        assert mdo_req.env["CAELAB_UI_LANGUAGE"] == "ko"
        assert mdo_req.env["CAELAB_THEME_MODE"] == "dark"
        # Parent os.environ remains clean so direct in-process MDOStudioWindow() stays 'en'
        assert "CAELAB_UI_LANGUAGE" not in os.environ
        assert "CAELAB_THEME_MODE" not in os.environ

        # Simulate child MDOStudioWindow startup with the handed-off environment
        with monkeypatch.context() as child_env:
            child_env.setenv("CAELAB_UI_LANGUAGE", mdo_req.env["CAELAB_UI_LANGUAGE"])
            child_env.setenv("CAELAB_THEME_MODE", mdo_req.env["CAELAB_THEME_MODE"])
            child_mdo = MDOStudioWindow()
            try:
                assert child_mdo.language == "ko"
                assert child_mdo.windowTitle() == "CAELab MDO 최적설계 스튜디오"
            finally:
                _close_window(child_mdo, app)

        # 2. Launching CAELab Prep in Light mode passes CAELAB_THEME_MODE='light' & CAELAB_UI_LANGUAGE='ko'
        set_theme_mode("light")
        prep_doc = tmp_path / "crash_assembly.caeprep"
        with zipfile.ZipFile(prep_doc, "w") as zf:
            zf.writestr("manifest.json", json.dumps({"format": "caelab-prep-project"}))
        box = window.add_box("preprocessor", "caelab_prep")
        window.register_file(box.node_id, prep_doc, role="document")
        window.open_box(box)

        assert len(launched_requests) == 2
        prep_req = launched_requests[-1]
        assert prep_req.program_id == "caelab_prep"
        assert prep_req.env["CAELAB_THEME_MODE"] == "light"
        assert prep_req.env["CAELAB_UI_LANGUAGE"] == "ko"
        assert "CAELAB_UI_LANGUAGE" not in os.environ
        assert "CAELAB_THEME_MODE" not in os.environ
    finally:
        window._dirty = False
        _close_window(window, app)
        wb.close()
