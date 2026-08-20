import copy
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from app.compressor import PromptCompressionService
from app.content_cache import CachedTextCompression, ContentCompressionCache
from app.tenant_profiles import TenantCompressionProfile
from app.token_estimator import (
    REGEX_TOKEN_ESTIMATOR,
    TokenEstimate,
    estimate_regex_tokens,
    merge_token_estimator_names,
)

TEXT_PART_TYPES = {"text", "input_text"}
DEFAULT_SYSTEM_AGGRESSIVENESS = 0.0
DEFAULT_TOOL_AGGRESSIVENESS = 0.0


@dataclass(frozen=True)
class MessageCompressionStats:
    index: int
    role: str
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    compression_applied: bool
    compressed: bool
    text_parts: int
    compressed_text_parts: int
    skipped_reason: str | None = None
    content_cache_hits: int = 0
    content_cache_misses: int = 0
    content_cache_stores: int = 0
    candidate_tokens_saved: int = 0
    candidate_reduction: float = 0.0
    tool_result_action: str | None = None


@dataclass(frozen=True)
class MessagesCompressionResult:
    messages: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    user_input_tokens: int
    user_output_tokens: int
    non_user_tokens_preserved: int
    elapsed_ms: float
    stats: list[MessageCompressionStats]
    token_estimator: str = REGEX_TOKEN_ESTIMATOR
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _TextCompressionResult:
    text: str
    original_tokens: int
    compressed_tokens: int
    changed: bool
    warnings: list[str]
    cache_status: str = "bypass"


@dataclass(frozen=True, slots=True)
class ToolResultCompressionPolicy:
    mode: str = "deterministic"
    aggressiveness: float = 0.15
    min_tokens: int = 8_000
    max_reduction: float = 0.15
    rollout_mode: str = "shadow"
    rollout_percentage: float = 100.0
    rollout_key: str | None = None


