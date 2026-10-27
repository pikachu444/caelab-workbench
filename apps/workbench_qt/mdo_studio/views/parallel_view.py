"""Parallel coordinates with declared-domain scales and full-data axis brushing."""
from __future__ import annotations
import numpy as np
from PySide6.QtWidgets import QHBoxLayout, QComboBox, QPushButton, QLabel
from matplotlib.collections import LineCollection
from .plot_base import PlotWidget


class ParallelCoordinatesWidget(PlotWidget):
    def __init__(self,parent=None,is_dark=True):
        super().__init__(parent,is_dark)
        row=QHBoxLayout(); self.axis=QComboBox(); row.addWidget(QLabel('Axis')); row.addWidget(self.axis,1)
        for label,callback in [('Move left',lambda:self._move(-1)),('Move right',lambda:self._move(1)),('Reset brushes',self.reset_brushes)]:
            button=QPushButton(label); button.clicked.connect(callback); row.addWidget(button)
        self.controls.addLayout(row)
        self.records=[]; self.all_records=[]; self.variables=[]; self.response_ids=[]; self.units={}; self.order=[]; self.brushes={}; self._press=None
        self.canvas.mpl_connect('button_press_event',self._start_brush)
        self.canvas.mpl_connect('button_release_event',self._end_brush)

    def set_study(self,records,variables,response_ids,units,all_records=None):
        self.records=list(records); self.variables=list(variables); self.response_ids=list(response_ids); self.units=dict(units)
        self.all_records=list(all_records) if all_records is not None else self.records
        keys=[v.id for v in variables]+list(response_ids)
        self.order=[k for k in self.order if k in keys]+[k for k in keys if k not in self.order]
        old=self.axis.currentText(); self.axis.clear(); self.axis.addItems(self.order); self.axis.setCurrentText(old)
        self.refresh()

    def _move(self,direction):
        index=self.axis.currentIndex(); other=index+direction
        if 0<=index<len(self.order) and 0<=other<len(self.order):
            key=self.order[index]; self.order[index],self.order[other]=self.order[other],self.order[index]
            self.axis.clear(); self.axis.addItems(self.order); self.axis.setCurrentText(key); self.refresh()

    def reset_brushes(self):
        self.brushes.clear(); self.filter_changed.emit(None); self.refresh()

    def _start_brush(self,event):
        if event.inaxes==self.ax and event.xdata is not None and event.ydata is not None and not self.canvas.toolbar.mode:
            axis=round(event.xdata)
            if 0<=axis<len(self.order): self._press=(axis,event.ydata)

    def _end_brush(self,event):
        if self._press and event.inaxes==self.ax and event.ydata is not None:
            axis,start=self._press; self.brushes[self.order[axis]]=tuple(sorted((start,event.ydata)))
            ids=[r.point_id for r,values in self._normalized(records=self.all_records) if all(lo<=values[self.order.index(key)]<=hi for key,(lo,hi) in self.brushes.items())]
            self.filter_changed.emit(ids); self.refresh()
        self._press=None

    def _scales(self):
        scales={}
        for key in self.order:
            variable=next((v for v in self.variables if v.id==key),None)
            if variable and hasattr(variable,'choices'):
                scales[key]=('category',variable.choices)
            elif variable and hasattr(variable,'values'):
                scales[key]=('discrete',variable.values)
            elif variable and hasattr(variable,'lower') and variable.lower is not None:
                scales[key]=('log' if getattr(variable,'transform','linear')=='log' else 'linear',(variable.lower,variable.upper))
            else:
                values=[r.observations[key] for r in self.all_records if key in r.observations]
                scales[key]=('linear',(min(values),max(values))) if values else ('linear',(0.,1.))
        return scales

    def _normalized(self,scales=None,records=None):
        scales=scales or self._scales(); result=[]
        for record in self.records if records is None else records:
            values=[]
            for key in self.order:
                value=record.physical_values.get(key,record.observations.get(key)); kind,domain=scales[key]
                if value is None: break
                if kind in ('category','discrete'):
                    if value not in domain: break
                    values.append(domain.index(value)/max(1,len(domain)-1))
                else:
                    lo,hi=domain
                    if kind=='log': value,lo,hi=np.log([value,lo,hi])
                    values.append((value-lo)/(hi-lo) if hi!=lo else .5)
            if len(values)==len(self.order): result.append((record,values))
        return result

    def set_selected(self,point_ids):
        super().set_selected(point_ids)

    def refresh(self):
        self.ax.clear(); self._picks.clear()
        if len(self.order)<2: self.empty('Declare at least two variables / responses.'); return
        scales=self._scales(); normalized=self._normalized(scales)
        keep=[row for row in normalized if row[0].point_id in self._selected or row[0].is_pareto]
        kept_ids={row[0].point_id for row in keep}
        rest=[row for row in normalized if row[0].point_id not in kept_ids]
        rows=keep+rest[::max(1,int(np.ceil(len(rest)/1500)))]
        segments=[list(zip(range(len(self.order)),values)) for _,values in rows]
        if segments:
            colors=['#38bdf8' if r.point_id in self._selected else '#a78bfa' if getattr(r,'mode','ACTUAL')=='SURROGATE' else '#f59e0b' if r.feasibility=='MARGINAL' else '#64748b' for r,_ in rows]
            lines=LineCollection(segments,colors=colors,linewidths=[2.5 if r.point_id in self._selected else .7 for r,_ in rows],alpha=.7,picker=5)
            self.ax.add_collection(lines); self._picks[lines]=[r.point_id for r,_ in rows]
        for i,key in enumerate(self.order):
            self.ax.axvline(i,color='#64748b',alpha=.5)
            kind,domain=scales[key]
            if kind in ('category','discrete'):
                for j,value in enumerate(domain): self.ax.text(i,j/max(1,len(domain)-1),str(value),fontsize=7,color='#94a3b8')
            else:
                for value,y in zip(domain,(0,1)): self.ax.text(i,y,f'{value:.4g}',fontsize=7,color='#94a3b8')
            if key in self.brushes:
                lo,hi=self.brushes[key]; self.ax.plot([i,i],[lo,hi],lw=8,color='#38bdf8',alpha=.5)
        self.ax.set_xlim(-.15,len(self.order)-.85); self.ax.set_ylim(-.08,1.08)
        self.ax.set_xticks(range(len(self.order)),[f'{k}\n[{self.units.get(k,"—")}]' for k in self.order],fontsize=8)
        self.ax.set_yticks([]); self.ax.set_title('Parallel coordinates — drag vertically on an axis to filter')
        self._apply_theme(); self.fig.tight_layout(); self.canvas.draw_idle()
        self.note.setText(f'Rendered {len(rows)} / {len(normalized)} complete records. Brushes filter full data; categories are labeled bands, not physical distances. {len(self.brushes)} active brushes.')
