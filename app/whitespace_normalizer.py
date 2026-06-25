import re
from dataclasses import dataclass


FENCE_PATTERN = re.compile(
    r"(?P<fence>`{3,}|~{3,})[^\n]*\n.*?(?:\n(?P=fence)[ \t]*(?:\n|$)|$)",
    re.DOTALL,
)

HTML_BLOCK_TAGS = (
    "html",
    "body",
    "main",
    "article",
    "section",
    "div",
    "table",
    "ul",
    "ol",
    "pre",
    "code",
    "p",
)
HTML_START_PATTERN = re.compile(r"\s*<(?:" + "|".join(HTML_BLOCK_TAGS) + r")\b", re.IGNORECASE)
HTML_BLOCK_PATTERN = re.compile(
    r"<(?P<tag>" + "|".join(HTML_BLOCK_TAGS) + r")\b[^>]*>"
    r".*?</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class WhitespaceNormalization:
    text: str
    kind: str
    compressible: bool


def looks_like_html(text: str) -> bool:
    return bool(HTML_START_PATTERN.match(text) and HTML_BLOCK_PATTERN.search(text))


def normalize_whitespace(text: str) -> WhitespaceNormalization:
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
