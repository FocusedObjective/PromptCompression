import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class GpuCompressionPolicy:
    schema_version: str
    min_model_segment_chars: int
    min_model_segment_tokens: int
    min_model_candidate_tokens: int
    min_model_incremental_savings_tokens: int
    min_model_incremental_reduction: float
    max_model_projected_latency_ms: float
    max_model_auto_placeholders: int
    cold_model_tight_latency_budget_ms: float
    max_protected_density: float
    max_structured_density: float
    skip_model_if_deterministic_reduction_gte: float


def _positive_int(data: dict[str, Any], name: str) -> int:
    value = data.get(name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"GPU compression policy {name} must be a positive integer")
    return value


def _bounded_float(data: dict[str, Any], name: str, *, maximum: float = 1.0) -> float:
    value = data.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"GPU compression policy {name} must be numeric")
    resolved = float(value)
    if resolved < 0.0 or resolved > maximum:
        raise ValueError(
            f"GPU compression policy {name} must be between 0 and {maximum}"
        )
    return resolved


def load_gpu_compression_policy() -> GpuCompressionPolicy:
    path = Path(__file__).with_name("gpu_compression_policy.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("GPU compression policy must be a JSON object")
    schema_version = data.get("schema_version")
    if not isinstance(schema_version, str) or not schema_version.strip():
        raise ValueError("GPU compression policy schema_version is required")
    return GpuCompressionPolicy(
        schema_version=schema_version,
        min_model_segment_chars=_positive_int(data, "min_model_segment_chars"),
        min_model_segment_tokens=_positive_int(data, "min_model_segment_tokens"),
        min_model_candidate_tokens=_positive_int(data, "min_model_candidate_tokens"),
        min_model_incremental_savings_tokens=_positive_int(
            data,
            "min_model_incremental_savings_tokens",
        ),
        min_model_incremental_reduction=_bounded_float(
            data,
            "min_model_incremental_reduction",
        ),
        max_model_projected_latency_ms=float(
            _positive_int(data, "max_model_projected_latency_ms")
        ),
        max_model_auto_placeholders=_positive_int(
            data,
            "max_model_auto_placeholders",
        ),
        cold_model_tight_latency_budget_ms=float(
            _positive_int(data, "cold_model_tight_latency_budget_ms")
        ),
        max_protected_density=_bounded_float(data, "max_protected_density"),
        max_structured_density=_bounded_float(data, "max_structured_density"),
        skip_model_if_deterministic_reduction_gte=_bounded_float(
            data,
            "skip_model_if_deterministic_reduction_gte",
        ),
    )


GPU_COMPRESSION_POLICY = load_gpu_compression_policy()
