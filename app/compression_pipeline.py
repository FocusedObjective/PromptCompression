import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.html_compactor import (
    compact_html_to_markdown,
    should_preserve_html_verbatim,
)
from app.toon_adapter import ToonEncodingError, encode_toon
from app.whitespace_normalizer import (
    ENABLE_STRICT_PROSE_WHITESPACE,
    normalize_whitespace,
)

MIN_JSON_CHARS = int(os.getenv("COMPRESSOR_MIN_JSON_CHARS", "300"))
MIN_JSON_LINES = int(os.getenv("COMPRESSOR_MIN_JSON_LINES", "4"))
MIN_TOON_SAVINGS = float(os.getenv("COMPRESSOR_MIN_TOON_SAVINGS", "0.08"))
ENABLE_HTML_MARKDOWN = os.getenv(
    "COMPRESSOR_ENABLE_HTML_MARKDOWN",
    "true",
).lower() in {"1", "true", "yes", "on"}
MIN_HTML_CHARS = int(os.getenv("COMPRESSOR_MIN_HTML_CHARS", "1000"))
MIN_HTML_MARKDOWN_SAVINGS = float(
    os.getenv("COMPRESSOR_MIN_HTML_MARKDOWN_SAVINGS", "0.20")
)
ENABLE_JSON_MINIFY = os.getenv(
    "COMPRESSOR_ENABLE_JSON_MINIFY",
    "false",
).lower() in {"1", "true", "yes", "on"}
MIN_JSON_MINIFY_SAVINGS = float(os.getenv("COMPRESSOR_MIN_JSON_MINIFY_SAVINGS", "0.05"))

NOCOMPRESS_PATTERN = re.compile(
    r"<nocompress>(?P<body>.*?)</nocompress>",
    re.IGNORECASE | re.DOTALL,
)

MARKDOWN_FENCE_PATTERN = re.compile(
    r"(?P<fence>`{3,}|~{3,})(?P<info>[^\n]*)\n"
    r"(?P<body>.*?)(?:\n(?P=fence)[ \t]*(?:\n|$)|(?P=fence))",
    re.DOTALL,
)
JSON_START_PATTERN = re.compile(r"[{\[]")
HTML_BLOCK_TAGS = (
    "html",
    "head",
    "body",
    "pre",
    "code",
    "script",
    "style",
    "template",
    "svg",
)
HTML_START_TAG_PATTERN = re.compile(
    r"<(?P<tag>" + "|".join(HTML_BLOCK_TAGS) + r")\b[^>]*>",
    re.IGNORECASE,
)
HTML_DOCTYPE_BEFORE_DOCUMENT_PATTERN = re.compile(
    r"<!doctype\s+html\b[^>]*>\s*$",
    re.IGNORECASE,
)
HTML_END_TAG_PATTERNS = {
    tag: re.compile(r"</" + tag + r"\s*>", re.IGNORECASE)
    for tag in HTML_BLOCK_TAGS
}
UI_BLOCK_PATTERN = re.compile(
    r"(?ms)^\[(?P<name>UI|FOLLOW_ON_QUESTIONS|SWITCH_PANEL_AGENT)\]\s*\n"
    r".*?^\[/(?P=name)\][ \t]*(?:\n|$)",
)
CONTRACT_SECTION_PATTERN = re.compile(
    r"(?ms)^# (?:UI RENDERING CONTRACT|CONTENT FORMATS|USER-FACING VOCABULARY|"
    r"ALLOWED UI COMPONENTS)\b"
    r".*?(?=^# (?:OPERATING MODE|DATA TO PROCESS|FAILURE MODES|"
    r"OPTIONAL FOLLOW-ON CONTENT|CHIP-|RULE PRECEDENCE|FINAL RULES)\b|\Z)",
)
FAILURE_SECTION_PATTERN = re.compile(
    r"(?ms)^# FAILURE MODES\b"
    r".*?(?:^---\s*$(?:\n|$)|(?=^# (?:OPTIONAL FOLLOW-ON CONTENT|CHIP-|"
    r"RULE PRECEDENCE|FINAL RULES)\b|\Z))",
)
FOLLOW_ON_SECTION_PATTERN = re.compile(
    r"(?ms)^# OPTIONAL FOLLOW-ON CONTENT\b"
    r".*?(?=^# (?:RULE PRECEDENCE|FINAL RULES)\b|\Z)",
)
DATA_PAYLOAD_PATTERN = re.compile(
    r"(?ms)^# DATA TO PROCESS\b.*?(?=^---\s*$|\Z)",
)


