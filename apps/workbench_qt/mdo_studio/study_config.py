"""Native Qt draft editors; parsing happens on Apply, preserving IME preedit."""
from __future__ import annotations
import re
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,
    QTableWidgetItem,QHeaderView,QDialogButtonBox,QComboBox,QPushButton,QTabWidget,
    QPlainTextEdit,QLineEdit,QSpinBox,QFormLayout,QDoubleSpinBox)
from caelab.mdo.variables import ContinuousVariable,DiscreteVariable,CategoricalVariable,LinkedVariable,VariableSpace,parse_unit
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import ConstraintSpec
from caelab.mdo.surrogate import SurrogateConfig
from caelab.mdo.doe import DOEConfig,projected_design_count
from caelab.mdo.inp_binder import scalar_literal_occurrences

try:
    from caelab_prep.ui.widgets.numeric import NumericEdit as PrepNumericEdit
except ModuleNotFoundError as exc:
    if not (exc.name == 'caelab_prep' or exc.name.startswith('caelab_prep.')):
        raise
    PrepNumericEdit = None


def cell(table,row,col,default=''):
    item=table.item(row,col)
    return item.text().strip() if item else default


def choice(table,row,col,values,current):
    widget=QComboBox(); widget.addItems(values); widget.setCurrentText(str(current)); table.setCellWidget(row,col,widget)
    return widget


class _NumericCellError(ValueError):
    def __init__(self,column,message):
        super().__init__(message)
        self.column=column


