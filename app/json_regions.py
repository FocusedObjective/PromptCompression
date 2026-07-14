from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any, Iterable


STRICT_CLASSES = frozenset({"strict_json_object", "strict_json_array"})
NON_REWRITABLE_CLASSES = frozenset(
    {
        "ndjson",
        "concatenated_json",
        "jsonc_like",
        "javascript_object_like",
        "template_or_bracket_syntax",
        "invalid_balanced",
        "invalid_unbalanced",
    }
)

START_PATTERN = re.compile(r"[\[{]")
TEMPLATE_START_PATTERN = re.compile(r"(?:\$\{|\{\{|\{%|\[[^\]\n]+\]\()")
COMMENT_PATTERN = re.compile(r"//|/\*")
TRAILING_COMMA_PATTERN = re.compile(r",\s*[}\]]")
UNQUOTED_KEY_PATTERN = re.compile(r"[{,]\s*[A-Za-z_$][\w$]*\s*:")
SINGLE_QUOTED_PATTERN = re.compile(r"'[^'\r\n]*'\s*[,}:\]]")
SECTION_HEADING_PATTERN = re.compile(r"(?m)^#{1,6}\s+.*$")


@dataclass(frozen=True)
class JsonRegion:
    start: int
    end: int
    syntax_class: str
    parsed_value: object | None
    canonical_sha256: str | None
    duplicate_keys: tuple[str, ...]
    context_flags: frozenset[str]
    parse_error: str | None

    @property
    def rewrite_eligible(self) -> bool:
        return (
            self.syntax_class in STRICT_CLASSES
            and not self.duplicate_keys
            and not (
                self.context_flags
                & {
                    "ambiguous_parent",
                    "code",
                    "exact_output",
                    "example",
                    "fixture",
                    "protected",
                    "schema",
                    "template",
                    "tool_protocol",
                }
            )
        )


class JsonRegionDetector:
    """Discover JSON-like regions without granting them rewrite authority."""

    def detect(
        self,
        text: str,
        *,
        excluded_spans: Iterable[tuple[int, int, str]] = (),
    ) -> list[JsonRegion]:
        exclusions = tuple(excluded_spans)
        regions: list[JsonRegion] = []
        failed_parents: list[tuple[int, int]] = []
        cursor = 0
        while cursor < len(text):
            match = START_PATTERN.search(text, cursor)
            if match is None:
                break
            start = match.start()
            excluded_kind = _excluded_kind(start, exclusions)
            if excluded_kind is not None:
                cursor = start + 1
                continue

            template_end = _template_or_bracket_end(text, start)
            if template_end is not None:
                regions.append(
                    JsonRegion(
                        start=start,
                        end=template_end,
                        syntax_class="template_or_bracket_syntax",
                        parsed_value=None,
                        canonical_sha256=None,
                        duplicate_keys=(),
                        context_flags=frozenset({"template"}),
                        parse_error=None,
                    )
                )
                cursor = start + 1
                continue

            if not _plausible_json_start(text, start):
                end = _balanced_end(text, start) or start + 1
                if end > start + 1:
                    failed_parents.append((start, end))
                candidate = text[start:end]
                syntax_class = _invalid_syntax_class(
                    candidate,
                    end if end > start + 1 else None,
                )
                if syntax_class in {"invalid_balanced", "invalid_unbalanced"}:
                    syntax_class = "template_or_bracket_syntax"
                regions.append(
                    JsonRegion(
                        start=start,
                        end=end,
                        syntax_class=syntax_class,
                        parsed_value=None,
                        canonical_sha256=None,
                        duplicate_keys=(),
                        context_flags=frozenset(),
                        parse_error="implausible_json_start",
                    )
                )
                cursor = start + 1
                continue

            duplicates: list[str] = []

            def pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                seen: set[str] = set()
                value: dict[str, Any] = {}
                for key, child in pairs:
                    if key in seen:
                        duplicates.append(key)
                    seen.add(key)
                    value[key] = child
                return value

            decoder = json.JSONDecoder(object_pairs_hook=pairs_hook)
            try:
                value, relative_end = decoder.raw_decode(text[start:])
            except json.JSONDecodeError as exc:
                balanced_end = _balanced_end(text, start)
                if balanced_end is not None:
                    failed_parents.append((start, balanced_end))
                end = balanced_end or len(text)
                candidate = text[start:end]
                syntax_class = _invalid_syntax_class(candidate, balanced_end)
                regions.append(
                    JsonRegion(
                        start=start,
                        end=end,
                        syntax_class=syntax_class,
                        parsed_value=None,
                        canonical_sha256=None,
                        duplicate_keys=(),
                        context_flags=_context_flags(text, start),
                        parse_error=_stable_parse_error(exc),
                    )
                )
                cursor = start + 1
                continue

            end = start + relative_end
            if not isinstance(value, (dict, list)) or not _valid_end_boundary(text, end):
                regions.append(
                    JsonRegion(
                        start=start,
                        end=end,
                        syntax_class="invalid_balanced",
                        parsed_value=None,
                        canonical_sha256=None,
                        duplicate_keys=(),
                        context_flags=_context_flags(text, start),
                        parse_error="invalid_token_boundary",
                    )
                )
                cursor = start + 1
                continue

            flags = set(_context_flags(text, start))
            if any(parent_start < start < parent_end for parent_start, parent_end in failed_parents):
                flags.add("ambiguous_parent")
            regions.append(
                JsonRegion(
                    start=start,
                    end=end,
                    syntax_class=(
                        "strict_json_object" if isinstance(value, dict) else "strict_json_array"
                    ),
                    parsed_value=value,
                    canonical_sha256=_canonical_hash(value),
                    duplicate_keys=tuple(sorted(set(duplicates))),
                    context_flags=frozenset(flags),
                    parse_error=None,
                )
            )
            cursor = end

        return _classify_sequences(text, regions)


