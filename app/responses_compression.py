from __future__ import annotations

import copy
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from app.compressor import PromptCompressionService
from app.content_cache import ContentCompressionCache
from app.message_compression import compress_user_messages
from app.tenant_profiles import TenantCompressionProfile
from app.token_estimator import (
    REGEX_TOKEN_ESTIMATOR,
    TokenEstimate,
    merge_token_estimator_names,
)


COMPRESSIBLE_RESPONSES_ROLES = {"developer", "system", "user"}


@dataclass(frozen=True)
class ResponsesContentPartCompressionStats:
    index: int
    type: str
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    compressed: bool
    preserved: bool
    skipped_reason: str | None = None


@dataclass(frozen=True)
class ResponsesItemCompressionStats:
    index: int
    type: str
    role: str | None
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    compression_applied: bool
    compressed: bool
    preserved: bool
    skipped_reason: str | None = None
    text_parts: int = 0
    compressed_text_parts: int = 0
    content_parts: list[ResponsesContentPartCompressionStats] = field(
        default_factory=list
    )
    content_cache_hits: int = 0
    content_cache_misses: int = 0
    content_cache_stores: int = 0


@dataclass(frozen=True)
class ResponsesCompressionResult:
    input: str | list[Any]
    input_tokens: int
    output_tokens: int
    elapsed_ms: float
    stats: list[ResponsesItemCompressionStats]
    token_estimator: str = REGEX_TOKEN_ESTIMATOR
    warnings: list[str] = field(default_factory=list)
    model_required: bool = False


@dataclass(frozen=True)
class _EligibleMessage:
    input_index: int
    content_kind: str
    content_part_indexes: tuple[int, ...]