class StudyConfigEditor(QWidget):
    applied=Signal(object)
    reverting=Signal()

    def __init__(self,tokens,study,response_units,response_selectors=None,parent=None):
        super().__init__(parent)
        self.tokens=tuple(tokens); self.study=study; self.response_units=dict(response_units); self.selectors=dict(response_selectors or {})
        layout=QVBoxLayout(self); layout.setContentsMargins(4,4,4,4)
        self.tabs=QTabWidget(); layout.addWidget(self.tabs)
        self.variables=QTableWidget(); self.responses=QTableWidget()
        self.uses_shared_numeric_controls = PrepNumericEdit is not None
        self.variables.setProperty('numericEditorSource', 'caelab-prep' if self.uses_shared_numeric_controls else 'Qt table')
        self.variables.itemChanged.connect(self._variable_item_changed)
        self.tabs.addTab(self.variables,'Variables')
        response_page=QWidget(); response_layout=QVBoxLayout(response_page); response_layout.setContentsMargins(0,0,0,0)
        response_layout.addWidget(self.responses); response_actions=QHBoxLayout(); response_layout.addLayout(response_actions)
        add=QPushButton('Add response'); add.clicked.connect(self._add_response); response_actions.addWidget(add)
        remove=QPushButton('Remove selected response'); remove.clicked.connect(lambda:self.responses.removeRow(self.responses.currentRow()) if self.responses.currentRow()>=0 else None); response_actions.addWidget(remove)
        self.tabs.addTab(response_page,'Responses / criteria')
        self.error=QLabel(); self.error.setWordWrap(True); self.error.setStyleSheet('color:#ef4444'); layout.addWidget(self.error)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Reset)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        self.buttons.button(QDialogButtonBox.StandardButton.Reset).setText('Revert')
        self.buttons.button(QDialogButtonBox.StandardButton.Reset).clicked.connect(self.revert); layout.addWidget(self.buttons)
        self.revert()

    def revert(self):
        self.reverting.emit()
        self.error.clear()
        self.variables.blockSignals(True)
        columns=['Token','Lower','Upper','Initial','Unit','Name','Domain','Levels / expression','Dependencies','Transform']
        self.variables.setColumnCount(len(columns)); self.variables.setHorizontalHeaderLabels(columns); self.variables.setRowCount(len(self.tokens))
        existing={v.id:v for v in self.study.space.variables}
        for row,token in enumerate(self.tokens):
            v=existing.get(token.lower()); domain='Continuous' if v is None else type(v).__name__.removesuffix('Variable')
            extras=','.join(map(str,getattr(v,'values',getattr(v,'choices',())))) if domain in ('Discrete','Categorical') else getattr(v,'expression','')
            values=[token,getattr(v,'lower',''),getattr(v,'upper',''),getattr(v,'initial',''),getattr(v,'unit',''),getattr(v,'name',token),domain,extras,','.join(getattr(v,'dependencies',())),getattr(v,'transform','linear')]
            for col,value in enumerate(values): self.variables.setItem(row,col,QTableWidgetItem('' if value is None else str(value)))
            self.variables.item(row,0).setFlags(self.variables.item(row,0).flags() & ~Qt.ItemFlag.ItemIsEditable)
            domain_box=choice(self.variables,row,6,['Continuous','Discrete','Categorical','Linked'],domain)
            domain_box.currentTextChanged.connect(lambda _value,r=row:self._install_numeric_editors(r))
            choice(self.variables,row,9,['linear','log'],getattr(v,'transform','linear'))
            self._install_numeric_editors(row)
        self.variables.blockSignals(False)
        columns=['Response','Unit','Objective','Constraint','Limit','Objective scale','Objective weight','Target','Target band','Tolerance','Marginal band','Source','Field','Component','Frame','Set','Node IDs','Reduction','Constraint scale','Constraint weight','Objective offset']
        self.responses.setColumnCount(len(columns)); self.responses.setHorizontalHeaderLabels(columns)
        keys=list(dict.fromkeys(['disp','stress','mass_eall',*self.response_units,*[o.response_id for o in self.study.objectives],*[c.response_id for c in self.study.constraints if c.response_id]]))
        self.responses.setRowCount(len(keys))
        for row,key in enumerate(keys):
            obj=next((o for o in self.study.objectives if o.response_id==key),None); con=next((c for c in self.study.constraints if c.response_id==key),None)
            selector=self.selectors.get(key,{})
            defaults=('FRD','DISP','magnitude','ALL') if key=='disp' else ('FRD','STRESS','von_mises','ALL') if key=='stress' else ('DAT','EMAS','value','EALL')
            values=[key,self.response_units.get(key,''),'—','—',getattr(con,'limit',''),getattr(obj,'scale',1),getattr(obj,'weight',1),getattr(obj,'target',''),getattr(obj,'target_band',''),getattr(con,'tolerance',0),getattr(con,'marginal_band',0),selector.get('source',defaults[0]),selector.get('field',defaults[1]),selector.get('component',defaults[2]),selector.get('frame','last'),selector.get('set',defaults[3]),','.join(map(str,selector.get('node_ids',[]))),selector.get('reduction','sum' if key=='mass_eall' else 'max'),getattr(con,'scale',1),getattr(con,'weight',1),getattr(obj,'offset',0)]
            for col,value in enumerate(values): self.responses.setItem(row,col,QTableWidgetItem('' if value is None else str(value)))
            choice(self.responses,row,2,['—','MIN','MAX','TARGET'],obj.sense if obj else '—')
            choice(self.responses,row,3,['—','UPPER','LOWER','EQUALITY'],con.kind if con else '—')
            choice(self.responses,row,11,['FRD','DAT'],values[11]); choice(self.responses,row,12,['DISP','STRESS','EMAS'],values[12])
            choice(self.responses,row,17,['max','min','mean','sum'],values[17])
        for table in (self.variables,self.responses):
            table.setAlternatingRowColors(True); table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            table.horizontalHeader().setDefaultSectionSize(105); table.verticalHeader().setDefaultSectionSize(30)
        self.variables.setColumnWidth(0,140); self.variables.setColumnWidth(5,170); self.variables.setColumnWidth(7,180)
        self.responses.setColumnWidth(0,145); self.responses.setColumnWidth(13,135)

    def _install_numeric_editors(self,row):
        """Reuse drafts only; variable validation and persisted state remain in MDO."""
        domain=self.variables.cellWidget(row,6).currentText()
        saved=next((v for v in self.study.space.variables if v.id==cell(self.variables,row,0).lower()),None)
        for col in (1,2,3):
            self.variables.removeCellWidget(row,col)
            if not self.uses_shared_numeric_controls or domain!='Continuous':
                continue
            committed=getattr(saved,{1:'lower',2:'upper',3:'initial'}[col],'')
            editor=PrepNumericEdit(committed,unit=cell(self.variables,row,4))
            editor.setText(cell(self.variables,row,col))
            editor.setObjectName('mdoSharedNumber')
            editor.setAccessibleName(f'{cell(self.variables,row,0)} {self.variables.horizontalHeaderItem(col).text()}')
            editor.setStyleSheet('QLineEdit[invalid="true"] { border: 1px solid #ef4444; }')
            editor.textChanged.connect(lambda text,r=row,c=col:self.variables.item(r,c).setText(text))
            self.variables.setCellWidget(row,col,editor)

    def _variable_item_changed(self,item):
        if item.column() in (1,2,3):
            editor=self.variables.cellWidget(item.row(),item.column())
            if editor is not None and editor.text()!=item.text():
                editor.setText(item.text())
        elif item.column()==4:
            for col in (1,2,3):
                editor=self.variables.cellWidget(item.row(),col)
                if editor is not None:
                    editor.unit=item.text().strip()
                    editor.set_error(editor.error)

    def accept_drafts(self):
        """Called by the host only after its existing configure operation succeeds."""
        for row in range(self.variables.rowCount()):
            for col in (1,2,3):
                editor=self.variables.cellWidget(row,col)
                if editor is not None:
                    editor.accept_value(cell(self.variables,row,col))

    def _continuous_value(self,row,col):
        editor=self.variables.cellWidget(row,col)
        try:
            return editor.numeric_value() if editor is not None else float(cell(self.variables,row,col))
        except ValueError as exc:
            raise _NumericCellError(col,str(exc)) from exc

    def configuration(self):
        variables=[]
        for row in range(self.variables.rowCount()):
            token,lower,upper,initial,unit,name=[cell(self.variables,row,c) for c in range(6)]
            domain=self.variables.cellWidget(row,6).currentText(); extra=cell(self.variables,row,7); dependencies=cell(self.variables,row,8)
            kw=dict(id=token.lower(),name=name or token)
            try:
                if domain=='Categorical': v=CategoricalVariable(**kw,choices=tuple(x.strip() for x in extra.split(',')),initial=initial)
                elif domain=='Discrete': v=DiscreteVariable(**kw,unit=unit,values=tuple(float(x.strip()) for x in extra.split(',')),initial=float(initial))
                elif domain=='Linked': v=LinkedVariable(**kw,unit=unit,expression=extra,dependencies=tuple(x.strip() for x in dependencies.split(',') if x.strip()),lower=float(lower) if lower else None,upper=float(upper) if upper else None)
                else: v=ContinuousVariable(**kw,unit=unit,lower=self._continuous_value(row,1),upper=self._continuous_value(row,2),initial=self._continuous_value(row,3),transform=self.variables.cellWidget(row,9).currentText())
                if domain!='Categorical' and not unit: raise ValueError('Declare a unit (1 for dimensionless).')
                variables.append(v)
            except (ValueError,TypeError) as exc:
                self.tabs.setCurrentWidget(self.variables)
                col=exc.column if isinstance(exc,_NumericCellError) else 1
                self.variables.setCurrentCell(row,col)
                editor=self.variables.cellWidget(row,col)
                if editor is not None: editor.setFocus()
                raise ValueError(f'{token}: {exc}') from exc
        space=VariableSpace(variables=tuple(variables)); objectives=[]; constraints=[]; units={}; selectors={}
        for row in range(self.responses.rowCount()):
            key=cell(self.responses,row,0); sense=self.responses.cellWidget(row,2).currentText(); kind=self.responses.cellWidget(row,3).currentText()
            if sense==kind=='—': continue
            try:
                if not key or key in units: raise ValueError('Response IDs must be nonempty and unique.')
                unit=cell(self.responses,row,1)
                if not unit: raise ValueError('Declare a unit (1 for dimensionless).')
                parse_unit(unit); units[key]=unit; scale=float(cell(self.responses,row,5)); weight=float(cell(self.responses,row,6))
                if sense!='—': objectives.append(ObjectiveSpec(id=key,response_id=key,sense=sense,scale=scale,weight=weight,offset=float(cell(self.responses,row,20)),target=float(cell(self.responses,row,7)) if sense=='TARGET' else None,target_band=float(cell(self.responses,row,8)) if sense=='TARGET' else None))
                if kind!='—': constraints.append(ConstraintSpec(id=key+'_limit',response_id=key,kind=kind,limit=float(cell(self.responses,row,4)),scale=float(cell(self.responses,row,18)),weight=float(cell(self.responses,row,19)),tolerance=float(cell(self.responses,row,9)),marginal_band=float(cell(self.responses,row,10)),unit=unit))
                frame=cell(self.responses,row,14); nodes=cell(self.responses,row,16)
                selectors[key]=dict(source=self.responses.cellWidget(row,11).currentText(),field=self.responses.cellWidget(row,12).currentText(),component=cell(self.responses,row,13),frame='last' if frame=='last' else int(frame),set=cell(self.responses,row,15),node_ids=[int(x.strip()) for x in nodes.split(',')] if nodes else [],reduction=self.responses.cellWidget(row,17).currentText(),unit=unit)
                if not nodes: selectors[key].pop('node_ids')
            except (ValueError,TypeError) as exc:
                self.tabs.setCurrentIndex(1); self.responses.setCurrentCell(row,1); raise ValueError(f'{key}: {exc}') from exc
        if not objectives: raise ValueError('Select at least one objective.')
        return space,objectives,constraints,units,selectors

    def _add_response(self):
        row=self.responses.rowCount(); self.responses.insertRow(row)
        defaults=[f'response_{row+1}','','—','—','',1,1,'','',0,0,'FRD','DISP','magnitude','last','ALL','','max',1,1,0]
        for col,value in enumerate(defaults): self.responses.setItem(row,col,QTableWidgetItem(str(value)))
        for col,values in [(2,['—','MIN','MAX','TARGET']),(3,['—','UPPER','LOWER','EQUALITY']),(11,['FRD','DAT']),(12,['DISP','STRESS','EMAS']),(17,['max','min','mean','sum'])]: choice(self.responses,row,col,values,defaults[col])
        self.responses.setCurrentCell(row,0)

    def apply(self):
        try: result=self.configuration()
        except (ValueError,TypeError) as exc: self.error.setText(str(exc)); return
        self.error.clear(); self.applied.emit(result)


