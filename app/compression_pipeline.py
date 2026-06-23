import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.toon_adapter import ToonEncodingError, encode_toon
from app.whitespace_normalizer import normalize_whitespace

MIN_JSON_CHARS = int(os.getenv("COMPRESSOR_MIN_JSON_CHARS", "300"))
MIN_JSON_LINES = int(os.getenv("COMPRESSOR_MIN_JSON_LINES", "4"))
MIN_TOON_SAVINGS = float(os.getenv("COMPRESSOR_MIN_TOON_SAVINGS", "0.08"))

NOCOMPRESS_PATTERN = re.compile(
    r"<nocompress>(?P<body>.*?)</nocompress>",
    re.IGNORECASE | re.DOTALL,
)

JSON_FENCE_PATTERN = re.compile(
    r"(?P<fence>`{3,})(?P<lang>json|JSON)[^\n]*\n(?P<body>.*?)(?P=fence)",
    re.DOTALL,
)

HTML_BLOCK_PATTERN = re.compile(
    r"<(?P<tag>html|body|main|article|section|div|table|ul|ol|pre|code|p)\b[^>]*>"
    r".*?</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class CompressionSegment:
    text: str
    compressible: bool
    kind: str


class PromptPreprocessor:
    def __init__(
        self,
        toon_encoder: Callable[[Any], str] = encode_toon,
        min_json_chars: int = MIN_JSON_CHARS,
        min_json_lines: int = MIN_JSON_LINES,
        min_toon_savings: float = MIN_TOON_SAVINGS,
    ) -> None:
        self.toon_encoder = toon_encoder
        self.min_json_chars = min_json_chars
        self.min_json_lines = min_json_lines
        self.min_toon_savings = min_toon_savings

    def prepare(self, text: str) -> list[CompressionSegment]:
        segments: list[CompressionSegment] = []
        cursor = 0

        for match in NOCOMPRESS_PATTERN.finditer(text):
            segments.extend(self._prepare_compressible_text(text[cursor : match.start()]))
            body = match.group("body")
            if body:
                segments.append(
                    CompressionSegment(text=body, compressible=False, kind="nocompress")
                )
            cursor = match.end()

        segments.extend(self._prepare_compressible_text(text[cursor:]))
        return [segment for segment in segments if segment.text]

    def _prepare_compressible_text(self, text: str) -> list[CompressionSegment]:
        segments: list[CompressionSegment] = []
        cursor = 0

        for match in JSON_FENCE_PATTERN.finditer(text):
            segments.extend(self._prepare_raw_json_text(text[cursor : match.start()]))
            body = match.group("body").rstrip("\n")
            json_segment = self._json_segment_for_candidate(body)
            if json_segment is None:
                segments.append(
                    CompressionSegment(
                        text=match.group(0),
                        compressible=True,
                        kind="prose",
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
                    )
                )
            else:
                segments.append(
                    CompressionSegment(
                        text=match.group(0),
                        compressible=False,
                        kind="json",
                    )
                )
            cursor = match.end()

        segments.extend(self._prepare_raw_json_text(text[cursor:]))
        return segments

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
            json_segment = self._json_segment_for_candidate(candidate)
            if json_segment is None:
                search_cursor = start + 1
                continue

            if start > cursor:
                segments.extend(self._prose_segments(text[cursor:start]))

            segments.append(json_segment)
            cursor = end
            search_cursor = end

        return segments

    def _prose_segments(self, text: str) -> list[CompressionSegment]:
        segments: list[CompressionSegment] = []
        cursor = 0

        for match in HTML_BLOCK_PATTERN.finditer(text):
            prose_segment = self._prose_segment(text[cursor : match.start()])
            if prose_segment is not None:
                segments.append(prose_segment)

            html_segment = self._prose_segment(match.group(0))
            if html_segment is not None:
                segments.append(html_segment)

            cursor = match.end()

        prose_segment = self._prose_segment(text[cursor:])
        if prose_segment is not None:
            segments.append(prose_segment)

        return segments

    def _prose_segment(self, text: str) -> CompressionSegment | None:
        if not text:
            return None

        normalized = normalize_whitespace(text)
        if not normalized.text:
            return None

        return CompressionSegment(
            text=normalized.text,
            compressible=normalized.compressible,
            kind=normalized.kind,
        )

    def _json_segment_for_candidate(self, candidate: str) -> CompressionSegment | None:
        parsed = self._parse_json(candidate)
        if parsed is None or not self._is_medium_large_json(candidate):
            return None

        if self._contains_llm_tool_exchange(parsed):
            return CompressionSegment(text=candidate, compressible=False, kind="json")

        try:
            toon = self.toon_encoder(parsed)
        except ToonEncodingError:
            return CompressionSegment(text=candidate, compressible=False, kind="json")

        if not toon.strip():
            return CompressionSegment(text=candidate, compressible=False, kind="json")

        savings = 1.0 - (len(toon) / len(candidate))
        if savings < self.min_toon_savings:
            return CompressionSegment(text=candidate, compressible=False, kind="json")

        return CompressionSegment(text=toon, compressible=False, kind="toon")

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
        positions = [
            position
            for position in (text.find("{", start), text.find("[", start))
            if position != -1
        ]
        if not positions:
            return None
        return min(positions)

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
