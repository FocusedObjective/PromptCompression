from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Iterable

from app.protected_spans import critical_clause_spans, protected_spans_for_text


def sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IntegrityResult:
    passed: bool
    failure_classes: tuple[str, ...] = ()
    protected_span_count_by_type: dict[str, int] = field(default_factory=dict)
    protected_spans_missing_by_type: dict[str, int] = field(default_factory=dict)
    protected_spans_added_by_type: dict[str, int] = field(default_factory=dict)
    json_round_trip_applicable: bool = False
    json_round_trip_validation_passed: bool = True
    original_canonical_json_sha256: str | None = None
    output_canonical_json_sha256: str | None = None
    placeholder_restoration_validation_passed: bool = True
    required_terms_validation_passed: bool = True

    @property
    def primary_failure(self) -> str | None:
        return self.failure_classes[0] if self.failure_classes else None


def evaluate_integrity(
    reference: str,
    output: str,
    *,
    placeholder_tokens: Iterable[str] = (),
    required_terms: Iterable[str] = (),
    allowed_span_kinds: frozenset[str] | None = None,
    protect_critical_clauses: bool = True,
) -> IntegrityResult:
    """Compare accepted output to the exact content entering a risky stage."""

    reference_spans = _protected_value_counts(
        reference,
        allowed_span_kinds=allowed_span_kinds,
        protect_critical_clauses=protect_critical_clauses,
    )
    output_spans = _protected_value_counts(
        output,
        allowed_span_kinds=allowed_span_kinds,
        protect_critical_clauses=protect_critical_clauses,
    )
    span_counts = Counter(kind for kind, _value in reference_spans.elements())
    missing: Counter[str] = Counter()
    added: Counter[str] = Counter()
    for (kind, value), count in reference_spans.items():
        missing[kind] += max(0, count - output_spans[(kind, value)])
    for (kind, value), count in output_spans.items():
        added[kind] += max(0, count - reference_spans[(kind, value)])
    missing = Counter({key: value for key, value in missing.items() if value})
    added = Counter({key: value for key, value in added.items() if value})

    placeholders = tuple(placeholder_tokens)
    placeholder_ok = all(token not in output for token in placeholders)
    required = tuple(dict.fromkeys(term for term in required_terms if term))
    required_ok = all(output.count(term) >= reference.count(term) for term in required)

    reference_json = _canonical_json_hash(reference)
    output_json = _canonical_json_hash(output)
    json_applicable = reference_json is not None
    json_ok = not json_applicable or reference_json == output_json

    failures: list[str] = []
    if missing or added:
        failures.extend(_span_failure_classes(missing, added))
    if not placeholder_ok:
        failures.append("placeholder")
    if not required_ok:
        failures.append("required_term")
    if not json_ok:
        failures.append("json_structure")

    return IntegrityResult(
        passed=not failures,
        failure_classes=tuple(dict.fromkeys(failures)),
        protected_span_count_by_type=dict(sorted(span_counts.items())),
        protected_spans_missing_by_type=dict(sorted(missing.items())),
        protected_spans_added_by_type=dict(sorted(added.items())),
        json_round_trip_applicable=json_applicable,
        json_round_trip_validation_passed=json_ok,
        original_canonical_json_sha256=reference_json,
        output_canonical_json_sha256=output_json if json_applicable else None,
        placeholder_restoration_validation_passed=placeholder_ok,
        required_terms_validation_passed=required_ok,
    )


