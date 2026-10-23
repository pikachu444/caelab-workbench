"""Independent, file-based expert discussion. Opening never calls a model."""
from __future__ import annotations

import argparse
import copy
import html
import json
import queue
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QSplitter,
    QTabWidget, QTextBrowser, QToolBar, QVBoxLayout, QWidget,
)

from caelab.workbench import Workbench
from .theme import THEME


def _text(value) -> str:
    return html.escape(str(value))


def _evidence_name(evidence) -> str:
    names = [Path(item['source_path']).name for item in evidence.get('files', [])
             if item['role'] in {'input', 'frd', 'dat'}]
    name = names[0] if names else evidence.get('solver_id', '자료')
    run = evidence.get('run_id')
    return f"{name} / 실행 {run[:8]}" if run else f"{name} / 직접 불러온 자료 {evidence.get('id', '')[:6]}"


class ExpertWindow(QMainWindow):
    def __init__(self, discussion_path=None, *, project_path=None, node_id=None,
                 run_id=None, workbench=None):
        super().__init__()
        self.workbench = workbench or Workbench()
        self.path = None
        self.document = {}
        self.project_path, self.node_id, self.run_id = project_path, node_id, run_id
        self._operation = None
        self._mailbox = queue.Queue()
        self._cancel = threading.Event()
        self._close_after = False
        self.setWindowTitle('전문가 논의')
        self.resize(1100, 780)
        self.setMinimumSize(760, 580)
        self.setStyleSheet(THEME + """
            QLabel#heading { font-size: 16px; font-weight: bold; color: #20374d; }
            QLabel#basis { background: #e3eef8; padding: 8px; color: #173b5e; }
            QPlainTextEdit, QTextBrowser, QListWidget { background: white; border: 1px solid #cfd6dd; }
            QPushButton#primary:disabled { background: #dbe3eb; color: #647383; border-color: #c4cdd6; }
            QPushButton:focus, QComboBox:focus { border: 2px solid #1668b4; }
        """)
        toolbar = QToolBar('대화 파일', self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        self._edit_actions = []
        for label, slot, shortcut in (
            ('새 대화', self.new_document, QKeySequence.New),
            ('열기', self.open_document, QKeySequence.Open),
            ('저장', self.save_document, QKeySequence.Save),
            ('다시 읽기', self.reload_document, QKeySequence.Refresh),
            ('자료 불러오기', self.import_files, None),
            ('제약과 실패 기록', self.edit_context, None),
        ):
            action = toolbar.addAction(label)
            action.triggered.connect(slot)
            if shortcut:
                action.setShortcut(shortcut)
            self._edit_actions.append(action)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 10)
        self.heading = QLabel('결과를 근거로 이어가는 대화')
        self.heading.setObjectName('heading')
        layout.addWidget(self.heading)
        self.status = QLabel('자료를 불러온 뒤 질문의 근거를 선택하세요.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.asset_warning = QLabel()
        self.asset_warning.setWordWrap(True)
        self.asset_warning.setStyleSheet('color: #8a3d11;')
        self.asset_warning.hide()
        layout.addWidget(self.asset_warning)
        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel('보존된 근거'))
        self.sources = QListWidget()
        self.sources.setMinimumHeight(90)
        self.sources.setWordWrap(True)
        self.sources.currentItemChanged.connect(self.preview_evidence)
        left_layout.addWidget(self.sources, 2)
        self.use_source = QPushButton('질문의 근거로 사용')
        self.use_source.clicked.connect(self.select_evidence)
        left_layout.addWidget(self.use_source)
        self.capture = QPushButton('실행 가져오기…')
        self.capture.setVisible(bool(project_path))
        self.capture.clicked.connect(self.capture_run)
        left_layout.addWidget(self.capture)
        detail_tabs = QTabWidget()
        self.details = {}
        for key, label in (('summary_text', '요약'), ('model_text', '입력'),
                           ('log_text', '로그'), ('provenance', '출처')):
            view = QPlainTextEdit()
            view.setReadOnly(True)
            detail_tabs.addTab(view, label)
            self.details[key] = view
        left_layout.addWidget(detail_tabs, 3)
        split.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(8, 0, 0, 0)
        tabs = QTabWidget()
        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        tabs.addTab(self.transcript, '대화')
        self.proposals = QPlainTextEdit()
        self.proposals.setReadOnly(True)
        proposal_tab = QWidget()
        proposal_layout = QVBoxLayout(proposal_tab)
        proposal_layout.setContentsMargins(0, 0, 0, 0)
        self.proposal_choice = QComboBox()
        self.proposal_choice.setAccessibleName('검토할 제안')
        self.proposal_choice.currentIndexChanged.connect(self.preview_proposal)
        proposal_layout.addWidget(self.proposal_choice)
        proposal_layout.addWidget(self.proposals, 1)
        proposal_actions = QHBoxLayout()
        self.check_proposal = QPushButton('원본 개정 확인')
        self.check_proposal.clicked.connect(lambda: self.review_proposal('check'))
        proposal_actions.addWidget(self.check_proposal)
        self.reject_proposal = QPushButton('제안 거절')
        self.reject_proposal.clicked.connect(lambda: self.review_proposal('reject'))
        proposal_actions.addWidget(self.reject_proposal)
        proposal_actions.addStretch()
        proposal_layout.addLayout(proposal_actions)
        tabs.addTab(proposal_tab, '변경 제안')
        self.request_details = QPlainTextEdit()
        self.request_details.setReadOnly(True)
        tabs.addTab(self.request_details, '요청 근거')
        self.asset_details = QPlainTextEdit()
        self.asset_details.setReadOnly(True)
        tabs.addTab(self.asset_details, '파일 확인')
        right_layout.addWidget(tabs, 1)
        self.basis = QLabel('이번 질문의 근거: 선택하지 않음')
        self.basis.setObjectName('basis')
        self.basis.setWordWrap(True)
        right_layout.addWidget(self.basis)
        role_line = QHBoxLayout()
        role_line.addWidget(QLabel('역할'))
        self.role = QComboBox()
        for template in self.workbench.expert_templates():
            self.role.addItem(template.name, template.id)
            self.role.setItemData(self.role.count() - 1,
                                  '읽는 자료: ' + ', '.join(template.allowed_tools), Qt.ToolTipRole)
        role_line.addWidget(self.role, 1)
        self.settings = QPushButton('공급자 설정')
        self.settings.clicked.connect(self.configure_provider)
        role_line.addWidget(self.settings)
        self.recheck = QPushButton('다시 확인')
        self.recheck.clicked.connect(self.refresh_provider)
        role_line.addWidget(self.recheck)
        right_layout.addLayout(role_line)
        self.provider = QLabel()
        self.provider.setWordWrap(True)
        right_layout.addWidget(self.provider)
        self.question = QPlainTextEdit()
        self.question.setPlaceholderText('확인할 점이나 이전 답변에 대한 반론을 적으세요. Ctrl+Enter로 질문합니다.')
        self.question.setMaximumHeight(110)
        self.question.setMinimumHeight(65)
        right_layout.addWidget(self.question)
        send_line = QHBoxLayout()
        self.wait_label = QLabel('질문을 보내기 전에는 모델을 호출하지 않습니다.')
        self.wait_label.setWordWrap(True)
        send_line.addWidget(self.wait_label, 1)
        self.stop = QPushButton('대기 중단')
        self.stop.clicked.connect(self.cancel_request)
        self.stop.setEnabled(False)
        send_line.addWidget(self.stop)
        self.ask_button = QPushButton('질문 보내기')
        self.ask_button.setObjectName('primary')
        self.ask_button.setShortcut('Ctrl+Return')
        self.ask_button.clicked.connect(self.ask)
        send_line.addWidget(self.ask_button)
        right_layout.addLayout(send_line)
        split.addWidget(right)
        split.setSizes([330, 740])
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)
        self.setCentralWidget(root)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll)
        self.timer.start(50)
        if discussion_path:
            self._load(Path(discussion_path), create=True)
            if run_id:
                self.status.setText(f'실행 {run_id[:8]}을 전달받았습니다. 실행 가져오기에서 근거를 보존하세요.')
        else:
            self.render()

    @property
    def assets(self):
        return self.path.parent / 'assets'

    def _error(self, error):
        self.status.setText(str(error))
        self.statusBar().showMessage(str(error))

    def _load(self, path, *, create=False):
        document = self.workbench.open_expert_discussion(path, create=create)
        self.path, self.document = Path(path).resolve(), document
        self.question.setPlainText(document.get('draft_question', ''))
        self.render()
        self.status.setText(f'대화 파일: {self.path}')
        problems = self.workbench.expert_discussion_issues(self.path, document)
        self.asset_warning.setText(f'자료 확인 안내 {len(problems)}개 — 원본 변경·누락 등의 상세 내용은 파일 확인 탭을 보세요.')
        self.asset_warning.setToolTip('\n'.join(problems))
        self.asset_warning.setVisible(bool(problems))
        self.asset_details.setPlainText('\n'.join(problems) or '불러온 대화의 보존 파일에서 누락이나 해시 불일치를 찾지 못했습니다.')

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
                raise ValueError('이미 있는 대화는 열기를 사용하세요. 새 대화로 덮어쓰지 않습니다.')
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
            self.statusBar().showMessage(f'저장됨: {self.path}', 4000)
            return True
        except Exception as error:
            self._error(error)
            return False

    def render(self):
        preview = self.sources.currentItem()
        preview_id = preview.data(Qt.UserRole) if preview else None
        self.sources.blockSignals(True)
        self.sources.clear()
        selected = self.document.get('selected_evidence_id')
        for evidence_id, evidence in self.document.get('evidences', {}).items():
            frame = evidence.get('frame_index')
            label = _evidence_name(evidence).replace(' / ', '\n', 1)
            item = QListWidgetItem(label + (f' / 프레임 {frame}' if frame is not None else ''))
            item.setData(Qt.UserRole, evidence_id)
            item.setToolTip(f"근거 {evidence_id}\n프레임 {evidence.get('frame_index')}\n{evidence.get('created_at')}")
            self.sources.addItem(item)
            if evidence_id == (preview_id or selected):
                self.sources.setCurrentItem(item)
        self.sources.blockSignals(False)
        self.preview_evidence()
        evidence = self.document.get('evidences', {}).get(selected)
        self.basis.setText('이번 질문의 근거: ' + (
            f"{_evidence_name(evidence)} / 프레임 {evidence.get('frame_index', '없음')}"
            if evidence else '선택하지 않음'))
        fragments = []
        labels = {'completed': '응답 완료', 'pending': '응답 대기', 'failed': '실패',
                  'unavailable': '공급자 사용 불가', 'timed_out': '시간 초과',
                  'cancelled': '대기 중단', 'interrupted': '이전 요청 중단'}
        for turn in self.document.get('turns', []):
            source = self.document.get('evidences', {}).get(turn.get('evidence_id'), {})
            meta = (f"{turn.get('role', '')} / {turn.get('provider_name', '')} / "
                    f"{_evidence_name(source)} / 프레임 {turn.get('frame_index')}")
            fragments.append(f"<p style='color:#526476'>{_text(meta)}</p>"
                             f"<p><b>질문</b><br>{_text(turn.get('question', '')).replace(chr(10), '<br>')}</p>"
                             f"<p><b>{_text(labels.get(turn.get('status'), turn.get('status', '')))}</b><br>"
                             f"{_text(turn.get('reply') or turn.get('error') or '').replace(chr(10), '<br>')}</p>")
            citations = turn.get('citations', {})
            if citations:
                shown, verified, unverified = (citations.get(key, []) for key in ('shown_ids', 'verified', 'unverified'))
                citation_text = (f'제공 자료 {len(shown)}개 / 일치한 인용 {len(verified)}개 / '
                                 f'확인되지 않은 인용 {len(unverified)}개')
                if unverified:
                    citation_text += ': ' + ', '.join(str(item) for item in unverified)
                fragments.append(f"<p style='color:#526476'>{_text(citation_text)}<br>인용 일치는 답변의 공학적 타당성을 보증하지 않습니다.</p>")
            fragments.append('<hr>')
        self.transcript.setHtml(''.join(fragments) or
            '<h3>근거를 확인하고 질문하세요</h3><p>입력 조건, 계산 로그, 결과를 불러오면 여기에 대화가 쌓입니다.</p>'
            '<p>이전 대화를 열어도 모델을 자동 호출하지 않습니다.</p>')
        proposal_id = self.proposal_choice.currentData()
        self.proposal_choice.blockSignals(True)
        self.proposal_choice.clear()
        for item in self.document.get('proposals', []):
            if not isinstance(item, dict) or not isinstance(item.get('id'), str):
                continue
            self.proposal_choice.addItem(f"{Path(str(item.get('target_file', ''))).name} / {item.get('location')}", item.get('id'))
        self.proposal_choice.setCurrentIndex(max(0, self.proposal_choice.findData(proposal_id)))
        self.proposal_choice.blockSignals(False)
        self.preview_proposal()
        requests = [{key: turn.get(key) for key in ('id', 'question', 'template_id', 'provider_id',
                     'evidence_id', 'run_id', 'input_revision', 'frame_index', 'evidence_sources',
                     'provided_materials', 'knowledge_status', 'knowledge_error', 'citations')}
                    for turn in self.document.get('turns', [])]
        self.request_details.setPlainText(json.dumps(requests, ensure_ascii=False, indent=2) if requests else
                                          '질문마다 실제 제공한 자료와 검색 조각, 인용 식별자 검사 결과를 기록합니다.')
        self.heading.setText(self.document.get('title', '결과를 근거로 이어가는 대화'))
        self.refresh_provider()
        self._set_busy(bool(self._operation))

    def preview_proposal(self, *_):
        item = next((item for item in self.document.get('proposals', [])
                     if isinstance(item, dict) and item.get('id') == self.proposal_choice.currentData()), None)
        if item is None:
            self.proposals.setPlainText('아직 변경 제안이 없습니다. 제안은 전용 프로그램에서 검토 후 수정합니다.')
            return
        labels = {'unapplied': '미적용', 'manual_required': '전용 프로그램에서 수정 필요 · 미적용',
                  'blocked': '검토 중단 · 미적용', 'rejected': '거절 · 미적용'}
        reviews = [review for review in (item.get('review_history') if isinstance(item.get('review_history'), list) else [])
                   if isinstance(review, dict)]
        latest_reason = item.get('review_reason') or (reviews[-1].get('reason', '') if reviews else '')
        self.proposals.setPlainText(
            f"{labels.get(str(item.get('status')), '미확인 · 미적용')}\n\n"
            + (f"검토: {latest_reason}\n\n" if latest_reason else '') +
            f"대상: {item.get('target_file')}\n위치: {item.get('location')}\n"
            f"변경 전: {item.get('before')}\n변경 후: {item.get('after')}\n"
            f"이유: {item.get('reason')}\n\n"
            f"실행: {item.get('run_id') or '직접 불러온 자료'}\n"
            f"근거: {item.get('evidence_id')}\n입력 개정: {item.get('input_revision')}\n"
            f"대상 파일 SHA256: {item.get('target_sha256') or '원본 개정 확인 시 대조'}\n\n"
            '이 변경은 자동 적용을 지원하지 않습니다. 원본 개정을 확인하고 전용 프로그램에서 수정하세요. '
            '재계산은 해석 창에서 별도로 실행한 뒤 새 결과를 이 대화로 가져오세요.\n\n'
            + '\n'.join(f"{review.get('reviewed_at', '')}: {review.get('reason', '')}" for review in reviews[:-1]))

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
                self._error('제안을 거절하는 이유를 입력하세요.')
                return
        if not self.save_document():
            return
        try:
            self.document = self.workbench.review_expert_proposal(
                self.path, proposal_id, expected_revision=self.document['revision'],
                decision=decision, reason=reason)
            self.render()
            self.status.setText('제안 검토를 저장했습니다. 원본 파일은 변경하지 않았습니다.')
        except Exception as error:
            self._error(error)

    def preview_evidence(self, *_):
        item = self.sources.currentItem()
        evidence = self.document.get('evidences', {}).get(item.data(Qt.UserRole), {}) if item else {}
        for key in ('summary_text', 'model_text', 'log_text'):
            self.details[key].setPlainText(evidence.get(key, ''))
        provenance = {key: value for key, value in evidence.items()
                      if key not in {'summary_text', 'model_text', 'log_text'}}
        self.details['provenance'].setPlainText(json.dumps(provenance, ensure_ascii=False, indent=2) if provenance else '')

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
        for widget in (self.use_source, self.capture, self.role, self.settings, self.recheck):
            widget.setEnabled(not busy and not live)
        for widget in (self.check_proposal, self.reject_proposal):
            widget.setEnabled(not busy and not live and bool(self.proposal_choice.currentData()))
        self.question.setReadOnly(busy or live)
        self.ask_button.setEnabled(not busy and not live and bool(self.document.get('selected_evidence_id'))
                                   and getattr(self, '_provider_available', False))
        self.stop.setEnabled(self._operation == 'ask')

    def refresh_provider(self):
        status = self.workbench.expert_provider_status(self.document.get('provider_id'))
        self._provider_available = status['available']
        self.provider.setText(f"{status['name']}: {status['reason']}")
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
                             ('OpenScience 구독 (연결 미구현)', 'openscience')]:
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
        hint = QLabel('주소·모델·키는 이 창의 실행 동안만 유지합니다. 공급자 선택은 대화에 저장합니다.\n설정 저장만으로 모델을 호출하지 않습니다.')
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
            str(item.get('reason', item)) if isinstance(item, dict) else str(item)
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
                self._error('같은 종류의 파일은 한 번에 하나씩 불러오세요. 서로 다른 실행은 별도 근거로 보존합니다.')
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
                runs.addItem(f'전달받은 실행 {self.run_id[:8]}' + (' (기록 없음)' if pinned_run is None else ''), pinned_run)
            if current_run and (pinned_run is None or current_run.id != pinned_run.id):
                runs.addItem(f'프로젝트에 저장된 선택 실행 {current_run.id[:8]}', current_run)
            if not runs.count():
                raise ValueError('연결된 선택 실행이 없습니다. 해석 창에서 실행을 선택하거나 자료를 직접 불러오세요.')
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
        if self._operation or not question or not self.document.get('selected_evidence_id'):
            return
        if not self.save_document():
            return
        path, revision = self.path, self.document['revision']
        evidence_id = self.document['selected_evidence_id']
        role, provider = self.role.currentData(), self.document.get('provider_id')
        def cancelled():
            if self._cancel.is_set():
                raise RuntimeError('사용자가 응답 대기를 중단했습니다.')
        self._start('ask', lambda: self.workbench.ask_expert_discussion(
            path, question, evidence_id=evidence_id, template_id=role, provider_id=provider,
            expected_revision=revision, check_cancelled=cancelled))

    def _start(self, operation, function):
        if self._operation:
            return
        self._operation = operation
        self._cancel.clear()
        self._set_busy(True)
        self.wait_label.setText('응답 대기 중 — 최대 60초' if operation == 'ask' else '자료를 읽고 보존하는 중')
        def work():
            try:
                self._mailbox.put((True, function()))
            except Exception as error:
                self._mailbox.put((False, error))
        threading.Thread(target=work, daemon=True).start()

    def cancel_request(self):
        self._cancel.set()
        self.stop.setEnabled(False)
        self.wait_label.setText('응답 대기를 중단하고 기록을 정리하는 중')

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
                self.status.setText('자료를 보존했습니다. 이번 질문의 근거가 이 자료로 설정되었습니다.')
            else:
                self.document = value
                self.question.setPlainText(value.get('draft_question', ''))
            self.render()
        except Exception as error:
            self._error(error)
            self._set_busy(False)
        self.wait_label.setText('질문을 보내기 전에는 모델을 호출하지 않습니다.')
        if self._close_after:
            self._close_after = False
            self.close()

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
        QMessageBox.critical(None, '전문가 프로그램을 열지 못했습니다', str(error))
        return 1
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
