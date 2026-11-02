"""Project persistence.

A project is a folder holding ``model.json`` plus geometry/mesh files.
Explicit saves prepare an owned ``runs/`` area and record its expectation
outside the model schema. Opening a legacy project creates nothing.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path

from .schema import StructuralModel
from ..native.run_store import RunStore, atomic_json

MODEL_FILENAME = "model.json"


def _read_document(path: Path) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError(f'모델 프로젝트 객체가 필요합니다: {path}')
    expected = data.get('run_store_expected', False)
    if type(expected) is not bool:
        raise ValueError('run_store_expected는 bool이어야 합니다.')
    if expected and not (path.parent / 'runs').is_dir():
        raise ValueError(f'기대 실행 저장소가 없습니다: {path.parent / "runs"}')
    return data


def run_store_expected(folder: str | Path) -> bool:
    """Read persisted storage state without adding it to StructuralModel."""
    return _read_document(Path(folder) / MODEL_FILENAME).get('run_store_expected', False)


def model_revision(model: StructuralModel) -> str:
    data = json.dumps(model.to_dict(), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(data).hexdigest()


def project_revision(folder: str | Path) -> str:
    try:
        return model_revision(load_project(folder))
    except FileNotFoundError:
        return ''


def save_project(model: StructuralModel, folder: str | Path, *, expected_revision: str | None = None) -> Path:
    folder = Path(folder)
    path = folder / MODEL_FILENAME
    try:
        previous = _read_document(path)
    except FileNotFoundError:
        previous = {}
    expected = previous.get('run_store_expected', False)
    document = {**model.to_dict(), 'run_store_expected': True}
    def check_revision():
        if expected_revision is not None and project_revision(folder) != expected_revision:
            raise ValueError('다른 창에서 모델이 변경되었습니다. 현재 변경은 유지했습니다. 저장된 모델을 다시 열어 확인하세요.')
    check_revision()
    folder.mkdir(parents=True, exist_ok=True)
    lease = RunStore(folder / 'runs', expected=expected).lease()
    try:
        check_revision()
        atomic_json(path, document)
    finally:
        lease.release()
    return path


def load_project(folder: str | Path) -> StructuralModel:
    folder = Path(folder)
    path = folder / MODEL_FILENAME if folder.is_dir() else folder
    data = _read_document(path)
    return StructuralModel.from_dict(data)