def compress_user_messages(
    messages: list[dict[str, Any]],
    compression_service: PromptCompressionService,
    aggressiveness: float,
    role_aggressiveness: Mapping[str, float] | None = None,
    tenant_profile: TenantCompressionProfile | None = None,
    mode: str | None = None,
    latency_budget_ms: float | None = None,
    compact_empty_user_messages: bool = False,
    compact_duplicate_user_text_parts: bool = False,
    content_cache: ContentCompressionCache | None = None,
    content_cache_enabled: bool = True,
    tool_result_policy: ToolResultCompressionPolicy | None = None,
) -> MessagesCompressionResult:
    start = time.perf_counter()
    compressed_messages: list[dict[str, Any]] = []
    stats: list[MessageCompressionStats] = []
    input_tokens = 0
    output_tokens = 0
    user_input_tokens = 0
    user_output_tokens = 0
    non_user_tokens_preserved = 0
    estimator_names: list[str] = []
    warnings: list[str] = []
    seen_user_text_parts: set[str] = set()
    estimate_text_tokens = _compression_text_estimator(
        compression_service,
        tenant_profile,
    )
    normalized_role_aggressiveness = _normalize_role_aggressiveness(
        role_aggressiveness,
        default_user_aggressiveness=aggressiveness,
    )

    for index, message in enumerate(messages):
        role = str(message.get("role", ""))
        normalized_role = role.lower()
        content = message.get("content")
        original_estimate = estimate_content_token_details(
            content,
            estimate_text_tokens=estimate_text_tokens,
        )
        original_tokens = original_estimate.count
        estimator_names.append(original_estimate.estimator)
        input_tokens += original_tokens

        tool_result_action: str | None = None
        effective_mode = mode
        forced_skip_reason: str | None = None
        role_aggressiveness_value = normalized_role_aggressiveness.get(normalized_role)
        if normalized_role == "tool" and tool_result_policy is not None:
            if not _tool_rollout_selected(tool_result_policy, tenant_profile):
                forced_skip_reason = "tool_rollout_not_selected"
            else:
                forced_skip_reason = _tool_content_skip_reason(content)
                if forced_skip_reason is None and original_tokens < tool_result_policy.min_tokens:
                    forced_skip_reason = "tool_below_min_tokens"
            if forced_skip_reason is None:
                role_aggressiveness_value = tool_result_policy.aggressiveness
                effective_mode = tool_result_policy.mode
                tool_result_action = "candidate"

        if forced_skip_reason is not None:
            role_aggressiveness_value = 0.0
        if role_aggressiveness_value is None or role_aggressiveness_value <= 0.0:
            compressed_messages.append(copy.deepcopy(message))
            output_tokens += original_tokens
            if normalized_role == "user":
                user_input_tokens += original_tokens
                user_output_tokens += original_tokens
            else:
                non_user_tokens_preserved += original_tokens
            stats.append(
                MessageCompressionStats(
                    index=index,
                    role=role,
                    original_tokens=original_tokens,
                    compressed_tokens=original_tokens,
                    tokens_saved=0,
                    compression_applied=False,
                    compressed=False,
                    text_parts=count_text_parts(content),
                    compressed_text_parts=0,
                    skipped_reason=(
                        forced_skip_reason
                        or "aggressiveness_zero"
                        if role_aggressiveness_value is not None
                        else "role_preserved"
                    ),
                    tool_result_action=(
                        "skipped" if normalized_role == "tool" else None
                    ),
                )
            )
            continue

        if (
            normalized_role == "user"
            and compact_empty_user_messages
            and _is_empty_user_content(content)
        ):
            user_input_tokens += original_tokens
            stats.append(
                MessageCompressionStats(
                    index=index,
                    role=role,
                    original_tokens=original_tokens,
                    compressed_tokens=0,
                    tokens_saved=original_tokens,
                    compression_applied=False,
                    compressed=original_tokens > 0,
                    text_parts=count_text_parts(content),
                    compressed_text_parts=0,
                    skipped_reason="empty_user_message_dropped",
                )
            )
            continue

        content_to_compress, duplicate_text_parts_dropped = (
            _drop_duplicate_user_text_parts(content, seen_user_text_parts)
            if normalized_role == "user" and compact_duplicate_user_text_parts
            else (content, 0)
        )
        if (
            normalized_role == "user"
            and compact_duplicate_user_text_parts
            and _is_empty_user_content(content_to_compress)
        ):
            stats.append(
                MessageCompressionStats(
                    index=index,
                    role=role,
                    original_tokens=original_tokens,
                    compressed_tokens=0,
                    tokens_saved=original_tokens,
                    compression_applied=False,
                    compressed=original_tokens > 0,
                    text_parts=count_text_parts(content),
                    compressed_text_parts=0,
                    skipped_reason="duplicate_user_text_dropped",
                )
            )
            user_input_tokens += original_tokens
            continue

        compressed_message = copy.deepcopy(message)
        (
            compressed_content,
            text_parts,
            compressed_text_parts,
            applied,
            content_warnings,
            cache_statuses,
        ) = (
            _compress_user_content(
                content_to_compress,
                compression_service=compression_service,
                aggressiveness=role_aggressiveness_value,
                tenant_profile=tenant_profile,
                mode=effective_mode,
                latency_budget_ms=latency_budget_ms,
                role=normalized_role,
                content_cache=content_cache,
                content_cache_enabled=content_cache_enabled,
            )
        )
        for warning in content_warnings:
            if warning not in warnings:
                warnings.append(warning)

        candidate_estimate = estimate_content_token_details(
            compressed_content,
            estimate_text_tokens=estimate_text_tokens,
        )
        candidate_tokens = candidate_estimate.count
        candidate_tokens_saved = max(0, original_tokens - candidate_tokens)
        candidate_reduction = (
            0.0
            if original_tokens <= 0
            else candidate_tokens_saved / original_tokens
        )
        delivered_content = compressed_content
        delivered_applied = applied
        delivered_compressed_text_parts = compressed_text_parts
        skipped_reason = (
            "duplicate_user_text_part_dropped"
            if duplicate_text_parts_dropped
            else None if applied else _user_skip_reason(content)
        )
        if normalized_role == "tool" and tool_result_policy is not None:
            if candidate_tokens_saved <= 0:
                delivered_content = copy.deepcopy(content)
                delivered_applied = False
                delivered_compressed_text_parts = 0
                skipped_reason = "tool_no_positive_savings"
                tool_result_action = "rollback"
                if "tool_result_no_savings_rollback" not in warnings:
                    warnings.append("tool_result_no_savings_rollback")
            elif candidate_reduction > tool_result_policy.max_reduction:
                delivered_content = copy.deepcopy(content)
                delivered_applied = False
                delivered_compressed_text_parts = 0
                skipped_reason = "tool_reduction_limit_exceeded"
                tool_result_action = "rollback"
                if "tool_result_reduction_rollback" not in warnings:
                    warnings.append("tool_result_reduction_rollback")
            elif tool_result_policy.rollout_mode == "shadow":
                delivered_content = copy.deepcopy(content)
                delivered_applied = False
                delivered_compressed_text_parts = 0
                skipped_reason = "tool_result_shadow"
                tool_result_action = "shadow"
                if "tool_result_shadow" not in warnings:
                    warnings.append("tool_result_shadow")
            else:
                tool_result_action = "applied"

        if "content" in compressed_message:
            compressed_message["content"] = delivered_content
        compressed_messages.append(compressed_message)
        compressed_estimate = estimate_content_token_details(
            delivered_content,
            estimate_text_tokens=estimate_text_tokens,
        )
        compressed_tokens = compressed_estimate.count
        estimator_names.append(compressed_estimate.estimator)
        tokens_saved = max(0, original_tokens - compressed_tokens)
        output_tokens += compressed_tokens
        if normalized_role == "user":
            user_input_tokens += original_tokens
            user_output_tokens += compressed_tokens
        elif compressed_tokens == original_tokens:
            non_user_tokens_preserved += original_tokens
        cache_counts = _cache_status_counts(cache_statuses)
        stats.append(
            MessageCompressionStats(
                index=index,
                role=role,
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                tokens_saved=tokens_saved,
                compression_applied=delivered_applied,
                compressed=tokens_saved > 0,
                text_parts=text_parts,
                compressed_text_parts=delivered_compressed_text_parts,
                skipped_reason=skipped_reason,
                content_cache_hits=cache_counts["hits"],
                content_cache_misses=cache_counts["misses"],
                content_cache_stores=cache_counts["stores"],
                candidate_tokens_saved=(
                    candidate_tokens_saved if normalized_role == "tool" else 0
                ),
                candidate_reduction=(
                    candidate_reduction if normalized_role == "tool" else 0.0
                ),
                tool_result_action=tool_result_action,
            )
        )

    return MessagesCompressionResult(
        messages=compressed_messages,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        user_input_tokens=user_input_tokens,
        user_output_tokens=user_output_tokens,
        non_user_tokens_preserved=non_user_tokens_preserved,
        elapsed_ms=(time.perf_counter() - start) * 1000,
        stats=stats,
        token_estimator=merge_token_estimator_names(estimator_names),
        warnings=warnings,
    )


