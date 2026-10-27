"""Point-ID linked Pareto projections; dominance comes only from the full study."""
from __future__ import annotations
import numpy as np
from PySide6.QtWidgets import QHBoxLayout, QComboBox, QLabel, QStackedWidget
from .plot_base import PlotWidget, Native3D


class ParetoFrontWidget(PlotWidget):
    def __init__(self, parent=None, is_dark=True):
        super().__init__(parent, is_dark)
        row=QHBoxLayout()
        self.dimension=QComboBox(); self.dimension.addItems(['2D', '3D'])
        row.addWidget(self.dimension)
        self.axes=[]
        for label in ('X','Y','Z'):
            row.addWidget(QLabel(label)); combo=QComboBox(); row.addWidget(combo,1); self.axes.append(combo)
            combo.currentIndexChanged.connect(self.refresh)
        self.controls.addLayout(row)
        self.native=Native3D(self)
        self.layout.insertWidget(self.layout.indexOf(self.canvas)+1, self.native, 1)
        self.native.hide()
        self.native.point_selected.connect(self.point_selected)
        self.dimension.currentIndexChanged.connect(self.refresh)
        self.records=[]; self.objectives=[]; self.units={}

    def set_study(self, records, objectives, units):
        self.records=list(records); self.objectives=list(objectives); self.units=dict(units)
        keys=[o.response_id for o in objectives]
        for i, combo in enumerate(self.axes):
            old=combo.currentData(); combo.blockSignals(True); combo.clear()
            for o in objectives: combo.addItem(f'{o.response_id} [{units.get(o.response_id,"—")}] ({o.sense})',o.response_id)
            combo.setCurrentIndex(keys.index(old) if old in keys else min(i,len(keys)-1))
            combo.blockSignals(False)
        self.refresh()

    def plot_study(self, records, obj_x_id, obj_y_id, obj_x_name=None, obj_y_name=None):
        from types import SimpleNamespace
        self.set_study(records,[SimpleNamespace(response_id=obj_x_id,sense='MIN'),SimpleNamespace(response_id=obj_y_id,sense='MIN')],{})
        if obj_x_name: self.ax.set_xlabel(obj_x_name)
        if obj_y_name: self.ax.set_ylabel(obj_y_name)

    def set_selected(self, point_ids):
        super().set_selected(point_ids)

    def refresh(self, *args):
        if not hasattr(self,'records'): return
        use3=self.dimension.currentText()=='3D'
        self.native.setVisible(use3 and self.native.available)
        self.canvas.setVisible(not use3 or not self.native.available)
        keys=[c.currentData() for c in self.axes[:3 if use3 else 2]]
        if len(set(keys)) != len(keys) or any(k is None for k in keys):
            self.empty('Choose two / three different objective responses.'); return
        rows=[r for r in self.records if r.status in ('COMPLETED','PREDICTED') and all(k in r.observations for k in keys)]
        self.ax.clear(); self._picks.clear(); self.native.clear()
        if not rows:
            self.empty('Run native evaluations or search a validated surrogate.'); return
        # Keep the entire full-objective front and selected points when decimating.
        keep=[r for r in rows if r.is_pareto or r.point_id in self._selected]
        rest=[r for r in rows if not r.is_pareto and r.point_id not in self._selected]
        stride=max(1,int(np.ceil(len(rest)/3000)))
        rendered=keep+rest[::stride]
        layers=[('Actual feasible','#22c55e','o',lambda r:getattr(r,'mode','ACTUAL')!='SURROGATE' and r.feasibility=='FEASIBLE'),
                ('Actual marginal','#f59e0b','^',lambda r:getattr(r,'mode','ACTUAL')!='SURROGATE' and r.feasibility=='MARGINAL'),
                ('Actual infeasible / unassessed','#ef4444','x',lambda r:getattr(r,'mode','ACTUAL')!='SURROGATE' and r.feasibility not in ('FEASIBLE','MARGINAL')),
                ('Surrogate prediction','#a78bfa','D',lambda r:getattr(r,'mode','ACTUAL')=='SURROGATE')]
        from matplotlib.colors import to_rgb
        for label,color,marker,predicate in layers:
            group=[r for r in rendered if predicate(r)]
            if not group: continue
            values=np.array([[r.observations[k] for k in keys] for r in group])
            artist=self.ax.scatter(values[:,0],values[:,1],c=color,marker=marker,s=35,alpha=.7,label=label,picker=6)
            self._picks[artist]=[r.point_id for r in group]
            if use3: self.native.points(values,[r.point_id for r in group],to_rgb(color))
        for label,group,color,size in [('Full-objective feasible front',[r for r in rendered if r.is_pareto and getattr(r,'mode','ACTUAL')!='SURROGATE'],'#38bdf8',65),('Selected',[r for r in rendered if r.point_id in self._selected],'#ffffff' if self.is_dark else '#0f172a',130)]:
            if group:
                values=np.array([[r.observations[k] for k in keys] for r in group])
                artist=self.ax.scatter(values[:,0],values[:,1],facecolors='none',edgecolors=color,s=size,linewidths=2,label=label,picker=6)
                self._picks[artist]=[r.point_id for r in group]
                if use3: self.native.points(values,[r.point_id for r in group],to_rgb(color),12)
        labels=[self.axes[i].currentText() for i in range(len(keys))]
        self.ax.set_xlabel(labels[0]); self.ax.set_ylabel(labels[1]); self.ax.set_title('Sampled Pareto projection')
        self.ax.legend(fontsize=8); self._apply_theme(); self.fig.tight_layout(); self.canvas.draw_idle()
        if use3: self.native.finish(labels)
        self.note.setText(f'Rendered {len(rendered)} / {len(rows)} points. Full-objective front retained; this projection does not recompute dominance.'+(' 2D fallback; native VTK unavailable.' if use3 and not self.native.available else ''))
