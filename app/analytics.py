from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from app.protected_spans import protected_spans_for_text
from app.version import DEPLOYMENT_VERSION


DIAGNOSTICS_SCHEMA_VERSION = "compression-diagnostics.v2"
BENCHMARK_SCHEMA_VERSION = "benchmark.v2"

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
    status: str
    reason: str
    elapsed_ms: float


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
    deployment_version: str
    model_checkpoint: str
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


@dataclass(frozen=True)
class DetailedAnalytics:
    diagnostics_schema_version: str
    original_sha256: str
    deterministic_text: str
    deterministic_sha256: str
    deterministic_characters: int
    deterministic_tokens: int
    deterministic_tokens_saved: int
    deterministic_transforms: list[TransformDiagnostic]
    deterministic_gate_reasons: dict[str, int]
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
    integrity: IntegrityValidation
    provenance: CompressionProvenance


def build_detailed_analytics(
    *,
    service: Any,
    input_text: str,
    preprocessed_text: str,
    force_dropped_text: str,
    pipeline_text: str,
    deterministic_text: str,
    final_text: str,
    prepared_segments: list[Any],
    final_segments: list[Any],
    profile: Any,
    token_estimator: str,
    original_tokens: int,
    deterministic_tokens: int,
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
) -> DetailedAnalytics:
    def estimate(value: str) -> int:
        return service.estimate_compression_tokens(value, profile).count
    transforms = _transform_diagnostics(
        service=service,
        input_text=input_text,
        preprocessed_text=preprocessed_text,
        force_dropped_text=force_dropped_text,
        pipeline_text=pipeline_text,
        deterministic_text=deterministic_text,
        final_text=final_text,
        prepared_segments=prepared_segments,
        final_segments=final_segments,
        profile=profile,
        estimate=estimate,
        placeholder_tokens=placeholder_tokens,
        duplicate_candidate_count=duplicate_candidate_count,
        duplicate_candidate_tokens=duplicate_candidate_tokens,
    )
    opportunities = _candidate_opportunities(
        service,
        input_text,
        final_segments,
        duplicate_candidate_count,
        duplicate_candidate_tokens,
    )
    gate_reasons: Counter[str] = Counter()
    for transform in transforms:
        if transform.status == "applied":
            continue
        gate_reasons[transform.reason] += (
            1
            if transform.status == "no_candidate"
            else max(1, transform.candidate_count - transform.applied_count)
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
    incremental_saved = max(0, deterministic_tokens - final_tokens)
    deterministic_saved = max(0, original_tokens - deterministic_tokens)
    component_saved = sum(
        item.tokens_saved
        for item in transforms
        if item.transform != "placeholder_restoration"
    )
    deterministic_residual = deterministic_saved - component_saved
    return DetailedAnalytics(
        diagnostics_schema_version=DIAGNOSTICS_SCHEMA_VERSION,
        original_sha256=sha256_text(input_text),
        deterministic_text=deterministic_text,
        deterministic_sha256=sha256_text(deterministic_text),
        deterministic_characters=len(deterministic_text),
        deterministic_tokens=deterministic_tokens,
        deterministic_tokens_saved=deterministic_saved,
        deterministic_transforms=transforms,
        deterministic_gate_reasons=dict(sorted(gate_reasons.items())),
        deterministic_component_tokens_saved=component_saved,
        deterministic_attribution_residual_tokens=deterministic_residual,
        deterministic_attribution_residual_reason=(
            None
            if deterministic_residual == 0
            else "token_estimator_non_additivity_or_overlapping_transforms"
        ),
        candidate_opportunities=opportunities,
        model_input_sha256=sha256_text(deterministic_text),
        model_input_characters=len(deterministic_text),
        model_input_tokens=deterministic_tokens,
        final_sha256=sha256_text(final_text),
        final_characters=len(final_text),
        final_tokens=final_tokens,
        model_incremental_tokens_saved=incremental_saved,
        model_incremental_reduction=(
            0.0 if deterministic_tokens <= 0 else incremental_saved / deterministic_tokens
        ),
        model_called=model_called,
        model_call_count=model_call_count,
        model_chunk_count=model_chunk_count,
        target_retention_rate=target_rate,
        effective_retention_rate=(
            1.0 if deterministic_tokens <= 0 else final_tokens / deterministic_tokens
        ),
        force_token_count=force_token_count,
        protected_placeholder_count=len(placeholder_tokens),
        model_skip_or_fallback_reason=model_reason,
        attribution_residual_tokens=attribution_residual_tokens,
        attribution_residual_reason=(
            None if attribution_residual_tokens == 0 else "token_estimator_non_additivity"
        ),
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
    deterministic_text: str,
    final_text: str,
    prepared_segments: list[Any],
    final_segments: list[Any],
    profile: Any,
    estimate: Callable[[str], int],
    placeholder_tokens: list[str],
    duplicate_candidate_count: int,
    duplicate_candidate_tokens: int,
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
    ) -> None:
        source_tokens = estimate(source) if source else 0
        output_tokens = estimate(output) if output else 0
        saved = max(0, source_tokens - output_tokens)
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
                status=status,
                reason=stable_reason,
                elapsed_ms=(
                    0.0 if started is None else (time.perf_counter() - started) * 1000
                ),
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
        if code == "json_minification" and not service.preprocessor.enable_json_minify:
            reason = "tenant_configuration_disabled"
        elif candidates and source == output:
            reason = "no_token_savings"
        add(code, source, output, candidate_count, reason=reason, started=started)

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
        deterministic_text,
        protected_count,
        applied_count=int(bool(protected_count and pipeline_text != deterministic_text)),
        reason="inside_protected_span",
        started=started,
    )
    started = time.perf_counter()
    restored_count = sum(token not in final_text for token in placeholder_tokens)
    add(
        "placeholder_restoration",
        deterministic_text,
        final_text,
        protected_count,
        applied_count=restored_count,
        reason="transform_failed" if restored_count != protected_count else None,
        started=started,
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
        deployment_version=DEPLOYMENT_VERSION,
        model_checkpoint=service.model_name,
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
    )


def _package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "not_installed"


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