def _normalize_role_aggressiveness(
    role_aggressiveness: Mapping[str, float] | None,
    *,
    default_user_aggressiveness: float,
) -> dict[str, float]:
    defaults = {
        "system": DEFAULT_SYSTEM_AGGRESSIVENESS,
        "tool": DEFAULT_TOOL_AGGRESSIVENESS,
        "user": default_user_aggressiveness,
    }
    if role_aggressiveness is None:
        return defaults

    defaults.update({
        str(role).lower(): aggressiveness
        for role, aggressiveness in role_aggressiveness.items()
    })
    return defaults


def estimate_content_tokens(content: Any) -> int:
    return estimate_content_token_details(content).count


def estimate_content_token_details(
    content: Any,
    estimate_text_tokens: Callable[[str], TokenEstimate] | None = None,
) -> TokenEstimate:
    estimator = estimate_text_tokens or estimate_regex_tokens
    if isinstance(content, str):
        return estimator(content)

    if isinstance(content, list):
        estimates = [
            estimate_part_token_details(part, estimate_text_tokens=estimator)
            for part in content
        ]
        return TokenEstimate(
            count=sum(estimate.count for estimate in estimates),
            estimator=merge_token_estimator_names(
                [estimate.estimator for estimate in estimates]
            ),
            tokenizer_backed=any(estimate.tokenizer_backed for estimate in estimates),
        )

    return TokenEstimate(count=0, estimator=REGEX_TOKEN_ESTIMATOR)


