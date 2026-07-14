from __future__ import annotations

import importlib.util
from pathlib import Path


ANALYZER_PATH = (
    Path(__file__).resolve().parents[1]
    / "reports"
    / "benchmark-baseline"
    / "analyze_exports.py"
)
SPEC = importlib.util.spec_from_file_location("benchmark_analyzer", ANALYZER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {ANALYZER_PATH}")
ANALYZER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ANALYZER)


def test_error_rows_are_not_counted_as_integrity_failures_or_latency_samples():
    completed = {
        "status": "completed",
        "original_text": "Keep UT-1042.",
        "final_text": "Keep UT-1042.",
        "original_tokens": 3,
        "final_tokens": 3,
        "latency_ms": 125.0,
        "validation": {"integrityPassed": True},
        "provenance": {"resolved_compression_settings": {}},
        "stages": {},
    }
    harness_error = {
        "status": "error",
        "original_tokens": None,
        "final_tokens": None,
        "latency_ms": 0,
        "validation": {"integrityPassed": None},
        "provenance": {},
        "stages": {},
    }

    metrics = ANALYZER.analyze_records([completed, harness_error])

    assert metrics["records"] == 2
    assert metrics["successful_records"] == 1
    assert metrics["error_records"] == 1
    assert metrics["integrity"]["integrity_evaluated_records"] == 1
    assert metrics["integrity"]["integrity_failures"] == 0
    assert metrics["integrity"]["integrity_failure_rate"] == 0.0
    assert metrics["distributions"]["latency_ms_p50"] == 125.0
