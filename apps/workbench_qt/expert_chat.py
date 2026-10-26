"""Display-only helpers for a local, untrusted Markdown conversation."""
from __future__ import annotations

import json
from pathlib import PureWindowsPath
import re

from PySide6.QtCore import QByteArray, Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextDocument, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QTextBrowser

from .theme import UI_FONT_STACK, get_palette


def human_text(value) -> str:
    return '' if value is None else str(value)


def user_error(error) -> str:
    """Internal diagnostics stay saved; show a meaningful action, not their payload."""
    text = str(error)
    if '다른 창' in text or '개정' in text and '충돌' in text:
        return '다른 창에서 대화가 변경되었습니다. 초안은 유지됩니다. 다시 읽은 뒤 저장하세요.'
    if '무결성' in text or '해시' in text or 'SHA' in text:
        return '첨부 자료의 무결성을 확인하지 못했습니다. 자료를 다시 불러온 뒤 질문하세요.'
    if '누락' in text or '없' in text or 'No such file' in text:
        return '필요한 파일이나 계산 기록을 찾지 못했습니다. 첨부 자료를 확인하세요.'
    if '중단' in text or 'cancel' in text.lower():
        return '응답 대기를 중단했습니다. 질문 초안은 유지됩니다.'
    if '시간' in text or 'timeout' in text.lower():
        return '응답 시간이 초과되었습니다. 연결을 확인한 뒤 다시 질문하세요.'
    if '제안' in text:
        return '제안의 내용이나 출처를 확인하지 못했습니다. 원본은 변경하지 않았습니다.'
    return '요청을 처리하지 못했습니다. 연결 설정과 첨부 자료를 확인한 뒤 다시 시도하세요.'


def source_name(path) -> str:
    # Keep the user-visible parent folder (A/B), never a run-store identifier.
    parts = PureWindowsPath(str(path or '')).parts
    if not parts:
        return '입력 출처 미확인'
    if len(parts) > 1 and not (any(part.endswith('.runs') for part in parts[:-1]) and re.fullmatch(r'[0-9a-fA-F]{32}', parts[-2])):
        return '/'.join(parts[-2:])
    return parts[-1]


def evidence_name(evidence) -> str:
    manifest = evidence.get('input_manifest') or {}
    path = manifest.get('input_path') or next((f.get('source_path') for f in evidence.get('files', [])
                                             if f.get('role') == 'input'), None)
    if path:
        name = source_name(path)
    else:
        roles = {'frd': '결과', 'dat': '수치 출력', 'log': '로그', 'raw': '파일', 'include': '추가 입력'}
        names = [f"{roles.get(item.get('role'), '파일')} {source_name(item.get('source_path'))}"
                 for item in evidence.get('files', []) if item.get('source_path')]
        name = ' · '.join(names) if names else '입력 출처 미확인'
    # This is the preserved capture time, not an invented solver completion time.
    stamp = evidence.get('created_at', '')
    try:
        from datetime import datetime
        stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone().strftime('%m/%d %H:%M:%S')
    except (ValueError, TypeError):
        stamp = ''
    return name + (f' · 자료 보존 {stamp}' if stamp else '')


class LocalDocument(QTextDocument):
    def loadResource(self, resource_type, name):  # noqa: N802
        return QByteArray()  # No local file, network image, stylesheet or other resource loads.


class ChatBrowser(QTextBrowser):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDocument(LocalDocument(self))
        font = QFont()
        font.setFamilies(['Segoe UI', 'Malgun Gothic', 'Cambria Math', 'Segoe UI Symbol'])
        self.setFont(font)
        self.document().setDefaultFont(font)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.follow_tail = True
        self.restored_anchor = None
        bar = self.verticalScrollBar()
        bar.rangeChanged.connect(self._follow_range)
        bar.actionTriggered.connect(self._manual_scroll)

    def _follow_range(self, minimum, maximum):
        if self.restored_anchor is not None:
            self._apply_restored_anchor()
        elif self.follow_tail:
            self.verticalScrollBar().setValue(maximum)

    def _manual_scroll(self, *_):
        self.restored_anchor = None
        self.follow_tail = False

    def wheelEvent(self, event):  # noqa: N802
        self._manual_scroll()
        super().wheelEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown, Qt.Key_Home, Qt.Key_End):
            self._manual_scroll()
        super().keyPressEvent(event)

    def restore_anchor(self, position, offset, follow):
        self.follow_tail = follow
        self.restored_anchor = None if follow else (position, offset)
        if follow:
            self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())
        else:
            self._apply_restored_anchor()

    def _apply_restored_anchor(self):
        if self.restored_anchor is None:
            return
        position, offset = self.restored_anchor
        cursor = QTextCursor(self.document())
        cursor.setPosition(min(max(0, position), self.document().characterCount() - 1))
        bar = self.verticalScrollBar()
        bar.setValue(bar.value() + self.cursorRect(cursor).top() - offset)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, 'restored_anchor'):
            self._apply_restored_anchor()

    def loadResource(self, resource_type, name):  # noqa: N802
        return QByteArray()


