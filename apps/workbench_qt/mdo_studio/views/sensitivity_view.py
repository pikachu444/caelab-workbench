"""Source-labeled sensitivity estimates and intervals from frozen experiments."""
from dataclasses import dataclass
import json
import numpy as np
from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QHBoxLayout,QGridLayout,QComboBox,QPushButton,QCheckBox,QDialog,QVBoxLayout,
    QPlainTextEdit,QDialogButtonBox,QFormLayout,QSpinBox,QDoubleSpinBox,QLabel)
from caelab.mdo.sensitivity import SensitivityConfig
from .plot_base import PlotWidget


@dataclass(frozen=True,slots=True)
class TornadoItem:
    variable_id:str
    metric:str
    magnitude:float
    interval:tuple[float,float]|None=None
    sign:int=1


class SensitivityTornadoWidget(PlotWidget):
    response_selected=Signal(str)
    analysis_requested=Signal()
    experiment_selected=Signal(str)

    def __init__(self,parent=None,is_dark=True):
        super().__init__(parent,is_dark)
        row=QHBoxLayout(); self.response=QComboBox(); self.method_selector=QComboBox()
        self.method_selector.addItem('Spearman — actual rows','spearman'); self.method_selector.addItem('Sobol — surrogate experiment','sobol'); self.method_selector.addItem('Morris — surrogate experiment','morris')
        for combo in (self.response,self.method_selector):
            combo.setMinimumContentsLength(4); combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon); row.addWidget(combo,1)
        self.controls.addLayout(row)
        self.experiment=QComboBox(); self.experiment.addItem('New / current analysis',None); self.controls.addWidget(self.experiment)
        self.experiment.setMinimumContentsLength(6); self.experiment.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.experiment.currentIndexChanged.connect(lambda *args:self.experiment_selected.emit(self.experiment.currentData() or ''))
        row=QGridLayout(); self.metric_selector=QComboBox(); row.addWidget(self.metric_selector,0,0,1,2)
        self.intervals=QCheckBox('Intervals'); self.intervals.setChecked(True); row.addWidget(self.intervals,0,2)
        settings=QPushButton('Experiment settings…'); settings.clicked.connect(self._settings); row.addWidget(settings,1,0)
        inspect=QPushButton('Inspect experiment'); inspect.clicked.connect(self._inspect); row.addWidget(inspect,1,1)
        analyze=QPushButton('Analyze'); analyze.clicked.connect(self.analysis_requested); row.addWidget(analyze,1,2)
        self.controls.addLayout(row)
        self.config=SensitivityConfig(base_samples=128,trajectories=10,resamples=200,max_evaluations=10000)
        self.report=None
        self.response.currentIndexChanged.connect(self._request); self.method_selector.currentIndexChanged.connect(self._request)
        self.metric_selector.currentIndexChanged.connect(self._render_report); self.intervals.toggled.connect(self._render_report)

    def _request(self,*args):
        self.report=None; self.experiment.blockSignals(True); self.experiment.setCurrentIndex(0); self.experiment.blockSignals(False)
        self.empty('Choose experiment settings, then Analyze.'); self.note.setText('Spearman uses actual pairs; Sobol/Morris use the complete surrogate experiment and make no native calls.')

    def set_responses(self,keys,units):
        previous=self.response.currentData(); self.response.blockSignals(True); self.response.clear()
        for key in keys: self.response.addItem(f'{key} [{units.get(key,"—")}]',key)
        self.response.setCurrentIndex(max(0,self.response.findData(previous))); self.response.blockSignals(False)

    def set_experiments(self,experiments):
        previous=self.experiment.currentData(); self.experiment.blockSignals(True); self.experiment.clear(); self.experiment.addItem('New / current analysis',None)
        for i,item in enumerate(experiments):
            self.experiment.addItem(f'{i+1}: {item["method"].upper()} · {item["response_id"]} · revision {item["revision"]}',item['id'])
        self.experiment.setCurrentIndex(max(0,self.experiment.findData(previous))); self.experiment.blockSignals(False)

    def _settings(self):
        dialog=QDialog(self); dialog.setWindowTitle('Paired surrogate sensitivity experiment'); layout=QVBoxLayout(dialog); form=QFormLayout(); layout.addLayout(form); widgets={}
        for name,minimum,maximum in [('base_samples',2,65536),('trajectories',2,10000),('levels',4,100),('seed',0,2147483647),('resamples',2,10000),('max_evaluations',4,1000000)]:
            widget=QSpinBox(); widget.setRange(minimum,maximum); widget.setValue(getattr(self.config,name)); widgets[name]=widget; form.addRow(name.replace('_',' ').title(),widget)
        confidence=QDoubleSpinBox(); confidence.setRange(.5,.999); confidence.setDecimals(3); confidence.setValue(self.config.confidence); form.addRow('Confidence',confidence)
        label=QLabel('Sobol/Morris use a full paired experiment on independent physical-uniform continuous factors at the saved surrogate model. They do not use the visual table filter and make no native solver calls.'); label.setWordWrap(True); layout.addWidget(label)
        error=QLabel(); error.setWordWrap(True); error.setStyleSheet('color:#ef4444'); layout.addWidget(error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); layout.addWidget(buttons); buttons.rejected.connect(dialog.reject)
        def accept():
            try: config=SensitivityConfig(method=self.method_selector.currentData() if self.method_selector.currentData()!='spearman' else 'sobol',confidence=confidence.value(),**{key:widget.value() for key,widget in widgets.items()})
            except ValueError as exc: error.setText(str(exc)); return
            self.config=config; dialog.accept()
        buttons.accepted.connect(accept)
        if dialog.exec()==QDialog.DialogCode.Accepted: self._request()

    def _inspect(self):
        dialog=QDialog(self); dialog.setWindowTitle('Sensitivity experiment and sources'); dialog.resize(800,600); layout=QVBoxLayout(dialog)
        text=QPlainTextEdit(); text.setReadOnly(True); text.setPlainText(json.dumps(self.report or {},ensure_ascii=False,indent=2)); layout.addWidget(text)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close); buttons.rejected.connect(dialog.reject); layout.addWidget(buttons); dialog.exec()

    def set_report(self,report):
        self.report=report; previous=self.metric_selector.currentText(); self.metric_selector.blockSignals(True); self.metric_selector.clear()
        self.response.blockSignals(True); self.response.setCurrentIndex(max(0,self.response.findData(report['response_id']))); self.response.blockSignals(False)
        self.method_selector.blockSignals(True); self.method_selector.setCurrentIndex(max(0,self.method_selector.findData(report['method']))); self.method_selector.blockSignals(False)
        metrics=[key for key in report.get('estimates',{}) if key not in ('pvalue','sample_count','missing_count') and not key.endswith('_conf')]
        self.metric_selector.addItems(metrics); self.metric_selector.setCurrentText(previous if previous in metrics else metrics[0] if metrics else '')
        self.metric_selector.blockSignals(False); self._render_report()

    def _render_report(self,*args):
        if not self.report: return
        report=self.report; metric=self.metric_selector.currentText(); values=report.get('estimates',{}).get(metric,[])
        halfwidth=report.get('estimates',{}).get(metric+'_conf',[]); items=[]
        for i,(key,value) in enumerate(zip(report.get('variable_ids',[]),values)):
            if value is None or not np.isfinite(value): continue
            interval=(value-halfwidth[i],value+halfwidth[i]) if self.intervals.isChecked() and i<len(halfwidth) and halfwidth[i] is not None else None
            items.append(TornadoItem(key,metric,abs(value),interval,1 if value>=0 else -1))
        self.plot_tornado(items,f'{report.get("response_id")} [{report.get("unit","—")}]')
        source=report.get('source_mode','unknown'); paired=report.get('method') in ('sobol','morris')
        self.note.setText(f'{source} · study revision {report.get("revision")} · n={report.get("sample_count")} · seed {report.get("seed")} · native calls {report.get("native_calls",0)}. '+
            (f'Full paired experiment, independent physical-uniform continuous factors; visual filter is not applied. Model qualified: {report.get("qualified")}; RMSE limits {report.get("qualification_limits",[])}. ' if paired else 'Actual paired ranks from the explicitly filtered population; undefined pairs remain absent. ')+
            ' '.join(report.get('warnings',[])))
        if self.experiment.currentData(): self.note.setText(self.note.text()+' Frozen saved experiment; the current visual filter is not applied.')
        self.note.setToolTip(f'Model: {report.get("model_id")} · experiment: {report.get("experiment_id")}')

    def plot_tornado(self,items,target_name='Response'):
        self.ax.clear(); self._apply_theme()
        if not items: self.empty('Insufficient valid paired data; inspect the experiment or run more native rows.'); return
        items=sorted(items,key=lambda item:abs(item.magnitude)); values=[item.magnitude*item.sign for item in items]; positions=np.arange(len(items))
        self.ax.barh(positions,values,color='#38bdf8',height=.55)
        for i,(item,value) in enumerate(zip(items,values)):
            if item.interval: self.ax.errorbar(value,i,xerr=[[max(0,value-item.interval[0])],[max(0,item.interval[1]-value)]],fmt='none',ecolor='#94a3b8',capsize=3)
        self.ax.set_yticks(positions,[item.variable_id for item in items]); self.ax.axvline(0,color='#64748b',lw=.8)
        self.ax.set_xlabel(items[0].metric); self.ax.set_title(f'Sensitivity — {target_name}'); self._apply_theme(); self.fig.tight_layout(); self.canvas.draw_idle()
