from scripts.run_deterministic_experiments import (
    condition_matrix,
    evaluate_downstream_checks,
    export_error_record,
)


def test_guardrail_ablation_keeps_deterministic_transforms_identical():
    conditions = condition_matrix(
        "critical_clause_shielding_off_ablation",
        include_model=True,
    )

    assert len(conditions) == 4
    assert all(condition["apply_deterministic"] for condition in conditions)
    assert [condition["profile"] for condition in conditions] == [
        "baseline",
        "critical_clause_shielding_off_ablation",
        "baseline",
        "critical_clause_shielding_off_ablation",
    ]


def test_downstream_checks_cover_relationship_order_and_format():
    case = {
        "downstream_checks": [
            {
                "id": "incident_relationship",
                "category": "relationship",
                "required_substrings": ["CACHE-440", "pin shard-affinity", "18:00 UTC"],
                "ordered": True,
                "same_paragraph": True,
            },
            {
                "id": "answer_format",
                "category": "required_format",
                "required_substrings": ["incident id, mitigation, deadline"],
            },
        ]
    }
    output = (
        "CACHE-440 uses pin shard-affinity until 18:00 UTC.\n\n"
        "Answer format: incident id, mitigation, deadline."
    )

    result = evaluate_downstream_checks(case, output)

    assert result["applicable"] is True
    assert result["passed"] is True
    assert result["categories"] == {
        "relationship": {"checks": 1, "failures": 0},
        "required_format": {"checks": 1, "failures": 0},
    }


def test_ordered_check_uses_a_later_occurrence_when_an_earlier_one_is_out_of_order():
    case = {
        "downstream_checks": [
            {
                "id": "incident_relationship",
                "category": "relationship",
                "required_substrings": ["CACHE-440", "search ranking drift"],
                "ordered": True,
            }
        ]
    }

    result = evaluate_downstream_checks(
        case,
        "Question about search ranking drift. CACHE-440 owns search ranking drift.",
    )

    assert result["passed"] is True


def test_downstream_relationship_fails_when_terms_are_separated():
    case = {
        "downstream_checks": [
            {
                "id": "credit_rule",
                "category": "permission",
                "required_substrings": ["service credit", "only if", "240 minutes"],
                "same_paragraph": True,
            }
        ]
    }

    result = evaluate_downstream_checks(
        case,
        "A service credit is possible.\n\nOnly if support approves.\n\n240 minutes.",
    )

    assert result["passed"] is False
    assert result["categories"]["permission"]["failures"] == 1


def test_error_export_preserves_reason_and_timeout_classification():
    record = export_error_record(
        case={"id": "case-1", "category": "test", "text": "input"},
        repeat=2,
        condition={
            "condition_id": "condition-1",
            "profile": "baseline",
            "mode": "model_force",
            "apply_deterministic": True,
        },
        tenant_id="tenant-a",
        constraints={
            "required_substrings": [],
            "required_whitespace_insensitive_substrings": [],
            "forbidden_substrings": [],
            "required_json_keys": [],
        },
        error=TimeoutError("model timeout after 300 seconds"),
        elapsed_ms=300_000,
    )

    assert record["status"] == "error"
    assert record["error_class"] == "TimeoutError"
    assert record["error_reason"] == "model timeout after 300 seconds"
    assert record["timed_out"] is True
    assert record["validation"]["integrityPassed"] is None
