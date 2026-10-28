"""Scalar TDP snapshots, stable identities and typed sorting/filtering roles."""
from __future__ import annotations
from copy import copy,deepcopy
from PySide6.QtCore import QAbstractTableModel,QAbstractProxyModel,QModelIndex,Qt
from PySide6.QtGui import QColor

_DISPLAY=int(Qt.ItemDataRole.DisplayRole)
_TOOLTIP=int(Qt.ItemDataRole.ToolTipRole)
_ALIGN=int(Qt.ItemDataRole.TextAlignmentRole)
_FOREGROUND=int(Qt.ItemDataRole.ForegroundRole)
_LEFT=int(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
_RIGHT=int(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
_ITEM_FLAGS=Qt.ItemFlag.ItemIsSelectable|Qt.ItemFlag.ItemIsEnabled
_NO_FLAGS=Qt.ItemFlag.NoItemFlags
_POINT_ID=int(Qt.ItemDataRole.UserRole)+1
_SORT=_POINT_ID+1
_SCALAR_ROLES=frozenset((_DISPLAY,_TOOLTIP,_ALIGN,_FOREGROUND,_POINT_ID,_SORT))
_INVALID_INDEX=QModelIndex()


class DesignPointsTableModel(QAbstractTableModel):
    PointIdRole = _POINT_ID
    SortRole = _SORT
    ParameterStart=9

    def __init__(self, records=None, parameter_keys=(), response_keys=(), is_dark=True, parent=None):
        super().__init__(parent)
        self.is_dark = is_dark
        self.records = []
        self._row_map = {}
        self.units = {}
        self.current_point_id = None
        self._cells=[]; self._sort_keys=[]; self._filter_data=[]
        self._colors={key:QColor(value) for key,value in {"FEASIBLE":"#16a34a","MARGINAL":"#d97706","INFEASIBLE":"#ef4444"}.items()}
        self.set_study_data(records or [], parameter_keys, response_keys)

    def set_theme(self, is_dark):
        self.is_dark = is_dark
        if self.records:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.records)-1, self.columnCount()-1))

    def set_study_data(self, records, parameter_keys, response_keys, *, units=None, current_point_id=None):
        self.beginResetModel()
        self.records = [self._snapshot(record) for record in records]
        self.parameter_keys, self.response_keys = list(parameter_keys), list(response_keys)
        self.units, self.current_point_id = dict(units or {}), current_point_id
        self._row_map = {r.point_id: i for i, r in enumerate(self.records)}
        self.headers = ["Point", "Name", "Revision", "Status", "Feasibility", "Pareto", "Data", "Attempts", "Current"]
        self.headers += [f"{k} [{self.units.get(k, '—')}]" for k in self.parameter_keys + self.response_keys]
        self.headers += ["Violation"]
        self._cells=[self._values(rec) for rec in self.records]
        self._sort_keys=[self._row_sort_keys(rec,row) for rec,row in zip(self.records,self._cells)]
        self._filter_data=[self._filter_record(rec) for rec in self.records]
        self.endResetModel()

    @staticmethod
    def _snapshot(record):
        result=copy(record)
        result.physical_values=dict(record.physical_values)
        result.observations=dict(record.observations)
        result.objectives=dict(record.objectives)
        attempts=getattr(record,'attempts',())
        # Full native file/hash histories are fetched only by the explicit attempt inspector.
        result.attempts=tuple({key:deepcopy(value) for key,value in attempt.items() if key in
            ('run_id','status','feasibility','total_violation','objectives','responses','duration_sec','finished_at','sequence')}
            for attempt in attempts) if isinstance(attempts,(list,tuple)) else attempts
        return result

    @staticmethod
    def _sort_key(value):
        return (0,0) if value is None else (1,value) if isinstance(value,(int,float)) else (2,str(value).casefold())

    @staticmethod
    def _filter_record(rec):
        return ((rec.name+' '+rec.point_id).casefold(),rec.status,rec.feasibility,rec.point_id,getattr(rec,'revision','1'))

    def _row_sort_keys(self,rec,values):
        return ((1,rec.index),*(self._sort_key(value) for value in values[1:]))

    def _values(self,rec):
        attempts=getattr(rec,'attempts',())
        return (f'DP_{rec.index:04d}',rec.name,getattr(rec,'revision','1'),rec.status,rec.feasibility or 'NOT_ASSESSED',
                'Front' if rec.is_pareto else '',getattr(rec,'mode','ACTUAL'),
                len(attempts) if isinstance(attempts,(list,tuple)) else attempts,
                'Current' if rec.point_id==self.current_point_id else '',
                *(rec.physical_values.get(key) for key in self.parameter_keys),
                *(rec.observations.get(key) for key in self.response_keys),
                None if rec.feasibility in (None,'NOT_ASSESSED') else rec.total_violation)

    def _cache_row(self,row):
        rec=self.records[row]; values=self._values(rec)
        self._cells[row]=values; self._sort_keys[row]=self._row_sort_keys(rec,values)
        self._filter_data[row]=self._filter_record(rec)

    def row_for_id(self, point_id):
        return self._row_map.get(point_id)

    def record(self, point_id):
        row = self.row_for_id(point_id)
        return self.records[row] if row is not None else None

    def update_record(self, record):
        row = self._row_map.get(record.point_id)
        if row is None:
            row = len(self.records)
            self.beginInsertRows(QModelIndex(), row, row)
            self._row_map[record.point_id] = row
            rec=self._snapshot(record); self.records.append(rec)
            values=self._values(rec); self._cells.append(values)
            self._sort_keys.append(self._row_sort_keys(rec,values)); self._filter_data.append(self._filter_record(rec))
            self.endInsertRows()
        else:
            self.records[row] = self._snapshot(record)
            self._cache_row(row)
            self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount()-1))

    def update_point_result(self, point_id, status, observations=None, feasibility=None,
                            total_violation=0., duration_sec=0., is_pareto=False):
        rec = self.record(point_id)
        if rec is None: return
        rec = deepcopy(rec)
        rec.status = status
        if observations is not None: rec.observations = dict(observations)
        if feasibility is not None: rec.feasibility = feasibility
        rec.total_violation, rec.duration_sec, rec.is_pareto = total_violation, duration_sec, is_pareto
        self.update_record(rec)

    def update_pareto_front(self, pareto_ids):
        ids = set(pareto_ids)
        for row,rec in enumerate(self.records):
            rec.is_pareto = rec.point_id in ids
            self._cache_row(row)
        if self.records:
            self.dataChanged.emit(self.index(0, 5), self.index(len(self.records)-1, 5))

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.headers):
            return self.headers[section]

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if role not in _SCALAR_ROLES: return None
        if not index.isValid() or not 0 <= index.row() < len(self.records): return None
        return self._data_at(index.row(),index.column(),role)

    def _data_at(self,row,column,role):
        if role == self.PointIdRole: return self.records[row].point_id
        if role == _FOREGROUND: return self._colors.get(self.records[row].feasibility) if column==4 else None
        value = self._cells[row][column]
        if role == self.SortRole: return self.records[row].index if column==0 else value
        if role == _DISPLAY:
            return "—" if value is None or value == "" else f"{value:.6g}" if isinstance(value, float) else str(value)
        if role == _TOOLTIP:
            return self.records[row].error_message or "No valid response for this point." if value is None else str(value)
        if role == _ALIGN: return _RIGHT if isinstance(value,(int,float)) else _LEFT


