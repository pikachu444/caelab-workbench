"""Debounced, revision-checked surrogate slices with native VTK 3D rendering."""
from __future__ import annotations
from dataclasses import dataclass
from threading import Event
import numpy as np
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool, QTimer
from PySide6.QtWidgets import QHBoxLayout, QFormLayout, QComboBox, QLabel, QDoubleSpinBox, QWidget, QCheckBox
from caelab.mdo.surrogate import predict
from .plot_base import PlotWidget, Native3D


class SliceSignals(QObject):
    done=Signal(int,object,object)


class SliceTask(QRunnable):
    def __init__(self,token,artifact,space,xid,yid,response,fixed,cancel):
        super().__init__(); self.token=token; self.artifact=artifact; self.space=space
        self.xid,self.yid,self.response,self.fixed=xid,yid,response,dict(fixed); self.signals=SliceSignals(); self.cancel=cancel

    def run(self):
        try:
            variables={v.id:v for v in self.space.variables}
            xv,yv=variables[self.xid],variables[self.yid]
            def grid(v): return np.geomspace(v.lower,v.upper,32) if getattr(v,'transform','linear')=='log' else np.linspace(v.lower,v.upper,32)
            x,y=np.meshgrid(grid(xv),grid(yv)); physical=[]
            for xx,yy in zip(x.ravel(),y.ravel()):
                if self.cancel.is_set(): self.signals.done.emit(self.token,None,'cancelled'); return
                values=dict(self.fixed); values[self.xid]=float(xx); values[self.yid]=float(yy)
                encoded=self.space.encode(values)
                physical.append(encoded)
            # Study service trains on the same VariableSpace encoded feature order.
            inputs=np.array([getattr(p,'vector',getattr(p,'encoded',p)) for p in physical],dtype=float)
            prediction=predict(self.artifact,inputs)
            if self.cancel.is_set(): self.signals.done.emit(self.token,None,'cancelled'); return
            index=prediction.response_ids.index(self.response)
            mean=prediction.mean[:,index].reshape(x.shape)
            mean=np.where(prediction.domain_warning.reshape(x.shape),np.nan,mean)
            sigma=np.sqrt(np.maximum(0,prediction.variance[:,index])).reshape(x.shape) if prediction.variance is not None else None
            self.signals.done.emit(self.token,(x,y,mean,sigma),None)
        except Exception as exc:
            self.signals.done.emit(self.token,None,str(exc))


