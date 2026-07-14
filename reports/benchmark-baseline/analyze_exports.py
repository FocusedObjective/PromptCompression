"""Aggregate benchmark-export.v2 files into a privacy-conscious baseline.

The report intentionally excludes prompt text. It attributes compression and
literal-retention changes to the deterministic and LLMLingua-2 stages so later
pipeline versions can be compared on a fixed benchmark corpus.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.protected_spans import protected_spans_for_text  # noqa: E402


SAFETY_PATTERNS = {
    "negation": re.compile(r"\b(?:no|not|never|without|unless|except|cannot|can't|don't)\b", re.I),
    "obligation": re.compile(r"\b(?:must|shall|required|require|mandatory|should)\b", re.I),
    "scope": re.compile(r"\b(?:only|all|any|each|every|always)\b", re.I),
    "permission": re.compile(r"\b(?:may|might|can|could|allowed|prohibited)\b", re.I),
    "destructive_action": re.compile(r"\b(?:delete|remove|drop|overwrite|reset|alter|change)\b", re.I),
}
WORD_RE = re.compile(r"[A-Za-z0-9_]+(?:['’.-][A-Za-z0-9_]+)*")


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def occurrence_counter(pattern: re.Pattern[str], text: str) -> Counter[str]:
    return Counter(match.group(0).lower() for match in pattern.finditer(text))


def protected_counter(text: str) -> Counter[tuple[str, str]]:
    return Counter((span.kind, span.text) for span in protected_spans_for_text(text))


def retained(source: Counter[Any], target: Counter[Any]) -> Counter[Any]:
    return Counter({key: min(count, target[key]) for key, count in source.items()})


def count_by_kind(values: Counter[tuple[str, str]]) -> Counter[str]:
    totals: Counter[str] = Counter()
    for (kind, _), count in values.items():
        totals[kind] += count
    return totals


def ratio(numerator: float, denominator: float) -> float | None:
    return numerator / denominator if denominator else None


def nonempty_constraint_record(record: dict[str, Any]) -> bool:
    constraints = record.get("evaluation_constraints") or {}
    return any(bool(value) for value in constraints.values())


def analyze_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    original_tokens = sum(int(record.get("original_tokens") or 0) for record in records)
    deterministic_tokens = sum(
        int((record.get("stages") or {}).get("deterministicTokens") or record.get("original_tokens") or 0)
        for record in records
    )
    final_tokens = sum(int(record.get("final_tokens") or 0) for record in records)
    deterministic_saved = original_tokens - deterministic_tokens
    model_saved = deterministic_tokens - final_tokens
    total_saved = original_tokens - final_tokens

    protected_total: Counter[str] = Counter()
    protected_after_deterministic: Counter[str] = Counter()
    protected_after_final: Counter[str] = Counter()
    protected_model_input: Counter[str] = Counter()
    protected_after_model: Counter[str] = Counter()
    safety_total: Counter[str] = Counter()
    safety_after_deterministic: Counter[str] = Counter()
    safety_after_final: Counter[str] = Counter()
    safety_model_input: Counter[str] = Counter()
    safety_after_model: Counter[str] = Counter()
    deleted_words: Counter[str] = Counter()
    deleted_word_documents: Counter[str] = Counter()
    transform_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "candidate_count": 0,
            "applied_count": 0,
            "tokens_saved": 0,
            "elapsed_ms": 0.0,
            "record_statuses": Counter(),
            "gate_reasons": Counter(),
            "gated_estimated_tokens": Counter(),
            "counterfactual_would_apply_records": 0,
            "counterfactual_estimated_tokens": 0,
        }
    )
    opportunity_totals: Counter[str] = Counter()
    opportunity_class_totals: Counter[str] = Counter()
    reductions: list[float] = []
    latencies: list[float] = []
    model_latencies: list[float] = []
    per_tenant: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)

    integrity_failures = 0
    json_applicable = 0
    json_failures = 0
    protected_applicable = 0
    protected_failures = 0
    placeholder_failures = 0
    structural_warning_records = 0
    structural_warning_count = 0
    structural_warning_types: Counter[str] = Counter()
    protected_missing_by_type: Counter[str] = Counter()
    protected_changed_by_type: Counter[str] = Counter()
    constraint_evaluated = 0
    constraint_failures = 0
    required_term_evaluated = 0
    required_term_failures = 0
    model_called = 0
    deterministic_changed = 0
    final_changed_after_model = 0
    output_rollbacks = 0
    output_rollback_reasons: Counter[str] = Counter()

    for record in records:
        stages = record.get("stages") or {}
        original = record.get("original_text") or ""
        deterministic = stages.get("deterministicText")
        if deterministic is None:
            deterministic = original
        final = record.get("final_text") or ""
        if stages.get("modelCalled"):
            model_called += 1
            model_latencies.append(float(record.get("latency_ms") or 0))
        if deterministic != original:
            deterministic_changed += 1
        if final != deterministic:
            final_changed_after_model += 1

        original_protected = protected_counter(original)
        deterministic_protected = protected_counter(deterministic)
        final_protected = protected_counter(final)
        protected_total.update(count_by_kind(original_protected))
        protected_after_deterministic.update(
            count_by_kind(retained(original_protected, deterministic_protected))
        )
        protected_after_final.update(count_by_kind(retained(original_protected, final_protected)))
        protected_model_input.update(count_by_kind(deterministic_protected))
        protected_after_model.update(count_by_kind(retained(deterministic_protected, final_protected)))

        for category, pattern in SAFETY_PATTERNS.items():
            original_values = occurrence_counter(pattern, original)
            deterministic_values = occurrence_counter(pattern, deterministic)
            final_values = occurrence_counter(pattern, final)
            safety_total[category] += sum(original_values.values())
            safety_after_deterministic[category] += sum(retained(original_values, deterministic_values).values())
            safety_after_final[category] += sum(retained(original_values, final_values).values())
            safety_model_input[category] += sum(deterministic_values.values())
            safety_after_model[category] += sum(retained(deterministic_values, final_values).values())

        if stages.get("modelCalled"):
            removed = Counter(token.lower() for token in WORD_RE.findall(deterministic))
            removed.subtract(Counter(token.lower() for token in WORD_RE.findall(final)))
            removed = +removed
            deleted_words.update(removed)
            deleted_word_documents.update(removed.keys())

        for transform in stages.get("deterministicTransforms") or []:
            name = str(transform.get("transform") or "unknown")
            stats = transform_stats[name]
            stats["candidate_count"] += int(transform.get("candidate_count") or 0)
            stats["applied_count"] += int(transform.get("applied_count") or 0)
            stats["tokens_saved"] += int(transform.get("tokens_saved") or 0)
            stats["elapsed_ms"] += float(transform.get("elapsed_ms") or 0)
            stats["record_statuses"][str(transform.get("status") or "unknown")] += 1
            stats["gate_reasons"].update(transform.get("gate_reason_counts") or {})
            stats["gated_estimated_tokens"].update(
                transform.get("gate_reason_estimated_tokens_saved") or {}
            )
            counterfactual = transform.get("counterfactual") or {}
            if counterfactual.get("would_apply"):
                stats["counterfactual_would_apply_records"] += 1
            stats["counterfactual_estimated_tokens"] += int(
                counterfactual.get("estimated_tokens_saved") or 0
            )

        for key, value in (record.get("candidateOpportunities") or {}).items():
            if isinstance(value, (int, float)):
                opportunity_totals[key] += value
            elif isinstance(value, dict):
                opportunity_class_totals.update(
                    {f"{key}.{child_key}": child_value for child_key, child_value in value.items()}
                )
        integrity = record.get("integrity") or {}
        validation = record.get("validation") or {}
        if not validation.get("integrityPassed", True):
            integrity_failures += 1
        if integrity.get("json_round_trip_applicable"):
            json_applicable += 1
            if not integrity.get("json_round_trip_validation_passed", True):
                json_failures += 1
        span_count = sum((integrity.get("protected_span_count_by_type") or {}).values())
        if span_count:
            protected_applicable += 1
        if not integrity.get("protected_span_validation_passed", True):
            protected_failures += 1
        protected_missing_by_type.update(integrity.get("protected_spans_missing_by_type") or {})
        protected_changed_by_type.update(integrity.get("protected_spans_changed_by_type") or {})
        if not integrity.get("placeholder_restoration_validation_passed", True):
            placeholder_failures += 1
        warnings = integrity.get("structural_validation_warnings") or []
        if warnings:
            structural_warning_records += 1
            structural_warning_count += len(warnings)
            structural_warning_types.update(str(warning) for warning in warnings)
        diagnostics = record.get("diagnostics") or {}
        rollback_count = int(diagnostics.get("output_rollback_count") or 0)
        output_rollbacks += rollback_count
        if rollback_count:
            output_rollback_reasons[
                str(diagnostics.get("output_rollback_reason") or "unknown")
            ] += rollback_count
        if nonempty_constraint_record(record):
            constraint_evaluated += 1
            if not (record.get("evaluation_constraint_results") or {}).get("passed", False):
                constraint_failures += 1
        if record.get("required_terms_retained") is not None:
            required_term_evaluated += 1
            if not record.get("required_terms_retained"):
                required_term_failures += 1

        original_count = int(record.get("original_tokens") or 0)
        final_count = int(record.get("final_tokens") or 0)
        reductions.append((original_count - final_count) / original_count if original_count else 0)
        latencies.append(float(record.get("latency_ms") or 0))
        provenance = record.get("provenance") or {}
        settings = provenance.get("resolved_compression_settings") or {}
        tenant_key = (
            str(record.get("tenant_id") or "unknown"),
            str(settings.get("tenant_profile_id") or "unknown"),
        )
        tenant = per_tenant[tenant_key]
        tenant["records"] += 1
        tenant["original_tokens"] += original_count
        tenant["deterministic_tokens_saved"] += int(stages.get("deterministicTokensSaved") or 0)
        tenant["model_tokens_saved"] += int(stages.get("modelIncrementalTokensSaved") or 0)
        tenant["integrity_failures"] += int(not validation.get("integrityPassed", True))

    protected = {}
    for kind in sorted(protected_total):
        total = protected_total[kind]
        protected[kind] = {
            "original_occurrences": total,
            "retained_after_deterministic": protected_after_deterministic[kind],
            "retained_after_final": protected_after_final[kind],
            "deterministic_retention": ratio(protected_after_deterministic[kind], total),
            "final_retention": ratio(protected_after_final[kind], total),
            "model_input_occurrences": protected_model_input[kind],
            "retained_across_model": protected_after_model[kind],
            "model_stage_retention": ratio(protected_after_model[kind], protected_model_input[kind]),
        }

    safety = {}
    for category in SAFETY_PATTERNS:
        total = safety_total[category]
        safety[category] = {
            "original_occurrences": total,
            "retained_after_deterministic": safety_after_deterministic[category],
            "retained_after_final": safety_after_final[category],
            "deterministic_retention": ratio(safety_after_deterministic[category], total),
            "final_retention": ratio(safety_after_final[category], total),
            "model_input_occurrences": safety_model_input[category],
            "retained_across_model": safety_after_model[category],
            "model_stage_retention": ratio(safety_after_model[category], safety_model_input[category]),
        }

    transforms = {}
    for name, values in sorted(transform_stats.items()):
        transforms[name] = {
            **{key: value for key, value in values.items() if not isinstance(value, Counter)},
            "elapsed_ms": round(values["elapsed_ms"], 3),
            "record_statuses": dict(values["record_statuses"]),
            "gate_reasons": dict(values["gate_reasons"]),
            "gated_estimated_tokens": dict(values["gated_estimated_tokens"]),
        }

    tenants = []
    for (tenant_id, profile_id), values in sorted(per_tenant.items()):
        original = values["original_tokens"]
        total = values["deterministic_tokens_saved"] + values["model_tokens_saved"]
        tenants.append(
            {
                "tenant_id": tenant_id,
                "tenant_profile_id": profile_id,
                **dict(values),
                "total_reduction": ratio(total, original),
                "integrity_failure_rate": ratio(values["integrity_failures"], values["records"]),
            }
        )

    return {
        "records": len(records),
        "stage_savings": {
            "original_tokens": original_tokens,
            "deterministic_tokens": deterministic_tokens,
            "final_tokens": final_tokens,
            "deterministic_tokens_saved": deterministic_saved,
            "model_tokens_saved": model_saved,
            "total_tokens_saved": total_saved,
            "deterministic_reduction": ratio(deterministic_saved, original_tokens),
            "model_incremental_reduction": ratio(model_saved, deterministic_tokens),
            "total_reduction": ratio(total_saved, original_tokens),
            "deterministic_share_of_savings": ratio(deterministic_saved, total_saved),
        },
        "pathways": {
            "model_called_records": model_called,
            "model_call_rate": ratio(model_called, len(records)),
            "deterministic_changed_records": deterministic_changed,
            "model_changed_records": final_changed_after_model,
            "unchanged_records": sum(
                (record.get("original_text") or "") == (record.get("final_text") or "")
                for record in records
            ),
        },
        "integrity": {
            "integrity_failures": integrity_failures,
            "integrity_failure_rate": ratio(integrity_failures, len(records)),
            "json_applicable_records": json_applicable,
            "json_failures": json_failures,
            "json_failure_rate_when_applicable": ratio(json_failures, json_applicable),
            "protected_span_applicable_records": protected_applicable,
            "protected_span_failures": protected_failures,
            "protected_span_failure_rate_when_applicable": ratio(protected_failures, protected_applicable),
            "protected_spans_missing_by_type": dict(sorted(protected_missing_by_type.items())),
            "protected_spans_changed_by_type": dict(sorted(protected_changed_by_type.items())),
            "placeholder_failures": placeholder_failures,
            "structural_warning_records": structural_warning_records,
            "structural_warning_count": structural_warning_count,
            "structural_warning_types": dict(sorted(structural_warning_types.items())),
            "constraint_evaluated_records": constraint_evaluated,
            "constraint_coverage": ratio(constraint_evaluated, len(records)),
            "constraint_failures": constraint_failures,
            "required_term_evaluated_records": required_term_evaluated,
            "required_term_coverage": ratio(required_term_evaluated, len(records)),
            "required_term_failures": required_term_failures,
            "output_rollbacks": output_rollbacks,
            "output_rollback_reasons": dict(sorted(output_rollback_reasons.items())),
        },
        "protected_span_retention": protected,
        "safety_lexeme_retention": safety,
        "deterministic_transforms": transforms,
        "candidate_opportunities": {
            **dict(sorted(opportunity_totals.items())),
            **dict(sorted(opportunity_class_totals.items())),
        },
        "distributions": {
            "record_reduction_p50": percentile(reductions, 0.50),
            "record_reduction_p95": percentile(reductions, 0.95),
            "latency_ms_p50": percentile(latencies, 0.50),
            "latency_ms_p95": percentile(latencies, 0.95),
            "model_called_latency_ms_p50": percentile(model_latencies, 0.50),
            "model_called_latency_ms_p95": percentile(model_latencies, 0.95),
            "latency_ms_per_1k_tokens_saved": ratio(sum(latencies), total_saved / 1000),
            "model_latency_ms_per_1k_model_tokens_saved": ratio(sum(model_latencies), model_saved / 1000),
        },
        "model_deleted_words_top": [
            {"token": token, "occurrences_removed": count, "affected_records": deleted_word_documents[token]}
            for token, count in deleted_words.most_common(50)
        ],
        "tenants": tenants,
    }


def read_export(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != "benchmark-export.v2":
        raise ValueError(f"{path}: expected benchmark-export.v2")
    return payload


def provenance_summary(records: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    fields = ("compressor_git_commit", "deployment_version", "model_checkpoint", "model_revision", "configuration_sha256")
    return {
        field: sorted({str((record.get("provenance") or {}).get(field) or "unknown") for record in records})
        for field in fields
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("exports", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    cohorts = []
    all_records: list[dict[str, Any]] = []
    for path in args.exports:
        payload = read_export(path)
        records = payload.get("records") or []
        all_records.extend(records)
        records_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in records:
            records_by_condition[str(record.get("condition_id") or "unknown")].append(record)
        cohorts.append(
            {
                "source_file": path.name,
                "cohort_id": (payload.get("cohort") or {}).get("cohort_id"),
                "exported_at": payload.get("exported_at"),
                "configuration": payload.get("configuration"),
                "provenance": provenance_summary(records),
                "metrics": analyze_records(records),
                "conditions": {
                    condition_id: analyze_records(condition_records)
                    for condition_id, condition_records in sorted(records_by_condition.items())
                },
            }
        )

    report = {
        "schema_version": "compression-baseline.v1",
        "privacy": "Aggregate metrics only; prompt text is excluded.",
        "cohorts": cohorts,
        "combined": {
            "warning": "Use only as a descriptive baseline; cohort mixes may differ over time.",
            "provenance": provenance_summary(all_records),
            "metrics": analyze_records(all_records),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "cohorts": len(cohorts), "records": len(all_records)}, indent=2))


if __name__ == "__main__":
    main()
