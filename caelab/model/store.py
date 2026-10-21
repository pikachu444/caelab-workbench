"""Project persistence.

A project is a folder holding ``model.json`` plus geometry/mesh files
and a ``runs/`` area created only when a job actually runs. Opening a
project never starts a solver or an LLM call.
"""

from __future__ import annotations

import json
from pathlib import Path

from .schema import StructuralModel

MODEL_FILENAME = "model.json"


def save_project(model: StructuralModel, folder: str | Path) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / MODEL_FILENAME
    path.write_text(
        json.dumps(model.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return path


def load_project(folder: str | Path) -> StructuralModel:
    folder = Path(folder)
    path = folder / MODEL_FILENAME if folder.is_dir() else folder
    data = json.loads(path.read_text(encoding="utf-8"))
    return StructuralModel.from_dict(data)