class ResponseSurfaceWidget(PlotWidget):
    def __init__(self,parent=None,is_dark=True):
        super().__init__(parent,is_dark)
        row=QHBoxLayout(); self.xaxis=QComboBox(); self.yaxis=QComboBox(); self.response=QComboBox()
        for text,combo in [('X',self.xaxis),('Y',self.yaxis),('Response',self.response)]: row.addWidget(QLabel(text)); row.addWidget(combo,1)
        self.uncertainty=QCheckBox('Uncertainty ±1.96σ'); row.addWidget(self.uncertainty)
        self.controls.addLayout(row)
        self.fixed_container=QWidget(); self.fixed_form=QFormLayout(self.fixed_container)
        self.fixed_form.setContentsMargins(0,0,0,0); self.controls.addWidget(self.fixed_container)
        self.native=Native3D(self); self.layout.insertWidget(self.layout.indexOf(self.canvas)+1,self.native,1)
        self.native.setVisible(self.native.available); self.canvas.setVisible(not self.native.available)
        self.artifact=None; self.space=None; self.records=[]; self.units={}; self._token=0; self._controls={}; self._fixed_values={}; self._result=None
        self._running=False; self._running_token=None; self._pending=None; self._cancel=None
        self.timer=QTimer(self); self.timer.setSingleShot(True); self.timer.setInterval(180); self.timer.timeout.connect(self._dispatch)
        for combo in (self.xaxis,self.yaxis): combo.currentIndexChanged.connect(self._axes_changed)
        self.response.currentIndexChanged.connect(self.schedule); self.uncertainty.toggled.connect(self._draw_result)

    def set_study(self,artifact,space,records,units):
        if artifact is self.artifact and space is self.space:
            self.records=list(records); self.units=dict(units)
            return
        self._token+=1; self.timer.stop()
        self._pending=None
        if self._cancel: self._cancel.set()
        self._result=None
        self.artifact,self.space,self.records,self.units=artifact,space,list(records),dict(units)
        continuous=[v for v in space.variables if hasattr(v,'lower') and hasattr(v,'initial') and not hasattr(v,'values')]
        for i,combo in enumerate((self.xaxis,self.yaxis)):
            old=combo.currentData(); combo.blockSignals(True); combo.clear()
            for variable in continuous: combo.addItem(f'{variable.id} [{variable.unit}]',variable.id)
            keys=[v.id for v in continuous]; combo.setCurrentIndex(keys.index(old) if old in keys else min(i,len(keys)-1)); combo.blockSignals(False)
        old=self.response.currentData(); self.response.blockSignals(True); self.response.clear()
        if artifact:
            for key in artifact.dataset.response_ids: self.response.addItem(f'{key} [{units.get(key,"—")}]',key)
            self.response.setCurrentIndex(max(0,self.response.findData(old)))
        self.response.blockSignals(False); self._axes_changed()

    def _axes_changed(self,*args):
        for key,widget in self._controls.items(): self._fixed_values[key]=widget.currentData() if isinstance(widget,QComboBox) else widget.value()
        self._result=None
        while self.fixed_form.rowCount(): self.fixed_form.removeRow(0)
        self._controls={}
        if self.space:
            for variable in self.space.variables:
                if variable.id in (self.xaxis.currentData(),self.yaxis.currentData()) or not hasattr(variable,'initial'): continue
                if hasattr(variable,'choices') or hasattr(variable,'values'):
                    widget=QComboBox()
                    for value in getattr(variable,'choices',getattr(variable,'values',())): widget.addItem(str(value),value)
                    widget.setCurrentIndex(max(0,widget.findData(self._fixed_values.get(variable.id,variable.initial)))); widget.currentIndexChanged.connect(self.schedule)
                else:
                    widget=QDoubleSpinBox(); widget.setDecimals(8); widget.setRange(variable.lower,variable.upper); widget.setValue(self._fixed_values.get(variable.id,variable.initial))
                    widget.setKeyboardTracking(False); widget.valueChanged.connect(self.schedule)
                widget.setAccessibleName(f'Fixed input {variable.name}'); self._controls[variable.id]=widget
                self.fixed_form.addRow(f'{variable.id} [{getattr(variable,"unit","—")}]',widget)
        self.schedule()

    def schedule(self,*args):
        self._token+=1
        if self._cancel: self._cancel.set()
        self.timer.start()

    def _dispatch(self):
        if not self.artifact:
            self._pending=None
            self.empty('Train and validate a surrogate to view a response slice.'); self.native.clear(); return
        xid,yid=self.xaxis.currentData(),self.yaxis.currentData()
        if not xid or not yid or xid==yid:
            self.empty('A 3D slice requires two different continuous variables.'); self.native.clear(); return
        fixed={v.id:v.initial for v in self.space.variables if hasattr(v,'initial')}
        for key,widget in self._controls.items(): fixed[key]=widget.currentData() if isinstance(widget,QComboBox) else widget.value()
        request=(self._token,self.artifact,self.space,xid,yid,self.response.currentData(),fixed)
        if self._running:
            self._pending=request
            if self._cancel: self._cancel.set()
            return
        self._start_task(request)

    def _start_task(self,request):
        self._running=True; self._running_token=request[0]; self._cancel=Event()
        task=SliceTask(*request,self._cancel)
        task.signals.done.connect(self._receive); self.note.setText('Computing surrogate slice… solver is not called.')
        QThreadPool.globalInstance().start(task)

    def _receive(self,token,result,error):
        if token!=self._running_token: return
        self._running=False; self._running_token=None
        if token==self._token and error and error!='cancelled':
            self.empty('Slice prediction unavailable'); self.note.setText(error); return
        if token==self._token and not error:
            self._result=result; self._draw_result()
        pending,self._pending=self._pending,None
        if pending is not None and pending[0]==self._token: self._start_task(pending)

    def _draw_result(self,*args):
        if self._result is None: return
        x,y,z,sigma=self._result; self.native.clear(); self.ax.clear()
        if self.native.available:
            self.native.surface(x,y,z)
            if self.uncertainty.isChecked() and sigma is not None:
                self.native.surface(x,y,z-1.96*sigma,(.5,.4,.8),.15); self.native.surface(x,y,z+1.96*sigma,(.5,.4,.8),.15)
            self.native.finish((self.xaxis.currentText(),self.yaxis.currentText(),self.response.currentText()))
        else:
            for i in np.linspace(0,len(y)-1,5,dtype=int): self.ax.plot(x[i],z[i],label=f'{self.yaxis.currentData()}={y[i,0]:.4g}')
            self.ax.set_xlabel(self.xaxis.currentText()); self.ax.set_ylabel(self.response.currentText()); self.ax.legend(fontsize=8)
            self.ax.set_title('2D slice fallback — five fixed Y values'); self._apply_theme(); self.fig.tight_layout(); self.canvas.draw_idle()
        metrics='; '.join(f'{m.response_id}: holdout RMSE {m.rmse:.4g} {m.unit}' for m in self.artifact.metrics)
        self.note.setToolTip(self.artifact.id)
        self.note.setText(f'Saved {self.artifact.config.method.upper()} model · study revision {self.artifact.dataset.training_revision}; {"qualified" if self.artifact.qualified else "not qualified"}. {metrics}\nPrediction only. Actual samples are not overlaid across unequal fixed inputs. 32 × 32 grid; uncertainty describes the model.')

    def plot_kriging_slice(self,surrogate,records,var_id,resp_id,lower,upper,fixed_values=None):
        # Compatibility for callers rendering one-dimensional studies.
        self.ax.clear(); self._apply_theme()
        if surrogate is None: self.empty('Train a surrogate first.'); return
        values=np.linspace(lower,upper,100); order=surrogate.dataset.feature_order
        inputs=np.column_stack([values if key==var_id else np.full(100,(fixed_values or {})[key]) for key in order])
        prediction=predict(surrogate,inputs); index=prediction.response_ids.index(resp_id)
        self.ax.plot(values,prediction.mean[:,index],label='GP prediction'); self.ax.legend(); self.canvas.draw_idle()

    def closeEvent(self,event):
        self._token+=1; self.timer.stop(); self._pending=None
        if self._cancel: self._cancel.set()
        self.native.close(); super().closeEvent(event)
