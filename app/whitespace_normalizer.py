import re
from dataclasses import dataclass


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


def normalize_whitespace(text: str) -> WhitespaceNormalization:
    if "<" not in text:
        return WhitespaceNormalization(
            text=_normalize_markdown_safe_whitespace(text),
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
        text=_normalize_markdown_safe_whitespace(text),
        kind="prose",
        compressible=True,
    )


def _normalize_markdown_safe_whitespace(text: str) -> str:
    if "```" not in text and "~~~" not in text:
        return _normalize_plain_lines(text)

    parts: list[str] = []
    cursor = 0

    for match in FENCE_PATTERN.finditer(text):
        parts.append(_normalize_plain_lines(text[cursor : match.start()]))
        parts.append(match.group(0))
        cursor = match.end()

    parts.append(_normalize_plain_lines(text[cursor:]))
    return "".join(parts)


def _normalize_plain_lines(text: str) -> str:
    if not text:
        return text

    preserve_trailing_separator = text[-1] in " \t"
    lines = text.split("\n")
    normalized_lines = [_trim_line(line) for line in lines]

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
