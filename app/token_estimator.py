import logging
import os
import unicodedata
from dataclasses import dataclass
from threading import Lock
from typing import Any


LOGGER = logging.getLogger(__name__)
REGEX_TOKEN_ESTIMATOR = "regex:unicode-word-or-non-space"
TIKTOKEN_FALLBACK_ENCODING = "o200k_base"
HF_TOKENIZER_ALLOW_DOWNLOAD_ENV = "COMPRESSOR_TOKENIZER_ALLOW_DOWNLOAD"

_HF_TOKENIZER_CACHE: dict[str, Any] = {}
_HF_TOKENIZER_FAILURES: set[str] = set()
_HF_TOKENIZER_LOCK = Lock()

_TIKTOKEN_ENCODING_CACHE: dict[str, Any] = {}
_TIKTOKEN_FAILURES: set[str] = set()
_TIKTOKEN_LOCK = Lock()


@dataclass(frozen=True)
class TokenEstimate:
    count: int
    estimator: str
    tokenizer_backed: bool = False


def estimate_token_count(text: str) -> int:
    """Approximate token count using ([\\p{L}\\p{N}]+|[^\\s]) semantics."""
    return estimate_regex_tokens(text).count


def estimate_regex_tokens(text: str) -> TokenEstimate:
    """Return the deterministic built-in fallback token estimate."""
    count = 0
    in_word = False

    for char in text:
        if char.isspace():
            in_word = False
            continue

        if _is_letter_or_number(char):
            if not in_word:
                count += 1
                in_word = True
            continue

        count += 1
        in_word = False

    return TokenEstimate(
        count=count,
        estimator=REGEX_TOKEN_ESTIMATOR,
        tokenizer_backed=False,
    )


def estimate_huggingface_tokens(
    text: str,
    model_name: str,
    *,
    tokenizer: Any | None = None,
) -> TokenEstimate:
    """Estimate tokens with a Hugging Face tokenizer, falling back to regex."""
    active_tokenizer = tokenizer or _load_huggingface_tokenizer(model_name)
    if active_tokenizer is None:
        return estimate_regex_tokens(text)

    count = _count_with_tokenizer(active_tokenizer, text)
    if count is None:
        return estimate_regex_tokens(text)

    return TokenEstimate(
        count=count,
        estimator=f"huggingface:{_tokenizer_name(active_tokenizer, model_name)}",
        tokenizer_backed=True,
    )


def estimate_downstream_tokens(text: str, model_name: str) -> TokenEstimate:
    """Estimate tokens for a downstream model when a known tokenizer is available."""
    if _looks_like_openai_model(model_name):
        estimate = _estimate_tiktoken_tokens(text, model_name)
        if estimate is not None:
            return estimate

    if model_name.startswith("hf:"):
        return estimate_huggingface_tokens(text, model_name.removeprefix("hf:"))

    return estimate_regex_tokens(text)


def merge_token_estimator_names(estimator_names: list[str]) -> str:
    names = [name for name in estimator_names if name]
    if not names:
        return REGEX_TOKEN_ESTIMATOR

    unique_names = sorted(set(names))
    if len(unique_names) == 1:
        return unique_names[0]

    return "mixed:" + ",".join(unique_names)


def _load_huggingface_tokenizer(model_name: str) -> Any | None:
    if model_name in _HF_TOKENIZER_CACHE:
        return _HF_TOKENIZER_CACHE[model_name]
    if model_name in _HF_TOKENIZER_FAILURES:
        return None

    with _HF_TOKENIZER_LOCK:
        if model_name in _HF_TOKENIZER_CACHE:
            return _HF_TOKENIZER_CACHE[model_name]
        if model_name in _HF_TOKENIZER_FAILURES:
            return None

        try:
            from transformers import AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(
                model_name,
                use_fast=True,
                local_files_only=not _allow_huggingface_tokenizer_download(),
            )
        except Exception as exc:  # pragma: no cover - depends on optional runtime deps
            LOGGER.warning(
                "Falling back to regex token estimate; failed to load Hugging Face "
                "tokenizer %s: %s",
                model_name,
                exc,
            )
            _HF_TOKENIZER_FAILURES.add(model_name)
            return None

        _HF_TOKENIZER_CACHE[model_name] = tokenizer
        return tokenizer


