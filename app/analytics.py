from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from app.protected_spans import protected_spans_for_text
from app.version import DEPLOYMENT_VERSION


DIAGNOSTICS_SCHEMA_VERSION = "compression-diagnostics.v3"
BENCHMARK_SCHEMA_VERSION = "benchmark.v3"

_REPEATABILITY_LOCK = Lock()
_REPEATABILITY: dict[str, tuple[str, int]] = {}

TRANSFORM_CODES = (
    "whitespace_canonicalization",
    "force_drop_preprocessing",
    "json_minification",
    "json_to_toon",
    "html_to_markdown",
    "nocompress_wrapper_handling",
    "exact_duplicate_block_removal",
    "protected_span_substitution",
    "placeholder_restoration",
)

GATE_REASON_CODES = (
    "no_candidate",
    "invalid_ambiguous_syntax",
    "json_parse_failed",
    "inside_protected_span",
    "unsupported_structure",
    "below_minimum_size",
    "token_increase",
    "no_token_savings",
    "density_safety_gate",
    "tenant_configuration_disabled",
    "transform_failed",
    "duplicate_not_structurally_safe_to_remove",
)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TransformDiagnostic:
    transform: str
    candidate_count: int
    candidate_characters: int
    candidate_tokens: int
    applied_count: int
    input_characters: int
    output_characters: int
    input_tokens: int
    output_tokens: int
    tokens_saved: int
    token_delta: int
    status: str
    reason: str
    elapsed_ms: float
    enabled: bool = True
    gate_reason_counts: dict[str, int] = field(default_factory=dict)
    gate_reason_estimated_tokens_saved: dict[str, int] = field(default_factory=dict)
    counterfactual: "CounterfactualDiagnostic | None" = None


@dataclass(frozen=True)
class CounterfactualDiagnostic:
    would_apply: bool
    estimated_tokens_saved: int
    output_sha256: str


@dataclass(frozen=True)
class StageDiagnostic:
    sha256: str
    characters: int
    tokens: int
    net_tokens_saved: int | None = None
    placeholder_token_delta: int | None = None
    raw_model_tokens_saved: int | None = None
    net_model_tokens_saved: int | None = None
    total_tokens_saved: int | None = None


@dataclass(frozen=True)
class CompressionStages:
    original: StageDiagnostic
    post_deterministic_content: StageDiagnostic
    model_input_with_placeholders: StageDiagnostic
    model_output_before_restoration: StageDiagnostic
    final_restored: StageDiagnostic


@dataclass(frozen=True)
class EvaluationConstraintsResult:
    passed: bool
    missing_required_substrings: list[str] = field(default_factory=list)
    missing_required_whitespace_insensitive_substrings: list[str] = field(
        default_factory=list
    )
    present_forbidden_substrings: list[str] = field(default_factory=list)
    missing_required_json_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RepeatabilityMetadata:
    cache_status: str
    cache_bypassed: bool
    deterministic_repeat_count: int
    deterministic_matches_previous: bool | None


@dataclass(frozen=True)
class CandidateOpportunities:
    blank_line_count: int = 0
    estimated_blank_line_removable_tokens: int = 0
    trailing_whitespace_line_count: int = 0
    multiple_space_run_count: int = 0
    valid_json_region_count: int = 0
    json_like_invalid_region_count: int = 0
    html_region_count: int = 0
    html_comment_count: int = 0
    toon_eligible_region_count: int = 0
    exact_duplicate_block_candidate_count: int = 0
    duplicate_candidate_characters: int = 0
    duplicate_candidate_tokens: int = 0
    markdown_heading_count: int = 0
    markdown_list_count: int = 0