def compress_responses_input(
    responses_input: str | list[Any],
    compression_service: PromptCompressionService,
    aggressiveness: float,
    role_aggressiveness: Mapping[str, float],
    tenant_profile: TenantCompressionProfile | None = None,
    mode: str | None = None,
    latency_budget_ms: float | None = None,
    content_cache: ContentCompressionCache | None = None,
    content_cache_enabled: bool = True,
    model_auto_plan_only: bool = False,
) -> ResponsesCompressionResult:
    """Compress eligible Responses input while preserving its native shape."""
    start = time.perf_counter()
    estimate_text = _compression_text_estimator(compression_service, tenant_profile)

    if isinstance(responses_input, str):
        return _compress_string_input(
            responses_input,
            compression_service=compression_service,
            aggressiveness=aggressiveness,
            role_aggressiveness=role_aggressiveness,
            tenant_profile=tenant_profile,
            mode=mode,
            latency_budget_ms=latency_budget_ms,
            content_cache=content_cache,
            content_cache_enabled=content_cache_enabled,
            model_auto_plan_only=model_auto_plan_only,
            estimate_text=estimate_text,
            start=start,
        )

    original_input = copy.deepcopy(responses_input)
    output_input = copy.deepcopy(responses_input)
    synthetic_messages: list[dict[str, Any]] = []
    eligible_messages: list[_EligibleMessage] = []

    for input_index, item in enumerate(responses_input):
        if not _is_eligible_message(item):
            continue
        role = str(item["role"]).lower()
        content = item.get("content")
        if isinstance(content, str):
            if not content:
                continue
            synthetic_messages.append({"role": role, "content": content})
            eligible_messages.append(_EligibleMessage(input_index, "string", ()))
            continue
        if not isinstance(content, list):
            continue
        part_indexes = tuple(
            part_index
            for part_index, part in enumerate(content)
            if _is_input_text_part(part)
        )
        if not part_indexes:
            continue
        synthetic_messages.append(
            {
                "role": role,
                "content": [copy.deepcopy(content[index]) for index in part_indexes],
            }
        )
        eligible_messages.append(_EligibleMessage(input_index, "parts", part_indexes))

    pipeline_result = compress_user_messages(
        synthetic_messages,
        compression_service=compression_service,
        aggressiveness=aggressiveness,
        role_aggressiveness=role_aggressiveness,
        tenant_profile=tenant_profile,
        mode=mode,
        latency_budget_ms=latency_budget_ms,
        content_cache=content_cache,
        content_cache_enabled=content_cache_enabled,
        model_auto_plan_only=model_auto_plan_only,
    )

    pipeline_by_input_index: dict[int, tuple[dict[str, Any], Any]] = {}
    for mapping, compressed_message, message_stat in zip(
        eligible_messages,
        pipeline_result.messages,
        pipeline_result.stats,
        strict=True,
    ):
        original_item = original_input[mapping.input_index]
        candidate_item = copy.deepcopy(original_item)
        if mapping.content_kind == "string":
            original_text = original_item["content"]
            candidate_text = compressed_message["content"]
            if _has_positive_text_savings(original_text, candidate_text, estimate_text):
                candidate_item["content"] = candidate_text
        else:
            candidate_parts = compressed_message["content"]
            for original_part_index, candidate_part in zip(
                mapping.content_part_indexes,
                candidate_parts,
                strict=True,
            ):
                original_part = original_item["content"][original_part_index]
                original_text = original_part["text"]
                candidate_text = candidate_part["text"]
                if _has_positive_text_savings(
                    original_text,
                    candidate_text,
                    estimate_text,
                ):
                    candidate_item["content"][original_part_index]["text"] = (
                        candidate_text
                    )

        original_estimate = _estimate_item(original_item, estimate_text)
        candidate_estimate = _estimate_item(candidate_item, estimate_text)
        if candidate_estimate.count < original_estimate.count:
            output_input[mapping.input_index] = candidate_item
        pipeline_by_input_index[mapping.input_index] = (
            output_input[mapping.input_index],
            message_stat,
        )

    stats: list[ResponsesItemCompressionStats] = []
    estimator_names: list[str] = []
    input_tokens = 0
    output_tokens = 0
    for index, original_item in enumerate(original_input):
        delivered_item = output_input[index]
        original_estimate = _estimate_item(original_item, estimate_text)
        delivered_estimate = _estimate_item(delivered_item, estimate_text)
        estimator_names.extend(
            [original_estimate.estimator, delivered_estimate.estimator]
        )
        input_tokens += original_estimate.count
        output_tokens += delivered_estimate.count
        message_data = pipeline_by_input_index.get(index)
        message_stat = message_data[1] if message_data is not None else None
        item_changed = delivered_item != original_item
        item_type = _item_type(original_item)
        role = _item_role(original_item)
        content_parts = _content_part_stats(
            original_item,
            delivered_item,
            estimate_text,
            eligible_skip_reason=(
                message_stat.skipped_reason
                if message_stat is not None and not message_stat.compression_applied
                else None
            ),
        )
        text_parts = sum(1 for part in content_parts if part.type == "input_text")
        compressed_text_parts = sum(1 for part in content_parts if part.compressed)
        skip_reason = _item_skip_reason(
            original_item,
            pipeline_skip_reason=(
                None
                if message_stat is None or message_stat.compression_applied
                else message_stat.skipped_reason
            ),
            compression_attempted=bool(
                message_stat is not None and message_stat.compression_applied
            ),
            item_changed=item_changed,
        )
        stats.append(
            ResponsesItemCompressionStats(
                index=index,
                type=item_type,
                role=role,
                original_tokens=original_estimate.count,
                compressed_tokens=delivered_estimate.count,
                tokens_saved=max(0, original_estimate.count - delivered_estimate.count),
                compression_applied=bool(
                    message_stat is not None and message_stat.compression_applied
                ),
                compressed=item_changed,
                preserved=not item_changed,
                skipped_reason=skip_reason,
                text_parts=(
                    1
                    if isinstance(original_item, dict)
                    and isinstance(original_item.get("content"), str)
                    and original_item.get("content")
                    else text_parts
                ),
                compressed_text_parts=(
                    1
                    if item_changed
                    and isinstance(original_item, dict)
                    and isinstance(original_item.get("content"), str)
                    else compressed_text_parts
                ),
                content_parts=content_parts,
                content_cache_hits=(
                    0 if message_stat is None else message_stat.content_cache_hits
                ),
                content_cache_misses=(
                    0 if message_stat is None else message_stat.content_cache_misses
                ),
                content_cache_stores=(
                    0 if message_stat is None else message_stat.content_cache_stores
                ),
            )
        )

    return ResponsesCompressionResult(
        input=output_input,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        elapsed_ms=(time.perf_counter() - start) * 1000,
        stats=stats,
        token_estimator=merge_token_estimator_names(estimator_names),
        warnings=pipeline_result.warnings,
        model_required=pipeline_result.model_required,
    )