def evaluate_segment_integrity(
    reference: str,
    output: str,
    *,
    model_reference_parts: Iterable[str],
    model_output_parts: Iterable[str],
    expected_protected_values: Iterable[tuple[str, str]],
    restored_protected_values: Iterable[tuple[str, str]],
    placeholder_tokens: Iterable[str] = (),
    required_terms: Iterable[str] = (),
    allowed_span_kinds: frozenset[str] | None = None,
    protect_critical_clauses: bool = True,
) -> IntegrityResult:
    """Validate only content that crossed the model boundary.

    Protected segments are compared by kind and content hash, while regex-based
    span detection runs only on prose supplied to and returned by the model.
    This avoids rescanning large verbatim blocks while still rejecting new or
    changed machine-critical values in generated prose.
    """

    reference_spans = _protected_value_counts_for_parts(
        model_reference_parts,
        allowed_span_kinds=allowed_span_kinds,
        protect_critical_clauses=protect_critical_clauses,
    )
    output_spans = _protected_value_counts_for_parts(
        model_output_parts,
        allowed_span_kinds=allowed_span_kinds,
        protect_critical_clauses=protect_critical_clauses,
    )
    expected_hashes = Counter(
        (kind, sha256_text(value)) for kind, value in expected_protected_values
    )
    restored_hashes = Counter(
        (kind, sha256_text(value)) for kind, value in restored_protected_values
    )

    span_counts = Counter(kind for kind, _value in reference_spans.elements())
    for (kind, _digest), count in expected_hashes.items():
        span_counts[kind] += count
    missing: Counter[str] = Counter()
    added: Counter[str] = Counter()
    for (kind, value), count in reference_spans.items():
        missing[kind] += max(0, count - output_spans[(kind, value)])
    for (kind, value), count in output_spans.items():
        added[kind] += max(0, count - reference_spans[(kind, value)])
    for (kind, digest), count in expected_hashes.items():
        missing[kind] += max(0, count - restored_hashes[(kind, digest)])
    for (kind, digest), count in restored_hashes.items():
        added[kind] += max(0, count - expected_hashes[(kind, digest)])
    missing = Counter({key: value for key, value in missing.items() if value})
    added = Counter({key: value for key, value in added.items() if value})

    placeholders = tuple(placeholder_tokens)
    placeholder_ok = all(token not in output for token in placeholders)
    required = tuple(dict.fromkeys(term for term in required_terms if term))
    required_ok = all(output.count(term) >= reference.count(term) for term in required)

    failures: list[str] = []
    if missing or added:
        failures.extend(_span_failure_classes(missing, added))
    if not placeholder_ok:
        failures.append("placeholder")
    if not required_ok:
        failures.append("required_term")

    return IntegrityResult(
        passed=not failures,
        failure_classes=tuple(dict.fromkeys(failures)),
        protected_span_count_by_type=dict(sorted(span_counts.items())),
        protected_spans_missing_by_type=dict(sorted(missing.items())),
        protected_spans_added_by_type=dict(sorted(added.items())),
        placeholder_restoration_validation_passed=placeholder_ok,
        required_terms_validation_passed=required_ok,
    )


def _protected_value_counts(
    text: str,
    *,
    allowed_span_kinds: frozenset[str] | None,
    protect_critical_clauses: bool,
) -> Counter[tuple[str, str]]:
    spans = [
        *protected_spans_for_text(text, allowed_kinds=allowed_span_kinds),
        *(critical_clause_spans(text) if protect_critical_clauses else []),
    ]
    return Counter((span.kind, span.text) for span in spans)


def _protected_value_counts_for_parts(
    parts: Iterable[str],
    *,
    allowed_span_kinds: frozenset[str] | None,
    protect_critical_clauses: bool,
) -> Counter[tuple[str, str]]:
    values: Counter[tuple[str, str]] = Counter()
    for part in parts:
        if part:
            values.update(
                _protected_value_counts(
                    part,
                    allowed_span_kinds=allowed_span_kinds,
                    protect_critical_clauses=protect_critical_clauses,
                )
            )
    return values


def _span_failure_classes(
    missing: Counter[str],
    added: Counter[str],
) -> list[str]:
    kinds = set(missing) | set(added)
    classes: list[str] = []
    priority = (
        ("url", {"url", "markdown_link"}),
        ("inline_code", {"inline_code", "code_fence"}),
        ("constraint", {"constraint", "critical_clause"}),
        (
            "identifier",
            {"identifier", "constant", "uuid", "timestamp"},
        ),
        ("protected_span", kinds),
    )
    consumed: set[str] = set()
    for name, group in priority:
        matched = (kinds - consumed) & group
        if matched:
            classes.append(name)
            consumed.update(matched)
    if kinds - consumed:
        classes.append("protected_span")
    return classes


def _canonical_json_hash(text: str) -> str | None:
    try:
        value = json.loads(text.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256_text(canonical)
