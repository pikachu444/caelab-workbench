"""GUI unit tests and screenshot validation for MDO Studio."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from apps.workbench_qt.mdo_studio.window import MDOStudioWindow
from apps.workbench_qt.theme import toggle_theme_mode, current_theme_mode


@pytest.fixture
def app(tmp_path):
    import hashlib,json
    from pathlib import Path
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    before=list(QFontDatabase.families()); registered=[]
    # Match the installed-font geometry used by the Windows entry and sibling GUI tests.
    if not before:
        for name in ('segoeui.ttf','malgun.ttf'):
            path=Path('C:/Windows/Fonts')/name
            if path.is_file():
                font_id=QFontDatabase.addApplicationFont(str(path))
                registered.append(dict(path=str(path),font_id=font_id,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    (tmp_path/'font-environment.json').write_text(json.dumps(dict(
        available_before=before,available_after=list(QFontDatabase.families()),registered=registered),
        indent=2),encoding='utf-8')
    return instance


def font_details(font):
    return dict(requested_family=font.family(),
        pixel_size=font.pixelSize(),point_size=font.pointSizeF())


def test_mdo_studio_window_creation(app):
    win = MDOStudioWindow()
    assert win.windowTitle().startswith("CAELab MDO Studio")
    assert win.tdp_model.rowCount() == 0
    assert win.tdp_model.columnCount() > 5
    assert win.viz_tabs.count() == 5
    from caelab.mdo.study_manager import StudyManager
    manager=StudyManager()
    manager.add_point({'thickness':2.}).revision='0'  # Candidate belongs to this unconfigured draft.
    win.close()
    win=MDOStudioWindow(study=manager)
    assert win.tdp_model.rowCount() == 1
    assert all(r.status == "PENDING" and not r.observations for r in win.study.records)
    # Select uncomputed point and check setup dock
    win.tdp_table.selectRow(0)
    assert win.setup_widget.lbl_title.text().startswith("DP_0001")
    assert not win.setup_widget.btn_apply_current.isEnabled()

    win.close()


def test_mdo_studio_theme_toggle(app):
    win = MDOStudioWindow()
    initial_mode = win.is_dark

    win._on_toggle_theme()
    assert win.is_dark != initial_mode

    # Toggle back
    win._on_toggle_theme()
    assert win.is_dark == initial_mode
    win.close()


@pytest.fixture
def layout_studio(app,tmp_path,monkeypatch):
    """Real Studio/service and preferences serialization, wholly outside user settings."""
    from PySide6.QtCore import QSettings
    import apps.workbench_qt.mdo_studio.window as studio
    from caelab.workbench import Workbench
    from caelab.mdo.study_manager import StudyManager
    from caelab.mdo.objectives import ObjectiveSpec
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH',str(tmp_path/'theme.ini'))
    settings_path=str(tmp_path/'layout.ini')
    monkeypatch.setattr(studio,'QSettings',lambda *args:QSettings(settings_path,QSettings.Format.IniFormat))
    windows=[]
    def create(view_state=None,*,width=960,height=720,populated=True):
        manager=StudyManager(objectives=[ObjectiveSpec(id=key,response_id=key) for key in ('disp','stress','mass')] if populated else ())
        for values in (((.0584,297.,.000113),(.0969,172.,.0000631)) if populated else ()):
            row=manager.add_point({}); row.status='COMPLETED'; row.feasibility='FEASIBLE'
            row.observations=dict(zip(('disp','stress','mass'),values)); row.revision='0'; row.mode='SURROGATE'
        service=Workbench().create_mdo_study(manager)
        native_calls=[]
        def forbid_native(*args,**kwargs):
            native_calls.append(True); raise AssertionError('Layout regression must never dispatch a native job')
        for method in ('submit_native','submit_batch','submit','launch'):
            monkeypatch.setattr(service.workbench.jobs,method,forbid_native)
        service.response_units={'disp':'mm','stress':'MPa','mass':'N*s**2/mm'} if populated else {}
        service.read_only=True; service.view_state=view_state or {}
        window=MDOStudioWindow(service=service); window.resize(width,height)
        window._layout_test_native_calls=native_calls
        windows.append(window)
        return window
    yield create
    for window in windows: window.close()
    app.processEvents()


@pytest.fixture
def native_layout_widget(app,monkeypatch):
    """Reuse the native-view suite's real Qt/VTK offscreen renderer and DPI adapter."""
    pytest.importorskip('vtkmodules')
    from test_mdo_native3d_views import vtk_widget
    return vtk_widget.__wrapped__(app,monkeypatch)


def drain_layout(app):
    for _ in range(5): app.processEvents()


