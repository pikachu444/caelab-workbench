"""Verified bytes of one recorded native frame, without an auxiliary FE model."""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
from pathlib import Path

from .evidence import native_results_file


@dataclass(frozen=True)
class NativeFrameData:
    run_id: str
    frame: dict
    units: dict
    unit_source: str
    parse_state: str
    stale: bool
    stale_reason: str
    path: Path
    sha256: str
    payload: bytes


def read_native_frame(run, frame_index: int) -> NativeFrameData:
    """Verify the exact bytes a viewer consumes, including a post-path-check hash."""
    if type(frame_index) is not int or frame_index < 0:
        raise ValueError('표시할 결과 프레임 번호를 선택하세요.')
    run = copy.deepcopy(run)
    path = native_results_file(run, frame_index=frame_index)
    relative = path.relative_to(run.workdir.resolve()).as_posix()
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != run.snapshot['artifacts'][relative]:
        raise ValueError('표시할 결과 파일의 해시가 일치하지 않습니다.')
    frame = next(frame for frame in run.result['frames'] if frame['index'] == frame_index)
    return NativeFrameData(run.id, frame, run.units, run.unit_source, run.parse_state,
                           run.stale, run.stale_reason, path, digest, payload)