def markdown_body(text: str, *, accepted_proposals=None) -> tuple[str, bool]:
    """Render ordinary Markdown; strip only internal fenced proposal payloads."""
    malformed = False
    protocol_items = []
    lines, kept = text.splitlines(), []
    index = 0
    in_code = False
    fence_marker = ''
    while index < len(lines):
        line = lines[index]
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
        if match and not in_code and match[2].strip().lower() == 'proposals':
            marker, payload = match[1], []
            index += 1
            while index < len(lines) and not re.match(r'^\s{0,3}' + re.escape(marker[0]) + '{' + str(len(marker)) + r',}\s*$', lines[index]):
                payload.append(lines[index])
                index += 1
            try:
                raw = json.loads('\n'.join(payload))
                if index == len(lines) or not isinstance(raw, list):
                    malformed = True
                else:
                    for item in raw:
                        if not isinstance(item, dict) or not all(key in item for key in ('target_file', 'location', 'before', 'after', 'reason')) or any(
                                not isinstance(item[key], str) or not item[key].strip() for key in ('target_file', 'location', 'reason')):
                            malformed = True
                            continue
                        json.dumps(item, allow_nan=False)
                        protocol_items.append({key: item[key] for key in ('target_file', 'location', 'before', 'after', 'reason')})
            except (ValueError, TypeError):
                malformed = True
        else:
            kept.append(line)
            if match:
                if not in_code:
                    in_code, fence_marker = True, match[1]
                elif match[1][0] == fence_marker[0] and len(match[1]) >= len(fence_marker) and not match[2].strip():
                    in_code = False
        index += 1
    if accepted_proposals is not None:
        saved = [{key: item.get(key) for key in ('target_file', 'location', 'before', 'after', 'reason')} for item in accepted_proposals]
        malformed = malformed or protocol_items != saved
    document = LocalDocument()
    document.setMarkdown(human_text('\n'.join(kept)), QTextDocument.MarkdownDialectGitHub | QTextDocument.MarkdownNoHTML)
    # Links remain readable text, but cannot act as navigation or carry active URLs.
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        fragments = []
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().isImageFormat():
                fragments.append((fragment.position(), fragment.length(), True))
            elif fragment.isValid() and fragment.charFormat().isAnchor():
                fragments.append((fragment.position(), fragment.length(), False))
            iterator += 1
        for start, length, image in reversed(fragments):
            cursor = QTextCursor(document)
            cursor.setPosition(start)
            cursor.setPosition(start + length, QTextCursor.KeepAnchor)
            if image:
                cursor.insertText('[이미지 표시 생략]')
                continue
            fmt = cursor.charFormat()
            fmt.setAnchor(False)
            fmt.setAnchorHref('')
            cursor.setCharFormat(fmt)
        block = block.next()
    # Format citations after parsing so code examples and Markdown stay intact.
    p = get_palette()
    block = document.begin()
    while block.isValid():
        if not block.blockFormat().nonBreakableLines():
            for match in re.finditer(r'\[근거:[^\]\n]+\]', block.text()):
                cursor = QTextCursor(document)
                start = block.position() + len(block.text()[:match.start()].encode('utf-16-le')) // 2
                end = start + len(match[0].encode('utf-16-le')) // 2
                cursor.setPosition(start)
                cursor.setPosition(end, QTextCursor.KeepAnchor)
                if cursor.charFormat().fontFixedPitch():
                    continue
                fmt = QTextCharFormat()
                fmt.setBackground(QColor(str(p['chat_citation_bg'])))
                fmt.setForeground(QColor(str(p['chat_citation_fg'])))
                cursor.mergeCharFormat(fmt)
        block = block.next()
    result = document.toHtml().split('<body', 1)[1].split('>', 1)[1].rsplit('</body>', 1)[0]
    return result, malformed


class QuestionEdit(QPlainTextEdit):
    submitted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.composing = False
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self._fit_height()

    def _fit_height(self, *_):
        lines = self.document().documentLayout().documentSize().height()
        height = min(144, max(64, int(lines * self.fontMetrics().lineSpacing()) + 26))
        if self.height() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._fit_height()

    def inputMethodEvent(self, event):  # noqa: N802
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            if not self.composing and not self.isReadOnly() and not event.isAutoRepeat():
                self.submitted.emit()
                event.accept()
                return
        super().keyPressEvent(event)