@dataclass(frozen=True)
class CompressionSegment:
    text: str
    compressible: bool
    kind: str
    source_text: str | None = None


@dataclass(frozen=True)
class _Span:
    start: int
    end: int


class PromptPreprocessor:
    def __init__(
        self,
        toon_encoder: Callable[[Any], str] = encode_toon,
        min_json_chars: int = MIN_JSON_CHARS,
        min_json_lines: int = MIN_JSON_LINES,
        min_toon_savings: float = MIN_TOON_SAVINGS,
        html_markdown_converter: Callable[[str], str | None] = compact_html_to_markdown,
        enable_html_markdown: bool = ENABLE_HTML_MARKDOWN,
        min_html_chars: int = MIN_HTML_CHARS,
        min_html_markdown_savings: float = MIN_HTML_MARKDOWN_SAVINGS,
        enable_json_minify: bool = ENABLE_JSON_MINIFY,
        min_json_minify_savings: float = MIN_JSON_MINIFY_SAVINGS,
        strict_prose_whitespace: bool = ENABLE_STRICT_PROSE_WHITESPACE,
    ) -> None:
        self.toon_encoder = toon_encoder
        self.min_json_chars = min_json_chars
        self.min_json_lines = min_json_lines
        self.min_toon_savings = min_toon_savings
        self.html_markdown_converter = html_markdown_converter
        self.enable_html_markdown = enable_html_markdown
        self.min_html_chars = min_html_chars
        self.min_html_markdown_savings = min_html_markdown_savings
        self.enable_json_minify = enable_json_minify
        self.min_json_minify_savings = min_json_minify_savings
        self.strict_prose_whitespace = strict_prose_whitespace

    def prepare(self, text: str) -> list[CompressionSegment]:
        html_markdown_segment = self._html_markdown_segment_for_candidate(text)
        if html_markdown_segment is not None:
            return [html_markdown_segment]

        segments: list[CompressionSegment] = []
        cursor = 0

        for match in NOCOMPRESS_PATTERN.finditer(text):
            segments.extend(self._prepare_compressible_text(text[cursor : match.start()]))
            body = match.group("body")
            if body:
                segments.append(
                    CompressionSegment(
                        text=body,
                        compressible=False,
                        kind="nocompress",
                        source_text=match.group(0),
                    )
                )
            cursor = match.end()

        segments.extend(self._prepare_compressible_text(text[cursor:]))
        return [segment for segment in segments if segment.text]

    def _prepare_compressible_text(self, text: str) -> list[CompressionSegment]:
        html_markdown_segment = self._html_markdown_segment_for_candidate(text)
        if html_markdown_segment is not None:
            return [html_markdown_segment]

        segments: list[CompressionSegment] = []
        cursor = 0

        for span in self._special_spans(text):
            segments.extend(self._prepare_compressible_text_without_verbatim(text[cursor : span.start]))
            segments.append(
                CompressionSegment(
                    text=text[span.start : span.end],
                    compressible=False,
                    kind="verbatim",
                    source_text=text[span.start : span.end],
                )
            )
            cursor = span.end

        segments.extend(self._prepare_compressible_text_without_verbatim(text[cursor:]))
        return segments

    def _prepare_compressible_text_without_verbatim(
        self,
        text: str,
    ) -> list[CompressionSegment]:
        if "```" not in text and "~~~" not in text:
            return self._prepare_raw_json_text(text)

        segments: list[CompressionSegment] = []
        cursor = 0

        for match in MARKDOWN_FENCE_PATTERN.finditer(text):
            segments.extend(self._prepare_raw_json_text(text[cursor : match.start()]))

            if not self._is_json_fence(match.group("info")):
                segments.append(
                    CompressionSegment(
                        text=match.group(0),
                        compressible=False,
                        kind="code",
                        source_text=match.group(0),
                    )
                )
                cursor = match.end()
                continue

            body = match.group("body").rstrip("\n")
            json_segment = self._json_segment_for_candidate(body, allow_toon=False)
            if json_segment is None:
                segments.append(
                    CompressionSegment(
                        text=match.group(0),
                        compressible=False,
                        kind="code",
                    )
                )
            elif json_segment.kind == "toon":
                segments.append(
                    CompressionSegment(
                        text=(
                            f"{match.group('fence')}toon\n"
                            f"{json_segment.text}\n"
                            f"{match.group('fence')}"
                        ),
                        compressible=False,
                        kind="toon",
                        source_text=match.group(0),
                    )
                )
            else:
                segments.append(
                    CompressionSegment(
                        text=match.group(0),
                        compressible=False,
                        kind="json",
                        source_text=match.group(0),
                    )
                )
            cursor = match.end()

        segments.extend(self._prepare_raw_json_text(text[cursor:]))
        return segments

    def _is_json_fence(self, info: str) -> bool:
        language = info.strip().split(maxsplit=1)[0].lower() if info.strip() else ""
        return language == "json"

    def _special_spans(self, text: str) -> list[_Span]:
        if "[" not in text and "#" not in text:
            return []

        spans: list[_Span] = []
        for pattern in (
            UI_BLOCK_PATTERN,
            CONTRACT_SECTION_PATTERN,
            FAILURE_SECTION_PATTERN,
            FOLLOW_ON_SECTION_PATTERN,
            DATA_PAYLOAD_PATTERN,
        ):
            spans.extend(_Span(match.start(), match.end()) for match in pattern.finditer(text))

        return self._merge_spans(spans)

    def _merge_spans(self, spans: list[_Span]) -> list[_Span]:
        if not spans:
            return []

        merged: list[_Span] = []
        for span in sorted(spans, key=lambda item: (item.start, item.end)):
            if not merged or span.start > merged[-1].end:
                merged.append(span)
                continue

            previous = merged[-1]
            merged[-1] = _Span(previous.start, max(previous.end, span.end))

        return merged

    def _prepare_raw_json_text(self, text: str) -> list[CompressionSegment]:
        segments: list[CompressionSegment] = []
        cursor = 0
        search_cursor = 0

        while search_cursor < len(text):
            start = self._find_json_start(text, search_cursor)
            if start is None:
                segments.extend(self._prose_segments(text[cursor:]))
                break

            end = self._find_balanced_json_end(text, start)
            if end is None:
                segments.extend(self._prose_segments(text[cursor:]))
                break

            candidate = text[start:end]
            leading_context = text[max(0, cursor) : start]
            json_segment = self._json_segment_for_candidate(
                candidate,
                leading_context=leading_context,
            )
            if json_segment is None:
                search_cursor = end
                continue

            if start > cursor:
                segments.extend(self._prose_segments(text[cursor:start]))

            segments.append(json_segment)
            cursor = end
            search_cursor = end

        return segments

    def _prose_segments(self, text: str) -> list[CompressionSegment]:
        if "<" not in text:
            prose_segment = self._prose_segment(text)
            return [] if prose_segment is None else [prose_segment]

        html_markdown_segment = self._html_markdown_segment_for_candidate(text)
        if html_markdown_segment is not None:
            return [html_markdown_segment]

        segments: list[CompressionSegment] = []
        cursor = 0

        for span in self._html_block_spans(text):
            prose_segment = self._prose_segment(text[cursor : span.start])
            if prose_segment is not None:
                segments.append(prose_segment)

            html_text = text[span.start : span.end]
            html_segment = self._html_markdown_segment_for_candidate(
                html_text,
                leading_context=text[max(0, cursor) : span.start],
            )
            if html_segment is None:
                html_segment = self._prose_segment(html_text)
            if html_segment is not None:
                segments.append(html_segment)

            cursor = span.end

        prose_segment = self._prose_segment(text[cursor:])
        if prose_segment is not None:
            segments.append(prose_segment)

        return segments

    def _html_markdown_segment_for_candidate(
        self,
        candidate: str,
        *,
        leading_context: str = "",
    ) -> CompressionSegment | None:
        if not self.enable_html_markdown:
            return None
        if len(candidate) < self.min_html_chars:
            return None
        if should_preserve_html_verbatim(candidate, leading_context=leading_context):
            return None

        markdown = self.html_markdown_converter(candidate)
        if markdown is None or not markdown.strip():
            return None

        savings = 1.0 - (len(markdown) / len(candidate))
        if savings < self.min_html_markdown_savings:
            return None

        return CompressionSegment(
            text=markdown,
            compressible=False,
            kind="html_markdown",
            source_text=candidate,
        )

    def _html_block_spans(self, text: str) -> list[_Span]:
        spans: list[_Span] = []
        cursor = 0
        missing_close_tags: set[str] = set()

        while cursor < len(text):
            start_match = HTML_START_TAG_PATTERN.search(text, cursor)
            if start_match is None:
                break

            tag = start_match.group("tag").lower()
            if tag in missing_close_tags:
                cursor = start_match.end()
                continue

            end_match = HTML_END_TAG_PATTERNS[tag].search(text, start_match.end())
            if end_match is None:
                missing_close_tags.add(tag)
                cursor = start_match.end()
                continue

            span_start = start_match.start()
            if tag == "html":
                doctype_match = HTML_DOCTYPE_BEFORE_DOCUMENT_PATTERN.search(
                    text[cursor:span_start]
                )
                if doctype_match is not None:
                    span_start = cursor + doctype_match.start()

            spans.append(_Span(span_start, end_match.end()))
            cursor = end_match.end()

        return spans

    def _prose_segment(self, text: str) -> CompressionSegment | None:
        if not text:
            return None

        normalized = normalize_whitespace(
            text,
            strict_prose=self.strict_prose_whitespace,
        )
        if not normalized.text:
            return None

        return CompressionSegment(
            text=normalized.text,
            compressible=normalized.compressible,
            kind=normalized.kind,
            source_text=text,
        )

    def _json_segment_for_candidate(
        self,
        candidate: str,
        *,
        allow_toon: bool = True,
        leading_context: str = "",
    ) -> CompressionSegment | None:
        if not self._is_medium_large_json(candidate):
            return None

        parsed = self._parse_json(candidate)
        if parsed is None:
            return None

        if (
            not allow_toon
            or self._context_requires_verbatim_json(leading_context)
            or self._contains_llm_tool_exchange(parsed)
            or self._contains_duplicate_json_keys(candidate)
        ):
            return CompressionSegment(
                text=candidate,
                compressible=False,
                kind="json",
                source_text=candidate,
            )

        try:
            toon = self.toon_encoder(parsed)
        except ToonEncodingError:
            return self._json_fallback_segment(candidate, parsed)

        if not toon.strip():
            return self._json_fallback_segment(candidate, parsed)

        savings = 1.0 - (len(toon) / len(candidate))
        if savings < self.min_toon_savings:
            return self._json_fallback_segment(candidate, parsed)

        return CompressionSegment(
            text=toon,
            compressible=False,
            kind="toon",
            source_text=candidate,
        )

    def _json_fallback_segment(
        self,
        candidate: str,
        parsed: Any,
    ) -> CompressionSegment:
        if not self.enable_json_minify:
            return CompressionSegment(
                text=candidate,
                compressible=False,
                kind="json",
                source_text=candidate,
            )

        minified = json.dumps(parsed, separators=(",", ":"), ensure_ascii=False)
        if not minified.strip():
            return CompressionSegment(
                text=candidate,
                compressible=False,
                kind="json",
                source_text=candidate,
            )

        savings = 1.0 - (len(minified) / len(candidate))
        if savings < self.min_json_minify_savings:
            return CompressionSegment(
                text=candidate,
                compressible=False,
                kind="json",
                source_text=candidate,
            )

        return CompressionSegment(
            text=minified,
            compressible=False,
            kind="json_minified",
            source_text=candidate,
        )

    def _contains_llm_tool_exchange(self, value: Any) -> bool:
        if isinstance(value, list):
            return any(self._contains_llm_tool_exchange(item) for item in value)

        if not isinstance(value, dict):
            return False

        role = value.get("role")
        if isinstance(role, str) and role.lower() in {"tool", "function"}:
            return True

        tool_markers = {
            "functionCall",
            "functionResponse",
            "function_call",
            "tool_call",
            "tool_call_id",
            "tool_calls",
            "tool_result",
            "tool_use",
            "tool_use_id",
        }
        if any(marker in value for marker in tool_markers):
            return True

        tool_types = {
            "function",
            "function_call",
            "function_call_output",
            "tool_call",
            "tool_result",
            "tool_use",
        }
        if value.get("type") in tool_types:
            return True

        return any(self._contains_llm_tool_exchange(item) for item in value.values())

    def _contains_duplicate_json_keys(self, candidate: str) -> bool:
        duplicate_found = False

        def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            nonlocal duplicate_found
            seen: set[str] = set()
            for key, _value in pairs:
                if key in seen:
                    duplicate_found = True
                seen.add(key)
            return dict(pairs)

        try:
            json.loads(candidate, object_pairs_hook=reject_duplicates)
        except json.JSONDecodeError:
            return False

        return duplicate_found

    def _context_requires_verbatim_json(self, leading_context: str) -> bool:
        context = leading_context[-300:].lower()
        if not context:
            return False

        explicit_json_terms = (
            "exact json",
            "exactly this json",
            "valid json",
            "json syntax",
            "json schema",
            "return json",
            "respond with json",
            "output json",
            "must be json",
            "json template",
            "template json",
            "json fixture",
            "fixture json",
            "json example",
            "example json",
        )
        if any(term in context for term in explicit_json_terms):
            return True

        if re.search(r"\b(?:schema|template|fixture)\s*:", context):
            return True

        exactness_terms = (
            "verbatim",
            "unchanged",
            "preserve",
            "do not change",
            "don't change",
            "return exactly",
        )
        json_target_terms = ("json", "schema", "template", "fixture")
        return any(term in context for term in exactness_terms) and any(
            term in context for term in json_target_terms
        )

    def _is_medium_large_json(self, candidate: str) -> bool:
        if len(candidate) >= self.min_json_chars:
            return True
        return candidate.count("\n") + 1 >= self.min_json_lines

    def _parse_json(self, candidate: str) -> Any | None:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            return None

    def _find_json_start(self, text: str, start: int) -> int | None:
        match = JSON_START_PATTERN.search(text, start)
        if match is None:
            return None
        return match.start()

    def _find_balanced_json_end(self, text: str, start: int) -> int | None:
        opening = text[start]
        closing = "}" if opening == "{" else "]"
        stack = [closing]
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
            elif char in "{[":
                stack.append("}" if char == "{" else "]")
            elif stack and char == stack[-1]:
                stack.pop()
                if not stack:
                    return index + 1
            elif char == closing and not stack:
                return index + 1

        return None