@dataclass(frozen=True)
class IntegrityValidation:
    protected_span_validation_passed: bool
    protected_span_count_by_type: dict[str, int]
    protected_spans_missing_by_type: dict[str, int]
    protected_spans_changed_by_type: dict[str, int]
    json_round_trip_applicable: bool
    json_round_trip_validation_passed: bool
    original_canonical_json_sha256: str | None
    output_canonical_json_sha256: str | None
    placeholder_restoration_validation_passed: bool
    structural_validation_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CompressionProvenance:
    compressor_git_commit: str
    compressor_source_sha256: str
    deployment_version: str
    model_checkpoint: str
    model_revision: str
    tokenizer_name: str
    tokenizer_version: str
    configuration_sha256: str
    resolved_compression_settings: dict[str, Any]
    enabled_deterministic_transforms: list[str]
    deterministic_thresholds: dict[str, int | float]
    tenant_profile_sha256: str
    benchmark_schema_version: str
    diagnostics_schema_version: str
    token_estimator: str
    runtime_versions: dict[str, str]
    seed: int
    deterministic_algorithms_enabled: bool


@dataclass(frozen=True)
class DetailedAnalytics:
    diagnostics_schema_version: str
    original_sha256: str
    request_id: str
    stages: CompressionStages
    deterministic_text: str
    deterministic_sha256: str
    deterministic_characters: int
    deterministic_tokens: int
    deterministic_tokens_saved: int
    deterministic_transforms: list[TransformDiagnostic]
    deterministic_gate_reasons: dict[str, int]
    deterministic_gate_potential_tokens_saved: dict[str, int]
    deterministic_component_tokens_saved: int
    deterministic_attribution_residual_tokens: int
    deterministic_attribution_residual_reason: str | None
    candidate_opportunities: CandidateOpportunities
    model_input_sha256: str
    model_input_characters: int
    model_input_tokens: int
    final_sha256: str
    final_characters: int
    final_tokens: int
    model_incremental_tokens_saved: int
    model_incremental_reduction: float
    model_called: bool
    model_call_count: int
    model_chunk_count: int
    target_retention_rate: float
    effective_retention_rate: float
    force_token_count: int
    protected_placeholder_count: int
    model_skip_or_fallback_reason: str | None
    attribution_residual_tokens: int
    attribution_residual_reason: str | None
    evaluation_constraints: EvaluationConstraintsResult | None
    repeatability: RepeatabilityMetadata
    timing_semantics: dict[str, str]
    cold_model_load: bool
    integrity: IntegrityValidation
    provenance: CompressionProvenance