class StudyConfigDialog(QDialog):
    def __init__(self,tokens,study,response_units,parent=None,response_selectors=None):
        super().__init__(parent); self.setWindowTitle('Configure Study'); self.resize(1000,620)
        layout=QVBoxLayout(self); self.editor=StudyConfigEditor(tokens,study,response_units,response_selectors)
        self.editor.buttons.hide(); layout.addWidget(self.editor)
        self.variables,self.responses=self.editor.variables,self.editor.responses
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def configuration(self): return self.editor.configuration()

    def accept(self):
        try: self.configuration()
        except (ValueError,TypeError) as exc: self.editor.error.setText(str(exc)); return
        super().accept()


class SurrogateTrainingDialog(QDialog):
    def __init__(self,response_units,parent=None):
        super().__init__(parent); self.setWindowTitle('Train / Validate Surrogate'); self.resize(660,530)
        layout=QVBoxLayout(self); form=QFormLayout(); layout.addLayout(form)
        self.method=QComboBox(); self.method.addItems(['gp','rbf','prs']); form.addRow('Method',self.method)
        self.seed=QSpinBox(); self.seed.setRange(0,2147483647); self.seed.setValue(42); form.addRow('Model seed',self.seed)
        self.validation_seed=QSpinBox(); self.validation_seed.setRange(0,2147483647); self.validation_seed.setValue(42); form.addRow('Validation seed',self.validation_seed)
        self.fraction=QDoubleSpinBox(); self.fraction.setRange(.05,.95); self.fraction.setSingleStep(.05); self.fraction.setValue(.2); form.addRow('Holdout fraction',self.fraction)
        self.policy=QComboBox(); self.policy.addItems(['Seeded grouped holdout','Chronological grouped holdout']); form.addRow('Split policy',self.policy)
        self.responses=QTableWidget(len(response_units),4); self.responses.setHorizontalHeaderLabels(['Use','Response','Unit','Qualification RMSE ≤'])
        for row,(key,unit) in enumerate(response_units.items()):
            use=QTableWidgetItem(); use.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsUserCheckable); use.setCheckState(Qt.CheckState.Checked); self.responses.setItem(row,0,use)
            for col,value in ((1,key),(2,unit),(3,'')):
                item=QTableWidgetItem(value)
                if col!=3: item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.responses.setItem(row,col,item)
        self.responses.setColumnWidth(0,45); self.responses.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch); self.responses.setColumnWidth(3,170)
        layout.addWidget(self.responses,1)
        label=QLabel('Identical designs and repeated native runs stay in the same split. Leave all qualification limits blank to report an unqualified model, or enter a physical-unit RMSE limit for every selected response.'); label.setWordWrap(True); layout.addWidget(label)
        self.error=QLabel(); self.error.setWordWrap(True); self.error.setStyleSheet('color:#ef4444'); layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def configuration(self):
        keys=[]; limits=[]
        for row in range(self.responses.rowCount()):
            if self.responses.item(row,0).checkState()!=Qt.CheckState.Checked: continue
            keys.append(cell(self.responses,row,1)); limits.append(cell(self.responses,row,3))
        if not keys: raise ValueError('Select at least one response.')
        if any(limits) and not all(limits): raise ValueError('Enter a qualification limit for every selected response, or leave all blank.')
        config=SurrogateConfig(method=self.method.currentText(),seed=self.seed.value(),qualification_rmse=tuple(float(value) for value in limits) if all(limits) else ())
        return keys,config,dict(holdout_fraction=self.fraction.value(),validation_seed=self.validation_seed.value(),chronological=self.policy.currentIndex()==1)

    def accept(self):
        try: self.configuration()
        except (ValueError,TypeError) as exc: self.error.setText(str(exc)); return
        super().accept()


