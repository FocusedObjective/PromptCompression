from app.protected_spans import (
    MACHINE_CRITICAL_SPAN_KINDS,
    critical_clause_spans,
    force_tokens_for_text,
    inline_code_spans,
    protected_spans_for_text,
)


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


def test_machine_critical_filter_excludes_unmarked_policy_values():
    text = (
        "Do not delete ORD-7781 before paying $15,000 by 2026-08-15. "
        "Use `sessions_spawn` at https://example.com/run."
    )

    spans = protected_spans_for_text(
        text,
        allowed_kinds=MACHINE_CRITICAL_SPAN_KINDS,
    )

    assert [(span.text, span.kind) for span in spans] == [
        ("ORD-7781", "identifier"),
        ("`sessions_spawn`", "inline_code"),
        ("https://example.com/run.", "url"),
    ]


def test_critical_clause_spans_preserve_policy_relationships_exactly():
    text = (
        "Context only. The customer may receive a credit only if the outage "
        "exceeds 240 minutes. Keep retry_limit at 3 unless legal approves a "
        "written amendment."
    )

    assert [span.text for span in critical_clause_spans(text)] == [
        "The customer may receive a credit only if the outage exceeds 240 minutes.",
        "Keep retry_limit at 3 unless legal approves a written amendment.",
    ]


def test_critical_clause_spans_include_never_imply_policy():
    text = "Never imply that credits are automatic. Summarize the remaining context."

    assert [span.text for span in critical_clause_spans(text)] == [
        "Never imply that credits are automatic.",
    ]


def test_critical_clause_spans_include_separated_ellipsis_terminator():
    text = (
        'Use only this skill and do not load another when the message is '
        '"Continue sourcing ...", then inspect the result.'
    )

    assert [span.text for span in critical_clause_spans(text)] == [
        'Use only this skill and do not load another when the message is '
        '"Continue sourcing ...',
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


def test_inline_code_spans_match_delimiter_run_lengths():
    text = (
        "Use ``code with ` inside`` and `simple`, but not \\`escaped\\` "
        "or \\``escaped run``."
    )

    spans = inline_code_spans(text)

    assert [span.text for span in spans] == [
        "``code with ` inside``",
        "`simple`",
    ]


def test_inline_code_spans_allow_multiline_content_and_escaped_closers():
    text = "Before `first line\nsecond \\` line` after."

    spans = inline_code_spans(text)

    assert [span.text for span in spans] == ["`first line\nsecond \\`"]


def test_url_span_stops_before_html_attribute_and_following_markup():
    text = (
        '<a href="https://github.com/tailwindlabs/tailwindcss/releases">'
        "<code>@tailwindcss/postcss</code>'s release notes</a>"
    )

    urls = [span.text for span in protected_spans_for_text(text) if span.kind == "url"]

    assert urls == ["https://github.com/tailwindlabs/tailwindcss/releases"]


def test_protected_spans_include_non_http_uris_and_snake_case_identifiers():
    text = (
        "Load runtime://skills/canary_test_2 and call "
        "ff_sourcing_run_orchestrator_v1."
    )

    spans = protected_spans_for_text(text)

    assert [(span.text, span.kind) for span in spans] == [
        ("runtime://skills/canary_test_2", "url"),
        ("ff_sourcing_run_orchestrator_v1", "identifier"),
    ]


def test_protected_spans_keep_uuid_and_timestamp_atomic():
    text = (
        "contactId: c111b3aa-0174-0c54-cd8d-ce861b853de6\n"
        "updatedAt: 2026-08-27T16:05:29.452Z\n"
    )

    spans = protected_spans_for_text(text)

    assert [(span.text, span.kind) for span in spans] == [
        ("c111b3aa-0174-0c54-cd8d-ce861b853de6", "uuid"),
        ("2026-08-27T16:05:29.452Z", "timestamp"),
    ]


def test_machine_critical_filter_includes_runtime_identity_values():
    text = (
        "Run 03e353c7-45f1-4b74-8c09-3df40f6375fe at "
        "2026-08-27T01:09:25-07:00."
    )

    spans = protected_spans_for_text(
        text,
        allowed_kinds=MACHINE_CRITICAL_SPAN_KINDS,
    )

    assert [(span.text, span.kind) for span in spans] == [
        ("03e353c7-45f1-4b74-8c09-3df40f6375fe", "uuid"),
        ("2026-08-27T01:09:25-07:00", "timestamp"),
    ]