def estimate_part_tokens(part: Any) -> int:
    return estimate_part_token_details(part).count


def estimate_part_token_details(
    part: Any,
    estimate_text_tokens: Callable[[str], TokenEstimate] | None = None,
) -> TokenEstimate:
    estimator = estimate_text_tokens or estimate_regex_tokens
    if isinstance(part, str):
        return estimator(part)

    if isinstance(part, dict) and isinstance(part.get("text"), str):
        return estimator(part["text"])

    return TokenEstimate(count=0, estimator=REGEX_TOKEN_ESTIMATOR)


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
        return estimate_regex_tokens(text)

    return estimate


def count_text_parts(content: Any) -> int:
    if isinstance(content, str):
        return 1 if content else 0

    if not isinstance(content, list):
        return 0

    count = 0
    for part in content:
        if isinstance(part, str) and part:
            count += 1
        elif _is_text_dict_part(part):
            count += 1
    return count


def _compress_user_content(
    content: Any,
    compression_service: PromptCompressionService,
    aggressiveness: float,
    tenant_profile: TenantCompressionProfile | None,
    mode: str | None,
    latency_budget_ms: float | None,
    role: str,
    content_cache: ContentCompressionCache | None,
    content_cache_enabled: bool,
) -> tuple[Any, int, int, bool, list[str], list[str]]:
    if isinstance(content, str):
        if not content:
            return content, 0, 0, False, [], []

        result = _compress_text(
            content,
            compression_service=compression_service,
            aggressiveness=aggressiveness,
            tenant_profile=tenant_profile,
            mode=mode,
            latency_budget_ms=latency_budget_ms,
            role=role,
            content_cache=content_cache,
            content_cache_enabled=content_cache_enabled,
        )
        return (
            result.text,
            1,
            1 if result.changed else 0,
            True,
            result.warnings,
            [result.cache_status],
        )

    if not isinstance(content, list):
        return content, 0, 0, False, [], []

    compressed_parts: list[Any] = []
    text_parts = 0
    compressed_text_parts = 0
    applied = False
    warnings: list[str] = []
    cache_statuses: list[str] = []

    for part in content:
        if isinstance(part, str):
            if not part:
                compressed_parts.append(part)
                continue
            result = _compress_text(
                part,
                compression_service=compression_service,
                aggressiveness=aggressiveness,
                tenant_profile=tenant_profile,
                mode=mode,
                latency_budget_ms=latency_budget_ms,
                role=role,
                content_cache=content_cache,
                content_cache_enabled=content_cache_enabled,
            )
            compressed_parts.append(result.text)
            for warning in result.warnings:
                if warning not in warnings:
                    warnings.append(warning)
            text_parts += 1
            compressed_text_parts += 1 if result.changed else 0
            applied = True
            cache_statuses.append(result.cache_status)
            continue

        if _is_text_dict_part(part):
            result = _compress_text(
                part["text"],
                compression_service=compression_service,
                aggressiveness=aggressiveness,
                tenant_profile=tenant_profile,
                mode=mode,
                latency_budget_ms=latency_budget_ms,
                role=role,
                content_cache=content_cache,
                content_cache_enabled=content_cache_enabled,
            )
            compressed_part = copy.deepcopy(part)
            compressed_part["text"] = result.text
            compressed_parts.append(compressed_part)
            for warning in result.warnings:
                if warning not in warnings:
                    warnings.append(warning)
            text_parts += 1
            compressed_text_parts += 1 if result.changed else 0
            applied = True
            cache_statuses.append(result.cache_status)
            continue

        compressed_parts.append(copy.deepcopy(part))

    return (
        compressed_parts,
        text_parts,
        compressed_text_parts,
        applied,
        warnings,
        cache_statuses,
    )