def proposal_text(item) -> str:
    valid = isinstance(item, dict) and all(isinstance(item.get(k), str) and item[k].strip()
                                          for k in ('target_file', 'location', 'reason')) and 'before' in item and 'after' in item
    if not valid:
        return '제안 내용을 확인할 수 없습니다 · 미적용\n\n저장된 제안의 형식 또는 출처가 불완전합니다. 원문은 보존되어 있습니다. 전문가에게 대상과 점검 내용을 다시 확인하세요.'
    check_only = item['after'] is None
    labels = {'unapplied': '미적용', 'manual_required': '전용 프로그램에서 확인 필요 · 미적용' if check_only else '전용 프로그램에서 수정 필요 · 미적용',
              'blocked': '검토 중단 · 미적용', 'rejected': '거절 · 미적용'}
    def content(value):
        if value is None:
            return '지정하지 않음'
        if isinstance(value, (str, int, float, bool)):
            return human_text(value)
        return '내용을 표시할 수 없습니다. 전문가에게 구체적인 값을 다시 확인하세요.'
    lines = [labels.get(str(item.get('status')), '상태 미확인 · 미적용'), '',
             human_text(item['reason']), '',
             f"대상: {source_name(item['target_file'])}", f"위치: {human_text(item['location'])}",
             f"현재 내용: {content(item['before'])}"]
    if not check_only:
        lines.append(f"제안 내용: {content(item['after'])}")
    lines += ['']
    review = item.get('review_reason')
    if item.get('status') == 'blocked':
        lines += ['검토: 원본 또는 보존 자료가 변경·누락되었거나 제안의 출처를 확인할 수 없습니다. 다시 자료를 확인하세요.', '']
    elif review and item.get('status') == 'rejected':
        lines += [f'거절 이유: {human_text(review)}', '']
    lines += ['전용 프로그램에서 설정을 확인하세요.' if check_only else '전용 프로그램에서 수정하세요.',
              '원본은 자동으로 바뀌지 않습니다. 재계산은 해석 창에서 별도로 실행한 뒤 새 결과를 가져오세요.']
    return '\n'.join(lines)


def evidence_cautions(evidence, issues=()) -> list[str]:
    notes = []
    units = evidence.get('units') or {}
    if not units:
        notes.append('단위 미확인')
    elif any(not units.get(key) for key in ('length', 'force', 'stress')):
        notes.append('일부 단위 미확인')
    process = evidence.get('process_state')
    if process in {'failed', 'cancelled', 'interrupted'}:
        notes.append({'failed': '계산 실패 자료', 'cancelled': '계산 중단 자료', 'interrupted': '완료를 확인하지 못한 계산'}[process])
    parse = evidence.get('parse_state')
    if parse in {'partial', 'unsupported', 'missing', 'failed'}:
        notes.append({'partial': '결과 일부만 읽음', 'unsupported': '읽기를 지원하지 않는 결과', 'missing': '판독할 결과 없음', 'failed': '결과를 읽지 못함'}[parse])
    if evidence.get('source_kind') == 'manual':
        notes.append('동일 계산의 파일인지 미확인')
    warnings = evidence.get('warnings') or []
    if any('오래' in str(value) or 'stale' in str(value).lower() for value in warnings):
        notes.append('이전 입력의 오래된 결과')
    if issues:
        if any('없' in str(issue) or '누락' in str(issue) for issue in issues):
            notes.append('보존한 파일 누락')
        else:
            notes.append('자료 변경 또는 출처 확인 필요')
    if warnings and not any('오래' in str(value) or 'stale' in str(value).lower() for value in warnings):
        notes.append('자료에 확인할 제한 사항 있음')
    return notes


def proposal_brief(item) -> str:
    detail = proposal_text(item)
    if detail.startswith('제안 내용을 확인할 수 없습니다'):
        return '제안 내용을 확인할 수 없습니다 · 미적용'
    status = {'rejected': '거절 · 미적용', 'blocked': '검토 중단 · 미적용',
              'manual_required': '전용 프로그램에서 확인 필요 · 미적용' if item['after'] is None else '전용 프로그램에서 수정 필요 · 미적용',
              'unapplied': '미적용'}.get(str(item.get('status')), '상태 미확인 · 미적용')
    return f"{status}\n{human_text(item['reason'])}\n대상: {source_name(item['target_file'])} · {human_text(item['location'])}"
