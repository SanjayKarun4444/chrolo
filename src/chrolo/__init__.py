"""Chrolo: failure-preserving minimization for agent workflows."""
from .cassette import BoundaryContext, BoundaryTool, CassetteRecorder, RecordedCallError, ReplayMismatch, ReplaySession
from .core import Event, MinimizeResult, minimize, parse_events
from .safety import filter_trace, redact_trace, scan_trace, summarize_trace
from .trajectory import DependencyInference, TrackedState, infer_dependencies, run_trajectory

__all__ = [
    "BoundaryContext", "BoundaryTool", "CassetteRecorder", "Event", "MinimizeResult", "RecordedCallError",
    "ReplayMismatch", "ReplaySession", "minimize", "parse_events",
    "filter_trace", "redact_trace", "scan_trace", "summarize_trace",
    "DependencyInference", "TrackedState", "infer_dependencies", "run_trajectory",
]
