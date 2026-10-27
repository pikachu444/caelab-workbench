"""Native Qt draft editors; parsing happens on Apply, preserving IME preedit."""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,
    QTableWidgetItem,QHeaderView,QDialogButtonBox,QComboBox,QPushButton,QTabWidget,
    QPlainTextEdit,QLineEdit,QSpinBox,QFormLayout)
from caelab.mdo.variables import ContinuousVariable,DiscreteVariable,CategoricalVariable,LinkedVariable,VariableSpace,parse_unit
from caelab.mdo.objectives import ObjectiveSpec
from caelab.mdo.constraints import ConstraintSpec


def cell(table,row,col,default=''):
    item=table.item(row,col)
    return item.text().strip() if item else default


def choice(table,row,col,values,current):
    widget=QComboBox(); widget.addItems(values); widget.setCurrentText(str(current)); table.setCellWidget(row,col,widget)
    return widget


class StudyConfigEditor(QWidget):
    applied=Signal(object)

    def __init__(self,tokens,study,response_units,response_selectors=None,parent=None):
        super().__init__(parent)
        self.tokens=tuple(tokens); self.study=study; self.response_units=dict(response_units); self.selectors=dict(response_selectors or {})
        layout=QVBoxLayout(self); layout.setContentsMargins(4,4,4,4)
        self.tabs=QTabWidget(); layout.addWidget(self.tabs)
        self.variables=QTableWidget(); self.responses=QTableWidget()
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
        self.error.clear()
        columns=['Token','Lower','Upper','Initial','Unit','Name','Domain','Levels / expression','Dependencies','Transform']
        self.variables.setColumnCount(len(columns)); self.variables.setHorizontalHeaderLabels(columns); self.variables.setRowCount(len(self.tokens))
        existing={v.id:v for v in self.study.space.variables}
        for row,token in enumerate(self.tokens):
            v=existing.get(token.lower()); domain='Continuous' if v is None else type(v).__name__.removesuffix('Variable')
            extras=','.join(map(str,getattr(v,'values',getattr(v,'choices',())))) if domain in ('Discrete','Categorical') else getattr(v,'expression','')
            values=[token,getattr(v,'lower',''),getattr(v,'upper',''),getattr(v,'initial',''),getattr(v,'unit',''),getattr(v,'name',token),domain,extras,','.join(getattr(v,'dependencies',())),getattr(v,'transform','linear')]
            for col,value in enumerate(values): self.variables.setItem(row,col,QTableWidgetItem('' if value is None else str(value)))
            self.variables.item(row,0).setFlags(self.variables.item(row,0).flags() & ~Qt.ItemFlag.ItemIsEditable)
            choice(self.variables,row,6,['Continuous','Discrete','Categorical','Linked'],domain)
            choice(self.variables,row,9,['linear','log'],getattr(v,'transform','linear'))
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
                else: v=ContinuousVariable(**kw,unit=unit,lower=float(lower),upper=float(upper),initial=float(initial),transform=self.variables.cellWidget(row,9).currentText())
                if domain!='Categorical' and not unit: raise ValueError('Declare a unit (1 for dimensionless).')
                variables.append(v)
            except (ValueError,TypeError) as exc:
                self.tabs.setCurrentWidget(self.variables); self.variables.setCurrentCell(row,1); raise ValueError(f'{token}: {exc}') from exc
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
            cursor=self.source.textCursor(); selected=cursor.selectedText()
            self.line.setValue(self.sources[self.files.currentText()][:cursor.selectionStart()].count('\n')+1)
            self.literal.setText(selected); self._preview()
        use_selection.clicked.connect(selected_literal)
        # textEdited preserves native composition and only previews committed text.
        self.literal.textEdited.connect(self._preview); self.token.textEdited.connect(self._preview); self._source_changed()

    def _source_changed(self,*args): self.source.setPlainText(self.sources[self.files.currentText()]); self._preview()

    def _preview(self,*args):
        try:
            lines=self.sources[self.files.currentText()].splitlines(); line=lines[self.line.value()-1]
            literal=self.literal.text(); token=self.token.text().strip(); occurrence=self.occurrence.value()
            if not literal or not token.isidentifier() or '<' in token: raise ValueError('Enter an exact literal and an identifier token.')
            pieces=line.split(literal)
            if len(pieces)<=occurrence: raise ValueError('The requested occurrence is absent on this line.')
            start=sum(len(piece)+len(literal) for piece in pieces[:occurrence])-len(literal)
            self.preview.setText(line+'\n→ '+line[:start]+'<'+token+'>'+line[start+len(literal):])
            self.binding=dict(token=token,source_path=self.files.currentText(),line_number=self.line.value(),expected_line=line,old_literal=literal,occurrence=occurrence)
            self.error.clear()
        except (ValueError,IndexError) as exc: self.binding=None; self.preview.clear(); self.error.setText(str(exc))

    def accept(self):
        self._preview()
        if self.binding: super().accept()
