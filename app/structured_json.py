import base64
import json
import re
from dataclasses import dataclass
from typing import Any, Callable


OPEN_COMPRESS_JSON_PATTERN = re.compile(
    r"<compress-json\b(?P<attrs>[^>]*)>",
    re.IGNORECASE,
)
CLOSE_COMPRESS_JSON_PATTERN = re.compile(r"</compress-json\s*>", re.IGNORECASE)
ATTRIBUTE_PATTERN = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"
    r"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
    re.DOTALL,
)
POLICY_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
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
    allow_inline_paths: bool = False,
) -> TaggedJsonTransformResult:
    """Compress selected string leaves while preserving JSON structure.

    Tenant paths authorize policy-based tags. Inline ``paths`` are accepted
    only when the caller explicitly enables them. If a tag supplies both, its
    requested paths are intersected with the tenant-authorized paths.
    """
    if "<compress-json" not in text.lower():
        return TaggedJsonTransformResult(text=text)

    configured_patterns, warnings = _parse_paths(value_paths)
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
        attrs = _parse_attributes(opening.group("attrs"))

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

        if attrs is None or any(name not in {"policy", "paths"} for name in attrs):
            warnings.append("json_tag_attributes_invalid")
            output_parts.append(_protect_json(body))
            cursor = closing.end()
            continue

        tag_policy = attrs.get("policy")
        if tag_policy is not None and POLICY_ID_PATTERN.fullmatch(tag_policy) is None:
            warnings.append("json_tag_policy_invalid")
            output_parts.append(_protect_json(body))
            cursor = closing.end()
            continue

        inline_paths = _split_inline_paths(attrs.get("paths"))
        inline_patterns: list[tuple[str | int, ...]] | None = None
        if inline_paths is not None:
            inline_patterns, inline_warnings = _parse_paths(inline_paths)
            warnings.extend(inline_warnings)

        patterns = _authorized_patterns(
            tag_policy=tag_policy,
            configured_policy=policy_id,
            configured_patterns=configured_patterns,
            inline_patterns=inline_patterns,
            allow_inline_paths=allow_inline_paths,
            warnings=warnings,
        )
        if patterns is None:
            output_parts.append(_protect_json(body))
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
        rebuilt = json.dumps(updated, ensure_ascii=False, separators=(",", ":"))
        output_parts.append(_protect_json(rebuilt))
        cursor = closing.end()

    return TaggedJsonTransformResult(
        text="".join(output_parts),
        compressed_value_count=compressed_value_count,
        warnings=tuple(dict.fromkeys(warnings)),
    )


def parse_value_path(path: str) -> tuple[str | int, ...] | None:
    """Parse the safe JSONPath subset, such as ``$.comments[*].body``."""
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


def _parse_attributes(source: str) -> dict[str, str] | None:
    attrs: dict[str, str] = {}
    cursor = 0
    while cursor < len(source):
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1
        if cursor == len(source):
            break
        match = ATTRIBUTE_PATTERN.match(source, cursor)
        if match is None:
            return None
        name = match.group("name").lower()
        if name in attrs:
            return None
        attrs[name] = match.group("value")
        cursor = match.end()
    return attrs


def _split_inline_paths(value: str | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    paths = tuple(path.strip() for path in value.split(",") if path.strip())
    return paths


def _parse_paths(
    value_paths: tuple[str, ...],
) -> tuple[list[tuple[str | int, ...]], list[str]]:
    patterns: list[tuple[str | int, ...]] = []
    warnings: list[str] = []
    for value_path in value_paths:
        parsed_path = parse_value_path(value_path)
        if parsed_path is None:
            warnings.append(f"json_value_path_invalid:{value_path}")
            continue
        if parsed_path not in patterns:
            patterns.append(parsed_path)
    return patterns, warnings


def _authorized_patterns(
    *,
    tag_policy: str | None,
    configured_policy: str | None,
    configured_patterns: list[tuple[str | int, ...]],
    inline_patterns: list[tuple[str | int, ...]] | None,
    allow_inline_paths: bool,
    warnings: list[str],
) -> list[tuple[str | int, ...]] | None:
    if tag_policy is not None:
        if configured_policy is None or tag_policy != configured_policy:
            warnings.append(f"json_tag_policy_not_authorized:{tag_policy}")
            return None
        if inline_patterns is None:
            return configured_patterns
        return [path for path in inline_patterns if path in configured_patterns]

    if inline_patterns is None:
        warnings.append("json_tag_authorization_missing")
        return None
    if not allow_inline_paths:
        warnings.append("json_tag_inline_paths_not_authorized")
        return None
    return inline_patterns


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
        compressed = compress_value(_format_path(path), value)
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
    return all(
        expected == "*" or expected == actual
        for expected, actual in zip(pattern, path)
    )


def _format_path(path: tuple[str | int, ...]) -> str:
    result = "$"
    for token in path:
        result += f"[{token}]" if isinstance(token, int) else f".{token}"
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


def _protect_json(text: str) -> str:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return f"<protected-json>{encoded}</protected-json>"
