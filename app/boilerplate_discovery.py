from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Callable, Iterable

from app.protected_spans import critical_clause_spans, protected_spans_for_text


INSTRUCTION_PATTERN = re.compile(
    r"\b(?:must|shall|should|may|can|cannot|can't|do\s+not|don't|never|unless|"
    r"except|only|all|any|each|every|delete|remove|keep|return|output|respond|"
    r"deadline|owner)\b",
    re.IGNORECASE,
)
IMPERATIVE_PATTERN = re.compile(
    r"^(?:add|change|check|create|delete|do|ensure|include|keep|provide|remove|"
    r"return|review|send|use|write)\b",
    re.IGNORECASE,
)
TASK_FIELD_PATTERN = re.compile(
    r"\b(?:account|case|customer|incident|order|request|task|tenant|ticket)[-_ ]?"
    r"(?:id|number)?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BoilerplateRecord:
    record_id: str
    conversation_id: str
    text: str


@dataclass(frozen=True)
class BoilerplateCandidate:
    tenant_id: str
    normalized_text: str
    record_count: int
    record_fraction: float
    conversation_count: int
    estimated_tokens_saved_per_record: int
    eligible: bool
    rejection_reasons: tuple[str, ...]


@dataclass(frozen=True)
class TenantBoilerplateApproval:
    tenant_id: str
    profile_version: str
    exact_phrases: tuple[str, ...]


def discover_tenant_boilerplate(
    tenant_id: str,
    records: Iterable[BoilerplateRecord],
    *,
    estimate_tokens: Callable[[str], int],
    minimum_records: int = 50,
    minimum_fraction: float = 0.30,
    minimum_tokens_per_record: int = 8,
) -> list[BoilerplateCandidate]:
    rows = list(records)
    occurrences: dict[str, set[str]] = defaultdict(set)
    conversations: dict[str, set[str]] = defaultdict(set)
    raw_values: dict[str, str] = {}
    for record in rows:
        for block in _paragraph_blocks(record.text):
            normalized = _normalize_block(block)
            if not normalized:
                continue
            occurrences[normalized].add(record.record_id)
            conversations[normalized].add(record.conversation_id)
            raw_values.setdefault(normalized, block)

    candidates: list[BoilerplateCandidate] = []
    total_records = len(rows)
    for normalized, record_ids in occurrences.items():
        phrase = raw_values[normalized]
        record_count = len(record_ids)
        fraction = 0.0 if total_records == 0 else record_count / total_records
        token_savings = estimate_tokens(phrase)
        reasons = _rejection_reasons(
            phrase,
            record_count=record_count,
            record_fraction=fraction,
            conversation_count=len(conversations[normalized]),
            token_savings=token_savings,
            minimum_records=minimum_records,
            minimum_fraction=minimum_fraction,
            minimum_tokens_per_record=minimum_tokens_per_record,
        )
        candidates.append(
            BoilerplateCandidate(
                tenant_id=tenant_id,
                normalized_text=normalized,
                record_count=record_count,
                record_fraction=fraction,
                conversation_count=len(conversations[normalized]),
                estimated_tokens_saved_per_record=token_savings,
                eligible=not reasons,
                rejection_reasons=tuple(reasons),
            )
        )
    return sorted(
        candidates,
        key=lambda item: (-item.record_count, -item.estimated_tokens_saved_per_record, item.normalized_text),
    )


def _paragraph_blocks(text: str) -> list[str]:
    return [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]


def _normalize_block(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _rejection_reasons(
    phrase: str,
    *,
    record_count: int,
    record_fraction: float,
    conversation_count: int,
    token_savings: int,
    minimum_records: int,
    minimum_fraction: float,
    minimum_tokens_per_record: int,
) -> list[str]:
    reasons: list[str] = []
    if record_count < minimum_records:
        reasons.append("below_minimum_records")
    if record_fraction < minimum_fraction:
        reasons.append("below_minimum_fraction")
    if conversation_count < 2:
        reasons.append("single_conversation_only")
    if token_savings < minimum_tokens_per_record:
        reasons.append("below_minimum_token_savings")
    if protected_spans_for_text(phrase) or critical_clause_spans(phrase):
        reasons.append("contains_protected_span_or_clause")
    if INSTRUCTION_PATTERN.search(phrase) or IMPERATIVE_PATTERN.search(phrase):
        reasons.append("instruction_or_policy_bearing")
    if "?" in phrase:
        reasons.append("contains_question")
    if TASK_FIELD_PATTERN.search(phrase):
        reasons.append("task_specific_field")
    return reasons
