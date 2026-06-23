import unicodedata


def estimate_token_count(text: str) -> int:
    """Approximate token count using ([\\p{L}\\p{N}]+|[^\\s]) semantics."""
    count = 0
    in_word = False

    for char in text:
        if char.isspace():
            in_word = False
            continue

        if _is_letter_or_number(char):
            if not in_word:
                count += 1
                in_word = True
            continue

        count += 1
        in_word = False

    return count


def _is_letter_or_number(char: str) -> bool:
    return unicodedata.category(char)[0] in {"L", "N"}
