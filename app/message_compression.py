import copy
import time
from dataclasses import dataclass
from typing import Any, Callable

from app.compressor import PromptCompressionService
from app.tenant_profiles import TenantCompressionProfile
from app.token_estimator import (
    REGEX_TOKEN_ESTIMATOR,
    TokenEstimate,
    estimate_regex_tokens,
    merge_token_estimator_names,
)

TEXT_PART_TYPES = {"text", "input_text"}


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


@dataclass(frozen=True)
class _TextCompressionResult:
    text: str
    original_tokens: int
    compressed_tokens: int
    changed: bool


def compress_user_messages(
    messages: list[dict[str, Any]],
    compression_service: PromptCompressionService,
    aggressiveness: float,
    tenant_profile: TenantCompressionProfile | None = None,
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
    estimate_text_tokens = _compression_text_estimator(
        compression_service,
        tenant_profile,
    )

    for index, message in enumerate(messages):
        role = str(message.get("role", ""))
        content = message.get("content")
        original_estimate = estimate_content_token_details(
            content,
            estimate_text_tokens=estimate_text_tokens,
        )
        original_tokens = original_estimate.count
        estimator_names.append(original_estimate.estimator)
        input_tokens += original_tokens

        if role.lower() != "user":
            compressed_messages.append(copy.deepcopy(message))
            output_tokens += original_tokens
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
                    skipped_reason="role_preserved",
                )
            )
            continue

        compressed_message = copy.deepcopy(message)
        compressed_content, text_parts, compressed_text_parts, applied = (
            _compress_user_content(
                content,
                compression_service=compression_service,
                aggressiveness=aggressiveness,
                tenant_profile=tenant_profile,
            )
        )
        if "content" in compressed_message:
            compressed_message["content"] = compressed_content
        compressed_messages.append(compressed_message)

        compressed_estimate = estimate_content_token_details(
            compressed_content,
            estimate_text_tokens=estimate_text_tokens,
        )
        compressed_tokens = compressed_estimate.count
        estimator_names.append(compressed_estimate.estimator)
        tokens_saved = max(0, original_tokens - compressed_tokens)
        output_tokens += compressed_tokens
        user_input_tokens += original_tokens
        user_output_tokens += compressed_tokens
        stats.append(
            MessageCompressionStats(
                index=index,
                role=role,
                original_tokens=original_tokens,
                compressed_tokens=compressed_tokens,
                tokens_saved=tokens_saved,
                compression_applied=applied,
                compressed=tokens_saved > 0,
                text_parts=text_parts,
                compressed_text_parts=compressed_text_parts,
                skipped_reason=None if applied else _user_skip_reason(content),
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
    )


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
) -> tuple[Any, int, int, bool]:
    if isinstance(content, str):
        if not content:
            return content, 0, 0, False

        result = _compress_text(
            content,
            compression_service=compression_service,
            aggressiveness=aggressiveness,
            tenant_profile=tenant_profile,
        )
        return result.text, 1, 1 if result.changed else 0, True

    if not isinstance(content, list):
        return content, 0, 0, False

    compressed_parts: list[Any] = []
    text_parts = 0
    compressed_text_parts = 0
    applied = False

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
            )
            compressed_parts.append(result.text)
            text_parts += 1
            compressed_text_parts += 1 if result.changed else 0
            applied = True
            continue

        if _is_text_dict_part(part):
            result = _compress_text(
                part["text"],
                compression_service=compression_service,
                aggressiveness=aggressiveness,
                tenant_profile=tenant_profile,
            )
            compressed_part = copy.deepcopy(part)
            compressed_part["text"] = result.text
            compressed_parts.append(compressed_part)
            text_parts += 1
            compressed_text_parts += 1 if result.changed else 0
            applied = True
            continue

        compressed_parts.append(copy.deepcopy(part))

    return compressed_parts, text_parts, compressed_text_parts, applied


def _compress_text(
    text: str,
    compression_service: PromptCompressionService,
    aggressiveness: float,
    tenant_profile: TenantCompressionProfile | None,
) -> _TextCompressionResult:
    result = compression_service.compress(
        text=text,
        aggressiveness=aggressiveness,
        include_sections=False,
        tenant_profile=tenant_profile,
    )
    return _TextCompressionResult(
        text=result.compressed_text,
        original_tokens=result.original_tokens,
        compressed_tokens=result.compressed_tokens,
        changed=result.compressed_text != text,
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
