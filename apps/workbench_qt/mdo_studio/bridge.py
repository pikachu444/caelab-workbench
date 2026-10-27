"""Thread-safe execution bridge between JobManager background worker and Qt GUI thread.

Conforms to chief-reviewer and gui-launcher-dev architectural invariants:
- Strict Signal-Slot isolation (all events marshalled through Qt.QueuedConnection)
- No direct GUI widget manipulation from background threads
- Zero zombie process guarantee on cancel/window closure
"""

from __future__ import annotations

from typing import Any
from PySide6.QtCore import QObject, Signal


class MDOExecutionBridge(QObject):
    """Thread-safe signal bridge between JobManager background batch runner and Qt GUI."""

    # Batch lifecycle signals
    batchStarted = Signal(str, int)              # (batch_id, total_count)
    batchProgress = Signal(object)                # (BatchProgress)
    batchFinished = Signal(str, str, int, int)    # (batch_id, final_state, completed_count, failed_count)

    # Design point (item) signals
    itemStarted = Signal(str, int)                # (point_id, index)
    itemFinished = Signal(str, object, object)    # (point_id, BatchItem, NativeRun)
    itemFailed = Signal(str, str)                 # (point_id, error_message)