class ParameterizeDialog(QDialog):
    def __init__(self,sources,parent=None):
        super().__init__(parent); self.setWindowTitle('Create scalar token binding'); self.resize(900,650)
        self.sources=dict(sources); self.binding=None
        layout=QVBoxLayout(self); self.files=QComboBox(); self.files.addItems(list(self.sources)); layout.addWidget(self.files)
        self.source=QPlainTextEdit(); self.source.setReadOnly(True); self.source.setAccessibleName('Immutable native source'); layout.addWidget(self.source,1)
        use_selection=QPushButton('Use selected scalar literal'); layout.addWidget(use_selection)
        form=QFormLayout(); self.line=QSpinBox(); self.line.setRange(1,10000000); self.literal=QLineEdit(); self.occurrence=QSpinBox(); self.occurrence.setRange(1,100000); self.token=QLineEdit()
        for label,widget in [('Line',self.line),('Exact scalar literal',self.literal),('Occurrence on line',self.occurrence),('Token name',self.token)]: form.addRow(label,widget)
        layout.addLayout(form); self.preview=QLabel(); self.preview.setWordWrap(True); layout.addWidget(self.preview)
        self.error=QLabel(); self.error.setWordWrap(True); self.error.setStyleSheet('color:#ef4444'); layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText('Use reviewed token'); buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.files.currentTextChanged.connect(self._source_changed)
        for widget in (self.line,self.occurrence): widget.valueChanged.connect(self._preview)
        def selected_literal():
            cursor=self.source.textCursor(); selected=cursor.selectedText(); start=QTextCursor(cursor)
            start.setPosition(cursor.selectionStart()); end=QTextCursor(cursor); end.setPosition(cursor.selectionEnd())
            if start.blockNumber()!=end.blockNumber(): self.error.setText('Select one complete numeric scalar on one source line.'); return
            line=start.block().text(); position=len(line.encode('utf-16-le')[:start.positionInBlock()*2].decode('utf-16-le'))
            try:
                spans=scalar_literal_occurrences(line,selected); occurrence=spans.index((position,position+len(selected)))+1
            except (ValueError,UnicodeError): self.error.setText('Select one complete numeric scalar on one source line.'); return
            self.line.setValue(start.blockNumber()+1); self.literal.setText(selected); self.occurrence.setValue(occurrence); self._preview()
        use_selection.clicked.connect(selected_literal)
        # textEdited preserves native composition and only previews committed text.
        self.literal.textEdited.connect(self._preview); self.token.textEdited.connect(self._preview); self._source_changed()

    def _source_changed(self,*args): self.source.setPlainText(self.sources[self.files.currentText()]); self._preview()

    def _preview(self,*args):
        try:
            lines=self.sources[self.files.currentText()].splitlines(); line=lines[self.line.value()-1]
            literal=self.literal.text(); token=self.token.text().strip(); occurrence=self.occurrence.value()
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',token): raise ValueError('Enter an exact numeric literal and an ASCII identifier token.')
            spans=scalar_literal_occurrences(line,literal)
            if len(spans)<occurrence: raise ValueError('The requested exact scalar occurrence is absent on this line.')
            start,end=spans[occurrence-1]
            self.preview.setText(line+'\n→ '+line[:start]+'<'+token+'>'+line[end:])
            self.binding=dict(token=token,source_path=self.files.currentText(),line_number=self.line.value(),expected_line=line,old_literal=literal,occurrence=occurrence)
            self.error.clear()
        except (ValueError,IndexError) as exc: self.binding=None; self.preview.clear(); self.error.setText(str(exc))

    def accept(self):
        self._preview()
        if self.binding: super().accept()