# Acceptance uses default scaling; the user withdrew the per-scale matrix requirement.
@pytest.mark.parametrize('ratio',(1.,))
@pytest.mark.parametrize('size',((960,720),(1380,880)))
@pytest.mark.parametrize('entry',('loaded','blank'))
def test_fresh_layout_first_3d_keeps_selected_row_and_native_labels(layout_studio,native_layout_widget,
        app,monkeypatch,tmp_path,ratio,size,entry):
    from PySide6.QtCore import Qt
    from test_mdo_native3d_views import (install_rendered_qt_widget,settle_qt,camera_state,
                                        assert_annotation_pixels_match_boxes)
    import json,hashlib
    from pathlib import Path
    install_rendered_qt_widget(native_layout_widget,monkeypatch,ratio)
    window=layout_studio(width=size[0],height=size[1],populated=entry=='loaded'); window.setup_dock.hide()
    window.resizeDocks([window.tree_dock],[330],Qt.Orientation.Horizontal)
    stages=[]; source_root=Path(__file__).resolve().parents[1]
    def record(stage):
        native=window.pareto_view.native
        row_rect=window.tdp_table.visualRect(window.tdp_proxy.index(0,0))
        stages.append(dict(stage=stage,outer=[window.width(),window.height()],
            splitter_height=window.center_splitter.height(),splitter=window.center_splitter.sizes(),
            viewport=window.tdp_table.viewport().rect().getRect(),row_rect=row_rect.getRect(),
            selected_ids=sorted(window._selected_ids),row_fully_visible=window.tdp_table.viewport().rect().contains(row_rect),
            header_height=window.tdp_table.horizontalHeader().height(),hscroll_height=window.tdp_table.horizontalScrollBar().height(),
            native=[native.widget.width(),native.widget.height()],render_size=native.widget.window.GetSize(),
            ratio=ratio,font_pixels=window.font().pixelSize(),dpi=window.logicalDpiY(),
            font=font_details(window.font()),
            camera_initialized=native._camera_initialized,initial_fit_pending=native._initial_fit_pending,
            camera=camera_state(native.renderer.GetActiveCamera()),ranges=native.physical_ranges,
            native_calls=len(window._layout_test_native_calls),
            annotation=native.annotation_layout,warning=native.annotation_message.text()))
        (tmp_path/'layout-stages.json').write_text(json.dumps(dict(scope='Offscreen Qt/VTK synthetic layout; no OS input or solver calls',
            source_hashes={name:hashlib.sha256((source_root/name).read_bytes()).hexdigest() for name in
                ('apps/workbench_qt/mdo_studio/window.py','apps/workbench_qt/mdo_studio/views/plot_base.py',
                 'apps/workbench_qt/mdo_studio/views/pareto_view.py')},
            stages=stages),indent=2),encoding='utf-8')
    apply=window._apply_default_splitter
    def apply_and_record(token):
        record('before-default'); apply(token); record('after-default')
    monkeypatch.setattr(window,'_apply_default_splitter',apply_and_record)
    window.show(); record('after-show'); settle_qt(app); record('after-layout-requests-2d')
    assert window.height()==size[1]
    assert window._default_splitter_pending is None
    initial_sizes=window.center_splitter.sizes()
    size_calls=[]; set_sizes=window.center_splitter.setSizes
    monkeypatch.setattr(window.center_splitter,'setSizes',lambda sizes:(size_calls.append(sizes),set_sizes(sizes))[1])
    if entry=='blank':
        from dataclasses import replace
        import threading
        from PySide6.QtTest import QTest
        from caelab.mdo.variables import VariableSpace,ContinuousVariable
        from caelab.mdo.objectives import ObjectiveSpec
        service=window.service; service.read_only=False
        deck=tmp_path/'ordinary.inp'
        deck.write_text('*MATERIAL,NAME=ROOT\n*ELASTIC\n200000,0.3\n*MATERIAL,NAME=TIP\n*ELASTIC\n200000,0.3\n')
        service.open_input(deck)
        service.parameterize([dict(token=f'E{i}',source_path=str(deck.resolve()),line_number=line,
            expected_line='200000,0.3',old_literal='200000',occurrence=1) for i,line in ((1,3),(2,6))])
        space=VariableSpace(variables=tuple(ContinuousVariable(id=f'E{i}',name=f'Material {i}',unit='MPa',
            lower=100000.,upper=300000.,initial=200000.) for i in (1,2)))
        window._configure_study(space,[ObjectiveSpec(id=key,response_id=key) for key in ('disp','stress','mass')],[],
            {'disp':'mm','stress':'MPa','mass':'N*s**2/mm'},response_selectors={'mass':dict(
                source='DAT',field='EMAS',component='value',frame='last',set='EALL',reduction='sum',unit='N*s**2/mm')})
        generated=service.generate_doe(2,42); window._populate_study(); record('configured-before-results')
        callback=window._callbacks()['on_item']
        def deliver():
            # Synthetic snapshots exercise the normal queued GUI path; no solver or model is invoked.
            for index,(row,values) in enumerate(zip(generated,((.0584,297.,.000113),(.0969,172.,.0000631)))):
                snapshot=replace(row,status='COMPLETED',mode='SURROGATE',feasibility='FEASIBLE',
                    observations=dict(zip(('disp','stress','mass'),values)))
                with service._lock:
                    service.study.records[index]=snapshot; service.study._record_map[row.point_id]=snapshot
                callback(row.point_id,service.record_snapshot(row.point_id),None)
        producer=threading.Thread(target=deliver); producer.start(); producer.join()
        QTest.qWait(450); settle_qt(app); record('first-queued-results-2d')
        assert window.tdp_model.rowCount()==2 and not window._pending_records
        assert all(row.mode=='SURROGATE' and not row.attempts for row in window.tdp_model.records)
        assert size_calls==[] and window.center_splitter.sizes()==initial_sizes
    window.pareto_view.dimension.setCurrentText('3D'); settle_qt(app)
    native=window.pareto_view.native
    window.tdp_table.selectRow(0); settle_qt(app)
    record('first-3d-no-explicit-fit')
    row_rect=window.tdp_table.visualRect(window.tdp_proxy.index(0,0))
    assert window.tdp_table.viewport().rect().contains(row_rect)
    assert window.tdp_table.rowHeight(0)==30
    assert window.tdp_table.horizontalHeader().isVisible()
    assert window.tdp_table.horizontalScrollBar().isVisible()
    assert window.center_splitter.sizes()==initial_sizes
    assert size_calls==[]
    from apps.workbench_qt.mdo_studio.views.pareto_view import PARETO_LEGEND_TEXT
    assert native.legend.text()==PARETO_LEGEND_TEXT
    if size[0]==1380:
        assert initial_sizes[0]<=round(.45*sum(initial_sizes))
        assert window.tdp_table.viewport().height()>=2*window.tdp_table.rowHeight(0)
    assert native.widget.window.GetSize()==(round(native.widget.width()*ratio),round(native.widget.height()*ratio))
    assert native.widget.window.GetDPI()==round(72*ratio)
    assert native.annotation_layout['effective_glyph_pixels']==pytest.approx(12*ratio)
    assert native.annotation_layout['valid']
    assert not native.annotation_message.text()
    initial=tmp_path/'initial'; initial.mkdir(); assert_annotation_pixels_match_boxes(native,initial)
    # Initial 2D -> 3D is checked without an explicit Fit. Keep the original modest drag.
    interactor=native.widget.interactor; start=(round(native.widget.width()*.55*ratio),round(native.widget.height()*.52*ratio))
    before=camera_state(native.renderer.GetActiveCamera())
    selected=set(window._selected_ids); physical=native.physical_ranges
    interactor.SetEventInformation(*start,0,0,'0',0,None); interactor.InvokeEvent('LeftButtonPressEvent')
    interactor.SetEventInformation(start[0]+round(100*ratio),start[1]-round(38*ratio),0,0,'0',0,None)
    interactor.InvokeEvent('MouseMoveEvent')
    moved=camera_state(native.renderer.GetActiveCamera())
    record('drag-move-before-release')
    assert moved!=before
    during=tmp_path/'move'; during.mkdir(); assert_annotation_pixels_match_boxes(native,during)
    assert native.annotation_layout['valid']
    interactor.InvokeEvent('LeftButtonReleaseEvent'); settle_qt(app)
    record('drag-release')
    assert camera_state(native.renderer.GetActiveCamera())==moved
    assert native.annotation_layout['valid'] and not native.annotation_message.text()
    assert window._selected_ids==selected and native.physical_ranges==physical
    release=tmp_path/'release'; release.mkdir(); assert_annotation_pixels_match_boxes(native,release)
    assert window._layout_test_native_calls==[]