def build_detailed_analytics(
    *,
    service: Any,
    input_text: str,
    preprocessed_text: str,
    force_dropped_text: str,
    pipeline_text: str,
    model_input_text: str,
    final_text: str,
    prepared_segments: list[Any],
    final_segments: list[Any],
    profile: Any,
    token_estimator: str,
    original_tokens: int,
    model_input_tokens: int,
    final_tokens: int,
    target_rate: float,
    model_called: bool,
    model_call_count: int,
    model_chunk_count: int,
    model_reason: str | None,
    placeholder_tokens: list[str],
    force_token_count: int,
    duplicate_candidate_count: int,
    duplicate_candidate_tokens: int,
    attribution_residual_tokens: int,
    model_output_text: str,
    post_deterministic_tokens: int,
    model_output_tokens: int,
    evaluate_disabled_transforms: bool = False,
    evaluation_constraints: dict[str, list[str]] | None = None,
    request_id: str | None = None,
    cold_model_load: bool = False,
) -> DetailedAnalytics:
    def estimate(value: str) -> int:
        return service.estimate_compression_tokens(value, profile).count
    transforms = _transform_diagnostics(
        service=service,
        input_text=input_text,
        preprocessed_text=preprocessed_text,
        force_dropped_text=force_dropped_text,
        pipeline_text=pipeline_text,
        model_input_text=model_input_text,
        model_output_text=model_output_text,
        final_text=final_text,
        prepared_segments=prepared_segments,
        final_segments=final_segments,
        profile=profile,
        estimate=estimate,
        placeholder_tokens=placeholder_tokens,
        duplicate_candidate_count=duplicate_candidate_count,
        duplicate_candidate_tokens=duplicate_candidate_tokens,
        evaluate_disabled_transforms=evaluate_disabled_transforms,
    )
    opportunities = _candidate_opportunities(
        service,
        input_text,
        final_segments,
        duplicate_candidate_count,
        duplicate_candidate_tokens,
    )
    gate_reasons: Counter[str] = Counter()
    gate_potential_tokens: Counter[str] = Counter()
    for transform in transforms:
        if transform.status == "applied":
            continue
        gate_reasons[transform.reason] += (
            1
            if transform.status == "no_candidate"
            else max(1, transform.candidate_count - transform.applied_count)
        )
        if transform.counterfactual is not None:
            gate_potential_tokens[transform.reason] += (
                transform.counterfactual.estimated_tokens_saved
            )
    if opportunities.json_like_invalid_region_count:
        gate_reasons["json_parse_failed"] += (
            opportunities.json_like_invalid_region_count
        )
    integrity = _integrity_validation(
        input_text,
        final_text,
        placeholder_tokens,
    )
    provenance = _provenance(service, profile, token_estimator)
    net_model_saved = max(0, post_deterministic_tokens - final_tokens)
    net_deterministic_saved = max(0, original_tokens - post_deterministic_tokens)
    component_saved = sum(
        item.tokens_saved
        for item in transforms
        if item.transform not in {
            "protected_span_substitution",
            "placeholder_restoration",
        }
    )
    deterministic_residual = net_deterministic_saved - component_saved
    resolved_request_id = request_id or str(uuid.uuid4())
    repeatability = _repeatability_metadata(
        original_sha256=sha256_text(input_text),
        configuration_sha256=provenance.configuration_sha256,
        deterministic_sha256=sha256_text(pipeline_text),
    )
    placeholder_delta = model_input_tokens - post_deterministic_tokens
    raw_model_saved = max(0, model_input_tokens - model_output_tokens)
    total_saved = max(0, original_tokens - final_tokens)
    stages = CompressionStages(
        original=_stage(input_text, original_tokens),
        post_deterministic_content=StageDiagnostic(
            **_stage_values(pipeline_text, post_deterministic_tokens),
            net_tokens_saved=net_deterministic_saved,
        ),
        model_input_with_placeholders=StageDiagnostic(
            **_stage_values(model_input_text, model_input_tokens),
            placeholder_token_delta=placeholder_delta,
        ),
        model_output_before_restoration=StageDiagnostic(
            **_stage_values(model_output_text, model_output_tokens),
            raw_model_tokens_saved=raw_model_saved,
        ),
        final_restored=StageDiagnostic(
            **_stage_values(final_text, final_tokens),
            net_model_tokens_saved=net_model_saved,
            total_tokens_saved=total_saved,
        ),
    )
    return DetailedAnalytics(
        diagnostics_schema_version=DIAGNOSTICS_SCHEMA_VERSION,
        original_sha256=sha256_text(input_text),
        request_id=resolved_request_id,
        stages=stages,
        deterministic_text=pipeline_text,
        deterministic_sha256=sha256_text(pipeline_text),
        deterministic_characters=len(pipeline_text),
        deterministic_tokens=post_deterministic_tokens,
        deterministic_tokens_saved=net_deterministic_saved,
        deterministic_transforms=transforms,
        deterministic_gate_reasons=dict(sorted(gate_reasons.items())),
        deterministic_gate_potential_tokens_saved=dict(
            sorted(gate_potential_tokens.items())
        ),
        deterministic_component_tokens_saved=component_saved,
        deterministic_attribution_residual_tokens=deterministic_residual,
        deterministic_attribution_residual_reason=(
            None
            if deterministic_residual == 0
            else "token_estimator_non_additivity_or_overlapping_transforms"
        ),
        candidate_opportunities=opportunities,
        model_input_sha256=sha256_text(model_input_text),
        model_input_characters=len(model_input_text),
        model_input_tokens=model_input_tokens,
        final_sha256=sha256_text(final_text),
        final_characters=len(final_text),
        final_tokens=final_tokens,
        model_incremental_tokens_saved=net_model_saved,
        model_incremental_reduction=(
            0.0
            if post_deterministic_tokens <= 0
            else net_model_saved / post_deterministic_tokens
        ),
        model_called=model_called,
        model_call_count=model_call_count,
        model_chunk_count=model_chunk_count,
        target_retention_rate=target_rate,
        effective_retention_rate=(
            1.0
            if post_deterministic_tokens <= 0
            else final_tokens / post_deterministic_tokens
        ),
        force_token_count=force_token_count,
        protected_placeholder_count=len(placeholder_tokens),
        model_skip_or_fallback_reason=model_reason,
        attribution_residual_tokens=attribution_residual_tokens,
        attribution_residual_reason=(
            None if attribution_residual_tokens == 0 else "token_estimator_non_additivity"
        ),
        evaluation_constraints=_evaluate_constraints(
            final_text, evaluation_constraints
        ),
        repeatability=repeatability,
        timing_semantics={
            "total_ms": "inclusive",
            "phase_timings": "exclusive",
            "diagnostics_ms": "exclusive_observer_overhead",
        },
        cold_model_load=cold_model_load,
        integrity=integrity,
        provenance=provenance,
    )