def _classify_sequences(text: str, regions: list[JsonRegion]) -> list[JsonRegion]:
    strict_indices = [
        index for index, region in enumerate(regions) if region.syntax_class in STRICT_CLASSES
    ]
    replacements: dict[int, JsonRegion] = {}
    for left_index, right_index in zip(strict_indices, strict_indices[1:]):
        left = regions[left_index]
        right = regions[right_index]
        between = text[left.end:right.start]
        if between and between.strip():
            continue
        syntax_class = "ndjson" if "\n" in between else "concatenated_json"
        replacements[left_index] = replace(left, syntax_class=syntax_class)
        replacements[right_index] = replace(right, syntax_class=syntax_class)
    return [replacements.get(index, region) for index, region in enumerate(regions)]


def _plausible_json_start(text: str, start: int) -> bool:
    opening = text[start]
    index = start + 1
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text):
        return False
    next_char = text[index]
    if opening == "{":
        return next_char in {'"', "}"}
    if next_char in {'{', '[', '"', ']', '-'} or next_char.isdigit():
        return True
    return text.startswith(("true", "false", "null"), index)


def _template_or_bracket_end(text: str, start: int) -> int | None:
    prefix = text[max(0, start - 1):start + 2]
    if prefix.startswith("${") or text.startswith(("{{", "{%"), start):
        return _balanced_end(text, start) or start + 1
    if text[start] == "[":
        close = text.find("]", start + 1)
        if close >= 0 and close + 1 < len(text) and text[close + 1] == "(":
            paren = text.find(")", close + 2)
            return len(text) if paren < 0 else paren + 1
    return None


def _balanced_end(text: str, start: int) -> int | None:
    stack = ["}" if text[start] == "{" else "]"]
    in_string = False
    escaped = False
    for index in range(start + 1, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            stack.append("]" if char == "[" else "}")
        elif stack and char == stack[-1]:
            stack.pop()
            if not stack:
                return index + 1
    return None


def _invalid_syntax_class(candidate: str, balanced_end: int | None) -> str:
    if balanced_end is None:
        return "invalid_unbalanced"
    if COMMENT_PATTERN.search(candidate) or TRAILING_COMMA_PATTERN.search(candidate):
        return "jsonc_like"
    if UNQUOTED_KEY_PATTERN.search(candidate) or SINGLE_QUOTED_PATTERN.search(candidate):
        return "javascript_object_like"
    return "invalid_balanced"


def _context_flags(text: str, start: int) -> frozenset[str]:
    heading_matches = list(SECTION_HEADING_PATTERN.finditer(text, 0, start))
    section_start = heading_matches[-1].start() if heading_matches else 0
    context = text[section_start:start].lower()
    flags: set[str] = set()
    terms = {
        "schema": ("schema",),
        "fixture": ("fixture",),
        "example": ("example",),
        "template": ("template",),
        "tool_protocol": ("tool call", "tool result", "function call", "protocol"),
    }
    for flag, values in terms.items():
        if any(value in context for value in values):
            flags.add(flag)
    exact_terms = ("exact", "verbatim", "unchanged", "do not change")
    structured_terms = ("json", "schema", "template", "fixture", "output format")
    if (
        any(value in context for value in exact_terms)
        and any(value in context for value in structured_terms)
    ):
        flags.add("exact_output")
    return frozenset(flags)


def _valid_end_boundary(text: str, end: int) -> bool:
    if end >= len(text):
        return True
    return text[end].isspace() or text[end] in ",.;:!?)]}{["


def _stable_parse_error(exc: json.JSONDecodeError) -> str:
    message = exc.msg.lower()
    if "property name" in message:
        return "expected_property_name"
    if "delimiter" in message:
        return "expected_delimiter"
    if "unterminated string" in message:
        return "unterminated_string"
    if "extra data" in message:
        return "extra_data"
    return "invalid_json"


def _canonical_hash(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _excluded_kind(
    position: int,
    exclusions: tuple[tuple[int, int, str], ...],
) -> str | None:
    for start, end, kind in exclusions:
        if start <= position < end:
            return kind
    return None