def _compress_string_input(
    responses_input: str,
    *,
    compression_service: PromptCompressionService,
    aggressiveness: float,
    role_aggressiveness: Mapping[str, float],
    tenant_profile: TenantCompressionProfile | None,
    mode: str | None,
    latency_budget_ms: float | None,
    content_cache: ContentCompressionCache | None,
    content_cache_enabled: bool,
    model_auto_plan_only: bool,
    estimate_text: Callable[[str], TokenEstimate],
    start: float,
) -> ResponsesCompressionResult:
    result = compress_user_messages(
        [{"role": "user", "content": responses_input}],
        compression_service=compression_service,
        aggressiveness=aggressiveness,
        role_aggressiveness=role_aggressiveness,
        tenant_profile=tenant_profile,
        mode=mode,
        latency_budget_ms=latency_budget_ms,
        content_cache=content_cache,
        content_cache_enabled=content_cache_enabled,
        model_auto_plan_only=model_auto_plan_only,
    )
    candidate = result.messages[0]["content"]
    original_estimate = estimate_text(responses_input)
    candidate_estimate = estimate_text(candidate)
    changed = (
        candidate != responses_input
        and candidate_estimate.count < original_estimate.count
    )
    delivered = candidate if changed else responses_input
    delivered_estimate = candidate_estimate if changed else original_estimate
    message_stat = result.stats[0]
    skip_reason = (
        None
        if changed
        else message_stat.skipped_reason
        if not message_stat.compression_applied and message_stat.skipped_reason
        else "no_positive_savings"
    )
    stats = [
        ResponsesItemCompressionStats(
            index=0,
            type="input_text",
            role="user",
            original_tokens=original_estimate.count,
            compressed_tokens=delivered_estimate.count,
            tokens_saved=max(0, original_estimate.count - delivered_estimate.count),
            compression_applied=message_stat.compression_applied,
            compressed=changed,
            preserved=not changed,
            skipped_reason=skip_reason,
            text_parts=1 if responses_input else 0,
            compressed_text_parts=1 if changed else 0,
            content_cache_hits=message_stat.content_cache_hits,
            content_cache_misses=message_stat.content_cache_misses,
            content_cache_stores=message_stat.content_cache_stores,
        )
    ]
    return ResponsesCompressionResult(
        input=delivered,
        input_tokens=original_estimate.count,
        output_tokens=delivered_estimate.count,
        elapsed_ms=(time.perf_counter() - start) * 1000,
        stats=stats,
        token_estimator=merge_token_estimator_names(
            [original_estimate.estimator, delivered_estimate.estimator]
        ),
        warnings=result.warnings,
        model_required=result.model_required,
    )


def _is_eligible_message(item: Any) -> bool:
    return (
        _is_message_item(item) and item["role"].lower() in COMPRESSIBLE_RESPONSES_ROLES
    )


def _is_message_item(item: Any) -> bool:
    """Recognize both explicit and concise OpenAI Responses messages."""
    if not isinstance(item, dict) or not isinstance(item.get("role"), str):
        return False
    return "type" not in item or item["type"] == "message"