def _transform_diagnostics(
    *,
    service: Any,
    input_text: str,
    preprocessed_text: str,
    force_dropped_text: str,
    pipeline_text: str,
    model_input_text: str,
    model_output_text: str,
    final_text: str,
    prepared_segments: list[Any],
    final_segments: list[Any],
    profile: Any,
    estimate: Callable[[str], int],
    placeholder_tokens: list[str],
    duplicate_candidate_count: int,
    duplicate_candidate_tokens: int,
    evaluate_disabled_transforms: bool,
) -> list[TransformDiagnostic]:
    diagnostics: list[TransformDiagnostic] = []

    def add(
        code: str,
        source: str,
        output: str,
        candidate_count: int,
        *,
        applied_count: int | None = None,
        reason: str | None = None,
        candidate_characters: int | None = None,
        candidate_tokens: int | None = None,
        started: float | None = None,
        enabled: bool = True,
        gate_reason_counts: dict[str, int] | None = None,
        gate_reason_estimated_tokens_saved: dict[str, int] | None = None,
        counterfactual: CounterfactualDiagnostic | None = None,
        tokens_saved_override: int | None = None,
    ) -> None:
        source_tokens = estimate(source) if source else 0
        output_tokens = estimate(output) if output else 0
        saved = (
            max(0, source_tokens - output_tokens)
            if tokens_saved_override is None
            else tokens_saved_override
        )
        applied = int(source != output) if applied_count is None else applied_count
        if applied:
            status = "applied" if saved > 0 or code in {
                "nocompress_wrapper_handling",
                "protected_span_substitution",
                "placeholder_restoration",
            } else "no_savings"
            stable_reason = "applied" if status == "applied" else "no_token_savings"
        elif candidate_count <= 0:
            status, stable_reason = "no_candidate", "no_candidate"
        else:
            status, stable_reason = "skipped", reason or "no_token_savings"
        diagnostics.append(
            TransformDiagnostic(
                transform=code,
                candidate_count=candidate_count,
                candidate_characters=(
                    len(source) if candidate_characters is None else candidate_characters
                ),
                candidate_tokens=(
                    source_tokens if candidate_tokens is None else candidate_tokens
                ),
                applied_count=applied,
                input_characters=len(source),
                output_characters=len(output),
                input_tokens=source_tokens,
                output_tokens=output_tokens,
                tokens_saved=saved,
                token_delta=output_tokens - source_tokens,
                status=status,
                reason=stable_reason,
                elapsed_ms=(
                    0.0 if started is None else (time.perf_counter() - started) * 1000
                ),
                enabled=enabled,
                gate_reason_counts=(
                    gate_reason_counts
                    if gate_reason_counts is not None
                    else ({stable_reason: max(1, candidate_count - applied)} if status != "applied" else {})
                ),
                gate_reason_estimated_tokens_saved=(
                    gate_reason_estimated_tokens_saved
                    if gate_reason_estimated_tokens_saved is not None
                    else (
                        {stable_reason: counterfactual.estimated_tokens_saved}
                        if status != "applied" and counterfactual is not None
                        else {}
                    )
                ),
                counterfactual=counterfactual,
            )
        )

    categories = {
        "whitespace_canonicalization": "prose",
        "json_minification": "json_minified",
        "json_to_toon": "toon",
        "html_to_markdown": "html_markdown",
        "nocompress_wrapper_handling": "nocompress",
    }
    valid_json_count, invalid_json_count = _json_region_counts(service, input_text)
    whitespace_candidate_count = (
        len(re.findall(r"(?m)^[ \t]*$", input_text))
        + len(re.findall(r"(?m)[ \t]+$", input_text))
        + len(re.findall(r"(?<!\n) {2,}", input_text))
    )
    html_candidate_count = len(
        re.findall(r"(?is)<(?:html|body|section|div|table)\b", input_text)
    )
    nocompress_candidate_count = len(
        re.findall(r"(?is)<nocompress>.*?</nocompress>", input_text)
    )
    for code, kind in categories.items():
        started = time.perf_counter()
        candidates = [
            segment for segment in prepared_segments
            if segment.kind == kind and segment.source_text is not None
        ]
        source = "".join(segment.source_text or "" for segment in candidates)
        output = "".join(segment.text for segment in candidates)
        candidate_count = len(candidates)
        reason = None
        if code == "whitespace_canonicalization":
            candidate_count = whitespace_candidate_count
        elif code in {"json_minification", "json_to_toon"}:
            candidate_count = valid_json_count + invalid_json_count
            if invalid_json_count and not valid_json_count:
                reason = "json_parse_failed"
            elif valid_json_count and not candidates:
                reason = "below_minimum_size"
        elif code == "html_to_markdown":
            candidate_count = html_candidate_count
            if html_candidate_count and not candidates:
                reason = "below_minimum_size"
        elif code == "nocompress_wrapper_handling":
            candidate_count = nocompress_candidate_count
        counterfactual = None
        enabled = True
        if code == "json_minification" and not service.preprocessor.enable_json_minify:
            reason = "tenant_configuration_disabled"
            enabled = False
            if evaluate_disabled_transforms:
                counterfactual = _json_minification_counterfactual(
                    service, input_text, profile
                )
        elif candidates and source == output:
            reason = "no_token_savings"
        add(
            code,
            source,
            output,
            candidate_count,
            reason=reason,
            started=started,
            enabled=enabled,
            counterfactual=counterfactual,
        )

    started = time.perf_counter()
    force_candidates = sum(
        input_text.lower().count(phrase.lower())
        for phrase in profile.force_drop_phrases
        if phrase
    )
    add(
        "force_drop_preprocessing",
        preprocessed_text,
        force_dropped_text,
        force_candidates,
        reason=(
            "tenant_configuration_disabled"
            if not profile.force_drop_phrases
            else "no_token_savings"
        ),
        started=started,
    )
    started = time.perf_counter()
    duplicate_chars = _duplicate_candidate_characters(service, final_segments)
    add(
        "exact_duplicate_block_removal",
        "",
        "",
        duplicate_candidate_count,
        applied_count=0,
        reason="duplicate_not_structurally_safe_to_remove",
        candidate_characters=duplicate_chars,
        candidate_tokens=duplicate_candidate_tokens,
        started=started,
    )
    started = time.perf_counter()
    protected_count = len(placeholder_tokens)
    add(
        "protected_span_substitution",
        pipeline_text,
        model_input_text,
        protected_count,
        applied_count=int(bool(protected_count and pipeline_text != model_input_text)),
        reason="inside_protected_span",
        started=started,
        tokens_saved_override=0,
    )
    started = time.perf_counter()
    restored_count = sum(token not in final_text for token in placeholder_tokens)
    add(
        "placeholder_restoration",
        model_output_text,
        final_text,
        protected_count,
        applied_count=restored_count,
        reason="transform_failed" if restored_count != protected_count else None,
        started=started,
        tokens_saved_override=0,
    )
    order = {name: index for index, name in enumerate(TRANSFORM_CODES)}
    return sorted(diagnostics, key=lambda item: order[item.transform])


