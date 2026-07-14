from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.compressor import COMPRESSION_MODE_DETERMINISTIC, PromptCompressionService  # noqa: E402
from app.experiment_profiles import ExperimentProfile  # noqa: E402
from app.tenant_profiles import build_tenant_profile  # noqa: E402
from scripts.run_deterministic_experiments import (  # noqa: E402
    export_record,
    load_cases,
    write_profile_summary,
)


def toon_profiles() -> list[ExperimentProfile]:
    profiles = []
    for characters in (120, 200, 300):
        for lines in (2, 3, 4):
            for tokens in (16, 32):
                for reduction in (0.05, 0.08):
                    profiles.append(
                        ExperimentProfile(
                            profile_id=(
                                f"toon_c{characters}_l{lines}_t{tokens}_r"
                                f"{int(reduction * 100)}"
                            ),
                            enable_critical_clause_shielding=True,
                            require_tokenizer_backed_gates=True,
                            min_toon_characters=characters,
                            min_toon_lines=lines,
                            min_toon_savings_tokens=tokens,
                            min_toon_reduction=reduction,
                        )
                    )
    return profiles


def html_profiles() -> list[ExperimentProfile]:
    return [
        ExperimentProfile(
            profile_id=f"html_c{characters}_t16_r20",
            enable_critical_clause_shielding=True,
            require_tokenizer_backed_gates=True,
            min_html_characters=characters,
            min_html_savings_tokens=16,
            min_html_reduction=0.20,
        )
        for characters in (300, 500, 1000)
    ]


def run_matrix(
    *,
    matrix_id: str,
    profiles: list[ExperimentProfile],
    service: PromptCompressionService,
    cases: list[dict[str, Any]],
    repeats: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    conditions: list[tuple[str, str | ExperimentProfile]] = [
        (f"{matrix_id}__baseline", "baseline"),
        *((f"{matrix_id}__{profile.profile_id}", profile) for profile in profiles),
    ]
    for condition_id, profile in conditions:
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
                result = service.compress(
                    case["text"],
                    aggressiveness=float(case.get("default_aggressiveness", 0.25)),
                    include_sections=False,
                    tenant_profile=tenant_profile,
                    mode=COMPRESSION_MODE_DETERMINISTIC,
                    collect_diagnostics=True,
                    apply_deterministic_transforms=True,
                    evaluate_disabled_transforms=True,
                    evaluation_constraints=constraints,
                    request_id=f"{condition_id}:{case['id']}:{repeat}",
                    experiment_profile=profile,
                )
                records.append(
                    export_record(
                        case=case,
                        repeat=repeat,
                        condition={
                            "condition_id": condition_id,
                            "apply_deterministic": True,
                        },
                        tenant_id=tenant_id,
                        constraints=constraints,
                        result=result,
                    )
                )
    return {
        "schema_version": "benchmark-export.v2",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "cohort": {
            "cohort_id": f"fixed-eval-corpus-{matrix_id}",
            "profile": matrix_id,
            "repeats": repeats,
            "case_count": len(cases),
        },
        "configuration": {
            "fixed_corpus": "data/eval_cases.json",
            "matrix": matrix_id,
            "profiles": [profile.export() for profile in profiles],
            "repeats": repeats,
        },
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "reports" / "benchmark-baseline" / "experiments-2026-07-14",
    )
    args = parser.parse_args()
    if args.repeats < 3:
        parser.error("--repeats must be at least 3")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    service = PromptCompressionService()
    cases = load_cases(REPO_ROOT / "data" / "eval_cases.json")
    for matrix_id, profiles in (
        ("toon_threshold_matrix", toon_profiles()),
        ("html_threshold_matrix", html_profiles()),
    ):
        payload = run_matrix(
            matrix_id=matrix_id,
            profiles=profiles,
            service=service,
            cases=cases,
            repeats=args.repeats,
        )
        path = args.output_dir / f"{matrix_id}.json"
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        write_profile_summary(payload, args.output_dir / f"{matrix_id}.md")
        print(path)


if __name__ == "__main__":
    main()