_DOE_METHOD_GUIDES = {
    'lhs': 'Latin Hypercube Sampling (LHS): Stratified random sampling across independent factors. Active settings: Sample count, Random seed, Budget.',
    'opt-lhs': 'Optimized LHS: Multi-start pairwise swap search optimizing space-filling quality. Active settings: Sample count, Random seed, Criterion, Restarts, Iterations, Budget.',
    'sobol': 'Scrambled Sobol sequence: Low-discrepancy quasi-Monte Carlo block. Sample count must be a power of two (1, 2, 4, 8, 16, 32, …). Active settings: Sample count, Random seed, Budget.',
    'full-factorial': 'Full-Factorial grid: Cartesian product of per-factor physical levels. Continuous factors require explicit physical levels (no arbitrary defaults are assumed). Sample count and Random seed are not used.',
    'box-behnken': 'Box-Behnken response-surface design: Requires at least 3 linear continuous factors. Active settings: Center points, Budget. Sample count and Random seed are not used.',
    'ccd': 'Central Composite Design (CCD): Response-surface design for linear continuous factors. Choose face-centered or inscribed within hard bounds. Active settings: CCD type, Center points, Budget. Sample count and Random seed are not used.',
}


class DOEGenerationDialog(QDialog):
    def __init__(self,space:VariableSpace,parent=None,*,budget:int=10000):
        super().__init__(parent); self.setWindowTitle('Generate DOE'); self.resize(740,560)
        self.space=space; self.config=None; self.projected_count=None
        layout=QVBoxLayout(self)
        self.form=QFormLayout(); layout.addLayout(self.form)
        self.method=QComboBox(); self.method.addItems(['lhs','opt-lhs','sobol','full-factorial','box-behnken','ccd'])
        self.form.addRow('Method',self.method)
        self.n_samples=QSpinBox(); self.n_samples.setRange(1,100000); self.n_samples.setValue(16)
        self.form.addRow('Sample count',self.n_samples)
        self.seed=QSpinBox(); self.seed.setRange(0,2147483647); self.seed.setValue(42)
        self.form.addRow('Random seed',self.seed)
        self.criterion=QComboBox(); self.criterion.addItems(['maximin','correlation','hybrid'])
        self.form.addRow('LHS criterion',self.criterion)
        self.restarts=QSpinBox(); self.restarts.setRange(1,1000); self.restarts.setValue(4)
        self.form.addRow('LHS restarts',self.restarts)
        self.iterations=QSpinBox(); self.iterations.setRange(0,100000); self.iterations.setValue(200)
        self.form.addRow('LHS swap iterations',self.iterations)
        self.ccd_type=QComboBox(); self.ccd_type.addItems(['face-centered','inscribed','circumscribed'])
        self.form.addRow('CCD type',self.ccd_type)
        self.centers=QSpinBox(); self.centers.setRange(1,1000); self.centers.setValue(1)
        self.form.addRow('Center points',self.centers)
        self.budget=QSpinBox(); self.budget.setRange(1,100000); self.budget.setValue(int(budget))
        self.form.addRow('Maximum point budget',self.budget)
        self.method_guide=QLabel(); self.method_guide.setWordWrap(True); layout.addWidget(self.method_guide)
        self.levels_label=QLabel('Full-factorial factor levels (enter comma-separated physical levels for every continuous factor; no arbitrary levels are assumed):')
        self.levels_label.setWordWrap(True); layout.addWidget(self.levels_label)
        ind=self.space.independent_variables
        self.levels_table=QTableWidget(len(ind),5)
        self.levels_table.setHorizontalHeaderLabels(['Variable','Domain','Bounds / Declared','Unit','Physical levels'])
        self._row_by_var={}
        for row,v in enumerate(ind):
            self._row_by_var[v.id.lower()]=row
            domain=type(v).__name__.removesuffix('Variable')
            bounds=f'[{v.lower:g}, {v.upper:g}]' if isinstance(v,ContinuousVariable) else ', '.join(map(str,getattr(v,'values',getattr(v,'choices',()))))
            unit=getattr(v,'unit','') or '—'
            default_levels='' if isinstance(v,ContinuousVariable) else ', '.join(map(str,getattr(v,'values',getattr(v,'choices',()))))
            for col,value in ((0,v.id),(1,domain),(2,bounds),(3,unit),(4,default_levels)):
                item=QTableWidgetItem(value)
                if col!=4: item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.levels_table.setItem(row,col,item)
        self.levels_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.levels_table.horizontalHeader().setSectionResizeMode(4,QHeaderView.ResizeMode.Stretch)
        self.levels_table.setAlternatingRowColors(True)
        layout.addWidget(self.levels_table,1)
        self.projected_label=QLabel(); self.projected_label.setWordWrap(True); layout.addWidget(self.projected_label)
        self.error=QLabel(); self.error.setWordWrap(True); self.error.setStyleSheet('color:#ef4444'); layout.addWidget(self.error)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept); self.buttons.rejected.connect(self.reject); layout.addWidget(self.buttons)
        self.method.currentTextChanged.connect(self._sync_method_controls)
        for widget in (self.n_samples,self.seed,self.restarts,self.iterations,self.centers,self.budget):
            widget.valueChanged.connect(self._update_preview)
        for combo in (self.criterion,self.ccd_type):
            combo.currentTextChanged.connect(self._update_preview)
        self.levels_table.itemChanged.connect(self._update_preview)
        self._sync_method_controls()

    def set_factor_levels(self,variable_id:str,levels):
        row=self._row_by_var.get(str(variable_id).lower())
        if row is None:
            raise ValueError(f'Unknown factorial factor: {variable_id}')
        text=levels if isinstance(levels,str) else ', '.join(str(x) for x in levels)
        self.levels_table.item(row,4).setText(text)

    def factor_levels_text(self,variable_id:str)->str:
        row=self._row_by_var.get(str(variable_id).lower())
        if row is None:
            raise ValueError(f'Unknown factorial factor: {variable_id}')
        return cell(self.levels_table,row,4)

    def _active_settings_summary(self)->str:
        method=self.method.currentText()
        if method=='lhs':
            return f'n_samples={self.n_samples.value()}, seed={self.seed.value()}, budget={self.budget.value()}'
        if method=='opt-lhs':
            return f'n_samples={self.n_samples.value()}, seed={self.seed.value()}, criterion={self.criterion.currentText()}, restarts={self.restarts.value()}, iterations={self.iterations.value()}, budget={self.budget.value()}'
        if method=='sobol':
            return f'n_samples={self.n_samples.value()} (power-of-two), seed={self.seed.value()}, budget={self.budget.value()}'
        if method=='full-factorial':
            return f'explicit factor levels, budget={self.budget.value()} (sample count & seed not used)'
        if method=='box-behnken':
            return f'centers={self.centers.value()}, budget={self.budget.value()} (sample count & seed not used)'
        return f'ccd_type={self.ccd_type.currentText()}, centers={self.centers.value()}, budget={self.budget.value()} (sample count & seed not used)'

    def _sync_method_controls(self,*args):
        method=self.method.currentText()
        uses_samples_and_seed=method in ('lhs','opt-lhs','sobol')
        uses_opt_lhs=method=='opt-lhs'
        uses_centers=method in ('box-behnken','ccd')
        uses_ccd_type=method=='ccd'
        uses_factorial=method=='full-factorial'
        for widget,active in (
            (self.n_samples,uses_samples_and_seed),
            (self.seed,uses_samples_and_seed),
            (self.criterion,uses_opt_lhs),
            (self.restarts,uses_opt_lhs),
            (self.iterations,uses_opt_lhs),
            (self.ccd_type,uses_ccd_type),
            (self.centers,uses_centers),
        ):
            widget.setEnabled(active)
            self.form.setRowVisible(widget,active)
        self.levels_label.setVisible(uses_factorial)
        self.levels_table.setVisible(uses_factorial)
        self.levels_table.setEnabled(uses_factorial)
        self._update_preview()

    def _parse_factorial_levels(self)->dict[str,tuple]:
        levels={}
        for row,v in enumerate(self.space.independent_variables):
            raw=cell(self.levels_table,row,4)
            if not raw:
                if isinstance(v,ContinuousVariable):
                    raise ValueError(f'Continuous factorial factors require explicit physical levels ({v.id})')
                raise ValueError(f'Factorial levels must be nonempty and unique ({v.id})')
            parts=[part.strip() for part in raw.split(',')]
            if any(not part for part in parts):
                raise ValueError(f'Factorial levels must be nonempty and unique ({v.id})')
            if isinstance(v,ContinuousVariable):
                parsed=[]
                for part in parts:
                    try: val=float(part)
                    except ValueError as exc: raise ValueError(f'Continuous factorial level for {v.id} must be a finite number: {part!r}') from exc
                    parsed.append(val)
                levels[v.id]=tuple(parsed)
            elif isinstance(v,DiscreteVariable):
                parsed=[]
                for part in parts:
                    try: val=float(part)
                    except ValueError as exc: raise ValueError(f'Not a discrete level for {v.id}: {part!r}') from exc
                    parsed.append(val)
                levels[v.id]=tuple(parsed)
            else:
                levels[v.id]=tuple(parts)
        return levels

    def _build_config(self)->DOEConfig:
        method=self.method.currentText()
        levels=self._parse_factorial_levels() if method=='full-factorial' else {}
        return DOEConfig(
            method=method,
            n_samples=self.n_samples.value(),
            seed=self.seed.value(),
            budget=self.budget.value(),
            criterion=self.criterion.currentText(),
            restarts=self.restarts.value(),
            iterations=self.iterations.value(),
            levels=levels,
            centers=self.centers.value(),
            ccd_type=self.ccd_type.currentText(),
        )

    def configuration(self)->DOEConfig:
        cfg=self._build_config()
        self.projected_count=projected_design_count(self.space,cfg,check_budget=True)
        self.config=cfg
        return cfg

    def _update_preview(self,*args):
        method=self.method.currentText()
        self.method_guide.setText(_DOE_METHOD_GUIDES.get(method,''))
        summary=self._active_settings_summary()
        try:
            cfg=self._build_config()
            count=projected_design_count(self.space,cfg,check_budget=True)
            self.config=cfg; self.projected_count=count
            self.projected_label.setText(f'Projected points: {count} / {self.budget.value()} budget · Active: {summary}')
            self.error.clear()
        except (ValueError,TypeError) as exc:
            self.config=None
            raw_count=None
            try:
                cfg=self._build_config()
                raw_count=projected_design_count(self.space,cfg,check_budget=False)
            except (ValueError,TypeError):
                raw_count=None
            self.projected_count=raw_count
            if raw_count is not None:
                self.projected_label.setText(f'Projected points: {raw_count} / {self.budget.value()} budget (exceeds budget) · Active: {summary}')
            else:
                self.projected_label.setText(f'Projected points: — / {self.budget.value()} budget · Active: {summary}')
            self.error.setText(str(exc))

    def accept(self):
        try:
            self.configuration()
        except (ValueError,TypeError) as exc:
            self._update_preview()
            self.error.setText(str(exc))
            return
        self.error.clear()
        super().accept()
