"""Actual Workbench host of Prep drafts; no solver or Windows acceptance claim."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox

from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.variables import ContinuousVariable, VariableSpace
from caelab.workbench import Workbench
from apps.workbench_qt.mdo_studio import study_config
from apps.workbench_qt.mdo_studio.window import MDOStudioWindow


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.setenv('CAELAB_UI_SETTINGS_PATH', str(tmp_path / 'theme.ini'))
    import apps.workbench_qt.mdo_studio.window as studio
    monkeypatch.setattr(studio, 'QSettings', lambda *args: QSettings(
        str(tmp_path / 'layout.ini'), QSettings.Format.IniFormat))
    app = QApplication.instance() or QApplication([])
    source = tmp_path / '사용자 형상.inp'
    source.write_text('*BEAM SECTION,SECTION=RECT\n<WIDTH>, <HEIGHT>\n')
    wb = Workbench()
    def no_run(*args, **kwargs):
        raise AssertionError('Configuring input must not launch a calculation')
    for method in ('launch', 'submit', 'submit_native', 'submit_batch'):
        monkeypatch.setattr(wb.jobs, method, no_run)
    service = wb.create_mdo_study()
    service.open_input(source)
    service.configure(VariableSpace(variables=(
        ContinuousVariable(id='width', name='Width', unit='mm', lower=1, upper=5, initial=2),
        ContinuousVariable(id='height', name='Height', unit='mm', lower=1, upper=4, initial=2),
    )), [ObjectiveSpec(id='d', response_id='disp')], [], {'disp': 'mm'})
    window = MDOStudioWindow(service=service)
    window.show(); window._on_configure(); app.processEvents()
    yield app, window, service, source
    window.close(); service.close(); app.processEvents()


def variable_row(editor, token='width'):
    return next(row for row in range(editor.variables.rowCount())
                if editor.variables.item(row, 0).text().lower() == token)


def shared_editor(window, row=None, col=3):
    if study_config.PrepNumericEdit is None:
        pytest.skip('Install optional caelab-prep[widgets] to verify actual shared controls')
    editor = window._editor
    assert editor.uses_shared_numeric_controls
    if row is None: row = variable_row(editor)
    field = editor.variables.cellWidget(row, col)
    assert isinstance(field, study_config.PrepNumericEdit)
    return field


def apply(window):
    QTest.mouseClick(window._editor.buttons.button(QDialogButtonBox.StandardButton.Apply),
                    Qt.MouseButton.LeftButton)
    QApplication.processEvents()


def type_value(field, value):
    field.setFocus(); field.selectAll(); QTest.keyClicks(field, value)


def test_real_host_shared_draft_apply_and_new_process_roundtrip(host, tmp_path):
    app, window, service, source = host
    field = shared_editor(window)
    original = source.read_bytes(); before_revision = service.revision
    for invalid in ('1e', 'nan', 'inf'):
        type_value(field, invalid); apply(window)
        assert field.text() == invalid and window._editor.error.text()
        assert window._editor.variables.currentColumn() == 3
        assert QApplication.focusWidget() is field
        assert service.revision == before_revision
        assert {v.id: v.initial for v in service.study.space.variables}['width'] == 2
    QTest.keyClick(field, Qt.Key.Key_Escape)
    assert field.text() == '2'
    type_value(field, '3'); apply(window)
    assert service.revision == str(int(before_revision) + 1)
    assert {v.id: v.initial for v in service.study.space.variables} == {'width': 3, 'height': 2}
    assert service._undo_stack == []  # Current Design file Undo remains service-owned.
    assert source.read_bytes() == original
    type_value(field, '3.8'); QTest.keyClick(field, Qt.Key.Key_Escape)
    assert float(field.text()) == 3
    saved = tmp_path / 'saved-study.json'; service.save(saved)
    script = """import json,sys
