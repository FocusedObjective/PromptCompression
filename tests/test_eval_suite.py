from app.compressor import CompressionOutputSection, CompressionResult, CompressionToken
from app.eval_suite import EvalCase, evaluate_compression, load_eval_cases, quality_passed


def build_result(
    compressed_text: str,
    *,
    output_sections: list[CompressionOutputSection] | None = None,
    reduction: float = 0.5,
    elapsed_ms: float = 100.0,
) -> CompressionResult:
    return CompressionResult(
        compressed_text=compressed_text,
        original_tokens=10,
        compressed_tokens=5,
        reduction=reduction,
        aggressiveness=0.25,
        target_rate=0.8,
        model="fake-model",
        elapsed_ms=elapsed_ms,
        labeled_tokens=[],
        output_sections=output_sections or [],
    )


def test_eval_cases_load_from_fixture():
    cases = load_eval_cases()

    assert len(cases) >= 6
    assert {case.id for case in cases} >= {
        "support_escalation_with_toon_data",
        "exact_json_schema_template",
        "tool_exchange_verbatim",
    }
    assert all(case.text for case in cases)


def test_eval_case_required_substrings_exist_in_original_text():
    cases = load_eval_cases()
    missing = [
        (case.id, required)
        for case in cases
        for required in case.required_substrings
        if required not in case.text
    ]

    assert missing == []


def test_evaluate_compression_passes_required_quality_checks():
    case = EvalCase(
        id="sample",
        title="Sample",
        category="test",
        description="Sample eval.",
        text="Original text",
        default_aggressiveness=0.25,
        required_substrings=["KEEP"],
        forbidden_substrings=["DROP"],
        expected_section_kinds=["prose"],
        target_min_reduction=0.25,
        max_elapsed_ms=200,
    )
    result = build_result(
        "KEEP compressed text",
        output_sections=[
            CompressionOutputSection(
                text="KEEP compressed text",
                kind="prose",
                compressed=True,
                protected=False,
                labeled_tokens=[
                    CompressionToken(text="KEEP", kept=True),
                ],
            )
        ],
    )

    checks = evaluate_compression(case, result)

    assert quality_passed(checks)
    assert all(check.passed for check in checks)


def test_evaluate_compression_fails_missing_required_text():
    case = EvalCase(
        id="sample",
        title="Sample",
        category="test",
        description="Sample eval.",
        text="Original text",
        default_aggressiveness=0.25,
        required_substrings=["KEEP"],
    )
    result = build_result("compressed text")

    checks = evaluate_compression(case, result)

    assert not quality_passed(checks)
    assert any(
        check.id == "required_0" and not check.passed and check.severity == "error"
        for check in checks
    )


def test_reduction_target_is_warning_not_quality_failure():
    case = EvalCase(
        id="sample",
        title="Sample",
        category="test",
        description="Sample eval.",
        text="Original text",
        default_aggressiveness=0.25,
        target_min_reduction=0.8,
    )
    result = build_result("compressed text", reduction=0.2)

    checks = evaluate_compression(case, result)

    assert quality_passed(checks)
    assert any(
        check.id == "target_min_reduction"
        and not check.passed
        and check.severity == "warning"
        for check in checks
    )
