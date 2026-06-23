import re
from dataclasses import dataclass
from html.parser import HTMLParser


FENCE_PATTERN = re.compile(
    r"(?P<fence>`{3,}|~{3,})[^\n]*\n.*?(?:\n(?P=fence)[ \t]*(?:\n|$)|$)",
    re.DOTALL,
)

HTML_TAG_PATTERN = re.compile(r"</?[A-Za-z][A-Za-z0-9:-]*(?:\s[^<>]*)?>")
PROTECTED_HTML_TAGS = {"code", "pre", "script", "style", "textarea"}


@dataclass(frozen=True)
class WhitespaceNormalization:
    text: str
    kind: str
    compressible: bool


class _HtmlWhitespaceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []
        self.protected_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self.get_starttag_text() or self._format_starttag(tag, attrs))
        if tag.lower() in PROTECTED_HTML_TAGS:
            self.protected_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self.get_starttag_text() or self._format_starttag(tag, attrs, closed=True))

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        if tag.lower() in PROTECTED_HTML_TAGS and self.protected_depth:
            self.protected_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.protected_depth:
            self.parts.append(data)
            return
        self.parts.append(re.sub(r"\s+", " ", data))

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if data.lstrip().startswith("[if"):
            self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")

    def unknown_decl(self, data: str) -> None:
        self.parts.append(f"<![{data}]>")

    def normalized(self) -> str:
        return "".join(self.parts)

    def _format_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        closed: bool = False,
    ) -> str:
        rendered_attrs = []
        for name, value in attrs:
            if value is None:
                rendered_attrs.append(name)
            else:
                rendered_attrs.append(f'{name}="{value}"')
        suffix = " /" if closed else ""
        if not rendered_attrs:
            return f"<{tag}{suffix}>"
        return f"<{tag} {' '.join(rendered_attrs)}{suffix}>"


def looks_like_html(text: str) -> bool:
    return bool(HTML_TAG_PATTERN.search(text))


def normalize_whitespace(text: str) -> WhitespaceNormalization:
    if looks_like_html(text):
        return WhitespaceNormalization(
            text=_normalize_html_whitespace(text),
            kind="html",
            compressible=False,
        )

    return WhitespaceNormalization(
        text=_normalize_markdown_safe_whitespace(text),
        kind="prose",
        compressible=True,
    )


def _normalize_html_whitespace(text: str) -> str:
    parser = _HtmlWhitespaceParser()
    parser.feed(text)
    parser.close()
    normalized = parser.normalized().strip()
    return f"{_leading_boundary(text)}{normalized}{_trailing_boundary(text)}"


def _leading_boundary(text: str) -> str:
    match = re.match(r"\s+", text)
    if match is None:
        return ""
    return _compact_boundary(match.group(0))


def _trailing_boundary(text: str) -> str:
    match = re.search(r"\s+$", text)
    if match is None:
        return ""
    return _compact_boundary(match.group(0))


def _compact_boundary(boundary: str) -> str:
    if "\n" in boundary or "\r" in boundary:
        return "\n"
    return " "


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
