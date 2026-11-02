"""Project persistence.

A project is a folder holding ``model.json`` plus geometry/mesh files.
Explicit saves prepare an owned ``runs/`` area and record its expectation
outside the model schema. Opening a legacy project creates nothing.
"""

from __future__ import annotations

import json
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


def save_project(model: StructuralModel, folder: str | Path) -> Path:
    folder = Path(folder)
    path = folder / MODEL_FILENAME
    try:
        previous = _read_document(path)
    except FileNotFoundError:
        previous = {}
    expected = previous.get('run_store_expected', False)
    folder.mkdir(parents=True, exist_ok=True)
    lease = RunStore(folder / 'runs', expected=expected).lease()
    try:
        atomic_json(path, {**model.to_dict(), 'run_store_expected': True})
    finally:
        lease.release()
    return path


def load_project(folder: str | Path) -> StructuralModel:
    folder = Path(folder)
    path = folder / MODEL_FILENAME if folder.is_dir() else folder
    data = _read_document(path)
    return StructuralModel.from_dict(data)