def _candidate_opportunities(
    service: Any,
    text: str,
    segments: list[Any],
    duplicate_count: int,
    duplicate_tokens: int,
) -> CandidateOpportunities:
    valid_json, invalid_json = _json_region_counts(service, text)
    duplicate_chars = _duplicate_candidate_characters(service, segments)
    blank_lines = len(re.findall(r"(?m)^[ \t]*$", text))
    return CandidateOpportunities(
        blank_line_count=blank_lines,
        estimated_blank_line_removable_tokens=blank_lines,
        trailing_whitespace_line_count=len(re.findall(r"(?m)[ \t]+$", text)),
        multiple_space_run_count=len(re.findall(r"(?<!\n) {2,}", text)),
        valid_json_region_count=valid_json,
        json_like_invalid_region_count=invalid_json,
        html_region_count=len(re.findall(r"(?is)<(?:html|body|section|div|table)\b", text)),
        html_comment_count=len(re.findall(r"(?s)<!--.*?-->", text)),
        toon_eligible_region_count=sum(1 for segment in segments if segment.kind == "toon"),
        exact_duplicate_block_candidate_count=duplicate_count,
        duplicate_candidate_characters=duplicate_chars,
        duplicate_candidate_tokens=duplicate_tokens,
        markdown_heading_count=len(re.findall(r"(?m)^#{1,6}\s+", text)),
        markdown_list_count=len(re.findall(r"(?m)^\s*(?:[-+*]|\d+[.)])\s+", text)),
    )


