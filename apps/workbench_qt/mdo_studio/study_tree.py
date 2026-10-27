"""Study artifact navigation; selecting a node never applies or evaluates."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget,QTreeWidgetItem,QWidget,QVBoxLayout


class StudyTreeWidget(QWidget):
    node_selected=Signal(str,str)
    def __init__(self,parent=None):
        super().__init__(parent); layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        self.tree=QTreeWidget(); self.tree.setHeaderHidden(True); self.tree.setAccessibleName('Study artifacts')
        self.tree.currentItemChanged.connect(self._selected); layout.addWidget(self.tree)

    def populate(self,study,*,surrogate=None,current_point_id=None):
        selected=self.tree.currentItem().data(0,Qt.ItemDataRole.UserRole) if self.tree.currentItem() else None
        self.tree.blockSignals(True); self.tree.clear()
        root=QTreeWidgetItem(self.tree,[study.name]); root.setExpanded(True)
        def item(parent,text,kind,key=''):
            result=QTreeWidgetItem(parent,[text]); result.setToolTip(0,text); result.setData(0,Qt.ItemDataRole.UserRole,(kind,key)); return result
        item(root,'Inputs / scalar tokens','input')
        variables=item(root,f'Variables ({len(study.space.variables)})','variables')
        for v in study.space.variables: item(variables,f'{v.name} [{getattr(v,"unit","—")}]','variables',v.id)
        criteria=item(root,'Criteria / response selectors','criteria')
        for o in study.objectives: item(criteria,f'{o.response_id} — {o.sense}','criteria',o.id)
        for c in study.constraints: item(criteria,f'{c.response_id or c.id} — {c.kind}','criteria',c.id)
        item(root,'DOE plans','doe')
        campaign=item(root,f'Evaluations ({sum(r.status=="COMPLETED" for r in study.records)} completed)','evaluations')
        for state in ('RUNNING','FAILED','CANCELLED'):
            count=sum(r.status==state for r in study.records)
            if count: item(campaign,f'{state}: {count}','evaluations',state)
        item(root,'Surrogates — '+('validation available' if surrogate else 'needs training'),'surrogates')
        item(root,'Optimization runs / predicted candidates','optimization')
        item(root,'Sensitivity analyses','sensitivity')
        views=item(root,'Views','views')
        for key in ('Pareto','Parallel','Surface','Sensitivity','Convergence'): item(views,key,'view',key)
        item(root,'Saved selections / current design'+(f' — {current_point_id}' if current_point_id else ''),'selection')
        root.setExpanded(True); views.setExpanded(True)
        if selected:
            def restore(parent):
                for i in range(parent.childCount()):
                    child=parent.child(i)
                    if child.data(0,Qt.ItemDataRole.UserRole)==selected: self.tree.setCurrentItem(child)
                    restore(child)
            restore(root)
        self.tree.blockSignals(False)

    def _selected(self,item,previous=None):
        value=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        if value: self.node_selected.emit(*value)
