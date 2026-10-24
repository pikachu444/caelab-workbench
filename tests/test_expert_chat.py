"""Safe Markdown and composing-aware keyboard behavior, without model calls."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6')
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QInputMethodEvent, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from apps.workbench_qt.expert_chat import (ChatBrowser, QuestionEdit, evidence_name,
                                          markdown_body, proposal_brief, proposal_text)

@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


def test_markdown_formats_text_table_and_normal_code_but_removes_only_proposals(app):
    text = '**강조**\n\n- 항목 하나\n- 항목 둘\n\n| 조건 | 값 |\n| --- | --- |\n| 힘 | 5 |\n\n```python\nprint("hello")\n```\n\n```proposals\n[]\n```'
    body, malformed = markdown_body(text)
    browser = ChatBrowser()
    browser.setHtml(body)
    plain = browser.toPlainText()
    assert not malformed and '**강조**' not in plain and '강조' in plain
    assert '항목 하나' in plain and 'print("hello")' in plain
    assert '<table' in body and 'font-weight:700' in body
    assert 'proposals' not in plain


@pytest.mark.parametrize('block', ['```proposals\n{bad}\n```', '```proposals\n[1]\n```', '```proposals\n[]'])
def test_bad_proposal_payload_is_not_shown_as_a_normal_proposal(app, block):
    body, malformed = markdown_body('응답 본문\n\n' + block)
    assert malformed and 'bad' not in body and 'proposals' not in body
    assert '응답 본문' in body


def test_untrusted_html_images_and_links_never_load_or_navigate(app, tmp_path):
    body, _ = markdown_body('<script>alert(1)</script>\n\n![외부](https://example.invalid/a.png)\n\n[위험](javascript:alert) [로컬](file:///secret)')
    browser = ChatBrowser()
    browser.setHtml(body)
    assert '<img' not in body and 'href=' not in body
    assert not browser.openLinks() and not browser.openExternalLinks()
    assert browser.loadResource(QTextDocument.ImageResource, QUrl('file:///secret')).isEmpty()
    assert browser.document().loadResource(QTextDocument.ImageResource, QUrl('https://example.invalid/a.png')).isEmpty()


def test_enter_shift_ctrl_and_ime_preedit_are_distinct(app):
    editor = QuestionEdit()
    sends = []
    editor.submitted.connect(lambda: sends.append(editor.toPlainText()))
    editor.show(); editor.setFocus(); app.processEvents()
    editor.setPlainText('초안')
    QTest.keyClick(editor, Qt.Key_Return, Qt.ShiftModifier)
    assert '\n' in editor.toPlainText() and sends == []
    app.sendEvent(editor, QInputMethodEvent('ㅎ', []))
    QTest.keyClick(editor, Qt.Key_Return)
    QTest.keyClick(editor, Qt.Key_Return, Qt.ControlModifier)
    assert sends == []
    commit = QInputMethodEvent()
    commit.setCommitString('한')
    app.sendEvent(editor, commit)
    QTest.keyClick(editor, Qt.Key_Return)
    QTest.keyClick(editor, Qt.Key_Return, Qt.ControlModifier)
    assert len(sends) == 2
    editor.setReadOnly(True)
    QTest.keyClick(editor, Qt.Key_Return)
    assert len(sends) == 2
    editor.close()


def test_human_proposal_preserves_zero_and_check_only_without_internal_payload(app):
    item = dict(target_file='C:/project/A/model.inp', location='ConstraintForce', before=0, after=None,
                reason='방향 확인', status='manual_required', id='a'*32, target_sha256='b'*64)
    detail = proposal_text(item)
    assert '현재 내용: 0' in detail and '전용 프로그램에서 설정을 확인하세요.' in detail
    assert all(value not in detail for value in ('None', 'SHA', 'a'*32, 'b'*64, '변경 후'))
    assert 'ConstraintForce' in proposal_brief(item) and '미적용' in proposal_brief(item)
    assert '불완전' in proposal_text({'id': 'legacy'})


def test_attachment_display_uses_real_origin_and_capture_time_not_ids(app):
    evidence = dict(id='a'*32, run_id='b'*32, created_at='2026-10-24T09:00:00Z',
                    input_manifest={'input_path': 'C:/project/A/same.inp'})
    name = evidence_name(evidence)
    assert 'A/same.inp' in name and '자료 보존' in name
    assert 'a'*32 not in name and 'b'*32 not in name
    assert evidence_name({}) == '입력 출처 미확인'


def test_requested_json_and_hash_like_code_remain_readable(app):
    token = 'a' * 64
    body, malformed = markdown_body('```json\n{"sha256": "' + token + '", "value": 0}\n```')
    browser = ChatBrowser()
    browser.setHtml(body)
    assert not malformed
    assert token in browser.toPlainText() and '"value": 0' in browser.toPlainText()


def test_nested_fences_in_ordinary_code_are_preserved(app):
    body, malformed = markdown_body('````text\n```proposals\nexample, not a protocol\n```\n````')
    browser = ChatBrowser()
    browser.setHtml(body)
    assert not malformed
    assert 'example, not a protocol' in browser.toPlainText()
    assert 'proposals' in browser.toPlainText()


def test_empty_composer_is_compact_and_grows_with_the_draft(app):
    editor = QuestionEdit()
    editor.resize(600, 64)
    editor.show(); app.processEvents()
    assert editor.height() == 64
    editor.setPlainText('작성할 긴 질문입니다.\n' * 10)
    app.processEvents()
    assert 64 < editor.height() <= 144
    editor.clear(); app.processEvents()
    assert editor.height() == 64
    editor.close()


@pytest.mark.parametrize('status, label', [('rejected', '거절'), ('blocked', '검토 중단')])
def test_proposal_brief_shows_actual_action_and_review_state(app, status, label):
    item = dict(target_file='A/model.inp', location='ConstraintForce', before=0, after=None,
                reason='FreeCAD에서 하중 대상 면·방향·총하중을 확인하세요.', status=status)
    brief = proposal_brief(item)
    assert label in brief and '미적용' in brief
    assert 'FreeCAD에서 하중 대상 면·방향·총하중' in brief
    assert proposal_text(item).splitlines()[2] == item['reason']


def test_malformed_proposal_status_does_not_crash_or_claim_approval(app):
    item = dict(target_file='A/input.inp', location='force', before=0, after=None,
                reason='값을 확인하세요.', status=[])
    assert '미확인' in proposal_brief(item)
    assert '미확인' in proposal_text(item)


def test_mathematical_minus_in_actual_engineering_values_is_preserved(app):
    original = 'SXX: −471.078 MPa\n\nEXX: −0.00198642'
    body, malformed = markdown_body(original)
    browser = ChatBrowser()
    browser.setHtml(body)
    assert not malformed
    assert '−471.078' in browser.toPlainText()
    assert '−0.00198642' in browser.toPlainText()
    assert browser.toPlainText().count('−') == 2