def _is_empty_user_content(content: Any) -> bool:
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str) and part.strip():
                return False
            if _is_text_dict_part(part) and part["text"].strip():
                return False
            if not (isinstance(part, str) or _is_text_dict_part(part)):
                return False
        return True
    return False


def _drop_duplicate_user_text_parts(
    content: Any,
    seen_text_parts: set[str],
) -> tuple[Any, int]:
    if isinstance(content, str):
        if content in seen_text_parts:
            return "", 1
        if content:
            seen_text_parts.add(content)
        return content, 0

    if not isinstance(content, list):
        return content, 0

    updated_parts: list[Any] = []
    dropped = 0
    for part in content:
        if isinstance(part, str):
            if part in seen_text_parts:
                dropped += 1
                continue
            if part:
                seen_text_parts.add(part)
            updated_parts.append(part)
            continue

        if _is_text_dict_part(part):
            text = part["text"]
            if text in seen_text_parts:
                dropped += 1
                continue
            if text:
                seen_text_parts.add(text)
            updated_parts.append(copy.deepcopy(part))
            continue

        updated_parts.append(copy.deepcopy(part))

    return updated_parts, dropped


def _compress_text(
    text: str,
    compression_service: PromptCompressionService,
    aggressiveness: float,
    tenant_profile: TenantCompressionProfile | None,
    mode: str | None,
    latency_budget_ms: float | None,
    role: str,
    content_cache: ContentCompressionCache | None,
    content_cache_enabled: bool,
) -> _TextCompressionResult:
    def compute() -> CachedTextCompression:
        result = compression_service.compress(
            text=text,
            aggressiveness=aggressiveness,
            include_sections=False,
            tenant_profile=tenant_profile,
            mode=mode,
            latency_budget_ms=latency_budget_ms,
            collect_diagnostics=False,
        )
        return CachedTextCompression(
            text=result.compressed_text,
            original_tokens=result.original_tokens,
            compressed_tokens=result.compressed_tokens,
            changed=result.compressed_text != text,
            warnings=tuple(result.warnings),
        )

    if content_cache is not None and content_cache_enabled:
        cached = content_cache.compress(
            text=text,
            role=role,
            model_name=compression_service.model_name,
            aggressiveness=aggressiveness,
            mode=mode,
            latency_budget_ms=latency_budget_ms,
            tenant_profile=tenant_profile,
            compute=compute,
        )
    else:
        cached = compute()
    return _TextCompressionResult(
        text=cached.text,
        original_tokens=cached.original_tokens,
        compressed_tokens=cached.compressed_tokens,
        changed=cached.changed,
        warnings=list(cached.warnings),
        cache_status=cached.cache_status,
    )


def _is_text_dict_part(part: Any) -> bool:
    if not isinstance(part, dict) or not isinstance(part.get("text"), str):
        return False

    part_type = part.get("type")
    return part_type is None or part_type in TEXT_PART_TYPES


def _user_skip_reason(content: Any) -> str:
    if isinstance(content, str):
        return "empty_text" if not content else "not_compressed"

    if isinstance(content, list):
        return "no_text_content"

    return "unsupported_content"


