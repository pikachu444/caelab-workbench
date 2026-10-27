"""Independent four-area Studio. Native state and execution belong to Workbench."""
from __future__ import annotations
import json
from copy import deepcopy
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt,QTimer,QRunnable,QThreadPool,QItemSelectionModel,QSettings,QByteArray
from PySide6.QtGui import QAction,QFont
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QSplitter,QTableView,QTabWidget,
    QToolBar,QLabel,QDockWidget,QStatusBar,QMessageBox,QFileDialog,QProgressBar,QInputDialog,QDialog,
    QLineEdit,QComboBox,QPushButton,QDialogButtonBox,QPlainTextEdit,QFormLayout,QSpinBox)
from caelab.workbench import Workbench
from caelab.mdo.study_manager import StudyManager
from caelab.mdo.surrogate import SurrogateConfig
from caelab.mdo.optimize import OptimizationConfig
from caelab.mdo.calculix_evaluator import extract_calculix_responses
from apps.workbench_qt.theme import build_stylesheet,toggle_theme_mode,theme_toggle_label,current_theme_mode
from .bridge import MDOExecutionBridge
from .design_points_model import DesignPointsTableModel,DesignPointsProxyModel
from .study_tree import StudyTreeWidget
from .setup_dock import SetupDockWidget
from .study_config import StudyConfigEditor,StudyConfigDialog,ParameterizeDialog
from .views.pareto_view import ParetoFrontWidget
from .views.parallel_view import ParallelCoordinatesWidget
from .views.response_surface_view import ResponseSurfaceWidget
from .views.sensitivity_view import SensitivityTornadoWidget,TornadoItem
from .views.convergence_view import ConvergenceWidget


class StudioTask(QRunnable):
    def __init__(self,generation,name,call,bridge):
        super().__init__(); self.generation,self.name,self.call,self.bridge=generation,name,call,bridge
    def run(self):
        try: result,error=self.call(),None
        except Exception as exc: result,error=None,str(exc)
        try: self.bridge.taskFinished.emit(self.generation,self.name,result,error)
        except RuntimeError: pass  # A closed view no longer receives numerical preview results.


def startup_service(workbench,study=None,*,study_path=None,project=None,node_id=None,read_only=False):
    if study_path is not None:
        return workbench.open_mdo_study(study_path,read_only=read_only,project=project,node_id=node_id)
    if read_only:
        stored=project.nodes[node_id].files.get('mdo_study') if project is not None and node_id in project.nodes else None
        if stored is None: raise ValueError('Read-only Studio requires --study or a saved MDO study on the selected project node.')
        return workbench.open_mdo_study(stored.path,read_only=True,project=project,node_id=node_id)
    return workbench.create_mdo_study(study,project=project,node_id=node_id)