def test_saved_global_and_study_splitters_win_over_fresh_default(layout_studio,app,tmp_path):
    import json
    seed=layout_studio(); seed.show(); drain_layout(app)
    seed.center_splitter.setSizes([260,364]); drain_layout(app)
    global_sizes=seed.center_splitter.sizes(); global_layout=seed.saveState(); seed._save_layout()
    seed.center_splitter.setSizes([200,424]); study_sizes=seed.center_splitter.sizes()
    study_splitter_state=bytes(seed.center_splitter.saveState()); study_state=study_splitter_state.hex()
    seed.setup_dock.setFloating(True); seed.setup_dock.hide()
    study_layout=bytes(seed.saveState()).hex(); seed.restoreState(global_layout)
    seed.center_splitter.setSizes(global_sizes); seed._save_layout()
    global_splitter_state=bytes(seed.center_splitter.saveState())
    global_window=layout_studio(); global_window.show(); drain_layout(app)
    assert global_window.center_splitter.sizes()==global_sizes
    assert bytes(global_window.center_splitter.saveState())==global_splitter_state
    assert global_window._default_splitter_pending is None
    study_window=layout_studio({'layout':study_layout,'splitter':study_state,'tab':1,'selected_ids':['DP_0001']})
    study_window.show(); drain_layout(app)
    (tmp_path/'saved-splitters.json').write_text(json.dumps(dict(
        font=font_details(study_window.font()),requested_global=[260,364],requested_study=[200,424],
        global_seed_sizes=global_sizes,global_reopened_sizes=global_window.center_splitter.sizes(),
        study_seed_sizes=study_sizes,study_reopened_sizes=study_window.center_splitter.sizes(),
        global_seed_state=global_splitter_state.hex(),
        global_reopened_state=bytes(global_window.center_splitter.saveState()).hex(),
        study_seed_state=study_state,study_reopened_state=bytes(study_window.center_splitter.saveState()).hex(),
        seed_outer=[seed.width(),seed.height()],reopened_outer=[study_window.width(),study_window.height()],
        seed_splitter_height=seed.center_splitter.height(),
        reopened_splitter_height=study_window.center_splitter.height()),indent=2),encoding='utf-8')
    assert study_window.center_splitter.sizes()==study_sizes
    assert bytes(study_window.center_splitter.saveState())==study_splitter_state
    assert study_window.viz_tabs.currentIndex()==1
    assert study_window._selected_ids=={'DP_0001'}
    assert study_window.setup_dock.isFloating() and study_window.setup_dock.isHidden()
    assert study_window._default_splitter_pending is None