def _json_region_counts(service: Any, text: str) -> tuple[int, int]:
    valid = invalid = 0
    cursor = 0
    while cursor < len(text):
        start = service.preprocessor._find_json_start(text, cursor)
        if start is None:
            break
        end = service.preprocessor._find_balanced_json_end(text, start)
        if end is None:
            invalid += 1
            cursor = start + 1
            continue
        candidate = text[start:end]
        try:
            json.loads(candidate)
            valid += 1
        except json.JSONDecodeError:
            invalid += 1
        cursor = end
    return valid, invalid


def _duplicate_candidate_characters(service: Any, segments: list[Any]) -> int:
    seen: set[str] = set()
    characters = 0
    for segment in segments:
        if not segment.compressible:
            continue
        for block in service._duplicate_candidate_blocks(segment.text):
            normalized = service._normalize_duplicate_block(block)
            if normalized in seen:
                characters += len(block)
            elif normalized:
                seen.add(normalized)
    return characters


def _integrity_validation(
    original: str,
    output: str,
    placeholder_tokens: list[str],
) -> IntegrityValidation:
    spans = protected_spans_for_text(original)
    counts = Counter(span.kind for span in spans)
    protected_values = Counter((span.kind, span.text) for span in spans)
    missing: Counter[str] = Counter()
    for (kind, value), expected_count in protected_values.items():
        missing[kind] += max(0, expected_count - output.count(value))
    missing = Counter({kind: count for kind, count in missing.items() if count})
    warnings: list[str] = []
    if missing:
        warnings.append("protected_span_missing_or_changed")
    placeholder_ok = all(token not in output for token in placeholder_tokens)
    if not placeholder_ok:
        warnings.append("placeholder_not_restored")
    original_json = _canonical_json_hash(original)
    output_json = _canonical_json_hash(output)
    json_applicable = original_json is not None
    json_passed = not json_applicable or original_json == output_json
    if json_applicable and not json_passed:
        warnings.append("json_round_trip_changed")
    return IntegrityValidation(
        protected_span_validation_passed=not missing,
        protected_span_count_by_type=dict(sorted(counts.items())),
        protected_spans_missing_by_type=dict(sorted(missing.items())),
        protected_spans_changed_by_type=dict(sorted(missing.items())),
        json_round_trip_applicable=json_applicable,
        json_round_trip_validation_passed=json_passed,
        original_canonical_json_sha256=original_json,
        output_canonical_json_sha256=output_json if json_applicable else None,
        placeholder_restoration_validation_passed=placeholder_ok,
        structural_validation_warnings=warnings,
    )


def _canonical_json_hash(text: str) -> str | None:
    try:
        value = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(canonical)


def _stage(text: str, tokens: int) -> StageDiagnostic:
    return StageDiagnostic(**_stage_values(text, tokens))


def _stage_values(text: str, tokens: int) -> dict[str, str | int]:
    return {
        "sha256": sha256_text(text),
        "characters": len(text),
        "tokens": tokens,
    }