def _is_input_text_part(part: Any) -> bool:
    return (
        isinstance(part, dict)
        and part.get("type") == "input_text"
        and isinstance(part.get("text"), str)
        and bool(part["text"])
    )


def _has_positive_text_savings(
    original: str,
    candidate: str,
    estimate_text: Callable[[str], TokenEstimate],
) -> bool:
    return (
        candidate != original
        and estimate_text(candidate).count < estimate_text(original).count
    )


def _estimate_item(
    item: Any,
    estimate_text: Callable[[str], TokenEstimate],
) -> TokenEstimate:
    if isinstance(item, str):
        return estimate_text(item)
    serialized = json.dumps(
        item,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=False,
        default=str,
    )
    return estimate_text(serialized)


def _content_part_stats(
    original_item: Any,
    delivered_item: Any,
    estimate_text: Callable[[str], TokenEstimate],
    eligible_skip_reason: str | None,
) -> list[ResponsesContentPartCompressionStats]:
    if not _is_eligible_message(original_item):
        return []
    original_content = original_item.get("content")
    delivered_content = (
        delivered_item.get("content") if isinstance(delivered_item, dict) else None
    )
    if not isinstance(original_content, list) or not isinstance(
        delivered_content, list
    ):
        return []
    stats: list[ResponsesContentPartCompressionStats] = []
    for index, original_part in enumerate(original_content):
        delivered_part = delivered_content[index]
        original_estimate = _estimate_item(original_part, estimate_text)
        delivered_estimate = _estimate_item(delivered_part, estimate_text)
        changed = delivered_part != original_part
        part_type = _item_type(original_part)
        stats.append(
            ResponsesContentPartCompressionStats(
                index=index,
                type=part_type,
                original_tokens=original_estimate.count,
                compressed_tokens=delivered_estimate.count,
                tokens_saved=max(0, original_estimate.count - delivered_estimate.count),
                compressed=changed,
                preserved=not changed,
                skipped_reason=(
                    None
                    if changed
                    else (eligible_skip_reason or "no_positive_savings")
                    if _is_input_text_part(original_part)
                    else "content_part_type_preserved"
                ),
            )
        )
    return stats


def _item_type(item: Any) -> str:
    if isinstance(item, dict) and isinstance(item.get("type"), str):
        return item["type"]
    if _is_message_item(item):
        return "message"
    if isinstance(item, str):
        return "input_text"
    return type(item).__name__


def _item_role(item: Any) -> str | None:
    if isinstance(item, dict) and isinstance(item.get("role"), str):
        return item["role"]
    return None


def _item_skip_reason(
    item: Any,
    *,
    pipeline_skip_reason: str | None,
    compression_attempted: bool,
    item_changed: bool,
) -> str | None:
    if item_changed:
        return None
    if compression_attempted:
        return "no_positive_savings"
    if pipeline_skip_reason is not None:
        return pipeline_skip_reason
    if not _is_message_item(item):
        return "item_type_preserved"
    role = item.get("role")
    if not isinstance(role, str) or role.lower() not in COMPRESSIBLE_RESPONSES_ROLES:
        return "role_not_compressible"
    content = item.get("content")
    if isinstance(content, str) and not content:
        return "empty_text"
    return "no_eligible_text"


def _compression_text_estimator(
    compression_service: PromptCompressionService,
    tenant_profile: TenantCompressionProfile | None,
) -> Callable[[str], TokenEstimate]:
    def estimate(text: str) -> TokenEstimate:
        estimate_compression_tokens = getattr(
            compression_service,
            "estimate_compression_tokens",
            None,
        )
        if callable(estimate_compression_tokens):
            return estimate_compression_tokens(text, tenant_profile)
        from app.token_estimator import estimate_regex_tokens

        return estimate_regex_tokens(text)

    return estimate
