"""Independent, file-based expert discussion. Opening never calls a model."""
from __future__ import annotations

import argparse
import copy
import html
import hashlib
import os
import queue
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSettings, QPoint
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from caelab.workbench import Workbench
from .theme import THEME
from .expert_chat import ChatBrowser, QuestionEdit, evidence_name, human_text, markdown_body, proposal_text, source_name, evidence_cautions, proposal_brief, user_error


def _evidence_name(evidence):
    return evidence_name(evidence)


class ExpertWindow(QMainWindow):
    def __init__(self, discussion_path=None, *, project_path=None, node_id=None,
                 run_id=None, workbench=None, view_settings=None):
        super().__init__()
        self.workbench = workbench or Workbench()
        self.view_settings = view_settings if view_settings is not None else QSettings(QSettings.defaultFormat(), QSettings.UserScope, 'CAELAB', 'ExpertChat')
        self._pending_view = None
        self.path = None
        self.document = {}
        self.project_path, self.node_id, self.run_id = project_path, node_id, run_id
        self._operation = None
        self._mailbox = queue.Queue()
        self._cancel = threading.Event()
        self._close_after = False
        self._pending_message = None
        self._asset_issues = []
        self.setWindowTitle('전문가 논의')
        self.resize(1100, 850)
        self.setMinimumSize(680, 580)
        self.setStyleSheet(THEME + """
            QMainWindow, QWidget#chatRoot { background: #fafaf9; }
            QLabel#heading { font-size: 19px; font-weight: 600; color: #202d39; }
            QTextBrowser#conversation { background: #fafaf9; border: none; font-size: 16px; }
            QPlainTextEdit#composer { background: white; border: 1px solid #b9c2cb;
                border-radius: 10px; padding: 10px; font-size: 15px; }
            QLabel#basis { color: #526476; font-size: 12px; }
            QPushButton#primary:disabled, QPushButton#primary:disabled:hover {
                background: #e1e6eb; border-color: #d5dce3; color: #5d6b78; }
            QPushButton:focus, QComboBox:focus { border: 2px solid #1668b4; }
        """)
        self._edit_actions = []
        menu = self.menuBar().addMenu('대화')
        for label, slot, shortcut in (
            ('새 대화', self.new_document, QKeySequence.New),
            ('열기…', self.open_document, QKeySequence.Open),
            ('저장', self.save_document, QKeySequence.Save),
            ('다시 읽기', self.reload_document, QKeySequence.Refresh),
            ('제약과 실패 기록…', self.edit_context, None),
        ):
            action = menu.addAction(label)
            action.triggered.connect(slot)
            if shortcut:
                action.setShortcut(shortcut)
            self._edit_actions.append(action)
        root = QWidget()
        root.setObjectName('chatRoot')
        outer = QHBoxLayout(root)
        outer.setContentsMargins(18, 12, 18, 14)
        outer.addStretch()
        column = QWidget()
        column.setMaximumWidth(900)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        top = QHBoxLayout()
        self.heading = QLabel('새 대화')
        self.heading.setWordWrap(True)
        self.heading.setObjectName('heading')
        top.addWidget(self.heading, 1)
        self.settings = QPushButton('연결 설정')
        self.settings.clicked.connect(self.configure_provider)
        top.addWidget(self.settings)
        layout.addLayout(top)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.hide()
        layout.addWidget(self.status)
        self.asset_warning = QLabel()
        self.asset_warning.setWordWrap(True)
        self.asset_warning.setStyleSheet('color: #8a3d11;')
        self.asset_warning.hide()
        layout.addWidget(self.asset_warning)
        self.transcript = ChatBrowser()
        self.transcript.setObjectName('conversation')
        self.transcript.document().setDefaultStyleSheet(
            'p { margin-top: 8px; margin-bottom: 12px; line-height: 150%; } '
            'pre { background-color: #eef0f2; white-space: pre-wrap; } '
            'td, th { padding: 6px; }')
        layout.addWidget(self.transcript, 1)
        controls = QHBoxLayout()
        self.attachments_button = QPushButton('첨부 자료')
        self.attachments_button.clicked.connect(self.show_attachments)
        controls.addWidget(self.attachments_button)
        self.proposals_button = QPushButton('제안 검토')
        self.proposals_button.clicked.connect(self.show_proposals)
        controls.addWidget(self.proposals_button)
        controls.addWidget(QLabel('다음 역할'))
        self.role = QComboBox()
        self.role.setAccessibleName('이번 질문의 전문가 역할')
        self.role.setToolTip('지금 작성하는 다음 질문에 적용됩니다. 이전 답변의 역할은 바뀌지 않습니다.')
        self.role.setMaximumWidth(190)
        for template in self.workbench.expert_templates():
            self.role.addItem(template.name, template.id)
        controls.addWidget(self.role)
        self.basis = QLabel('이번 질문의 첨부: 선택하지 않음')
        self.basis.setObjectName('basis')
        self.basis.setWordWrap(True)
        layout.addWidget(self.basis)
        self.question = QuestionEdit()
        self.question.setObjectName('composer')
        self.question.setPlaceholderText('질문을 적으세요… Enter 보내기 · Shift+Enter 줄바꿈')
        self.question.setToolTip('Enter 또는 Ctrl+Enter로 보내기 · Shift+Enter로 줄바꿈. 한글 조합 중에는 보내지 않습니다.')
        self.question.submitted.connect(self.ask)
        layout.addWidget(self.question)
        send_line = controls
        self.wait_label = QLabel('')
        self.wait_label.setMaximumWidth(110)
        self.wait_label.setWordWrap(True)
        send_line.addWidget(self.wait_label, 1)
        self.stop = QPushButton('중단')
        self.stop.clicked.connect(self.cancel_request)
        self.stop.setEnabled(False)
        send_line.addWidget(self.stop)
        self.ask_button = QPushButton('보내기')
        self.ask_button.setObjectName('primary')
        self.ask_button.clicked.connect(self.ask)
        send_line.addWidget(self.ask_button)
        layout.addLayout(send_line)
        outer.addWidget(column, 1)
        outer.addStretch()
        self.setCentralWidget(root)

        self.attachments_dialog = QDialog(self)
        self.attachments_dialog.setWindowTitle('대화의 첨부 자료')
        self.attachments_dialog.resize(680, 520)
        self.attachments_dialog.setStyleSheet('QWidget { font-size: 14px; } QPlainTextEdit { padding: 8px; }')
        attachment_layout = QVBoxLayout(self.attachments_dialog)
        attachment_layout.addWidget(QLabel('목록 선택은 미리보기입니다. 이번 질문에 쓸 자료는 아래에서 선택하세요.'))
        self.sources = QListWidget()
        self.sources.setWordWrap(True)
        self.sources.currentItemChanged.connect(self.preview_evidence)
        attachment_layout.addWidget(self.sources, 1)
        self.attachment_preview = QPlainTextEdit()
        self.attachment_preview.setReadOnly(True)
        attachment_layout.addWidget(self.attachment_preview, 1)
        attachment_actions = QHBoxLayout()
        self.use_source = QPushButton('이번 질문에 첨부')
        self.use_source.clicked.connect(self.select_evidence)
        attachment_actions.addWidget(self.use_source)
        self.capture = QPushButton('계산 결과 가져오기…')
        self.capture.setVisible(bool(project_path))
        self.capture.clicked.connect(self.capture_run)
        attachment_actions.addWidget(self.capture)
        self.import_button = QPushButton('파일 첨부…')
        self.import_button.clicked.connect(self.import_files)
        attachment_actions.addWidget(self.import_button)
        attachment_layout.addLayout(attachment_actions)
        self.proposals_dialog = QDialog(self)
        self.proposals_dialog.setWindowTitle('미적용 제안 검토')
        self.proposals_dialog.resize(680, 570)
        self.proposals_dialog.setStyleSheet('QWidget { font-size: 14px; } QPlainTextEdit { padding: 8px; }')
        proposal_layout = QVBoxLayout(self.proposals_dialog)
        self.proposal_choice = QComboBox()
        self.proposal_choice.setAccessibleName('검토할 제안')
        self.proposal_choice.currentIndexChanged.connect(self.preview_proposal)
        proposal_layout.addWidget(self.proposal_choice)
        self.proposals = QPlainTextEdit()
        self.proposals.setReadOnly(True)
        proposal_layout.addWidget(self.proposals, 1)
        proposal_actions = QHBoxLayout()
        self.check_proposal = QPushButton('원본 변경 여부 확인')
        self.check_proposal.clicked.connect(self.check_selected_proposal)
        proposal_actions.addWidget(self.check_proposal)
        self.reject_proposal = QPushButton('제안 거절')
        self.reject_proposal.clicked.connect(self.reject_selected_proposal)
        proposal_actions.addWidget(self.reject_proposal)
        proposal_layout.addLayout(proposal_actions)
        # Configuration remains available; lengthy transport diagnostics are not conversation content.
        self.provider = QLabel(self)
        self.provider.hide()
        self.recheck = QPushButton('설정 다시 확인', self)
        self.recheck.hide()
        self.recheck.clicked.connect(self.refresh_provider)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(50)
        if discussion_path:
            self._load(Path(discussion_path), create=True)
            if run_id:
                self._notice('프로젝트의 계산 결과가 전달되었습니다. 첨부 자료에서 가져올 결과를 확인하세요.')
        else:
            self.render()

    @property
    def assets(self):
        return self.path.parent / 'assets'

    def _notice(self, text):
        self.status.setText(human_text(text))
        self.status.setVisible(bool(text))

    def _error(self, error):
        self._notice(user_error(error))

    def show_attachments(self):
        self.attachments_dialog.show()

    def show_proposals(self):
        self.proposals_dialog.show()

    def _load(self, path, *, create=False):
        self._save_view_state()
        document = self.workbench.open_expert_discussion(path, create=create)
        self.path, self.document = Path(path).resolve(), document
        self._pending_message = None
        self.question.setPlainText(document.get('draft_question', ''))
        self._asset_issues = self.workbench.expert_discussion_issues(self.path, document)
        self.render()
        self._notice('')
        problems = self._asset_issues
        self.asset_warning.setText(('보존된 제안의 내용이나 출처를 확인할 수 없습니다. 대화는 읽을 수 있지만 제안은 다시 확인해 주세요.'
                                   if any('제안' in problem for problem in problems) else
                                   '첨부 자료에 누락·변경 또는 출처 확인 문제가 있습니다. 자료를 다시 확인한 뒤 질문하세요.') if problems else '')
        self.asset_warning.setVisible(bool(problems))
        self._read_view_state()

    def _ensure_document(self):
        if self.path:
            return True
        self.new_document()
        return self.path is not None

    def new_document(self):
        if not self.save_document():
            return
        name, _ = QFileDialog.getSaveFileName(self, '새 대화 파일', 'discussion.json', '대화 (*.json)')
        if not name:
            return
        try:
            path = Path(name)
            if path.exists():
                self._notice('이미 있는 대화는 열기를 사용하세요. 새 대화로 덮어쓰지 않습니다.')
                return
            self._load(path, create=True)
        except Exception as error:
            self._error(error)

    def open_document(self):
        if not self.save_document():
            return
        name, _ = QFileDialog.getOpenFileName(self, '대화 열기', '', '대화 (*.json)')
        if name:
            try:
                self._load(Path(name))
            except Exception as error:
                self._error(error)

    def reload_document(self):
        if self.path:
            # A conflict must not discard the unsaved question draft.
            draft = self.question.toPlainText()
            try:
                self._load(self.path)
                if draft:
                    self.question.setPlainText(draft)
            except Exception as error:
                self._error(error)

    def save_document(self):
        if not self.path:
            return True
        if self._operation:
            return False
        try:
            candidate = copy.deepcopy(self.document)
            candidate['draft_question'] = self.question.toPlainText()
            self.document = self.workbench.save_expert_discussion(
                self.path, candidate, expected_revision=self.document['revision'])
            self._save_view_state()
            self.statusBar().showMessage('대화를 저장했습니다.', 3000)
            return True
        except Exception as error:
            self._error(error)
            return False

    def render(self):
        bar = self.transcript.verticalScrollBar()
        self.transcript.restored_anchor = None
        old_position, follow = bar.value(), bar.maximum() - bar.value() <= 32
        self.transcript.follow_tail = follow
        preview = self.sources.currentItem()
        preview_id = preview.data(Qt.UserRole) if preview else None
        selected = self.document.get('selected_evidence_id')
        if preview_id not in self.document.get('evidences', {}):
            preview_id = selected
        self.sources.blockSignals(True)
        self.sources.clear()
        for evidence_id, evidence in self.document.get('evidences', {}).items():
            label = _evidence_name(evidence)
            cautions = self._cautions(evidence)
            if cautions:
                label += '\n' + ' · '.join(cautions)
            item = QListWidgetItem(label + (' · 이번 질문에 첨부' if evidence_id == selected else ''))
            item.setData(Qt.UserRole, evidence_id)
            self.sources.addItem(item)
            if evidence_id == (preview_id or selected):
                self.sources.setCurrentItem(item)
        self.sources.blockSignals(False)
        self.preview_evidence()
        evidence = self.document.get('evidences', {}).get(selected)
        self.basis.setText('이번 질문의 첨부: ' + (_evidence_name(evidence) if evidence else '선택하지 않음')
                           + ('\n' + ' · '.join(self._cautions(evidence)) if evidence and self._cautions(evidence) else ''))
        self.attachments_button.setText(f"첨부 자료 ({self.sources.count()})")
        fragments = []
        labels = {'completed': '', 'pending': '답변을 기다리고 있습니다…', 'failed': '답변을 받지 못했습니다.',
                  'unavailable': '연결 설정을 확인해 주세요.', 'timed_out': '응답 시간이 초과되었습니다.',
                  'cancelled': '응답 대기를 중단했습니다.', 'interrupted': '이전 질문의 응답이 중단되었습니다.'}
        turns = list(self.document.get('turns', []))
        if self._pending_message:
            turns.append(self._pending_message)
        for turn in turns:
            source = self.document.get('evidences', {}).get(turn.get('evidence_id'), {})
            question = html.escape(human_text(turn.get('question', ''))).replace('\n', '<br>')
            cautions = self._cautions(source)
            source_label = _evidence_name(source) + (' · ' + ' · '.join(cautions) if cautions else '')
            fragments += [f"<p style='color:#526476; margin-top:26px'>나 · {html.escape(source_label)}</p>",
                          f"<table width='100%' bgcolor='#edf2f5' cellpadding='14'><tr><td>{question}</td></tr></table>",
                          f"<p style='color:#24557b; margin-top:22px'><b>{html.escape(human_text(turn.get('role') or '전문가'))}</b></p>"]
            proposals = [p for p in self.document.get('proposals', []) if isinstance(p, dict) and
                         p.get('request_id') == turn.get('id') and turn.get('id')]
            reply, malformed = markdown_body(turn.get('reply') or '', accepted_proposals=proposals)
            fragments.append(reply)
            status = labels.get(turn.get('status'), '응답 상태를 확인할 수 없습니다.')
            if status:
                fragments.append(f"<p style='color:#8a3d11'>{status}</p>")
            if turn.get('error'):
                fragments.append(f"<p>{html.escape(user_error(turn['error']))}</p>")
            proposals = [p for p in self.document.get('proposals', []) if isinstance(p, dict) and
                         p.get('request_id') == turn.get('id') and turn.get('id')]
            if proposals:
                fragments.extend(f"<p style='color:#526476'><b>제안</b><br>{html.escape(proposal_brief(proposal)).replace(chr(10), '<br>')}</p>" for proposal in proposals)
            if malformed:
                fragments.append('<p style="color:#8a3d11">제안 형식을 읽지 못했습니다. 원문은 보존되어 있습니다. 대상과 내용을 다시 질문해 주세요.</p>')
        self.transcript.setHtml(''.join(fragments) or '<h2>어떤 점을 함께 살펴볼까요?</h2><p>입력이나 계산 결과를 첨부하고 질문을 적어 주세요.</p>')
        bar.setValue(bar.maximum() if follow else old_position)
        proposal_id = self.proposal_choice.currentData()
        self.proposal_choice.blockSignals(True)
        self.proposal_choice.clear()
        for index, item in enumerate(self.document.get('proposals', [])):
            if isinstance(item, dict) and isinstance(item.get('id'), str):
                self.proposal_choice.addItem(f"제안 {index + 1} · {source_name(item.get('target_file'))}", item['id'])
        self.proposal_choice.setCurrentIndex(max(0, self.proposal_choice.findData(proposal_id)))
        self.proposal_choice.blockSignals(False)
        self.preview_proposal()
        self.proposals_button.setText(f"제안 검토 ({len(self.document.get('proposals', []))})")
        title = self.document.get('title') or '새 대화'
        if title == '새 대화' and self.document.get('turns'):
            question_title = ' '.join(self.document['turns'][0].get('question', '').split())
            title = question_title if len(question_title) <= 36 else question_title[:36].rsplit(' ', 1)[0] + '…'
        self.heading.setText(human_text(title))
        self.setWindowTitle(human_text(title) + ' — 전문가 논의')
        self.refresh_provider()
        self._set_busy(bool(self._operation))

    def preview_proposal(self, *_):
        item = next((item for item in self.document.get('proposals', [])
                     if isinstance(item, dict) and item.get('id') == self.proposal_choice.currentData()), None)
        self.proposals.setPlainText(proposal_text(item) if item is not None else
                                   '아직 제안이 없습니다. 원본 변경과 계산은 전용 프로그램에서 별도로 실행합니다.')

    def check_selected_proposal(self):
        self.review_proposal('check')

    def reject_selected_proposal(self):
        self.review_proposal('reject')

    def review_proposal(self, decision):
        proposal_id = self.proposal_choice.currentData()
        if self._operation or not proposal_id:
            return
        reason = ''
        if decision == 'reject':
            reason, accepted = QInputDialog.getMultiLineText(self, '제안 거절', '다음 논의에 남길 이유')
            if not accepted:
                return
            if not reason.strip():
                self._notice('제안을 거절하는 이유를 입력하세요.')
                return
        if not self.save_document():
            return
        try:
            self.document = self.workbench.review_expert_proposal(
                self.path, proposal_id, expected_revision=self.document['revision'],
                decision=decision, reason=reason)
            self.render()
            self._notice('제안 검토를 저장했습니다. 원본 파일은 변경하지 않았습니다.')
        except Exception as error:
            self._error(error)

    def preview_evidence(self, *_):
        item = self.sources.currentItem()
        evidence = self.document.get('evidences', {}).get(item.data(Qt.UserRole), {}) if item else {}
        roles = {'input': '원본 입력', 'include': '추가 원본 입력', 'execution_input': '보존 실행 입력',
                 'frd': '결과', 'dat': '수치 출력', 'log': '계산 로그', 'raw': '선택 파일'}
        lines = [_evidence_name(evidence)] if evidence else ['첨부 자료를 선택하세요.']
        for file in evidence.get('files', []):
            if file.get('role') in roles:
                lines.append(f"{roles[file['role']]}: {source_name(file.get('source_path'))}")
        units = evidence.get('units') or {}
        unit_names = {'length': '길이', 'force': '힘', 'time': '시간', 'stress': '응력', 'temperature': '온도'}
        if evidence:
            lines += ['', '첨부할 때 보존한 사본을 읽습니다. 원본을 다시 읽어 바꾸지 않습니다.', '단위: ' + (', '.join(f"{label} {units.get(key, '미확인')}" for key, label in unit_names.items()) if units else '미확인'),
                      '단위 출처: 사용자 선언' if evidence.get('unit_source') == 'user' else '단위 출처: 미확인',
                      '사용자 선택 자료 · 동일 계산의 파일인지 미확인' if evidence.get('source_kind') == 'manual' else '보존된 계산 자료',
                      '선택한 보존 결과: ' + str(evidence['frame_index'] + 1) + '번째 프레임' if isinstance(evidence.get('frame_index'), int) else '판독한 결과 단계 없음']
        lines += self._cautions(evidence) if evidence else []
        self.attachment_preview.setPlainText('\n'.join(lines))

    def _cautions(self, evidence):
        related = [issue for issue in self._asset_issues if evidence.get('id') and evidence['id'] in issue]
        return evidence_cautions(evidence, related)

    def select_evidence(self):
        item = self.sources.currentItem()
        if not item or self._operation:
            return
        previous = self.document.get('selected_evidence_id')
        self.document['selected_evidence_id'] = item.data(Qt.UserRole)
        if not self.save_document():
            self.document['selected_evidence_id'] = previous
        self.render()

    def _set_busy(self, busy):
        live = bool((self.document.get('pending_request') or {}).get('live'))
        for action in self._edit_actions:
            action.setEnabled(not busy)
        for widget in (self.use_source, self.capture, self.import_button, self.role, self.settings, self.recheck):
            widget.setEnabled(not busy and not live)
        for widget in (self.check_proposal, self.reject_proposal):
            widget.setEnabled(not busy and not live and bool(self.proposal_choice.currentData()))
        self.question.setReadOnly(busy or live)
        self.ask_button.setEnabled(not busy and not live and bool(self.document.get('selected_evidence_id'))
                                   and getattr(self, '_provider_available', False))
        self.stop.setEnabled(self._operation == 'ask' and not self._cancel.is_set())

    def refresh_provider(self):
        status = self.workbench.expert_provider_status(self.document.get('provider_id'))
        self._provider_available = status['available']
        self.provider.setText('공급자 설정 확인됨' if status['available'] else '공급자 설정이 필요합니다.')
        self.settings.setText('연결 설정' if status['available'] else '연결 설정 필요')
        self.provider.setToolTip('설정 확인이며 실제 호출 성공을 뜻하지 않습니다.')
        self._set_busy(bool(self._operation))

    def configure_provider(self):
        if not self._ensure_document():
            return
        configuration = self.workbench.expert_provider_configuration()
        dialog = QDialog(self)
        dialog.setWindowTitle('전문가 공급자 설정')
        form = QFormLayout(dialog)
        choice = QComboBox()
        for label, value in [('선택하지 않음', 'none'), ('OpenAI 호환 엔드포인트', 'openai_compatible'),
                             ('OpenScience 구독', 'openscience')]:
            choice.addItem(label, value)
        choice.setCurrentIndex(max(0, choice.findData(self.document.get('provider_id') or configuration['provider_id'])))
        form.addRow('공급자', choice)
        fields = {}
        for key, label in [('endpoint', '엔드포인트'), ('model', '모델'), ('api_key', 'API 키')]:
            field = QLineEdit(configuration[key])
            if key == 'api_key':
                field.setEchoMode(QLineEdit.Password)
            form.addRow(label, field)
            fields[key] = field
        hint = QLabel('주소·모델·키는 이 창의 실행 동안만 유지합니다. 공급자 선택은 대화에 저장합니다.\n'
                      'OpenScience는 기존 Windows 로그인·설치 설정을 사용하며, 모델 공란은 기존 선택을 유지합니다.\n'
                      '설정 저장만으로 모델을 호출하지 않습니다.')
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        self.workbench.configure_expert_provider(**{key: field.text().strip() for key, field in fields.items()})
        self.document['provider_id'] = choice.currentData()
        self.save_document()
        self.refresh_provider()

    def edit_context(self):
        if not self._ensure_document():
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('제약과 후보별 실패 기록')
        dialog.resize(620, 460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel('한 줄에 한 항목을 적습니다. 저장된 이전 기록은 새 근거를 붙여도 유지됩니다.'))
        constraints = QPlainTextEdit('\n'.join(str(x) for x in self.document.get('constraints', [])))
        layout.addWidget(QLabel('사용자 제약'))
        layout.addWidget(constraints)
        failures = QPlainTextEdit()
        failures.setPlaceholderText('이번에 추가할 후보별 실패 이유')
        layout.addWidget(QLabel('실패 이유 추가'))
        layout.addWidget(failures)
        history = QPlainTextEdit('\n'.join(
            user_error(item.get('reason', '')) if isinstance(item, dict) and 'request_id' in item
            else str(item.get('reason', item)) if isinstance(item, dict) else str(item)
            for item in self.document.get('failures', [])))
        history.setReadOnly(True)
        history.setMaximumHeight(90)
        layout.addWidget(QLabel('이전 실패 기록'))
        layout.addWidget(history)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.Accepted:
            previous = copy.deepcopy(self.document)
            self.document['constraints'] = [line for line in constraints.toPlainText().splitlines() if line.strip()]
            self.document['failures'].extend(line for line in failures.toPlainText().splitlines() if line.strip())
            if not self.save_document():
                self.document = previous

    def import_files(self):
        if not self._ensure_document() or not self.save_document():
            return
        names, _ = QFileDialog.getOpenFileNames(self, '입력, 결과, 로그 선택', '',
                                               '해석 자료 (*.inp *.frd *.dat *.log *.txt);;모든 파일 (*)')
        if not names:
            return
        files = {}
        for name in names:
            role = {'.inp': 'input', '.frd': 'frd', '.dat': 'dat', '.log': 'log', '.txt': 'log'}.get(Path(name).suffix.lower(), 'raw')
            if role in files:
                self._notice('같은 종류의 파일은 한 번에 하나씩 불러오세요. 서로 다른 실행은 별도 근거로 보존합니다.')
                return
            files[role] = Path(name)
        dialog = QDialog(self)
        dialog.setWindowTitle('자료의 솔버와 단위')
        form = QFormLayout(dialog)
        solver = QComboBox()
        for label, value in [('CalculiX', 'calculix'), ('Code_Aster', 'code_aster'),
                             ('LS-DYNA', 'lsdyna'), ('OpenRadioss', 'radioss'), ('미확인', 'unknown')]:
            solver.addItem(label, value)
        form.addRow('솔버', solver)
        unit_fields = {}
        for key, label in [('length', '길이'), ('force', '힘'), ('time', '시간'), ('stress', '응력')]:
            field = QLineEdit()
            field.setPlaceholderText('모르면 비워 두세요')
            form.addRow(label, field)
            unit_fields[key] = field
        hint = QLabel('사용자가 선택한 파일 묶음입니다. 동일 실행의 파일인지 자동 판정하지 않습니다.')
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            solver_id = solver.currentData()
            units = {key: field.text().strip() for key, field in unit_fields.items() if field.text().strip()}
            assets = self.assets
            self._start('attach', lambda: self.workbench.import_expert_files(
                assets, files=files, solver_id=solver_id, units=units))

    def capture_run(self, _checked=False):
        if not self._ensure_document() or not self.save_document():
            return
        try:
            project = self.workbench.open_native_project(self.project_path)
            current_run = self.workbench.expert_evidence_run(project, self.node_id)
            pinned_run = None
            if self.run_id:
                try:
                    pinned_run = self.workbench.expert_evidence_run(project, self.node_id, self.run_id)
                except ValueError:
                    pass  # Show the missing pinned choice; never substitute a new run silently.
            dialog = QDialog(self)
            dialog.setWindowTitle('가져올 실행과 결과 프레임')
            layout = QFormLayout(dialog)
            runs, frames = QComboBox(), QComboBox()
            runs.setObjectName('capture_run_choice')
            frames.setObjectName('capture_frame_choice')
            if self.run_id:
                runs.addItem('전달받은 계산 · ' + (source_name(pinned_run.input_path) + ' · ' + pinned_run.created_at[:16] if pinned_run else '기록 없음'), pinned_run)
            if current_run and (pinned_run is None or current_run.id != pinned_run.id):
                runs.addItem('현재 선택 · ' + source_name(current_run.input_path) + ' · ' + current_run.created_at[:16], current_run)
            if not runs.count():
                self._notice('연결된 선택 실행이 없습니다. 해석 창에서 실행을 선택하거나 자료를 직접 불러오세요.')
                return
            layout.addRow('실행', runs)
            layout.addRow('프레임', frames)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addRow(buttons)

            def update_frames():
                run = runs.currentData()
                frames.clear()
                for frame in ((run.result or {}).get('frames', []) if run else []):
                    frames.addItem(f"단계 {frame.get('step')} / 증분 {frame.get('increment')} / 값 {frame.get('value')}", frame['index'])
                if not frames.count():
                    frames.addItem('판독한 결과 프레임 없음 — 입력과 로그 가져오기', None)
                elif run:
                    frames.setCurrentIndex(max(0, frames.findData((run.result or {}).get('summary', {}).get('frame_index'))))
                buttons.button(QDialogButtonBox.Ok).setEnabled(run is not None)

            runs.currentIndexChanged.connect(update_frames)
            update_frames()
            if dialog.exec() != QDialog.Accepted:
                return
            run, frame_index = runs.currentData(), frames.currentData()
            if run is None:
                return
            assets = self.assets
            self._start('attach', lambda: self.workbench.capture_expert_run(run, assets, frame_index=frame_index))
        except Exception as error:
            self._error(error)

    def ask(self):
        question = self.question.toPlainText().strip()
        if self._operation or not question or not self.document.get('selected_evidence_id') or not getattr(self, '_provider_available', False):
            return
        if not self.save_document():
            return
        path, revision = self.path, self.document['revision']
        evidence_id = self.document['selected_evidence_id']
        role, provider = self.role.currentData(), self.document.get('provider_id')
        def cancelled():
            if self._cancel.is_set():
                raise RuntimeError('사용자가 응답 대기를 중단했습니다.')
        self._pending_message = dict(question=question, role=self.role.currentText(), evidence_id=evidence_id, status='pending')
        self._start('ask', lambda: self.workbench.ask_expert_discussion(
            path, question, evidence_id=evidence_id, template_id=role, provider_id=provider,
            expected_revision=revision, check_cancelled=cancelled))

    def _start(self, operation, function):
        if self._operation:
            return
        self._operation = operation
        self._cancel.clear()
        self._set_busy(True)
        self.wait_label.setText('답변을 기다리는 중' if operation == 'ask' else '첨부 자료를 보존하는 중')
        self.render()
        if operation == 'ask':
            self.transcript.follow_tail = True
            self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum())
        def work():
            try:
                self._mailbox.put((True, function()))
            except Exception as error:
                self._mailbox.put((False, error))
        threading.Thread(target=work, daemon=True).start()

    def cancel_request(self):
        self._cancel.set()
        self.stop.setEnabled(False)
        self.wait_label.setText('중단 요청 중…')
        if self._pending_message:
            self._pending_message['status'] = 'cancelled'
        self.render()

    def _poll(self):
        if not self._operation:
            return
        try:
            ok, value = self._mailbox.get_nowait()
        except queue.Empty:
            return
        operation, self._operation = self._operation, None
        try:
            if not ok:
                raise value
            if operation == 'attach':
                self.document = self.workbench.attach_expert_evidence(
                    self.path, value, expected_revision=self.document['revision'])
                self._asset_issues = self.workbench.expert_discussion_issues(self.path, self.document)
                self._notice('자료를 첨부했습니다.')
            else:
                self.document = value
                self._pending_message = None
                self.question.setPlainText(value.get('draft_question', ''))
            self.render()
        except Exception as error:
            self._error(error)
            if self._pending_message:
                self._pending_message.update(status='failed', error=str(error))
            self.render()
            self._set_busy(False)
        self.wait_label.setText('')
        if self._close_after:
            self._close_after = False
            self.close()

    def _view_key(self):
        path = os.path.normcase(str(self.path.resolve()))
        return 'documents/' + hashlib.sha256(path.encode('utf-8')).hexdigest()

    def _save_view_state(self):
        if self.path is None or not self.isVisible():
            return
        try:
            cursor = self.transcript.cursorForPosition(QPoint(0, 0))
            bar = self.transcript.verticalScrollBar()
            key = self._view_key()
            self.view_settings.setValue(key + '/anchor', cursor.position())
            self.view_settings.setValue(key + '/offset', self.transcript.cursorRect(cursor).top())
            self.view_settings.setValue(key + '/follow', bar.maximum() - bar.value() <= 32)
            self.view_settings.sync()
        except (OSError, ValueError, TypeError):
            pass  # View preferences must never prevent saving the conversation.

    def _read_view_state(self):
        self._pending_view = (0, 0, True)
        try:
            key = self._view_key()
            if self.view_settings.contains(key + '/anchor'):
                self._pending_view = (self.view_settings.value(key + '/anchor', 0, type=int),
                                      self.view_settings.value(key + '/offset', 0, type=int),
                                      self.view_settings.value(key + '/follow', False, type=bool))
                self.transcript.follow_tail = self._pending_view[2]
                if self.isVisible():
                    QTimer.singleShot(0, self._restore_view_state)
        except (OSError, ValueError, TypeError):
            pass

    def _restore_view_state(self):
        if self._pending_view is not None:
            self.transcript.restore_anchor(*self._pending_view)
            self._pending_view = None

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._restore_view_state)

    def closeEvent(self, event):  # noqa: N802
        if self._operation:
            self._close_after = True
            self.cancel_request()
            event.ignore()
            return
        live_elsewhere = bool((self.document.get('pending_request') or {}).get('live'))
        if not live_elsewhere and not self.save_document():
            answer = QMessageBox.question(self, '대화를 저장하지 못했습니다',
                '다른 창의 변경 또는 파일 오류로 저장하지 못했습니다. 이 창의 저장되지 않은 변경을 버리고 닫을까요?',
                QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if answer != QMessageBox.Discard:
                event.ignore()
                return
        self.timer.stop()
        self.workbench.close()
        event.accept()


def main(argv=None):
    parser = argparse.ArgumentParser(description='파일을 근거로 토론하는 독립 전문가 프로그램')
    parser.add_argument('--discussion')
    parser.add_argument('--project')
    parser.add_argument('--node')
    parser.add_argument('--run-id')
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setStyle('Fusion')
    try:
        window = ExpertWindow(args.discussion, project_path=args.project, node_id=args.node, run_id=args.run_id)
    except Exception as error:
        traceback.print_exc()
        QMessageBox.critical(None, '전문가 프로그램을 열지 못했습니다',
                             user_error(error) + '\n대화 파일을 확인하거나 보존된 다른 대화 파일로 프로그램을 다시 시작해 주세요.')
        return 1
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