def _json_minification_counterfactual(
    service: Any,
    text: str,
    profile: Any,
) -> CounterfactualDiagnostic:
    cursor = 0
    original_parts: list[str] = []
    minified_parts: list[str] = []
    while cursor < len(text):
        start = service.preprocessor._find_json_start(text, cursor)
        if start is None:
            break
        end = service.preprocessor._find_balanced_json_end(text, start)
        if end is None:
            cursor = start + 1
            continue
        candidate = text[start:end]
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            cursor = end
            continue
        original_parts.append(candidate)
        minified_parts.append(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        )
        cursor = end
    original = "".join(original_parts)
    minified = "".join(minified_parts)
    original_tokens = (
        service.estimate_compression_tokens(original, profile).count if original else 0
    )
    minified_tokens = (
        service.estimate_compression_tokens(minified, profile).count if minified else 0
    )
    savings = max(0, original_tokens - minified_tokens)
    return CounterfactualDiagnostic(
        would_apply=bool(original_parts and savings > 0),
        estimated_tokens_saved=savings,
        output_sha256=sha256_text(minified),
    )


def _evaluate_constraints(
    output: str,
    constraints: dict[str, list[str]] | None,
) -> EvaluationConstraintsResult | None:
    if not constraints:
        return None
    required = constraints.get("required_substrings", [])
    whitespace_required = constraints.get(
        "required_whitespace_insensitive_substrings", []
    )
    forbidden = constraints.get("forbidden_substrings", [])
    json_keys = constraints.get("required_json_keys", [])
    normalized_output = re.sub(r"\s+", " ", output).strip()
    available_json_keys = _json_keys_in_text(output)
    result = EvaluationConstraintsResult(
        passed=True,
        missing_required_substrings=[value for value in required if value not in output],
        missing_required_whitespace_insensitive_substrings=[
            value
            for value in whitespace_required
            if re.sub(r"\s+", " ", value).strip() not in normalized_output
        ],
        present_forbidden_substrings=[value for value in forbidden if value in output],
        missing_required_json_keys=[
            value for value in json_keys if value not in available_json_keys
        ],
    )
    passed = not (
        result.missing_required_substrings
        or result.missing_required_whitespace_insensitive_substrings
        or result.present_forbidden_substrings
        or result.missing_required_json_keys
    )
    return EvaluationConstraintsResult(
        passed=passed,
        missing_required_substrings=result.missing_required_substrings,
        missing_required_whitespace_insensitive_substrings=(
            result.missing_required_whitespace_insensitive_substrings
        ),
        present_forbidden_substrings=result.present_forbidden_substrings,
        missing_required_json_keys=result.missing_required_json_keys,
    )