def _count_with_tokenizer(tokenizer: Any, text: str) -> int | None:
    try:
        encoded = tokenizer(
            text,
            add_special_tokens=False,
            return_attention_mask=False,
            return_token_type_ids=False,
        )
        input_ids = encoded["input_ids"]
    except TypeError:
        try:
            input_ids = tokenizer.encode(text, add_special_tokens=False)
        except Exception:
            LOGGER.exception("Failed to count tokens with tokenizer %r", tokenizer)
            return None
    except Exception:
        LOGGER.exception("Failed to count tokens with tokenizer %r", tokenizer)
        return None

    if input_ids and isinstance(input_ids[0], list):
        input_ids = input_ids[0]

    return len(input_ids)


def _estimate_tiktoken_tokens(text: str, model_name: str) -> TokenEstimate | None:
    encoding = _load_tiktoken_encoding(model_name)
    if encoding is None:
        return None

    try:
        return TokenEstimate(
            count=len(encoding.encode(text)),
            estimator=f"tiktoken:{getattr(encoding, 'name', model_name)}",
            tokenizer_backed=True,
        )
    except Exception:
        LOGGER.exception("Failed to count tokens with tiktoken for %s", model_name)
        return None


def _load_tiktoken_encoding(model_name: str) -> Any | None:
    if model_name in _TIKTOKEN_ENCODING_CACHE:
        return _TIKTOKEN_ENCODING_CACHE[model_name]
    if model_name in _TIKTOKEN_FAILURES:
        return None

    with _TIKTOKEN_LOCK:
        if model_name in _TIKTOKEN_ENCODING_CACHE:
            return _TIKTOKEN_ENCODING_CACHE[model_name]
        if model_name in _TIKTOKEN_FAILURES:
            return None

        try:
            import tiktoken
        except Exception as exc:  # pragma: no cover - optional dependency
            LOGGER.warning(
                "Falling back to regex token estimate; tiktoken is unavailable: %s",
                exc,
            )
            _TIKTOKEN_FAILURES.add(model_name)
            return None

        try:
            encoding = tiktoken.encoding_for_model(model_name)
        except KeyError:
            try:
                encoding = tiktoken.get_encoding(TIKTOKEN_FALLBACK_ENCODING)
            except Exception as exc:  # pragma: no cover - optional dependency data
                LOGGER.warning(
                    "Falling back to regex token estimate; failed to load tiktoken "
                    "encoding for %s: %s",
                    model_name,
                    exc,
                )
                _TIKTOKEN_FAILURES.add(model_name)
                return None
        except Exception as exc:  # pragma: no cover - optional dependency data
            LOGGER.warning(
                "Falling back to regex token estimate; failed to load tiktoken "
                "encoding for %s: %s",
                model_name,
                exc,
            )
            _TIKTOKEN_FAILURES.add(model_name)
            return None

        _TIKTOKEN_ENCODING_CACHE[model_name] = encoding
        return encoding


def _tokenizer_name(tokenizer: Any, model_name: str) -> str:
    name = getattr(tokenizer, "name_or_path", None)
    if isinstance(name, str) and name:
        return name
    return model_name


def _allow_huggingface_tokenizer_download() -> bool:
    return os.getenv(HF_TOKENIZER_ALLOW_DOWNLOAD_ENV, "").lower() in {
        "1",
        "true",
        "yes",
    }


def _looks_like_openai_model(model_name: str) -> bool:
    normalized = model_name.lower()
    return normalized.startswith(("gpt-", "o1", "o3", "o4", "text-", "ft:gpt-"))


def _is_letter_or_number(char: str) -> bool:
    return unicodedata.category(char)[0] in {"L", "N"}
