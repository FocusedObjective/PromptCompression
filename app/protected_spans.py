import re

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

PROTECTED_PATTERNS = [
    re.compile(r"https?://\S+", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    re.compile(r"\b\d+(?:[.,:/-]\d+)*(?:%|ms|s|sec|seconds|m|min|hours|gb|mb|kb|usd|dollars)?\b", re.IGNORECASE),
    re.compile(r"\$\s?\d+(?:[.,]\d+)*"),
    re.compile(r"`[^`]+`"),
    re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b"),
]


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