def test_invalid_splitter_restore_uses_default_and_keeps_valid_global(layout_studio,app):
    from PySide6.QtCore import QSettings,QByteArray
    import apps.workbench_qt.mdo_studio.window as studio
    settings=studio.QSettings('CAELab','MDO Studio'); settings.setValue('splitter',QByteArray(b'invalid'))
    window=layout_studio({'splitter':'invalid hex','layout':'invalid hex'})
    assert window._default_splitter_pending is not None
    window.show(); drain_layout(app)
    window.center_splitter.setSizes([250,374]); saved=window.center_splitter.sizes(); window._save_layout()
    restored=layout_studio({'splitter':b'invalid Qt state'.hex()}); restored.show(); drain_layout(app)
    assert restored.center_splitter.sizes()==saved
    assert restored._default_splitter_pending is None


@pytest.mark.parametrize('action',('handle','new-study','close','restore-study'))
def test_pending_default_yields_to_user_restore_adoption_and_close(layout_studio,app,action):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    window=layout_studio(); token=window._default_splitter_pending
    assert token is not None
    if action=='handle':
        QTest.mousePress(window.center_splitter.handle(1),Qt.MouseButton.LeftButton)
        QTest.mouseRelease(window.center_splitter.handle(1),Qt.MouseButton.LeftButton)
    elif action=='new-study': window._on_new_study()
    elif action=='close': window.close()
    else:
        window.service.view_state={'splitter':bytes(window.center_splitter.saveState()).hex()}
        window._restore_study_view()
    assert window._default_splitter_pending is None
    window._apply_default_splitter(token)
    assert window._default_splitter_pending is None


def test_restore_default_is_one_shot_and_resize_tab_delta_do_not_reset_custom(layout_studio,app,monkeypatch):
    window=layout_studio(); window.show(); drain_layout(app)
    window.center_splitter.setSizes([240,384]); window._save_layout()
    calls=[]; original=window.center_splitter.setSizes
    monkeypatch.setattr(window.center_splitter,'setSizes',lambda sizes:(calls.append(list(sizes)),original(sizes))[1])
    window._default_layout(); old=window._default_splitter_pending
    window._request_default_splitter(); window._apply_default_splitter(old)
    drain_layout(app)
    assert len(calls)==1
    window.center_splitter.setSizes([250,374]); calls.clear()
    window.resize(1100,760); window.viz_tabs.setCurrentIndex(1)
    window._populate_study(); drain_layout(app)
    row=window.service.record_snapshot('DP_0001')
    window.bridge.itemFinished.emit(row.point_id,row,None); drain_layout(app)
    window._flush_deltas(); drain_layout(app)
    assert calls==[]