def _json_keys_in_text(text: str) -> set[str]:
    keys: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            keys.update(str(key) for key in value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    try:
        visit(json.loads(text.strip()))
    except json.JSONDecodeError:
        for match in re.finditer(r'"(?P<key>[^"\\]+)"\s*:', text):
            keys.add(match.group("key"))
    return keys


def _repeatability_metadata(
    *,
    original_sha256: str,
    configuration_sha256: str,
    deterministic_sha256: str,
) -> RepeatabilityMetadata:
    key = f"{configuration_sha256}:{original_sha256}"
    with _REPEATABILITY_LOCK:
        previous = _REPEATABILITY.get(key)
        repeat_count = 1 if previous is None else previous[1] + 1
        _REPEATABILITY[key] = (deterministic_sha256, repeat_count)
    return RepeatabilityMetadata(
        cache_status="not_configured",
        cache_bypassed=True,
        deterministic_repeat_count=repeat_count,
        deterministic_matches_previous=(
            None if previous is None else previous[0] == deterministic_sha256
        ),
    )


def _provenance(service: Any, profile: Any, token_estimator: str) -> CompressionProvenance:
    settings = {
        "device": service.device,
        "min_rate": service.min_rate,
        "min_segment_characters": service.min_segment_chars,
        "min_segment_tokens": service.min_segment_tokens,
        "literal_placeholdering_enabled": service.literal_placeholdering_enabled,
        "json_minification_enabled": service.preprocessor.enable_json_minify,
        "html_markdown_enabled": service.preprocessor.enable_html_markdown,
        "strict_prose_whitespace": service.preprocessor.strict_prose_whitespace,
        "tenant_id": profile.tenant_id,
        "tenant_profile_id": profile.profile_id,
        "tenant_profile_source": profile.source,
        "force_keep_token_count": len(profile.force_keep_tokens),
        "force_drop_phrase_count": len(profile.force_drop_phrases),
        "tenant_min_rate": profile.min_rate,
    }
    thresholds = {
        "minimum_json_characters": service.preprocessor.min_json_chars,
        "minimum_json_lines": service.preprocessor.min_json_lines,
        "minimum_toon_savings": service.preprocessor.min_toon_savings,
        "minimum_html_characters": service.preprocessor.min_html_chars,
        "minimum_html_markdown_savings": service.preprocessor.min_html_markdown_savings,
        "minimum_json_minify_savings": service.preprocessor.min_json_minify_savings,
        "minimum_duplicate_block_tokens": service.min_duplicate_block_tokens,
    }
    config_json = json.dumps(
        {"settings": settings, "thresholds": thresholds},
        sort_keys=True,
        separators=(",", ":"),
    )
    profile_json = json.dumps(
        {
            "tenant_id": profile.tenant_id,
            "profile_id": profile.profile_id,
            "source": profile.source,
            "min_rate": profile.min_rate,
            "force_keep_token_count": len(profile.force_keep_tokens),
            "force_drop_phrase_count": len(profile.force_drop_phrases),
        },
        sort_keys=True,
    )
    return CompressionProvenance(
        compressor_git_commit=_git_commit(),
        compressor_source_sha256=_source_sha256(),
        deployment_version=DEPLOYMENT_VERSION,
        model_checkpoint=service.model_name,
        model_revision=_model_revision(),
        tokenizer_name=token_estimator,
        tokenizer_version=_package_version("transformers"),
        configuration_sha256=sha256_text(config_json),
        resolved_compression_settings=settings,
        enabled_deterministic_transforms=[
            "whitespace_canonicalization",
            "force_drop_preprocessing",
            *( ["json_minification"] if service.preprocessor.enable_json_minify else [] ),
            "json_to_toon",
            *( ["html_to_markdown"] if service.preprocessor.enable_html_markdown else [] ),
            "nocompress_wrapper_handling",
            "protected_span_substitution",
            "placeholder_restoration",
        ],
        deterministic_thresholds=thresholds,
        tenant_profile_sha256=sha256_text(profile_json),
        benchmark_schema_version=BENCHMARK_SCHEMA_VERSION,
        diagnostics_schema_version=DIAGNOSTICS_SCHEMA_VERSION,
        token_estimator=token_estimator,
        runtime_versions={
            "python": platform.python_version(),
            "implementation": sys.implementation.name,
            "llmlingua": _package_version("llmlingua"),
            "torch": _package_version("torch"),
            "transformers": _package_version("transformers"),
        },
        seed=int(os.getenv("COMPRESSOR_SEED", "0")),
        deterministic_algorithms_enabled=_deterministic_algorithms_enabled(),
    )


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not_installed"


def _model_revision() -> str:
    configured = os.getenv("COMPRESSOR_MODEL_REVISION")
    if configured:
        return configured
    revision_file = Path("/app/model_revision.txt")
    try:
        return revision_file.read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "local_or_unknown"


def _deterministic_algorithms_enabled() -> bool:
    torch = sys.modules.get("torch")
    if torch is None:
        return False
    check = getattr(torch, "are_deterministic_algorithms_enabled", None)
    return bool(check()) if callable(check) else False


def _git_commit() -> str:
    configured = os.getenv("COMPRESSOR_GIT_COMMIT") or os.getenv("GIT_COMMIT")
    if configured:
        return configured
    git_dir = Path(__file__).resolve().parents[1] / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = head[5:]
            ref_path = git_dir / ref
            if ref_path.exists():
                return ref_path.read_text(encoding="utf-8").strip()
            for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
                commit, _, name = line.partition(" ")
                if name == ref:
                    return commit
        return head
    except OSError:
        return "unknown"


def _source_sha256() -> str:
    configured = os.getenv("COMPRESSOR_SOURCE_SHA256")
    if configured:
        return configured
    root = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    try:
        for path in sorted(root.rglob("*.py")):
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    except OSError:
        return "unknown"
    return digest.hexdigest()
