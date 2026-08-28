import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class ProtectedSpan:
    start: int
    end: int
    text: str
    kind: str

STRUCTURE_TOKENS = [
    "\n",
    ".",
    ",",
    ":",
    ";",
    "!",
    "?",
    "-",
    "_",
    "/",
    "\\",
    "`",
    "'",
    '"',
    "(",
    ")",
    "[",
    "]",
    "{",
    "}",
    "<",
    ">",
]

CRITICAL_WORDS = [
    "no",
    "not",
    "never",
    "without",
    "unless",
    "except",
    "only",
    "must",
    "required",
    "shall",
    "should",
    "cannot",
    "can't",
    "don't",
    "do",
    "delete",
    "remove",
]

PROTECTED_PATTERN_SPECS = [
    (
        "code_fence",
        re.compile(
            r"(?P<fence>`{3,}|~{3,})[^\n]*\n.*?(?:\n(?P=fence)[ \t]*(?:\n|$)|$)",
            re.DOTALL,
        ),
    ),
    # Markdown and templating syntax are executable/structural data, not prose
    # for a language model to rewrite. Tenant-specific output formats should
    # use <nocompress> rather than expanding this global list.
    (
        "markdown_link",
        re.compile(r"!?\[[^\]\r\n]+\]\([^\s)\r\n]+\)"),
    ),
    ("citation", re.compile(r"\[citation:[^\]\r\n]+\]", re.IGNORECASE)),
    (
        "template",
        re.compile(
            r"\{\{[-~]?\s*[^{}\r\n]+?\s*[-~]?\}\}"
            r"|\{%[-~]?\s*[^%\r\n]+?\s*[-~]?%\}"
            r"|\$\{[^}\r\n]+\}"
            r"|\{[A-Za-z_][A-Za-z0-9_.-]*\}"
        ),
    ),
    # Stop before HTML/template delimiters. ``\S+`` greedily included markup
    # following href values, producing false integrity failures when prose was
    # compressed while the URL itself remained intact.
    (
        "url",
        re.compile(
            r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s<>\"']+",
            re.IGNORECASE,
        ),
    ),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    (
        "uuid",
        re.compile(
            r"(?<![0-9A-Fa-f])(?:"
            r"\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}"
            r"|[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}"
            r")(?![0-9A-Fa-f])"
        ),
    ),
    (
        "timestamp",
        re.compile(
            r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
            r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})\b",
            re.IGNORECASE,
        ),
    ),
    ("money", re.compile(r"\$\s?\d+(?:[.,]\d+)*")),
    (
        "constraint",
        re.compile(
            r"\b(?:do\s+not|don't|never|must\s+not|cannot|can't)\s+"
            r"(?:delete|remove|alter|change|increase|decrease|modify|recommend)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "identifier",
        re.compile(
            r"\b(?:"
            r"[A-Z][A-Z0-9_]*(?:[-_][A-Z0-9]+)+"
            r"|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"
            r")\b"
        ),
    ),
    (
        "number",
        re.compile(
            r"\b\d+(?:[.,:/-]\d+)*(?:%|ms|s|sec|seconds|m|min|hours|gb|mb|kb|usd|dollars)?\b",
            re.IGNORECASE,
        ),
    ),
    ("constant", re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b")),
]
PROTECTED_PATTERNS = [pattern for _, pattern in PROTECTED_PATTERN_SPECS]
MACHINE_CRITICAL_SPAN_KINDS = frozenset(
    {
        "code_fence",
        "markdown_link",
        "citation",
        "template",
        "url",
        "email",
        "uuid",
        "timestamp",
        "inline_code",
        "identifier",
    }
)
PARSER_BOUNDARY_SPAN_KINDS = frozenset(
    {
        "markdown_link",
        "citation",
        "template",
        "inline_code",
    }
)

CLAUSE_PATTERN = re.compile(r"(?m)(?:^|(?<=[.!?]))[^\n.!?]*[^\s\n.!?][.!?]?")
CLAUSE_ACTION_PATTERN = re.compile(
    r"\b(?:add|alter|approve|change|create|delete|deliver|emit|exceed|include|"
    r"imply|increase|keep|modify|output|preserve|provide|raise|receive|remove|respond|"
    r"return|send|set|use|write)(?:s|d|ing)?\b",
    re.IGNORECASE,
)
CLAUSE_POLICY_PATTERN = re.compile(
    r"\b(?:do\s+not|don't|never|without|must(?:\s+not)?|shall(?:\s+not)?|"
    r"should(?:\s+not)?|may(?:\s+not)?|can(?:not|'t)?|required|permitted|"
    r"only\s+if|unless|except|scope|within)\b",
    re.IGNORECASE,
)
CLAUSE_FORMAT_PATTERN = re.compile(
    r"\b(?:required|exact|specified)\s+(?:output\s+)?(?:format|ordering|order|schema)\b",
    re.IGNORECASE,
)
CLAUSE_GOVERNING_VALUE_PATTERN = re.compile(
    r"\b(?:at|above|below|under|over|equals?|exceeds?|within|before|after)\b.*"
    r"(?:\d|https?://|\b[A-Z][A-Z0-9_]*(?:[-_][A-Z0-9]+)+\b)",
    re.IGNORECASE,
)
CLAUSE_SEPARATED_TERMINATOR_PATTERN = re.compile(r"[ \t]+[.!?]+")


def protected_spans_for_text(
    text: str,
    *,
    allowed_kinds: frozenset[str] | None = None,
) -> list[ProtectedSpan]:
    candidates: list[ProtectedSpan] = []
    for kind, pattern in PROTECTED_PATTERN_SPECS:
        if allowed_kinds is not None and kind not in allowed_kinds:
            continue
        for match in pattern.finditer(text):
            value = match.group(0)
            if value.strip():
                candidates.append(
                    ProtectedSpan(
                        start=match.start(),
                        end=match.end(),
                        text=value,
                        kind=kind,
                    )
                )

    if allowed_kinds is None or "inline_code" in allowed_kinds:
        candidates.extend(inline_code_spans(text))

    if not candidates:
        return []

    spans: list[ProtectedSpan] = []
    for candidate in sorted(
        candidates,
        key=lambda span: (span.start, -(span.end - span.start), span.end),
    ):
        if spans and candidate.start < spans[-1].end:
            continue
        spans.append(candidate)
    return spans


def inline_code_spans(text: str) -> list[ProtectedSpan]:
    """Return Markdown code spans using matching backtick-run delimiters."""

    runs: list[tuple[int, int]] = []
    cursor = 0
    while cursor < len(text):
        if text[cursor] != "`":
            cursor += 1
            continue
        end = cursor + 1
        while end < len(text) and text[end] == "`":
            end += 1
        runs.append((cursor, end))
        cursor = end

    run_indices_by_length: dict[int, list[int]] = defaultdict(list)
    for index, (start, end) in enumerate(runs):
        run_indices_by_length[end - start].append(index)

    spans: list[ProtectedSpan] = []
    run_index = 0
    while run_index < len(runs):
        opener_start, opener_end = runs[run_index]
        preceding_backslashes = 0
        backslash_cursor = opener_start - 1
        while backslash_cursor >= 0 and text[backslash_cursor] == "\\":
            preceding_backslashes += 1
            backslash_cursor -= 1

        if preceding_backslashes % 2:
            run_index += 1
            continue

        delimiter_length = opener_end - opener_start
        matching_indices = run_indices_by_length.get(delimiter_length, [])
        match_position = bisect_right(matching_indices, run_index)
        if match_position >= len(matching_indices):
            run_index += 1
            continue

        closer_index = matching_indices[match_position]
        _, closer_end = runs[closer_index]
        spans.append(
            ProtectedSpan(
                start=opener_start,
                end=closer_end,
                text=text[opener_start:closer_end],
                kind="inline_code",
            )
        )
        run_index = closer_index + 1

    return spans


def critical_clause_spans(text: str) -> list[ProtectedSpan]:
    """Return complete safety-sensitive clauses for model-stage shielding."""

    spans: list[ProtectedSpan] = []
    for match in CLAUSE_PATTERN.finditer(text):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip())
        clause = raw.strip()
        if not clause:
            continue
        start = match.start() + leading
        end = start + len(clause)
        has_action = CLAUSE_ACTION_PATTERN.search(clause) is not None
        sensitive = (
            (has_action and CLAUSE_POLICY_PATTERN.search(clause) is not None)
            or CLAUSE_FORMAT_PATTERN.search(clause) is not None
            or (has_action and CLAUSE_GOVERNING_VALUE_PATTERN.search(clause) is not None)
        )
        if sensitive:
            separated_terminator = CLAUSE_SEPARATED_TERMINATOR_PATTERN.match(
                text,
                end,
            )
            if separated_terminator is not None:
                end = separated_terminator.end()
                clause = text[start:end]
            spans.append(
                ProtectedSpan(
                    start=start,
                    end=end,
                    text=clause,
                    kind="critical_clause",
                )
            )
    return spans


def force_tokens_for_text(text: str, max_tokens: int = 100) -> list[str]:
    """Return tokens LLMLingua should strongly prefer to keep.

    This is intentionally simple for the MVP. Later, replace this with true
    span-level preservation during reconstruction.
    """
    tokens: list[str] = []
    seen: set[str] = set()

    def add_token(value: str) -> None:
        if len(tokens) >= max_tokens:
            return
        if value and value not in seen:
            seen.add(value)
            tokens.append(value)

    for token in STRUCTURE_TOKENS:
        add_token(token)

    for token in CRITICAL_WORDS:
        add_token(token)

    for pattern in PROTECTED_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0).strip()
            add_token(value)

    for span in inline_code_spans(text):
        add_token(span.text.strip())

    return tokens
