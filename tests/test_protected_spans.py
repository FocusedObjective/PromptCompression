from app.protected_spans import force_tokens_for_text, protected_spans_for_text


def test_force_tokens_include_structure_and_negation():
    tokens = force_tokens_for_text("Do not delete this.")
    assert "." in tokens
    assert "not" in tokens


def test_force_tokens_include_urls_and_numbers():
    tokens = force_tokens_for_text("Visit https://example.com and pay $15 by 2026-06-23.")
    assert "https://example.com" in tokens
    assert "$15" in tokens
    assert "2026-06-23" in tokens


def test_force_tokens_are_capped_for_large_inputs():
    text = " ".join(f"https://example.com/{index} {index}" for index in range(200))

    tokens = force_tokens_for_text(text, max_tokens=100)

    assert len(tokens) == 100
    assert "not" in tokens
    assert "https://example.com/0" in tokens


def test_protected_spans_include_exact_money_ids_and_constraints():
    text = "Do not delete ORD-7781 before paying $15,000 by 2026-08-15."

    spans = protected_spans_for_text(text)

    assert [(span.text, span.kind) for span in spans] == [
        ("Do not delete", "constraint"),
        ("ORD-7781", "identifier"),
        ("$15,000", "money"),
        ("2026-08-15", "number"),
    ]


def test_protected_spans_keep_longest_non_overlapping_match():
    text = "The account ORD-7781 costs $15,000."

    spans = protected_spans_for_text(text)

    assert [span.text for span in spans] == ["ORD-7781", "$15,000"]


def test_protected_spans_include_markdown_citations_and_templates():
    text = (
        'Return [the guide](https://example.com/guide), '
        '[citation: Guide.pdf, page: 8], {{ customer.name }}, '
        '${account_id}, {request_id}, and {% if enabled %}.'
    )

    spans = protected_spans_for_text(text)

    assert [(span.text, span.kind) for span in spans] == [
        ("[the guide](https://example.com/guide)", "markdown_link"),
        ("[citation: Guide.pdf, page: 8]", "citation"),
        ("{{ customer.name }}", "template"),
        ("${account_id}", "template"),
        ("{request_id}", "template"),
        ("{% if enabled %}", "template"),
    ]
