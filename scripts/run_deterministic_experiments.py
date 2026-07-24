from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.compressor import (  # noqa: E402
    COMPRESSION_MODE_DETERMINISTIC,
    COMPRESSION_MODE_MODEL_FORCE,
    PromptCompressionService,
)
from app.experiment_profiles import EXPERIMENT_PROFILE_IDS  # noqa: E402
from app.tenant_profiles import build_tenant_profile  # noqa: E402


DEFAULT_PROFILES = (
    "strict_whitespace_token_positive",
    "json_minify_safe",
    "literal_aliases_safe",
)


def load_cases(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def condition_matrix(profile_id: str, include_model: bool) -> list[dict[str, Any]]:
    if profile_id == "critical_clause_shielding_off_ablation":
        conditions = [
            {
                "condition_id": f"{profile_id}__shielding_on_deterministic__det_on",
                "profile": "baseline",
                "mode": COMPRESSION_MODE_DETERMINISTIC,
                "apply_deterministic": True,
            },
            {
                "condition_id": f"{profile_id}__shielding_off_deterministic__det_on",
                "profile": profile_id,
                "mode": COMPRESSION_MODE_DETERMINISTIC,
                "apply_deterministic": True,
            },
        ]
        if include_model:
            conditions.extend(
                [
                    {
                        "condition_id": f"{profile_id}__shielding_on_model_force__det_on",
                        "profile": "baseline",
                        "mode": COMPRESSION_MODE_MODEL_FORCE,
                        "apply_deterministic": True,
                    },
                    {
                        "condition_id": f"{profile_id}__shielding_off_model_force__det_on",
                        "profile": profile_id,
                        "mode": COMPRESSION_MODE_MODEL_FORCE,
                        "apply_deterministic": True,
                    },
                ]
            )
        return conditions

    conditions = [
        {
            "condition_id": f"{profile_id}__baseline_deterministic__det_on",
            "profile": "baseline",
            "mode": COMPRESSION_MODE_DETERMINISTIC,
            "apply_deterministic": True,
        },
        {
            "condition_id": f"{profile_id}__experiment_deterministic__det_on",
            "profile": profile_id,
            "mode": COMPRESSION_MODE_DETERMINISTIC,
            "apply_deterministic": True,
        },
    ]
    if include_model:
        conditions.extend(
            [
                {
                    "condition_id": f"{profile_id}__model_only__det_off",
                    "profile": "baseline",
                    "mode": COMPRESSION_MODE_MODEL_FORCE,
                    "apply_deterministic": False,
                },
                {
                    "condition_id": f"{profile_id}__experiment_model_force__det_on",
                    "profile": profile_id,
                    "mode": COMPRESSION_MODE_MODEL_FORCE,
                    "apply_deterministic": True,
                },
            ]
        )
    return conditions


def run_profile(
    *,
    service: PromptCompressionService,
    profile_id: str,
    cases: list[dict[str, Any]],
    repeats: int,
    include_model: bool,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    conditions = condition_matrix(profile_id, include_model)
    for condition in conditions:
        for repeat in range(1, repeats + 1):
            for case_index, case in enumerate(cases):
                tenant_id = "benchmark_tenant_a" if case_index % 2 == 0 else "benchmark_tenant_b"
                tenant_profile = build_tenant_profile(
                    tenant_id=tenant_id,
                    profile_id=f"{tenant_id}:fixed-v1",
                )
                constraints = {
                    "required_substrings": case.get("required_substrings", []),
                    "required_whitespace_insensitive_substrings": case.get(
                        "required_whitespace_insensitive_substrings", []
                    ),
                    "forbidden_substrings": case.get("forbidden_substrings", []),
                    "required_json_keys": [],
                }
                started = time.perf_counter()
                try:
                    result = service.compress(
                        case["text"],
                        aggressiveness=float(case.get("default_aggressiveness", 0.25)),
                        include_sections=False,
                        tenant_profile=tenant_profile,
                        mode=condition["mode"],
                        collect_diagnostics=True,
                        apply_deterministic_transforms=condition["apply_deterministic"],
                        evaluate_disabled_transforms=True,
                        evaluation_constraints=constraints,
                        request_id=(
                            f"{profile_id}:{condition['condition_id']}:{case['id']}:{repeat}"
                        ),
                        experiment_profile=condition["profile"],
                    )
                except Exception as exc:
                    records.append(
                        export_error_record(
                            case=case,
                            repeat=repeat,
                            condition=condition,
                            tenant_id=tenant_id,
                            constraints=constraints,
                            error=exc,
                            elapsed_ms=(time.perf_counter() - started) * 1000,
                        )
                    )
                    continue
                records.append(
                    export_record(
                        case=case,
                        repeat=repeat,
                        condition=condition,
                        tenant_id=tenant_id,
                        constraints=constraints,
                        result=result,
                    )
                )

    return {
        "schema_version": "benchmark-export.v2",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "cohort": {
            "cohort_id": f"fixed-eval-corpus-{profile_id}",
            "profile": profile_id,
            "repeats": repeats,
            "case_count": len(cases),
        },
        "configuration": {
            "fixed_corpus": "data/eval_cases.json",
            "profile": profile_id,
            "conditions": conditions,
            "repeats": repeats,
        },
        "records": records,
    }


def export_record(
    *,
    case: dict[str, Any],
    repeat: int,
    condition: dict[str, Any],
    tenant_id: str,
    constraints: dict[str, list[str]],
    result: Any,
) -> dict[str, Any]:
    if result.diagnostics is None or result.diagnostics.analytics is None:
        raise RuntimeError("benchmark result is missing detailed analytics")
    analytics = result.diagnostics.analytics
    integrity = asdict(analytics.integrity)
    constraint_results = (
        None
        if analytics.evaluation_constraints is None
        else asdict(analytics.evaluation_constraints)
    )
    required_terms_retained = _required_terms_retained(
        result.compressed_text,
        constraints,
    )
    downstream_evaluation = evaluate_downstream_checks(
        case,
        result.compressed_text,
    )
    stages = {
        "deterministicText": analytics.deterministic_text,
        "deterministicSha256": analytics.deterministic_sha256,
        "deterministicCharacters": analytics.deterministic_characters,
        "deterministicTokens": analytics.deterministic_tokens,
        "deterministicTokensSaved": analytics.deterministic_tokens_saved,
        "deterministicTransforms": [
            asdict(transform) for transform in analytics.deterministic_transforms
        ],
        "modelInputSha256": analytics.model_input_sha256,
        "modelIncrementalTokensSaved": analytics.model_incremental_tokens_saved,
        "modelCalled": analytics.model_called,
        "finalSha256": analytics.final_sha256,
    }
    return {
        "schema_version": "benchmark.v3",
        "status": "ok",
        "prompt_id": case["id"],
        "case_id": case["id"],
        "category": case.get("category"),
        "condition_id": condition["condition_id"],
        "repeat": repeat,
        "tenant_id": tenant_id,
        "experiment_profile": result.experiment_profile,
        "compression_mode": result.compression_mode,
        "apply_deterministic_transforms": condition["apply_deterministic"],
        "original_text": case["text"],
        "final_text": result.compressed_text,
        "original_tokens": result.original_tokens,
        "final_tokens": result.compressed_tokens,
        "latency_ms": result.elapsed_ms,
        "stages": stages,
        "candidateOpportunities": asdict(analytics.candidate_opportunities),
        "integrity": integrity,
        "evaluation_constraints": constraints,
        "evaluation_constraint_results": constraint_results,
        "required_terms_retained": required_terms_retained,
        "downstream_evaluation": downstream_evaluation,
        "validation": {
            "integrityPassed": (
                analytics.integrity.protected_span_validation_passed
                and analytics.integrity.placeholder_restoration_validation_passed
                and analytics.integrity.json_round_trip_validation_passed
                and analytics.integrity.required_terms_validation_passed
            ),
            "constraintsPassed": (
                True if constraint_results is None else constraint_results["passed"]
            ),
            "downstreamPassed": downstream_evaluation["passed"],
        },
        "provenance": asdict(analytics.provenance),
        "diagnostics": {
            "output_rollback_count": result.diagnostics.output_rollback_count,
            "output_rollback_reason": result.diagnostics.output_rollback_reason,
            "rejected_output_sha256": result.diagnostics.rejected_output_sha256,
            "token_estimator": result.token_estimator,
            "timings": asdict(result.diagnostics.timings),
        },
    }


def export_error_record(
    *,
    case: dict[str, Any],
    repeat: int,
    condition: dict[str, Any],
    tenant_id: str,
    constraints: dict[str, list[str]],
    error: Exception,
    elapsed_ms: float,
) -> dict[str, Any]:
    error_class = type(error).__name__
    error_reason = str(error) or error_class
    timeout = isinstance(error, TimeoutError) or "timeout" in error_reason.lower()
    return {
        "schema_version": "benchmark.v3",
        "status": "error",
        "prompt_id": case["id"],
        "case_id": case["id"],
        "category": case.get("category"),
        "condition_id": condition["condition_id"],
        "repeat": repeat,
        "tenant_id": tenant_id,
        "experiment_profile": condition["profile"],
        "compression_mode": condition["mode"],
        "apply_deterministic_transforms": condition["apply_deterministic"],
        "original_text": case["text"],
        "final_text": None,
        "original_tokens": None,
        "final_tokens": None,
        "latency_ms": elapsed_ms,
        "stages": {},
        "candidateOpportunities": {},
        "integrity": {},
        "evaluation_constraints": constraints,
        "evaluation_constraint_results": None,
        "required_terms_retained": None,
        "downstream_evaluation": None,
        "validation": {
            "integrityPassed": None,
            "constraintsPassed": None,
            "downstreamPassed": None,
        },
        "provenance": {},
        "diagnostics": {},
        "error": error_reason,
        "error_class": error_class,
        "error_reason": error_reason,
        "timed_out": timeout,
    }


def evaluate_downstream_checks(
    case: dict[str, Any],
    output: str,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    paragraphs = [value for value in output.split("\n\n") if value.strip()]
    for index, raw_check in enumerate(case.get("downstream_checks", [])):
        required = list(raw_check.get("required_substrings", []))
        missing = [value for value in required if value not in output]
        ordered = bool(raw_check.get("ordered", False))
        order_passed = True
        if ordered and not missing:
            cursor = 0
            for value in required:
                position = output.find(value, cursor)
                if position < 0:
                    order_passed = False
                    break
                cursor = position + len(value)
        elif ordered:
            order_passed = False
        same_paragraph = bool(raw_check.get("same_paragraph", False))
        paragraph_passed = not same_paragraph or any(
            all(value in paragraph for value in required) for paragraph in paragraphs
        )
        results.append(
            {
                "id": str(raw_check.get("id") or f"downstream_{index}"),
                "category": str(raw_check.get("category") or "uncategorized"),
                "passed": not missing and order_passed and paragraph_passed,
                "missing_required_substrings": missing,
                "ordered": ordered,
                "order_passed": order_passed,
                "same_paragraph": same_paragraph,
                "same_paragraph_passed": paragraph_passed,
            }
        )

    categories: dict[str, dict[str, int]] = {}
    for result in results:
        category = result["category"]
        summary = categories.setdefault(category, {"checks": 0, "failures": 0})
        summary["checks"] += 1
        summary["failures"] += int(not result["passed"])
    return {
        "applicable": bool(results),
        "passed": all(result["passed"] for result in results),
        "categories": categories,
        "checks": results,
    }


def _required_terms_retained(
    output: str,
    constraints: dict[str, list[str]],
) -> bool:
    if any(term not in output for term in constraints["required_substrings"]):
        return False
    normalized_output = " ".join(output.split())
    return all(
        " ".join(term.split()) in normalized_output
        for term in constraints["required_whitespace_insensitive_substrings"]
    )


def write_profile_summary(payload: dict[str, Any], output_path: Path) -> None:
    records = payload["records"]
    by_condition: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_condition.setdefault(record["condition_id"], []).append(record)
    lines = [
        f"# {payload['cohort']['profile']} benchmark",
        "",
        f"Repeats: {payload['cohort']['repeats']}; cases: {payload['cohort']['case_count']}.",
        "",
        "| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for condition_id, condition_records in by_condition.items():
        tokens_saved = sum(
            record["original_tokens"] - record["final_tokens"]
            for record in condition_records
        )
        rollbacks = sum(
            record["diagnostics"]["output_rollback_count"]
            for record in condition_records
        )
        integrity_failures = sum(
            not record["validation"]["integrityPassed"]
            for record in condition_records
        )
        p50 = statistics.median(record["latency_ms"] for record in condition_records)
        lines.append(
            f"| `{condition_id}` | {len(condition_records)} | {tokens_saved} | "
            f"{rollbacks} | {integrity_failures} | {p50:.2f} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", nargs="+", default=list(DEFAULT_PROFILES))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--include-model", action="store_true")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "reports" / "benchmark-baseline" / "experiments-2026-07-14",
    )
    args = parser.parse_args()
    unknown = sorted(set(args.profiles) - set(EXPERIMENT_PROFILE_IDS))
    if unknown:
        parser.error(f"unknown profiles: {', '.join(unknown)}")
    if args.repeats < 3:
        parser.error("--repeats must be at least 3")

    cases = load_cases(REPO_ROOT / "data" / "eval_cases.json")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    service = PromptCompressionService()
    for profile_id in args.profiles:
        payload = run_profile(
            service=service,
            profile_id=profile_id,
            cases=cases,
            repeats=args.repeats,
            include_model=args.include_model,
        )
        suffix = "full-matrix" if args.include_model else "deterministic-matrix"
        export_path = args.output_dir / f"{profile_id}-{suffix}.json"
        export_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        write_profile_summary(
            payload,
            args.output_dir / f"{profile_id}-{suffix}.md",
        )
        print(export_path)


if __name__ == "__main__":
    main()
