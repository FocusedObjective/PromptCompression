from app.boilerplate_discovery import BoilerplateRecord, discover_tenant_boilerplate


def test_discovery_requires_exact_repetition_across_conversations():
    phrase = "This report was generated automatically by Example Support Analytics."
    records = [
        BoilerplateRecord(f"r{index}", f"c{index}", f"{phrase}\n\nUnique {index}.")
        for index in range(3)
    ]

    candidates = discover_tenant_boilerplate(
        "tenant-a",
        records,
        estimate_tokens=lambda value: len(value.split()),
        minimum_records=3,
        minimum_fraction=1.0,
        minimum_tokens_per_record=8,
    )

    candidate = next(item for item in candidates if item.normalized_text == phrase)
    assert candidate.eligible is True


def test_discovery_rejects_instruction_and_protected_candidates():
    phrase = "Do not delete ticket UT-1042 before 2026-08-15."
    records = [
        BoilerplateRecord(f"r{index}", f"c{index}", phrase)
        for index in range(3)
    ]

    candidate = discover_tenant_boilerplate(
        "tenant-a",
        records,
        estimate_tokens=lambda value: len(value.split()),
        minimum_records=3,
        minimum_fraction=1.0,
        minimum_tokens_per_record=1,
    )[0]

    assert candidate.eligible is False
    assert "contains_protected_span_or_clause" in candidate.rejection_reasons
    assert "instruction_or_policy_bearing" in candidate.rejection_reasons