def _cache_status_counts(statuses: list[str]) -> dict[str, int]:
    return {
        "hits": sum(status in {"hit", "shared"} for status in statuses),
        "misses": sum(status in {"store", "miss"} for status in statuses),
        "stores": statuses.count("store"),
    }


def _tool_rollout_selected(
    policy: ToolResultCompressionPolicy,
    tenant_profile: TenantCompressionProfile | None,
) -> bool:
    if policy.rollout_percentage <= 0.0:
        return False
    if policy.rollout_percentage >= 100.0:
        return True
    cohort_key = policy.rollout_key or (
        "default" if tenant_profile is None else tenant_profile.tenant_id
    )
    digest = hashlib.sha256(cohort_key.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") % 10_000
    return bucket < round(policy.rollout_percentage * 100)


def _tool_content_skip_reason(content: Any) -> str | None:
    texts: list[str]
    if isinstance(content, str):
        texts = [content]
    elif isinstance(content, list) and content:
        texts = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif _is_text_dict_part(part):
                texts.append(part["text"])
            else:
                return "tool_non_text_content_protected"
    else:
        return "tool_non_text_content_protected"

    if not any(text.strip() for text in texts):
        return "tool_empty_text"
    if any(_tool_text_is_structured_or_exact(text) for text in texts):
        return "tool_structured_content_protected"
    return None


def _tool_text_is_structured_or_exact(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    try:
        json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        pass
    else:
        return True

    lowered = stripped.casefold()
    protected_markers = (
        "```",
        "~~~",
        "<schema",
        "<tool",
        "tool_call_id",
        '"tool_calls"',
        '"function_call"',
        '"properties"',
        "return exactly",
        "verbatim output",
        "preserve whitespace exactly",
        "do not modify the text",
        "do not change the text",
    )
    if any(marker in lowered for marker in protected_markers):
        return True

    if re.search(r"</?\s*[a-z][^>]*>", stripped, flags=re.IGNORECASE):
        return True

    code_patterns = (
        r"(?m)^\s*#!",
        r"(?m)^\s*(?:async\s+def|def|class|from\s+\S+\s+import|import\s+\S+)",
        r"(?m)^\s*(?:function|const|let|var|interface|enum|namespace|package|using)\s+",
        r"(?m)^\s*(?:public|private|protected|static)\s+",
        r"(?im)^\s*(?:select\s+.+\s+from|insert\s+into|update\s+\S+\s+set|delete\s+from|create\s+(?:table|view)|alter\s+table|drop\s+(?:table|view))\b",
        r"(?m)^\s*[A-Za-z_][\w.\-]*\s*=\s*\S+",
        r"(?m)^\s*(?:Traceback \(most recent call last\):|File \".*\", line \d+|at\s+\S+\s*\()",
        r"(?:=>|:=|===|!==|&&|\|\|)",
        r"(?m)^\s*[A-Za-z_$][\w.$]*\([^\n)]*\)\s*(?:;|\{|=>)?\s*$",
    )
    if any(re.search(pattern, stripped) for pattern in code_patterns):
        return True

    lines = [line for line in stripped.splitlines() if line.strip()]
    yaml_key_lines = sum(
        re.match(r"^\s*(?:-\s+)?[A-Za-z_][\w.\-]*\s*:\s*\S", line) is not None
        for line in lines
    )
    if stripped.startswith("---") or yaml_key_lines >= 2:
        return True

    if _looks_like_delimited_table(lines):
        return True
    if _looks_like_markdown_table(lines):
        return True
    return False


def _looks_like_delimited_table(lines: list[str]) -> bool:
    if len(lines) < 2:
        return False
    for delimiter in ("\t", ",", "|"):
        counts = [line.count(delimiter) for line in lines]
        if counts[0] > 0 and len(set(counts)) == 1:
            return True
    return False


def _looks_like_markdown_table(lines: list[str]) -> bool:
    return any(
        re.match(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$", line)
        is not None
        for line in lines
    )
