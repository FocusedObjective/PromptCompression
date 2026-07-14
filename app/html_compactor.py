import re
from html import unescape
from html.parser import HTMLParser


HTML_DOCUMENT_PATTERN = re.compile(
    r"(?is)^\s*(?:<!doctype\s+html\b[^>]*>\s*)?<html\b[^>]*>.*</html>\s*$"
)
HTML_EXACTNESS_CONTEXT_PATTERN = re.compile(
    r"(?i)\b(audit|debug|exact|review|source|verbatim)\b"
)
HTML_TARGET_CONTEXT_PATTERN = re.compile(
    r"(?i)\b(accessibility|css|dom|html|markup|render|selector)\b"
)
HTML_PRESERVE_TARGET_CONTEXT_PATTERN = re.compile(
    r"(?i)\bpreserve(?:\s+\w+){0,3}\s+"
    r"(?:accessibility|css|dom|html|markup|render|selector)\b"
)


def looks_like_html_document(text: str) -> bool:
    return bool(HTML_DOCUMENT_PATTERN.match(text))


def compact_html_to_markdown(html: str) -> str | None:
    extracted = _trafilatura_markdown(html)
    if extracted is None:
        extracted = fallback_html_to_markdown(html)

    normalized = _normalize_markdown(extracted)
    return normalized or None


def html_to_markdown_is_equivalent(html: str, markdown: str) -> bool:
    """Conservative preservation proof for expanded HTML experiments."""

    parser = _HtmlPreservationParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return False
    if not parser.has_content_region or parser.rejected:
        return False
    cursor = 0
    normalized_markdown = re.sub(r"\s+", " ", markdown).strip()
    for value in parser.ordered_text:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized:
            continue
        index = normalized_markdown.find(normalized, cursor)
        if index < 0:
            return False
        cursor = index + len(normalized)
    for label, destination in parser.links:
        if f"[{label}]({destination})" not in markdown:
            return False
    return True


def should_preserve_html_verbatim(
    html: str,
    *,
    leading_context: str = "",
) -> bool:
    if not looks_like_html_document(html):
        return True
    context = leading_context[-300:]
    for line in context.splitlines():
        if (
            HTML_EXACTNESS_CONTEXT_PATTERN.search(line)
            and HTML_TARGET_CONTEXT_PATTERN.search(line)
        ):
            return True
        if HTML_PRESERVE_TARGET_CONTEXT_PATTERN.search(line):
            return True
    return False


def _trafilatura_markdown(html: str) -> str | None:
    try:
        from trafilatura import extract
    except ImportError:
        return None

    result = extract(
        html,
        output_format="markdown",
        include_comments=False,
        include_formatting=True,
        include_links=True,
        include_tables=True,
    )
    if not isinstance(result, str):
        return None
    return result


class _FallbackMarkdownParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.table_cell_open = False

    def handle_starttag(
        self,
        tag: str,
        _attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()

        if tag in {"script", "style", "template", "svg", "noscript"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return

        if tag == "title":
            self._break(2)
        elif tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._break(2)
            self.parts.append(f"{'#' * int(tag[1])} ")
        elif tag in {"p", "section", "article", "main", "header", "footer", "nav"}:
            self._break(2)
        elif tag == "br":
            self._break(1)
        elif tag == "li":
            self._break(1)
            self.parts.append("- ")
        elif tag == "blockquote":
            self._break(2)
            self.parts.append("> ")
        elif tag == "tr":
            self._break(1)
            self.parts.append("| ")
        elif tag in {"td", "th"}:
            if self.table_cell_open:
                self.parts.append(" | ")
            self.table_cell_open = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "template", "svg", "noscript"}:
            self.skip_depth = max(0, self.skip_depth - 1)

        if self.skip_depth:
            return

        if tag in {"title", "h1", "h2", "h3", "h4", "h5", "h6", "p"}:
            self._break(2)
        elif tag in {"li", "blockquote"}:
            self._break(1)
        elif tag == "tr":
            self.parts.append(" |")
            self.table_cell_open = False
            self._break(1)

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return

        text = re.sub(r"\s+", " ", unescape(data)).strip()
        if not text:
            return
        if self.parts and self.parts[-1] and not self.parts[-1].endswith(
            (" ", "\n", "> ", "- ", "| ")
        ):
            self.parts.append(" ")
        self.parts.append(text)

    def _break(self, count: int) -> None:
        current = "".join(self.parts)
        trailing = len(current) - len(current.rstrip("\n"))
        needed = max(0, count - trailing)
        if needed:
            self.parts.append("\n" * needed)

    def markdown(self) -> str:
        return "".join(self.parts)


class _HtmlPreservationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.content_depth = 0
        self.has_content_region = False
        self.rejected = False
        self.ordered_text: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._active_link: tuple[str, list[str]] | None = None

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        tag = tag.lower()
        if tag in {"form", "input", "button", "select", "textarea", "script", "template", "svg", "pre", "code"} or "-" in tag:
            self.rejected = True
        if tag in {"main", "article"}:
            self.content_depth += 1
            self.has_content_region = True
        if self.content_depth and tag == "a":
            destination = dict(attrs).get("href")
            if destination is None:
                self.rejected = True
            else:
                self._active_link = (destination, [])

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._active_link is not None:
            destination, parts = self._active_link
            label = re.sub(r"\s+", " ", "".join(parts)).strip()
            if label:
                self.links.append((label, destination))
            self._active_link = None
        if tag in {"main", "article"} and self.content_depth:
            self.content_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.content_depth:
            return
        value = re.sub(r"\s+", " ", unescape(data)).strip()
        if not value:
            return
        self.ordered_text.append(value)
        if self._active_link is not None:
            self._active_link[1].append(value)


def fallback_html_to_markdown(html: str) -> str:
    parser = _FallbackMarkdownParser()
    parser.feed(html)
    parser.close()
    return parser.markdown()


def _normalize_markdown(text: str | None) -> str:
    if not text:
        return ""

    lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
    normalized: list[str] = []
    blank_run = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank_run += 1
            if blank_run <= 1 and normalized:
                normalized.append("")
            continue
        blank_run = 0
        normalized.append(stripped)

    return "\n".join(normalized).strip()
