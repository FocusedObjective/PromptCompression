import json
import re
from dataclasses import dataclass
from typing import Any, Callable


OPEN_COMPRESS_JSON_PATTERN = re.compile(
    r"<compress-json\b(?P<attrs>[^>]*)>",
    re.IGNORECASE,
)
CLOSE_COMPRESS_JSON_PATTERN = re.compile(r"</compress-json\s*>", re.IGNORECASE)
POLICY_ATTRIBUTE_PATTERN = re.compile(
    r"\s*policy\s*=\s*(?P<quote>['\"])(?P<policy>[A-Za-z0-9_.:-]{1,128})"
    r"(?P=quote)\s*",
    re.IGNORECASE,
)
PATH_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")


@dataclass(frozen=True)
class TaggedJsonTransformResult:
    text: str
    compressed_value_count: int = 0
    warnings: tuple[str, ...] = ()


def transform_tagged_json(
    text: str,
    *,
    policy_id: str | None,
    value_paths: tuple[str, ...],
    max_values: int,
    compress_value: Callable[[str, str], str | None],
) -> TaggedJsonTransformResult:
    """Transform explicitly tagged JSON while keeping structure deterministic.

    A matching tenant policy may compress allowlisted string leaves. The
    rebuilt JSON is compact and later enters the normal TOON-or-protect path.
    Invalid or duplicate-key payloads are wrapped as no-compress content so an
    opt-in tag can never make malformed structured content less safe.
    """
    if "<compress-json" not in text.lower():
        return TaggedJsonTransformResult(text=text)

    patterns: list[tuple[str | int, ...]] = []
    warnings: list[str] = []
    for value_path in value_paths:
        parsed_path = parse_value_path(value_path)
        if parsed_path is None:
            warnings.append(f"json_value_path_invalid:{value_path}")
            continue
        patterns.append(parsed_path)

    compressed_value_count = 0
    output_parts: list[str] = []
    cursor = 0
    while True:
        opening = OPEN_COMPRESS_JSON_PATTERN.search(text, cursor)
        if opening is None:
            output_parts.append(text[cursor:])
            break

        output_parts.append(text[cursor : opening.start()])
        body_start = opening.end()
        while body_start < len(text) and text[body_start].isspace():
            body_start += 1

        parsed, body_end, duplicate_keys = _decode_json_at(text, body_start)
        if parsed is None:
            closing = CLOSE_COMPRESS_JSON_PATTERN.search(text, opening.end())
            if closing is None:
                warnings.append("json_tag_unclosed_protected")
                output_parts.append(_protect_verbatim(text[opening.end() :]))
                cursor = len(text)
                break
            warnings.append("json_tag_invalid_json_protected")
            output_parts.append(
                _protect_verbatim(text[opening.end() : closing.start()].strip())
            )
            cursor = closing.end()
            continue

        close_start = body_end
        while close_start < len(text) and text[close_start].isspace():
            close_start += 1
        closing = CLOSE_COMPRESS_JSON_PATTERN.match(text, close_start)
        if closing is None:
            warnings.append("json_tag_missing_close_after_json_protected")
            output_parts.append(_protect_verbatim(text[body_start:body_end]))
            cursor = body_end
            continue

        body = text[body_start:body_end]
        attrs = opening.group("attrs")
        policy_match = POLICY_ATTRIBUTE_PATTERN.fullmatch(attrs)
        tag_policy = None if policy_match is None else policy_match.group("policy")

        if not isinstance(parsed, (dict, list)):
            warnings.append("json_tag_root_must_be_object_or_array_protected")
            output_parts.append(_protect_verbatim(body))
            cursor = closing.end()
            continue

        if duplicate_keys:
            warnings.append("json_tag_duplicate_keys_protected")
            output_parts.append(_protect_verbatim(body))
            cursor = closing.end()
            continue

        if policy_id is None or tag_policy != policy_id:
            if tag_policy is not None:
                warnings.append(f"json_tag_policy_not_authorized:{tag_policy}")
            output_parts.append(body)
            cursor = closing.end()
            continue

        remaining = max(0, max_values - compressed_value_count)
        updated, count = _transform_value_tree(
            parsed,
            path=(),
            patterns=patterns,
            remaining=remaining,
            compress_value=compress_value,
        )
        compressed_value_count += count
        output_parts.append(
            json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
        )
        cursor = closing.end()

    transformed = "".join(output_parts)
    return TaggedJsonTransformResult(
        text=transformed,
        compressed_value_count=compressed_value_count,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def parse_value_path(path: str) -> tuple[str | int, ...] | None:
    """Parse a safe JSONPath subset such as $.comments[*].body."""
    value = path.strip()
    if value == "$":
        return ()
    if not value.startswith("$."):
        return None

    tokens: list[str | int] = []
    cursor = 2
    while cursor < len(value):
        key_match = PATH_KEY_PATTERN.match(value, cursor)
        if key_match is None:
            return None
        tokens.append(key_match.group(0))
        cursor = key_match.end()

        if value.startswith("[*]", cursor):
            tokens.append("*")
            cursor += 3

        if cursor == len(value):
            break
        if value[cursor] != ".":
            return None
        cursor += 1

    return tuple(tokens)


def _transform_value_tree(
    value: Any,
    *,
    path: tuple[str | int, ...],
    patterns: list[tuple[str | int, ...]],
    remaining: int,
    compress_value: Callable[[str, str], str | None],
) -> tuple[Any, int]:
    if remaining <= 0:
        return value, 0

    if isinstance(value, str):
        if not any(_path_matches(pattern, path) for pattern in patterns):
            return value, 0
        path_text = _format_path(path)
        compressed = compress_value(path_text, value)
        if compressed is None or compressed == value:
            return value, 0
        return compressed, 1

    count = 0
    if isinstance(value, list):
        updated_list: list[Any] = []
        for index, item in enumerate(value):
            updated, item_count = _transform_value_tree(
                item,
                path=(*path, index),
                patterns=patterns,
                remaining=remaining - count,
                compress_value=compress_value,
            )
            updated_list.append(updated)
            count += item_count
        return updated_list, count

    if isinstance(value, dict):
        updated_dict: dict[str, Any] = {}
        for key, item in value.items():
            updated, item_count = _transform_value_tree(
                item,
                path=(*path, key),
                patterns=patterns,
                remaining=remaining - count,
                compress_value=compress_value,
            )
            updated_dict[key] = updated
            count += item_count
        return updated_dict, count

    return value, 0


def _path_matches(
    pattern: tuple[str | int, ...],
    path: tuple[str | int, ...],
) -> bool:
    if len(pattern) != len(path):
        return False
    return all(expected == "*" or expected == actual for expected, actual in zip(pattern, path))


def _format_path(path: tuple[str | int, ...]) -> str:
    result = "$"
    for token in path:
        if isinstance(token, int):
            result += f"[{token}]"
        else:
            result += f".{token}"
    return result


def _decode_json_at(text: str, start: int) -> tuple[Any | None, int, bool]:
    duplicate_keys = False

    def collect_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        nonlocal duplicate_keys
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                duplicate_keys = True
            result[key] = value
        return result

    decoder = json.JSONDecoder(object_pairs_hook=collect_pairs)
    try:
        parsed, end = decoder.raw_decode(text, start)
    except json.JSONDecodeError:
        return None, start, False
    return parsed, end, duplicate_keys


def _protect_verbatim(text: str) -> str:
    return f"<nocompress>{text}</nocompress>"