class MDOStudioWindow(QMainWindow):
    def __init__(self,study=None,parent=None,*,service=None,study_path=None,project=None,node_id=None,read_only=False):
        super().__init__(parent)
        self.workbench=service.workbench if service else Workbench()
        self.service=service or startup_service(self.workbench,study,study_path=study_path,project=project,node_id=node_id,read_only=read_only)
        if read_only and not self.service.read_only: raise ValueError('Open the saved service read-only before attaching it to Studio.')
        self.setWindowTitle('CAELab MDO Studio'); self.resize(1380,880)
        self.setFont(QFont('Segoe UI',9)); self.is_dark=current_theme_mode()=='dark'
        self._generation=0; self._busy=None; self._selected_ids=set(); self._pending_records={}; self._closing=False
        self._is_specification=False; self._editor=None; self._hidden_selection=False; self._sensitivity_job=0
        self.bridge=MDOExecutionBridge(self)
        self.bridge.itemFinished.connect(self._on_item_finished)
        self.bridge.batchProgress.connect(self._on_batch_progress)
        self.bridge.batchFinished.connect(self._on_batch_finished)
        self.bridge.taskFinished.connect(self._task_finished)
        self._delta_timer=QTimer(self); self._delta_timer.setInterval(100); self._delta_timer.timeout.connect(self._flush_deltas)
        self._viz_timer=QTimer(self); self._viz_timer.setSingleShot(True); self._viz_timer.setInterval(250); self._viz_timer.timeout.connect(self._update_visualizations)
        self._build_ui(); self._populate_study(); self.apply_theme(); self._restore_layout()

    @property
    def study(self): return self.service.study
    @property
    def current_deck_path(self): return self.service.input_path
    @property
    def template_deck(self): return self.service.template
    @property
    def response_units(self): return self.service.response_units
    @property
    def _active_batch_id(self): return self.service.active_batch_id
    @property
    def _configured(self): return bool(self.study.objectives and self.study.space.variables)
    @property
    def _study_file(self): return self.service.study_file

    def _action(self,menu,text,slot,shortcut=None,toolbar=False):
        action=QAction(text,self); action.triggered.connect(slot)
        if shortcut: action.setShortcut(shortcut)
        menu.addAction(action)
        if toolbar: self.toolbar.addAction(action)
        return action

    def _build_ui(self):
        self.toolbar=QToolBar('Study commands',self); self.toolbar.setObjectName('mdo.commands'); self.toolbar.setMovable(False); self.addToolBar(self.toolbar)
        file=self.menuBar().addMenu('&File'); setup=self.menuBar().addMenu('&Study'); run=self.menuBar().addMenu('&Run'); view=self.menuBar().addMenu('&View')
        self.act_new=self._action(file,'New Study',self._on_new_study,'Ctrl+N')
        self.act_open=self._action(file,'Open Study…',lambda:self._on_open_study(),'Ctrl+O',True)
        self.act_input=self._action(file,'Open Native Input…',lambda:self._on_load_deck())
        self.act_save=self._action(file,'Save',lambda:self._on_save_study(),'Ctrl+S')
        self.act_save_as=self._action(file,'Save As…',lambda:self._on_save_study(save_as=True),'Ctrl+Shift+S')
        self.act_export=self._action(file,'Export Filtered Table…',self._export_csv)
        self.act_parameterize=self._action(setup,'Create Scalar Token…',self._parameterize)
        self.act_configure=self._action(setup,'Configure Study',self._on_configure,toolbar=True)
        self.act_generate=self._action(setup,'Generate DOE…',lambda:self._on_generate_doe(),toolbar=True)
        self.act_train=self._action(setup,'Train / Validate Surrogate…',self._train)
        self.act_optimize=self._action(setup,'Constrained Surrogate Search…',self._optimize)
        self.act_run=self._action(run,'Run Selected / Pending',self._run_selected,'F5',True)
        self.act_all=self._action(run,'Run All Pending',self._run_all_points)
        self.act_confirm=self._action(run,'Fresh Native Confirmation',self._confirm_selected)
        self.act_clone=self._action(run,'Clone Selected as Candidate',self._clone_selected)
        self.act_stop=self._action(run,'Stop Scheduling',self._stop_scheduling,toolbar=True)
        self.act_cancel=self._action(run,'Cancel Active Campaign',self._stop_evaluations)
        self.act_current=self._action(run,'Set as Current Design Point',lambda:self._on_apply_as_current(self._first_selected()))
        self.act_undo=self._action(run,'Undo Current Design Apply',self._undo)
        self.act_rerun=self._action(run,'Run Current Design',self._rerun_current)
        self.act_result=self._action(run,'Open Selected Actual Result in ParaView',lambda:self._open_result(self._first_selected()))
        self.act_current_result=self._action(run,'Open Current Rerun in ParaView',lambda:self._open_result(current=True))
        self.act_attempts=self._action(run,'Inspect Selected Attempts / Log',lambda:self._inspect_attempts(self._first_selected()))
        self.status_bar=QStatusBar(); self.setStatusBar(self.status_bar)
        self.progress_bar=QProgressBar(); self.progress_bar.setMaximumWidth(160); self.progress_bar.hide(); self.status_bar.addPermanentWidget(self.progress_bar)
        self.tree_dock=QDockWidget('Study',self); self.tree_dock.setObjectName('mdo.study'); self.study_tree=StudyTreeWidget(); self.tree_dock.setWidget(self.study_tree)
        self.tree_dock.setMinimumWidth(160); self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea,self.tree_dock)
        self.setup_dock=QDockWidget('Setup',self); self.setup_dock.setObjectName('mdo.setup'); self.setup_dock.setMinimumWidth(250)
        self.setup_widget=SetupDockWidget(self.is_dark); self.setup_dock.setWidget(self.setup_widget); self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,self.setup_dock)
        self.setup_widget.apply_as_current.connect(self._on_apply_as_current); self.setup_widget.re_evaluate.connect(lambda point:self._confirm_selected([point]))
        self.setup_widget.inspect_attempts.connect(self._inspect_attempts); self.setup_widget.open_result.connect(self._open_result)
        self.study_tree.node_selected.connect(self._tree_selected)
        for dock in (self.tree_dock,self.setup_dock): view.addAction(dock.toggleViewAction())
        self._action(view,'Restore Default Layout',self._default_layout); self._action(view,'Save Layout',self._save_layout)
        compact=self._action(view,'Compact Rows',lambda checked:self.tdp_table.verticalHeader().setDefaultSectionSize(24 if checked else 30)); compact.setCheckable(True)
        self.act_theme=self._action(view,theme_toggle_label(),self._on_toggle_theme)
        self.center_splitter=QSplitter(Qt.Orientation.Vertical); self.center_splitter.setObjectName('mdo.central')
        container=QWidget(); layout=QVBoxLayout(container); layout.setContentsMargins(4,4,4,4)
        self.study_label=QLabel(); self.study_label.setWordWrap(True); layout.addWidget(self.study_label)
        filters=QHBoxLayout(); self.search=QLineEdit(); self.search.setPlaceholderText('Filter point name / ID'); self.search.setAccessibleName('Filter design points')
        self.status_filter=QComboBox(); self.status_filter.addItems(['All','PENDING','RUNNING','QUEUED','COMPLETED','PREDICTED','FAILED','CANCELLED','STALE'])
        self.feasibility_filter=QComboBox(); self.feasibility_filter.addItems(['All','FEASIBLE','MARGINAL','INFEASIBLE','NOT_ASSESSED'])
        filters.addWidget(self.search,1); filters.addWidget(self.status_filter); filters.addWidget(self.feasibility_filter)
        clear=QPushButton('Clear filters'); clear.clicked.connect(self._clear_filters); filters.addWidget(clear); layout.addLayout(filters)
        self.tdp_lbl=QLabel('Design points'); layout.addWidget(self.tdp_lbl)
        self.tdp_model=DesignPointsTableModel(is_dark=self.is_dark); self.tdp_proxy=DesignPointsProxyModel(self); self.tdp_proxy.setSourceModel(self.tdp_model)
        self.tdp_table=QTableView(); self.tdp_table.setAccessibleName('Table of design points'); self.tdp_table.setModel(self.tdp_proxy)
        self.tdp_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows); self.tdp_table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.tdp_table.setSortingEnabled(True); self.tdp_table.sortByColumn(0,Qt.SortOrder.AscendingOrder)
        self.tdp_table.verticalHeader().setDefaultSectionSize(30); self.tdp_table.horizontalHeader().setDefaultSectionSize(115)
        self.tdp_table.setColumnWidth(1,190); self.tdp_table.setAlternatingRowColors(True)
        self.tdp_table.selectionModel().selectionChanged.connect(self._on_row_selected); layout.addWidget(self.tdp_table,1)
        self.search.textChanged.connect(self._filters_changed); self.status_filter.currentTextChanged.connect(self._filters_changed); self.feasibility_filter.currentTextChanged.connect(self._filters_changed)
        self.center_splitter.addWidget(container)
        self.viz_tabs=QTabWidget(); self.pareto_view=ParetoFrontWidget(is_dark=self.is_dark); self.parallel_view=ParallelCoordinatesWidget(is_dark=self.is_dark)
        self.surface_view=ResponseSurfaceWidget(is_dark=self.is_dark); self.tornado_view=SensitivityTornadoWidget(is_dark=self.is_dark); self.convergence_view=ConvergenceWidget(is_dark=self.is_dark)
        self.views=[self.pareto_view,self.parallel_view,self.surface_view,self.tornado_view,self.convergence_view]
        for widget,title in zip(self.views,('Pareto','Parallel','Surface','Sensitivity','Convergence')): self.viz_tabs.addTab(widget,title)
        self.viz_tabs.currentChanged.connect(lambda *args:self._viz_timer.start())
        for widget in (self.pareto_view,self.parallel_view,self.convergence_view): widget.point_selected.connect(self.select_point)
        self.parallel_view.filter_changed.connect(self._brush_filter)
        self.tornado_view.response_selected.connect(lambda *args:self._sensitivity(self.tdp_model.records))
        self.center_splitter.addWidget(self.viz_tabs); self.center_splitter.setSizes([350,430]); self.setCentralWidget(self.center_splitter)
        self._study_actions=[self.act_new,self.act_open,self.act_input,self.act_generate,self.act_parameterize,self.act_save,self.act_save_as,self.act_train,self.act_optimize]

    def _background(self,name,call):
        if self._busy:
            self.status_bar.showMessage('Wait for the current operation to finish.'); return
        self._busy=name; self._sync_actions(); self.status_bar.showMessage(name+'…')
        QThreadPool.globalInstance().start(StudioTask(self._generation,name,call,self.bridge))

    def _task_finished(self,generation,name,result,error):
        if generation!=self._generation or self._closing: return
        if name.startswith('Sensitivity '):
            if name!=f'Sensitivity {self._sensitivity_job}': return
            items,target=result if result else ([],self.tornado_view.response.currentData())
            self.tornado_view.plot_tornado(items,f'{target} [{self.response_units.get(target,"—")}]')
            if error: self.status_bar.showMessage(error)
            return
        self._busy=None
        if error:
            QMessageBox.warning(self,name,error); self.status_bar.showMessage(f'{name} failed: {error}')
        elif name=='Prepare current design': self._review_apply(result)
        elif name in ('Open study','Open input'):
            old=self.service; self.service=result
            if hasattr(old,'close'): old.close()
            self._generation+=1; self._selected_ids.clear(); self._clear_filters(); self._restore_study_view()
        else:
            self.status_bar.showMessage(f'{name} completed. No calculation starts unless explicitly requested.')
        if name in ('Open input','Parameterize','Configure','Open study') and not error:
            self._editor=None
            self._input_context()
        self._populate_study(); self._sync_actions()
        if name in ('Open study','Open input') and not error: self._reveal_selection()
        if self._active_batch_id: self._delta_timer.start()

    def _sync_actions(self):
        active=bool(self._active_batch_id); busy=bool(self._busy); writable=not self.service.read_only
        for action in self._study_actions: action.setEnabled(not active and not busy and writable)
        self.act_configure.setEnabled(not busy and bool(self.template_deck) and writable)
        self.act_run.setEnabled(not active and not busy and writable and self._configured)
        self.act_all.setEnabled(self.act_run.isEnabled()); self.act_confirm.setEnabled(self.act_run.isEnabled() and bool(self._selected_ids))
        self.act_clone.setEnabled(not active and not busy and writable and bool(self._selected_ids))
        self.act_stop.setEnabled(active); self.act_cancel.setEnabled(active)
        self.progress_bar.setVisible(active or busy)
        if busy and not active: self.progress_bar.setRange(0,0)
        else: self.progress_bar.setRange(0,100)
        rec=self.tdp_model.record(self._first_selected())
        candidate=bool(rec and rec.status=='COMPLETED' and getattr(rec,'mode','ACTUAL')!='SURROGATE' and getattr(rec,'revision',self.service.revision)==self.service.revision)
        for action in (self.act_current,self.act_result): action.setEnabled(candidate and not active and not busy and writable)
        self.setup_widget.btn_apply_current.setEnabled(self.act_current.isEnabled())
        self.setup_widget.btn_confirm.setEnabled(self.act_confirm.isEnabled())
        self.setup_widget.btn_result.setEnabled(bool(rec and rec.status=='COMPLETED' and getattr(rec,'mode','ACTUAL')!='SURROGATE') and writable)
        self.act_undo.setEnabled(bool(self.service.current_point_id) and not active and not busy and writable)
        self.act_rerun.setEnabled(self.act_undo.isEnabled()); self.act_current_result.setEnabled(bool(self.service.current_point_id) and not busy)
        if self._editor: self._editor.buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(not active and not busy and writable)

    def _populate_study(self):
        selected=set(self._selected_ids)
        units={v.id:getattr(v,'unit','—') for v in self.study.space.variables}; units.update(self.response_units)
        responses=list(dict.fromkeys([o.response_id for o in self.study.objectives]+[c.response_id for c in self.study.constraints if c.response_id])) or ['response']
        self.tdp_table.selectionModel().blockSignals(True)
        self.tdp_model.set_study_data(self.study.records,[v.id for v in self.study.space.variables],responses,units=units,current_point_id=self.service.current_point_id)
        self._selected_ids=selected; self._restore_selection(); self.tdp_table.selectionModel().blockSignals(False)
        self.study_label.setText(self.study.name+(' — read only' if self.service.read_only else ''))
        self.study_tree.populate(self.study,surrogate=self.service.surrogate,current_point_id=self.service.current_point_id)
        self._show_selection(); self._update_visualizations(); self._sync_actions()

    def _first_selected(self): return next(iter(sorted(self._selected_ids)),None)

    def _restore_selection(self):
        selection=self.tdp_table.selectionModel(); selection.clearSelection()
        visible=0
        for point in self._selected_ids:
            row=self.tdp_model.row_for_id(point)
            if row is not None:
                proxy=self.tdp_proxy.mapFromSource(self.tdp_model.index(row,0))
                if proxy.isValid(): selection.select(proxy,QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows); visible+=1
        self._hidden_selection=visible<len(self._selected_ids)
        self.tdp_lbl.setText(f'{self.tdp_proxy.rowCount()} / {self.tdp_model.rowCount()} points · {len(self._selected_ids)} selected'+(' (selection hidden by filter)' if self._hidden_selection else ''))

    def _on_row_selected(self,*args):
        self._selected_ids={index.data(DesignPointsTableModel.PointIdRole) for index in self.tdp_table.selectionModel().selectedRows()}
        self._show_selection(); self._sync_actions()

    def _show_selection(self):
        rec=self.tdp_model.record(self._first_selected()); self.setup_widget.display_record(rec,self.tdp_model.units)
        for view in (self.pareto_view,self.parallel_view): view.set_selected(self._selected_ids)
        if rec:
            self.setup_widget.lbl_guide.setPlainText((rec.error_message+'\n' if rec.error_message else '')+f'{getattr(rec,"mode","ACTUAL")} · revision {getattr(rec,"revision","1")}\nSelection does not apply or run this design.')
        self._restore_selection_label()
        self._viz_timer.start()

    def _restore_selection_label(self):
        visible=sum(self.tdp_proxy.mapFromSource(self.tdp_model.index(self.tdp_model.row_for_id(p),0)).isValid() for p in self._selected_ids if self.tdp_model.row_for_id(p) is not None)
        self._hidden_selection=visible<len(self._selected_ids)
        self.tdp_lbl.setText(f'{self.tdp_proxy.rowCount()} / {self.tdp_model.rowCount()} points · {len(self._selected_ids)} selected'+(' (selection hidden by filter)' if self._hidden_selection else ''))

    def select_point(self,point_id):
        if self.tdp_model.row_for_id(point_id) is None: return
        self._selected_ids={point_id}; self.tdp_table.selectionModel().blockSignals(True); self._restore_selection(); self.tdp_table.selectionModel().blockSignals(False)
        row=self.tdp_model.row_for_id(point_id); index=self.tdp_proxy.mapFromSource(self.tdp_model.index(row,0))
        if index.isValid(): self.tdp_table.scrollTo(index)
        self._show_selection(); self._sync_actions()

    def _reveal_selection(self):
        row=self.tdp_model.row_for_id(self._first_selected())
        if row is not None:
            index=self.tdp_proxy.mapFromSource(self.tdp_model.index(row,0))
            if index.isValid(): self.tdp_table.scrollTo(index,QTableView.ScrollHint.PositionAtCenter)

    def _filters_changed(self,*args):
        selected=set(self._selected_ids); self.tdp_table.selectionModel().blockSignals(True)
        self.tdp_proxy.set_filters(text=self.search.text(),status=self.status_filter.currentText(),feasibility=self.feasibility_filter.currentText())
        self._selected_ids=selected; self._restore_selection(); self.tdp_table.selectionModel().blockSignals(False)

    def _brush_filter(self,point_ids):
        selected=set(self._selected_ids); self.tdp_table.selectionModel().blockSignals(True); self.tdp_proxy.set_filters(point_ids=point_ids)
        self._selected_ids=selected; self._restore_selection(); self.tdp_table.selectionModel().blockSignals(False)

    def _clear_filters(self):
        self.search.clear(); self.status_filter.setCurrentIndex(0); self.feasibility_filter.setCurrentIndex(0); self._brush_filter(None)
        if hasattr(self,'parallel_view'): self.parallel_view.brushes.clear()

    def _update_visualizations(self):
        records=self.tdp_model.records; units=self.tdp_model.units
        index=self.viz_tabs.currentIndex()
        if index==0: self.pareto_view.set_study(records,self.study.objectives,units)
        elif index==1: self.parallel_view.set_study(records,self.study.space.variables,self.tdp_model.response_keys,units)
        elif index==2: self.surface_view.set_study(self.service.surrogate,self.study.space,records,units)
        elif index==3:
            self.tornado_view.set_responses(self.tdp_model.response_keys,self.response_units); self._sensitivity(records)
        elif index==4: self.convergence_view.set_study(records,self.study.objectives,units,self.service.revision)

    def _sensitivity(self,records):
        self._sensitivity_job+=1
        target=self.tornado_view.response.currentData()
        variables=[v for v in self.study.space.independent_variables if not hasattr(v,'choices')]
        rows=[r for r in records if r.status=='COMPLETED' and getattr(r,'mode','ACTUAL')!='SURROGATE' and getattr(r,'revision',self.service.revision)==self.service.revision and target in r.observations and all(v.id in r.physical_values for v in variables)]
        def compute():
            items=[]
            if len(rows)>=3 and variables:
                from caelab.mdo.sensitivity import spearman
                pairs=spearman(np.array([[r.physical_values[v.id] for v in variables] for r in rows]),np.array([[r.observations[target]] for r in rows]),variable_ids=[v.id for v in variables],response_ids=[target])
                items=[TornadoItem(p.variable_id,'Spearman rho (dimensionless)',abs(p.rho),sign=1 if p.rho>=0 else -1) for p in pairs if p.rho is not None]
            return items,target
        QThreadPool.globalInstance().start(StudioTask(self._generation,f'Sensitivity {self._sensitivity_job}',compute,self.bridge))

    def _tree_selected(self,category,key):
        if category in ('variables','criteria'): self._on_configure(tab=0 if category=='variables' else 1)
        elif category=='input': self._input_context()
        elif category=='doe': self._method_context('DOE')
        elif category=='surrogates': self._method_context('Surrogate')
        elif category=='optimization': self._method_context('Optimization')
        elif category=='sensitivity': self.viz_tabs.setCurrentIndex(3)
        elif category=='evaluations':
            self.status_filter.setCurrentText(key or 'All'); self.setup_widget.context_tabs.setCurrentIndex(0)
        elif category=='selection': self.setup_widget.context_tabs.setCurrentIndex(0)
        elif category=='view' and key in ('Pareto','Parallel','Surface','Sensitivity','Convergence'): self.viz_tabs.setCurrentIndex(('Pareto','Parallel','Surface','Sensitivity','Convergence').index(key))

    def _input_context(self):
        widget=QWidget(); layout=QVBoxLayout(widget); label=QLabel(str(self.current_deck_path or 'Open a native CalculiX input.')); label.setWordWrap(True); layout.addWidget(label)
        for text,slot in [('Open Native Input…',lambda:self._on_load_deck()),('Create Scalar Token…',self._parameterize),('Configure Variables / Responses',self._on_configure)]:
            button=QPushButton(text); button.clicked.connect(slot); layout.addWidget(button)
        layout.addStretch(); self.setup_widget.set_editor(widget,'Input'); self.setup_dock.show()

    def _method_context(self,kind):
        widget=QWidget(); layout=QVBoxLayout(widget)
        if kind=='Surrogate' and self.service.surrogate:
            artifact=self.service.surrogate; text=f'Saved {artifact.config.method.upper()} model · study revision {artifact.dataset.training_revision}\nQualified: {artifact.qualified}\n'+ '\n'.join(f'{m.response_id}: RMSE {m.rmse:.6g} {m.unit}; holdout n={m.n}' for m in artifact.metrics)
            label=QLabel(text); label.setToolTip(artifact.id); label.setWordWrap(True); layout.addWidget(label)
        labels={'DOE':('Generate DOE…',lambda:self._on_generate_doe()),'Surrogate':('Train / Validate…',self._train),'Optimization':('Constrained Search…',self._optimize)}
        text,slot=labels[kind]; button=QPushButton(text); button.clicked.connect(slot); layout.addWidget(button); layout.addStretch(); self.setup_widget.set_editor(widget,kind); self.setup_dock.show()

    def _on_configure(self,*args,tab=0):
        if not self.template_deck: self._input_context(); return
        if self._editor is None:
            self._editor=StudyConfigEditor(self.template_deck.token_names,self.study,self.response_units,getattr(self.service,'response_selectors',{}))
            self._editor.applied.connect(lambda config:self._configure_study(*config))
        self.setup_widget.set_editor(self._editor,'Setup')
        self._editor.tabs.setCurrentIndex(tab); self.setup_widget.context_tabs.setCurrentIndex(1); self.setup_dock.show(); self._sync_actions()

    def _configure_study(self,space,objectives,constraints,response_units,response_selectors=None):
        if self._active_batch_id:
            if self._editor: self._editor.error.setText('Campaign revision is locked; retain this draft and Apply after it finishes.')
            return
        try: self.service.configure(space,objectives,constraints,response_units,response_selectors=response_selectors)
        except (ValueError,TypeError,OSError) as exc:
            if self._editor: self._editor.error.setText(str(exc))
            else: QMessageBox.warning(self,'Study configuration',str(exc))
            return
        self._populate_study(); self.status_bar.showMessage('New study revision applied. Historical rows retain their original revision.')
        if self._editor:
            self._editor.study=self.study; self._editor.response_units=dict(self.response_units); self._editor.selectors=dict(getattr(self.service,'response_selectors',{}))

    def _parameterize(self):
        if not self.current_deck_path: self._on_load_deck(); return
        dialog=ParameterizeDialog(self.service.input_sources(),self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self._background('Parameterize',lambda:self.service.parameterize([dialog.binding]))

    def _on_new_study(self):
        if self._active_batch_id or self._busy: return
        old=self.service
        if hasattr(old,'close'): old.close()
        self.service=self.workbench.create_mdo_study(); self._generation+=1; self._selected_ids.clear(); self._editor=None; self._clear_filters(); self._populate_study()

    def _on_load_deck(self,path=None):
        if self._active_batch_id or self._busy: return
        if path is None:
            path,_=QFileDialog.getOpenFileName(self,'Open native input','','CalculiX input (*.inp)')
            if not path: return
        project,node=getattr(self.service,'project',None),getattr(self.service,'node_id',None)
        def open_candidate():
            candidate=self.workbench.create_mdo_study(project=project,node_id=node)
            try: candidate.open_input(Path(path))
            except Exception:
                candidate.close(); raise
            return candidate
        self._background('Open input',open_candidate)

    def _on_generate_doe(self,n_samples=None,seed=None):
        if self._active_batch_id or self._busy: return
        method='lhs'
        if n_samples is None:
            method,ok=QInputDialog.getItem(self,'Generate DOE','Method',['lhs','opt-lhs','full-factorial','sobol','ccd','box-behnken'],0,False)
            if not ok: return
            n_samples,ok=QInputDialog.getInt(self,'Generate DOE','Sample count',16,1,10000)
            if not ok: return
        if seed is None:
            seed,ok=QInputDialog.getInt(self,'Generate DOE','Random seed',42,0,2147483647)
            if not ok: return
        self._background('Generate DOE',lambda:self.service.generate_doe(n_samples,seed,method=method))

    def _callbacks(self):
        generation=self._generation; service=self.service
        def item(point,record,run):
            if generation==self._generation and service is self.service: self.bridge.itemFinished.emit(point,record,run)
        def progress(prog):
            if generation==self._generation and service is self.service: self.bridge.batchProgress.emit(prog)
        def done(*values):
            if generation==self._generation and service is self.service: self.bridge.batchFinished.emit(*values)
        return dict(on_item=item,on_progress=progress,on_finished=done)

    def _run_selected(self):
        ids=list(self._selected_ids) or [r.point_id for r in self.study.records if r.status=='PENDING' and getattr(r,'mode','ACTUAL')!='SURROGATE' and getattr(r,'revision',self.service.revision)==self.service.revision]
        if self._hidden_selection:
            QMessageBox.information(self,'Hidden selection','Selected points are hidden by the filter. Clear filters or select visible points before running.'); return
        self._background('Run native evaluations',lambda:self.service.submit(ids,**self._callbacks()))

    def _run_all_points(self):
        ids=[r.point_id for r in self.study.records if r.status in ('PENDING','FAILED','CANCELLED') and getattr(r,'mode','ACTUAL')!='SURROGATE' and getattr(r,'revision',self.service.revision)==self.service.revision]
        self._background('Run native evaluations',lambda:self.service.submit(ids,**self._callbacks()))

    def _confirm_selected(self,point_ids=None):
        ids=list(point_ids if isinstance(point_ids,(list,tuple)) else self._selected_ids)
        self._background('Fresh native confirmation',lambda:self.service.confirm(ids,**self._callbacks()))

    def _clone_selected(self):
        point=self._first_selected()
        if point: self._background('Clone candidate',lambda:self.service.clone_point(point))

    def _rerun_current(self): self._background('Run current design',lambda:self.service.rerun_current(**self._callbacks()))
    def _stop_scheduling(self):
        QThreadPool.globalInstance().start(StudioTask(self._generation,'Stop scheduling',self.service.stop_scheduling,self.bridge))
        self.status_bar.showMessage('Scheduling stop requested. The current owned attempt may finish.')
    def _stop_evaluations(self):
        QThreadPool.globalInstance().start(StudioTask(self._generation,'Cancel campaign',self.service.cancel,self.bridge))
        self.status_bar.showMessage('Cancellation requested for this campaign.')

    def _on_item_finished(self,point_id,record,run):
        if getattr(record,'study_id',self.service.study_id)!=self.service.study_id or getattr(record,'revision',self.service.revision)!=self.service.revision: return
        if getattr(record,'batch_id',self._active_batch_id) not in (None,self._active_batch_id): return
        self._pending_records[point_id]=record; self._delta_timer.start()

    def _flush_deltas(self):
        if not self._pending_records: return
        selected=set(self._selected_ids); position=self.tdp_table.verticalScrollBar().value()
        self.tdp_table.selectionModel().blockSignals(True)
        for record in self._pending_records.values(): self.tdp_model.update_record(record)
        self._pending_records.clear(); self._selected_ids=selected; self._restore_selection()
        self.tdp_table.verticalScrollBar().setValue(position); self.tdp_table.selectionModel().blockSignals(False)
        self._show_selection(); self._sync_actions(); self._viz_timer.start()

    def _on_batch_progress(self,prog):
        if getattr(prog,'study_id',self.service.study_id)!=self.service.study_id or getattr(prog,'revision',self.service.revision)!=self.service.revision: return
        if prog.batch_id!=self._active_batch_id: return
        self.progress_bar.setRange(0,100); self.progress_bar.setValue(int(prog.completed_count/max(1,prog.total_count)*100)); self.status_bar.showMessage(prog.heartbeat_message)

    def _on_batch_finished(self,batch_id,state,completed,failed):
        self._flush_deltas(); self._delta_timer.stop(); self._populate_study()
        self.status_bar.showMessage(f'배치 완료 ({state}): {completed} completed, {failed} failed. Actual attempts remain available.'); self._sync_actions()

    def _point_is_verified(self,record,*,require_feasible=True): return self.service.is_verified(record.point_id,require_feasible=require_feasible)

    def _on_apply_as_current(self,point_id):
        if point_id: self._background('Prepare current design',lambda:self.service.prepare_apply(point_id))

    def _review_apply(self,plan):
        text='\n'.join(f'{key}: {value}' for key,value in plan['values'].items())+'\n\nTargets:\n'+'\n'.join(t['path'] for t in plan['targets'])
        if plan['violations']: text+='\n\nViolated criteria:\n'+'\n'.join(plan['violations'])
        text+='\n\nCreate verified backups and apply these values? This does not run the solver or update CAD.'
        if QMessageBox.question(self,'Set as Current Design Point',text,QMessageBox.StandardButton.Apply|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)==QMessageBox.StandardButton.Apply:
            self._background('Apply current design',lambda:self.service.apply(plan,allow_infeasible=bool(plan['violations'])))

    def _undo(self): self._background('Undo current design',self.service.undo)
    def _open_result(self,point_id=None,*,current=False): self._background('Open actual result',lambda:self.service.open_result(point_id,current=current))

    def _inspect_attempts(self,point_id):
        rec=self.service.point_details(point_id)
        if not rec: return
        dialog=QDialog(self); dialog.setWindowTitle(f'Actual attempts — {rec.name}'); dialog.resize(800,600); layout=QVBoxLayout(dialog)
        text=QPlainTextEdit(); text.setReadOnly(True); text.setPlainText(json.dumps(getattr(rec,'attempts',[]),ensure_ascii=False,indent=2,default=str)); layout.addWidget(text,1)
        attempts=getattr(rec,'attempts',[])
        attempt_choice=QComboBox()
        for i,attempt in enumerate(attempts): attempt_choice.addItem(f'Attempt {i+1} — {attempt.get("status","unknown")} — {attempt.get("run_id","")}',i)
        if attempts: attempt_choice.setCurrentIndex(len(attempts)-1)
        layout.addWidget(attempt_choice)
        log=QPlainTextEdit(); log.setReadOnly(True); log.setPlaceholderText('Choose Open Latest Log to inspect retained native output.'); layout.addWidget(log,1)
        button=QPushButton('Open Latest Log')
        def read_log():
            try:
                evidence=attempts[attempt_choice.currentIndex()] if attempts else self.service.point_evidence(point_id)
                folder=Path(evidence['workdir']); paths=[Path(evidence['log_path'])] if evidence.get('log_path') else [folder/'job.log',folder/'solver.log',folder/'stdout.log',folder/'stderr.log']
                available=[p for p in paths if p.is_file()]
                if not available: log.setPlainText('No retained log file is available for this attempt.'); return
                log.setPlainText('\n'.join(p.name+'\n'+p.read_text(encoding='utf-8',errors='replace')[-200000:] for p in available))
            except (ValueError,KeyError,OSError) as exc: log.setPlainText(str(exc))
        button.clicked.connect(read_log); layout.addWidget(button); buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons); dialog.exec()

    def _train(self):
        method,ok=QInputDialog.getItem(self,'Train surrogate','Method',['gp','rbf','prs'],0,False)
        if ok: self._background('Train surrogate',lambda:self.service.train(config=SurrogateConfig(method=method,seed=42)))

    def _optimize(self):
        dialog=QDialog(self); dialog.setWindowTitle('Constrained surrogate search'); layout=QVBoxLayout(dialog); form=QFormLayout(); layout.addLayout(form)
        algorithm=QComboBox(); algorithm.addItems(['nsga2','slsqp','de']); form.addRow('Algorithm',algorithm)
        controls={}
        for name,value,minimum,maximum in [('max_evaluations',200,4,100000),('population_size',20,4,10000),('max_iterations',30,1,10000),('seed',42,0,2147483647)]:
            widget=QSpinBox(); widget.setRange(minimum,maximum); widget.setValue(value); form.addRow(name.replace('_',' ').title(),widget); controls[name]=widget
        label=QLabel('Search uses saved surrogate predictions and constraints. Candidates require fresh native confirmation before current-design apply.'); label.setWordWrap(True); layout.addWidget(label)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            config=OptimizationConfig(algorithm=algorithm.currentText(),**{k:w.value() for k,w in controls.items()}); self._background('Constrained search',lambda:self.service.optimize(config))

    def _view_state(self):
        return dict(layout=bytes(self.saveState()).hex(),splitter=bytes(self.center_splitter.saveState()).hex(),selected_ids=sorted(self._selected_ids),tab=self.viz_tabs.currentIndex(),convergence=dict(metric=self.convergence_view.metric_selector.currentIndex(),reference=self.convergence_view.reference.text(),axis=self.convergence_view.xaxis.currentIndex()))

    def _on_save_study(self,path=None,*,save_as=False):
        if save_as and path is None:
            QMessageBox.information(self,'Save Independent Study Copy','The copy is independent. The original project keeps its study relation. External INP source files remain shared.')
        if path is None and (save_as or not self._study_file):
            path,_=QFileDialog.getSaveFileName(self,'Save Study As' if save_as else 'Save Study',str(self._study_file or (self.current_deck_path.with_suffix('.mdo.json') if self.current_deck_path else 'study.json')),'MDO Study (*.json)')
            if not path: return
        self.service.view_state=self._view_state()
        self._background('Save study as' if save_as else 'Save study',lambda:self.service.save_as(path) if save_as else self.service.save(path))

    def _on_open_study(self,path=None,*,read_only=False):
        if path is None:
            path,_=QFileDialog.getOpenFileName(self,'Open Study','','MDO Study (*.json)')
            if not path: return
        self._background('Open study',lambda:self.workbench.open_mdo_study(path,read_only=read_only))

    def _export_csv(self):
        path,_=QFileDialog.getSaveFileName(self,'Export filtered table','','CSV (*.csv)')
        if path:
            ids=[self.tdp_proxy.index(row,0).data(DesignPointsTableModel.PointIdRole) for row in range(self.tdp_proxy.rowCount())]
            self._background('Export CSV',lambda:self.service.export_csv(path,ids))

    def _save_layout(self):
        settings=QSettings('CAELab','MDO Studio'); settings.setValue('layout',self.saveState()); settings.setValue('splitter',self.center_splitter.saveState())

    def _restore_layout(self):
        settings=QSettings('CAELab','MDO Studio'); state=settings.value('layout'); splitter=settings.value('splitter')
        if isinstance(state,QByteArray) and state.size()<100000: self.restoreState(state)
        if isinstance(splitter,QByteArray) and splitter.size()<10000: self.center_splitter.restoreState(splitter)
        self._restore_study_view()

    def _restore_study_view(self):
        state=getattr(self.service,'view_state',{})
        try:
            for key,restore,limit in [('layout',self.restoreState,100000),('splitter',self.center_splitter.restoreState,10000)]:
                value=bytes.fromhex(state.get(key,''))
                if value and len(value)<limit: restore(QByteArray(value))
            self._selected_ids=set(state.get('selected_ids',[])); self._restore_selection(); self.viz_tabs.setCurrentIndex(int(state.get('tab',0)))
            convergence=state.get('convergence',{}); self.convergence_view.reference.setText(convergence.get('reference',''))
            self.convergence_view.metric_selector.setCurrentIndex(int(convergence.get('metric',0))); self.convergence_view.xaxis.setCurrentIndex(int(convergence.get('axis',0)))
            self._show_selection(); self._reveal_selection()
        except (ValueError,TypeError): pass

    def _default_layout(self):
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea,self.tree_dock); self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,self.setup_dock)
        self.tree_dock.show(); self.setup_dock.show(); self.center_splitter.setSizes([350,430])

    def _on_toggle_theme(self): toggle_theme_mode(); self.apply_theme()
    def apply_theme(self):
        self.is_dark=current_theme_mode()=='dark'; self.setStyleSheet(build_stylesheet(current_theme_mode())); self.act_theme.setText(theme_toggle_label())
        self.setup_widget.set_theme(self.is_dark); self.tdp_model.set_theme(self.is_dark)
        for view in self.views: view.set_theme(self.is_dark)

    def closeEvent(self,event):
        if self._busy:
            self.status_bar.showMessage('Wait for the current operation before closing.'); event.ignore(); return
        if self._active_batch_id:
            self._stop_evaluations(); self.status_bar.showMessage('Cancelling the owned campaign; close again after it finishes.'); event.ignore(); return
        if hasattr(self.service,'close') and self.service.close() is False: event.ignore(); return
        self._closing=True; self._generation+=1; self._viz_timer.stop(); self._delta_timer.stop()
        for view in self.views: view.close()
        self._save_layout()
        if not self.workbench.close(): self._closing=False; event.ignore(); return
        event.accept()
