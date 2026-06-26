import re
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
    ("url", re.compile(r"https?://\S+", re.IGNORECASE)),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("inline_code", re.compile(r"`[^`]+`")),
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
        re.compile(r"\b[A-Z][A-Z0-9_]*(?:[-_][A-Z0-9]+)+\b"),
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


def protected_spans_for_text(text: str) -> list[ProtectedSpan]:
    candidates: list[ProtectedSpan] = []
    for kind, pattern in PROTECTED_PATTERN_SPECS:
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

    return tokens
