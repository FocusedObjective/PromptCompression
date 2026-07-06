import os
import re
from dataclasses import dataclass

ENABLE_STRICT_PROSE_WHITESPACE = os.getenv(
    "COMPRESSOR_ENABLE_STRICT_PROSE_WHITESPACE",
    "false",
).lower() in {"1", "true", "yes", "on"}


FENCE_PATTERN = re.compile(
    r"(?P<fence>`{3,}|~{3,})[^\n]*\n.*?(?:\n(?P=fence)[ \t]*(?:\n|$)|$)",
    re.DOTALL,
)

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
HTML_FULL_START_PATTERN = re.compile(
    r"\s*<(?P<tag>" + "|".join(HTML_BLOCK_TAGS) + r")\b[^>]*>",
    re.IGNORECASE,
)
HTML_END_TAG_PATTERNS = {
    tag: re.compile(r"</" + tag + r"\s*>", re.IGNORECASE)
    for tag in HTML_BLOCK_TAGS
}
MARKDOWN_TABLE_SEPARATOR_PATTERN = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
ORDERED_LIST_PATTERN = re.compile(r"^\s*\d+[.)]\s+")
UNORDERED_LIST_PATTERN = re.compile(r"^\s*[-+*]\s+")
YAML_LIKE_PATTERN = re.compile(r"^\s*[A-Za-z0-9_.-]+\s*:\s+\S")


@dataclass(frozen=True)
class WhitespaceNormalization:
    text: str
    kind: str
    compressible: bool


def looks_like_html(text: str) -> bool:
    start_match = HTML_FULL_START_PATTERN.match(text)
    if start_match is None:
        return False

    tag = start_match.group("tag").lower()
    return HTML_END_TAG_PATTERNS[tag].search(text, start_match.end()) is not None


def normalize_whitespace(
    text: str,
    *,
    strict_prose: bool = ENABLE_STRICT_PROSE_WHITESPACE,
) -> WhitespaceNormalization:
    if "<" not in text:
        return WhitespaceNormalization(
            text=_normalize_markdown_safe_whitespace(text, strict_prose=strict_prose),
            kind="prose",
            compressible=True,
        )

    if looks_like_html(text):
        return WhitespaceNormalization(
            text=text,
            kind="html",
            compressible=False,
        )

    return WhitespaceNormalization(
        text=_normalize_markdown_safe_whitespace(text, strict_prose=strict_prose),
        kind="prose",
        compressible=True,
    )


def _normalize_markdown_safe_whitespace(text: str, *, strict_prose: bool) -> str:
    if "```" not in text and "~~~" not in text:
        return _normalize_plain_lines(text, strict_prose=strict_prose)

    parts: list[str] = []
    cursor = 0

    for match in FENCE_PATTERN.finditer(text):
        parts.append(
            _normalize_plain_lines(
                text[cursor : match.start()],
                strict_prose=strict_prose,
            )
        )
        parts.append(match.group(0))
        cursor = match.end()

    parts.append(_normalize_plain_lines(text[cursor:], strict_prose=strict_prose))
    return "".join(parts)


def _normalize_plain_lines(text: str, *, strict_prose: bool) -> str:
    if not text:
        return text

    preserve_trailing_separator = text[-1] in " \t"
    lines = text.split("\n")
    normalized_lines = [
        _normalize_line(line, strict_prose=strict_prose)
        for line in lines
    ]

    collapsed: list[str] = []
    blank_run = 0
    for line in normalized_lines:
        if line == "":
            blank_run += 1
            if blank_run <= 2:
                collapsed.append(line)
            continue

        blank_run = 0
        collapsed.append(line)

    normalized = "\n".join(collapsed)
    if preserve_trailing_separator and normalized and not normalized.endswith(" "):
        return f"{normalized} "
    return normalized


def _trim_line(line: str) -> str:
    stripped = line.rstrip(" \t")
    trailing_count = len(line) - len(stripped)
    if trailing_count >= 2:
        return f"{stripped}  "
    return stripped


def _normalize_line(line: str, *, strict_prose: bool) -> str:
    trimmed = _trim_line(line)
    if not strict_prose or not _can_collapse_interior_spaces(trimmed):
        return trimmed

    hard_break = trimmed.endswith("  ")
    body = trimmed[:-2] if hard_break else trimmed
    collapsed = re.sub(r"[ \t]{2,}", " ", body)
    return f"{collapsed}  " if hard_break else collapsed


def _can_collapse_interior_spaces(line: str) -> bool:
    if not line.strip():
        return False
    if line.startswith((" ", "\t")):
        return False
    if "|" in line:
        return False
    if line.startswith(">"):
        return False
    if ORDERED_LIST_PATTERN.match(line) or UNORDERED_LIST_PATTERN.match(line):
        return False
    if MARKDOWN_TABLE_SEPARATOR_PATTERN.match(line):
        return False
    if YAML_LIKE_PATTERN.match(line):
        return False
    if _looks_ascii_aligned(line):
        return False
    return True


def _looks_ascii_aligned(line: str) -> bool:
    return len(re.findall(r"\S {3,}\S", line)) >= 2 and not re.search(r"[.!?]", line)
