"""Event detection/comparison on series responses.

Ported from the original ``caelab/numerical.py`` ``detect_event`` /
``compare_events``. Unknown occurrence stays UNKNOWN and is never
turned into a zero time; occurrence disagreement requires an
explicitly configured nonoccurrence penalty.
"""
from __future__ import annotations

import math
from copy import deepcopy

import numpy as np

from .core import _number


def detect_event(response, threshold, *, direction="rising"):
    """First threshold crossing, observed nonoccurrence, or explicit unknown."""
    threshold = _number(threshold, "threshold")
    if direction not in ("rising", "falling"):
        raise ValueError("Event direction must be rising or falling")
    axes = response.get("axes", [])
    if response.get("kind") != "series" or len(axes) != 1 \
            or not axes[0].get("name") or not axes[0].get("unit"):
        raise ValueError("Event detection requires a series with one explicit axis and unit")
    if not response.get("unit") or not response.get("component") \
            or not response.get("location"):
        raise ValueError("Event detection requires explicit response unit, component and location")
    time = np.asarray(axes[0].get("values", []), dtype=float)
    data = np.asarray(response.get("value", []), dtype=float)
    event = {"kind": "event", "value": None, "state": "UNKNOWN",
             "unit": axes[0]["unit"], "component": response.get("component"),
             "location": response.get("location"),
             "reduction": "first_threshold_crossing", "observed_interval": None,
             "event_definition": {"threshold": threshold,
                                  "response_unit": response.get("unit"),
                                  "direction": direction,
                                  "axis_name": axes[0]["name"],
                                  "interpolation": "linear"}}
    if time.ndim != 1 or not len(time) or not np.all(np.isfinite(time)) \
            or np.any(np.diff(time) <= 0):
        event["failure_reason"] = "Missing, nonfinite or unordered observation axis"
        return event
    event["observed_interval"] = [float(time[0]), float(time[-1])]
    if data.shape != time.shape or not np.all(np.isfinite(data)):
        event["failure_reason"] = "Missing or nonfinite response samples; event occurrence is unknown"
        return event
    passed = data >= threshold if direction == "rising" else data <= threshold
    crossings = np.flatnonzero(passed)
    if not len(crossings):
        event["state"] = "NOT_OBSERVED"
    else:
        index = int(crossings[0])
        if index == 0 and data[0] != threshold:
            event["failure_reason"] = "Threshold already exceeded at the first sample; crossing time is outside recorded support"
            event["occurrence_known_before_or_at_window_start"] = True
            return event
        crossing = time[0] if index == 0 else time[index - 1] \
            + (threshold - data[index - 1]) / (data[index] - data[index - 1]) \
            * (time[index] - time[index - 1])
        event.update(state="OCCURRED", value=float(crossing))
    return event


def compare_events(prediction, observation, *, nonoccurrence_penalty=None,
                   weight=1, scale=1):
    """Compare matched event definitions without turning missing times into zero."""
    weight, scale = _number(weight, "event weight"), _number(scale, "event scale")
    if weight <= 0 or scale <= 0:
        raise ValueError("Event weight and scale must be positive")
    for key in ("kind", "unit", "component", "location", "reduction", "event_definition"):
        if key not in observation or prediction.get(key) != observation[key]:
            raise ValueError(f"Event {key} mismatch")
    if observation["kind"] != "event":
        raise ValueError("Event comparison requires event responses")
    states = prediction.get("state"), observation.get("state")
    if any(state not in ("OCCURRED", "NOT_OBSERVED", "UNKNOWN") for state in states):
        raise ValueError("Event state must be OCCURRED, NOT_OBSERVED or UNKNOWN")
    result = {"prediction": deepcopy(prediction), "observation": deepcopy(observation),
              "comparable": False, "difference": None, "residual": None,
              "unit": observation["unit"]}
    if "UNKNOWN" in states:
        result["reason"] = "MISSING_OR_UNDETERMINED_EVENT"
        return result
    intervals = []
    for event in (prediction, observation):
        interval = event.get("observed_interval")
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise ValueError("Events require explicit observed intervals")
        start, end = [_number(value, "observed interval") for value in interval]
        if start > end:
            raise ValueError("Observed interval is reversed")
        intervals.append((start, end))
        if event["state"] == "OCCURRED" and not start <= _number(event.get("value"), "event time") <= end:
            raise ValueError("Event time lies outside its observed interval")
    if states == ("OCCURRED", "OCCURRED"):
        difference = prediction["value"] - observation["value"]
        reason = "EVENT_TIME_DIFFERENCE"
    elif states == ("NOT_OBSERVED", "NOT_OBSERVED"):
        if intervals[0] != intervals[1]:
            result["reason"] = "DIFFERENT_CENSORING_WINDOWS"
            return result
        difference, reason = 0.0, "AGREEMENT_WITHIN_DECLARED_OBSERVATION_WINDOW"
    else:
        event_index = 0 if states[0] == "OCCURRED" else 1
        occurred = prediction if event_index == 0 else observation
        if not intervals[1 - event_index][0] <= occurred["value"] <= intervals[1 - event_index][1]:
            result["reason"] = "EVENT_OUTSIDE_COMMON_OBSERVATION_WINDOW"
            return result
        if nonoccurrence_penalty is None:
            raise ValueError("Occurrence disagreement requires an explicitly "
                             "configured nonoccurrence penalty")
        difference = _number(nonoccurrence_penalty, "nonoccurrence penalty")
        if difference < 0:
            raise ValueError("Nonoccurrence penalty must be nonnegative")
        reason = "CONFIGURED_OCCURRENCE_DISAGREEMENT_PENALTY"
    result.update(comparable=True, difference=float(difference),
                  residual=float(difference * math.sqrt(weight) / scale),
                  reason=reason)
    return result