class DesignPointsProxyModel(QAbstractProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text, self.status, self.feasibility, self.point_ids, self.revision = "", "All", "All", None, None
        self._rows=[]; self._proxy_rows={}; self._column=0; self._descending=False; self._source=None
        self._column_count=0; self._last_sort_keys=[]

    def setSourceModel(self,model):
        self.beginResetModel(); self._source=model; self._column_count=model.columnCount()
        super().setSourceModel(model)
        self._rows=list(range(model.rowCount())); self._proxy_rows={r:r for r in self._rows}
        self.endResetModel()
        model.modelReset.connect(self._reset_source)
        model.rowsInserted.connect(self._reset_source)
        model.dataChanged.connect(self._source_changed)
        self._rebuild()

    def set_filters(self, *, text=None, status=None, feasibility=None, point_ids=...,revision=...):
        if text is not None: self.text = text.casefold()
        if status is not None: self.status = status
        if feasibility is not None: self.feasibility = feasibility
        if point_ids is not ...: self.point_ids = None if point_ids is None else set(point_ids)
        if revision is not ...: self.revision=revision
        self._rebuild()

    def _mapping(self):
        if self._source is None: return []
        self._column_count=self._source.columnCount()
        if self._column>=self._column_count: self._column=0
        data=self._source._filter_data; text=self.text; status=self.status; feasibility=self.feasibility; ids=self.point_ids; revision=self.revision
        if not text and status=='All' and feasibility=='All' and ids is None and revision is None:
            rows=list(range(len(data)))
        else:
            rows=[row for row,(name,state,feasible,point_id,rev) in enumerate(data)
                  if (not text or text in name) and (status=='All' or status==state)
                  and (feasibility=='All' or feasibility==feasible) and (ids is None or point_id in ids)
                  and (revision is None or revision==rev)]
        self._last_sort_keys=[values[self._column] for values in self._source._sort_keys] if self._column>=0 else []
        if self._column>=0: rows.sort(key=self._last_sort_keys.__getitem__,reverse=self._descending)
        return rows

    def _reset_source(self,*args):
        self.beginResetModel(); self._rows=self._mapping(); self._proxy_rows={source:row for row,source in enumerate(self._rows)}; self.endResetModel()

    def _rebuild(self):
        rows=self._mapping()
        if rows==self._rows: return
        if len(rows)!=len(self._rows) or set(rows)!=set(self._rows):
            self.beginResetModel(); self._rows=rows; self._proxy_rows={source:row for row,source in enumerate(rows)}; self.endResetModel()
        else:
            old=self.persistentIndexList(); sources=[self._rows[index.row()] for index in old]
            self.layoutAboutToBeChanged.emit()
            self._rows=rows; self._proxy_rows={source:row for row,source in enumerate(rows)}
            self.changePersistentIndexList(old,[self.index(self._proxy_rows[source],index.column()) for source,index in zip(sources,old)])
            self.layoutChanged.emit()

    def _source_changed(self,first,last,roles=()):
        changed=range(first.row(),last.row()+1)
        if any((row in self._proxy_rows)!=self.filterAcceptsRow(row,None)
               or (self._column>=0 and self._last_sort_keys[row]!=self._source._sort_keys[row][self._column]) for row in changed):
            self._rebuild()
        for source_row in range(first.row(),last.row()+1):
            row=self._proxy_rows.get(source_row)
            if row is not None: self.dataChanged.emit(self.index(row,first.column()),self.index(row,last.column()),roles)

    def sort(self,column,order=Qt.SortOrder.AscendingOrder):
        self._column=column; self._descending=order==Qt.SortOrder.DescendingOrder; self._rebuild()

    def mapToSource(self,index):
        return self._source.index(self._rows[index.row()],index.column()) if index.isValid() and 0<=index.row()<len(self._rows) else QModelIndex()

    def mapFromSource(self,index):
        row=self._proxy_rows.get(index.row()) if index.isValid() else None
        return self.index(row,index.column()) if row is not None else QModelIndex()

    def index(self,row,column,parent=QModelIndex()):
        return self.createIndex(row,column) if not parent.isValid() and 0<=row<len(self._rows) and 0<=column<self._column_count else QModelIndex()

    def parent(self,index): return _INVALID_INDEX
    def rowCount(self,parent=QModelIndex()): return 0 if parent.isValid() else len(self._rows)
    def columnCount(self,parent=QModelIndex()): return 0 if parent.isValid() else self._column_count
    def headerData(self,section,orientation,role=Qt.ItemDataRole.DisplayRole):
        return self._source.headerData(section,orientation,role) if self._source else None
    def flags(self,index): return _ITEM_FLAGS if index.isValid() else _NO_FLAGS
    def data(self,index,role=Qt.ItemDataRole.DisplayRole):
        if role not in _SCALAR_ROLES or not index.isValid(): return None
        row=index.row()
        if not 0<=row<len(self._rows): return None
        return self._source._data_at(self._rows[row],index.column(),role)

    def multiData(self,index,role_data):
        if not index.isValid() or not 0<=index.row()<len(self._rows):
            for item in role_data: item.clearData()
            return
        row=self._rows[index.row()]; column=index.column(); source=self._source
        for item in role_data:
            role=item.role()
            value=source._data_at(row,column,role) if role in _SCALAR_ROLES else None
            if value is None: item.clearData()
            else: item.setData(value)

    def filterAcceptsRow(self, row, parent):
        text,status,feasibility,point_id,revision=self._source._filter_data[row]
        return ((not self.text or self.text in text)
                and (self.status == "All" or self.status == status)
                and (self.feasibility == "All" or self.feasibility == feasibility)
                and (self.revision is None or self.revision==revision)
                and (self.point_ids is None or point_id in self.point_ids))
