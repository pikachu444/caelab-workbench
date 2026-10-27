"""Attempt-based actual progress with a user-frozen hypervolume reference."""
from __future__ import annotations
import numpy as np
from PySide6.QtCore import QObject,Signal,QRunnable,QThreadPool
from PySide6.QtWidgets import QComboBox,QLineEdit,QHBoxLayout,QLabel
from .plot_base import PlotWidget


class TraceSignals(QObject):
    done=Signal(int,object,object)


class HypervolumeTask(QRunnable):
    def __init__(self,token,rows,objectives,reference):
        super().__init__(); self.token,self.rows,self.objectives,self.reference=token,rows,objectives,reference; self.signals=TraceSignals()
    def run(self):
        try:
            from caelab.mdo.pareto import hypervolume
            reference=[float(v.strip()) for v in self.reference.split(',')]
            if len(reference)!=len(self.objectives) or not np.isfinite(reference).all(): raise ValueError('Enter one finite minimization-coordinate reference for each objective.')
            points=[]; values=[]
            for row in self.rows:
                if row['status']=='COMPLETED' and row['feasibility']=='FEASIBLE' and all(o.id in row['objectives'] for o in self.objectives):
                    points.append([row['objectives'][o.id] for o in self.objectives])
                    values.append(hypervolume(points,reference).value)
                else: values.append(np.nan)
            self.signals.done.emit(self.token,values,None)
        except Exception as exc: self.signals.done.emit(self.token,None,str(exc))


class ConvergenceWidget(PlotWidget):
    def __init__(self,parent=None,is_dark=True):
        super().__init__(parent,is_dark)
        row=QHBoxLayout(); self.metric_selector=QComboBox(); self.metric_selector.addItems(['Feasible count','Constraint violation','Failures','Best feasible scalar objective','Measured hypervolume'])
        self.xaxis=QComboBox(); self.xaxis.addItems(['Actual evaluation count','Cumulative solver wall time [s]'])
        row.addWidget(self.metric_selector,1); row.addWidget(self.xaxis,1); self.controls.addLayout(row)
        self.reference=QLineEdit(); self.reference.setPlaceholderText('Fixed HV reference in minimization coordinates, comma separated')
        self.controls.addWidget(self.reference)
        self.metric_selector.currentIndexChanged.connect(self.refresh); self.xaxis.currentIndexChanged.connect(self.refresh); self.reference.editingFinished.connect(self.refresh)
        self.records=[]; self.objectives=[]; self.units={}; self.rows=[]; self.revision=None; self._token=0

    def set_study(self,records,objectives,units,revision=None):
        self.records=list(records); self.objectives=list(objectives); self.units=dict(units); self.revision=revision; self.refresh()

    def refresh(self,*args):
        self._token+=1; rows=[]
        for rec in self.records:
            if getattr(rec,'mode','ACTUAL')=='SURROGATE' or (self.revision is not None and getattr(rec,'revision',self.revision)!=self.revision): continue
            attempts=getattr(rec,'attempts',())
            for attempt in attempts:
                rows.append(dict(point_id=rec.point_id,status=attempt.get('status','FAILED'),
                    feasibility=attempt.get('feasibility',rec.feasibility if len(attempts)==1 else 'NOT_ASSESSED'),
                    total_violation=attempt.get('total_violation',rec.total_violation if len(attempts)==1 else None),
                    objectives=attempt.get('objectives',rec.objectives if len(attempts)==1 else {}),
                    responses=attempt.get('responses',{}),duration=attempt.get('duration_sec',rec.duration_sec if len(attempts)==1 else 0),
                    stamp=attempt.get('finished_at',''),order=rec.index))
        rows.sort(key=lambda row:(row['stamp'],row['order'])); self.rows=rows
        metric=self.metric_selector.currentText(); self.reference.setVisible(metric=='Measured hypervolume')
        if not rows: self.empty('Run native evaluations to inspect actual attempt convergence and failures.'); return
        if metric=='Best feasible scalar objective' and len(self.objectives)!=1:
            self.empty('Multi-objective study: use measured HV, feasibility or violation; no scalar best is defined.'); return
        if metric=='Measured hypervolume':
            if not self.reference.text().strip(): self.empty('Set a fixed HV reference using the saved objective scales/senses.'); return
            task=HypervolumeTask(self._token,rows,tuple(self.objectives),self.reference.text()); task.signals.done.connect(self._hv_done)
            self.note.setText('Computing measured actual hypervolume with the fixed reference…'); QThreadPool.globalInstance().start(task); return
        values=[]; count=0; best=None; previous=None
        for row in rows:
            if metric=='Feasible count': count+=int(row['status']=='COMPLETED' and row['feasibility']=='FEASIBLE'); values.append(count)
            elif metric=='Failures': count+=int(row['status'] in ('FAILED','CANCELLED','INTERRUPTED')); values.append(count)
            elif metric=='Constraint violation': values.append(row['total_violation'] if row['feasibility'] not in (None,'NOT_ASSESSED') and row['total_violation'] is not None else np.nan)
            else:
                obj=self.objectives[0]
                if row['status']=='COMPLETED' and row['feasibility']=='FEASIBLE' and obj.id in row['objectives']:
                    score=row['objectives'][obj.id]
                    if best is None or score<best: best=score; previous=row['responses'].get(obj.response_id,np.nan)
                    values.append(previous)
                else: values.append(np.nan)
        self._draw(values)

    def _hv_done(self,token,values,error):
        if token!=self._token: return
        if error: self.empty('Hypervolume unavailable'); self.note.setText(error)
        else: self._draw(values)

    def _draw(self,values):
        self.ax.clear(); self._picks.clear()
        x=np.cumsum([r['duration'] for r in self.rows]) if self.xaxis.currentIndex()==1 else np.arange(1,len(self.rows)+1)
        artist,=self.ax.plot(x,values,marker='.',color='#38bdf8',picker=5); self._picks[artist]=[r['point_id'] for r in self.rows]
        label=self.metric_selector.currentText()
        if label=='Best feasible scalar objective': label+=f' [{self.units.get(self.objectives[0].response_id,"—")}]'
        self.ax.set_xlabel(self.xaxis.currentText()); self.ax.set_ylabel(label); self.ax.set_title('Native attempt trace')
        self._apply_theme(); self.fig.tight_layout(); self.canvas.draw_idle()
        self.note.setText(f'{len(self.rows)} actual attempts at study revision {self.revision}; predictions/cache hits excluded. Failed or unassessed attempts preserve gaps.'+(' Fixed reference: '+self.reference.text()+'; saved objective transforms/scales; no adaptive normalization.' if self.metric_selector.currentText()=='Measured hypervolume' else ''))
