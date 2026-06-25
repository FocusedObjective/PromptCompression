from app.protected_spans import force_tokens_for_text


def test_force_tokens_include_structure_and_negation():
    tokens = force_tokens_for_text("Do not delete this.")
    assert "." in tokens
    assert "not" in tokens


def test_force_tokens_include_urls_and_numbers():
    tokens = force_tokens_for_text("Visit https://example.com and pay $15 by 2026-06-23.")
    assert "https://example.com" in tokens
    assert "$15" in tokens
    assert "2026-06-23" in tokens


def test_force_tokens_are_capped_for_large_inputs():
    text = " ".join(f"https://example.com/{index} {index}" for index in range(200))

    tokens = force_tokens_for_text(text, max_tokens=100)

    assert len(tokens) == 100
    assert "not" in tokens
    assert "https://example.com/0" in tokens