from caelab.workbench import Workbench
s=Workbench().open_mdo_study(sys.argv[1],read_only=True)
print(json.dumps({'revision':s.revision,'values':[(v.id,v.initial,v.unit) for v in s.study.space.variables]}))
s.close()
"""
    result = subprocess.run([sys.executable, '-c', script, str(saved)], text=True,
                            capture_output=True, check=True, timeout=20)
    restored = json.loads(result.stdout)
    assert restored['revision'] == service.revision
    assert {name: (value, unit) for name, value, unit in restored['values']} == {
        'width': (3.0, 'mm'), 'height': (2.0, 'mm')}
    for _ in range(2):
        window._on_toggle_theme(); app.processEvents()
        assert not window.grab().isNull()


def test_stale_revision_unit_domain_and_campaign_reject_without_losing_draft(host):
    _app, window, service, _source = host
    field = shared_editor(window)
    editor = window._editor; before = service.revision; row = variable_row(editor)
    for col, value in ((4, ''), (4, 'unknown_unit'), (1, '6')):
        editor.variables.item(row, col).setText(value); apply(window)
        assert service.revision == before and editor.error.text()
        assert editor.variables.item(row, col).text() == value
        editor.revert(); field = shared_editor(window)
    type_value(field, '3')
    config = editor.configuration()
    updated = VariableSpace(variables=(
        ContinuousVariable(id='width', name='Updated width', unit='cm', lower=1, upper=9, initial=4),
        next(v for v in service.study.space.variables if v.id == 'height'),
    ))
    service.configure(updated, *config[1:])
    external_revision = service.revision
    apply(window)
    assert service.revision == external_revision and field.text() == '3'
    assert 'revision changed' in editor.error.text()
    editor.revert()
    assert editor.variables.item(row, 4).text() == 'cm'
    assert shared_editor(window).text() == '4'
    field = shared_editor(window); type_value(field, '5')
    service.active_batch_id = 'active-audit-campaign'
    apply(window)
    assert service.revision == external_revision and field.text() == '5'
    service.active_batch_id = None
    service.read_only = True; window._sync_actions()
    assert not editor.buttons.button(QDialogButtonBox.StandardButton.Apply).isEnabled()
    service.read_only = False


def test_optional_dependency_absence_keeps_original_table_input(host, monkeypatch):
    _app, window, service, _source = host
    monkeypatch.setattr(study_config, 'PrepNumericEdit', None)
    editor = study_config.StudyConfigEditor(service.template.token_names, service.study,
                                          service.response_units, service.response_selectors)
    assert not editor.uses_shared_numeric_controls
    assert editor.variables.property('numericEditorSource') == 'Qt table'
    row = variable_row(editor)
    assert editor.variables.cellWidget(row, 3) is None
    editor.variables.item(row, 3).setText('3')
    assert {v.id: v.initial for v in editor.configuration()[0].variables}['width'] == 3
    editor.close()


def test_categorical_domain_keeps_text_initial_and_returns_to_shared_number(host):
    _app, window, _service, _source = host
    shared_editor(window)
    editor = window._editor; table = editor.variables; row = variable_row(editor)
    table.cellWidget(row, 6).setCurrentText('Categorical')
    assert table.cellWidget(row, 3) is None
    table.item(row, 7).setText('steel, aluminium'); table.item(row, 3).setText('steel')
    assert {v.id: v.initial for v in editor.configuration()[0].variables}['width'] == 'steel'
    table.cellWidget(row, 6).setCurrentText('Continuous')
    type_value(shared_editor(window), '2.5')
    assert {v.id: v.initial for v in editor.configuration()[0].variables}['width'] == 2.5


def test_domain_roundtrip_does_not_promote_draft_to_committed_value(host):
    _app, window, service, _source = host
    field = shared_editor(window); editor = window._editor; row = variable_row(editor)
    def domain_roundtrip():
        editor.variables.cellWidget(row, 6).setCurrentText('Discrete')
        editor.variables.cellWidget(row, 6).setCurrentText('Continuous')
        return shared_editor(window)
    before = service.revision
    type_value(field, '3'); field = domain_roundtrip()
    assert field.text() == '3'
    QTest.keyClick(field, Qt.Key.Key_Escape)
    assert float(field.text()) == 2 and service.revision == before
    type_value(field, '3'); apply(window)
    type_value(field, '4'); field = domain_roundtrip()
    QTest.keyClick(field, Qt.Key.Key_Escape)
    assert float(field.text()) == 3
    assert {v.id: v.initial for v in service.study.space.variables}['width'] == 3
