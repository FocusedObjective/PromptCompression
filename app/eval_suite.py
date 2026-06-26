import json
from dataclasses import dataclass, field
from pathlib import Path

from app.compressor import CompressionResult

DEFAULT_EVAL_CASES_PATH = Path(__file__).resolve().parents[1] / "data" / "eval_cases.json"


@dataclass(frozen=True)
class EvalCase:
    id: str
    title: str
    category: str
    description: str
    text: str
    default_aggressiveness: float
    required_substrings: list[str] = field(default_factory=list)
    forbidden_substrings: list[str] = field(default_factory=list)
    expected_section_kinds: list[str] = field(default_factory=list)
    target_min_reduction: float | None = None
    max_elapsed_ms: float | None = None


@dataclass(frozen=True)
class QualityCheck:
    id: str
    label: str
    passed: bool
    severity: str
    detail: str


def load_eval_cases(path: Path = DEFAULT_EVAL_CASES_PATH) -> list[EvalCase]:
    raw_cases = json.loads(path.read_text(encoding="utf-8"))
    cases: list[EvalCase] = []
    for raw_case in raw_cases:
        cases.append(
            EvalCase(
                id=raw_case["id"],
                title=raw_case["title"],
                category=raw_case["category"],
                description=raw_case["description"],
                text=raw_case["text"],
                default_aggressiveness=float(raw_case["default_aggressiveness"]),
                required_substrings=list(raw_case.get("required_substrings", [])),
                forbidden_substrings=list(raw_case.get("forbidden_substrings", [])),
                expected_section_kinds=list(raw_case.get("expected_section_kinds", [])),
                target_min_reduction=raw_case.get("target_min_reduction"),
                max_elapsed_ms=raw_case.get("max_elapsed_ms"),
            )
        )
    return cases


def evaluate_compression(
    case: EvalCase,
    result: CompressionResult,
) -> list[QualityCheck]:
    checks: list[QualityCheck] = []
    compressed_text = result.compressed_text

    checks.append(
        QualityCheck(
            id="non_empty_output",
            label="Compressed output is non-empty",
            passed=bool(compressed_text.strip()),
            severity="error",
            detail="Output contains text." if compressed_text.strip() else "Output is empty.",
        )
    )
    checks.append(
        QualityCheck(
            id="not_larger_than_input",
            label="Output is not larger than input",
            passed=result.compressed_tokens <= result.original_tokens,
            severity="error",
            detail=(
                f"{result.original_tokens} -> {result.compressed_tokens} estimated tokens."
            ),
        )
    )

    for index, required in enumerate(case.required_substrings):
        checks.append(
            QualityCheck(
                id=f"required_{index}",
                label=f"Preserves required text: {required}",
                passed=required in compressed_text,
                severity="error",
                detail=(
                    "Found exact substring."
                    if required in compressed_text
                    else "Missing exact substring."
                ),
            )
        )

    for index, forbidden in enumerate(case.forbidden_substrings):
        checks.append(
            QualityCheck(
                id=f"forbidden_{index}",
                label=f"Removes forbidden text: {forbidden}",
                passed=forbidden not in compressed_text,
                severity="error",
                detail=(
                    "Forbidden substring is absent."
                    if forbidden not in compressed_text
                    else "Forbidden substring is still present."
                ),
            )
        )

    section_kinds = {section.kind for section in result.output_sections}
    for expected_kind in case.expected_section_kinds:
        checks.append(
            QualityCheck(
                id=f"section_{expected_kind}",
                label=f"Includes expected section type: {expected_kind}",
                passed=expected_kind in section_kinds,
                severity="error",
                detail=(
                    f"Seen section types: {', '.join(sorted(section_kinds)) or 'none'}."
                ),
            )
        )

    if case.target_min_reduction is not None:
        checks.append(
            QualityCheck(
                id="target_min_reduction",
                label=f"Meets target reduction: {case.target_min_reduction:.0%}",
                passed=result.reduction >= case.target_min_reduction,
                severity="warning",
                detail=f"Observed reduction: {result.reduction:.1%}.",
            )
        )

    if case.max_elapsed_ms is not None:
        checks.append(
            QualityCheck(
                id="max_elapsed_ms",
                label=f"Within target latency: {case.max_elapsed_ms:.0f} ms",
                passed=result.elapsed_ms <= case.max_elapsed_ms,
                severity="warning",
                detail=f"Observed latency: {result.elapsed_ms:.1f} ms.",
            )
        )

    return checks


def quality_passed(checks: list[QualityCheck]) -> bool:
    return all(check.passed for check in checks if check.severity == "error")
