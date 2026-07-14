from app.compression_pipeline import PromptPreprocessor
from app.token_estimator import TokenEstimate
from app.whitespace_normalizer import normalize_whitespace


def test_plain_text_whitespace_is_normalized_conservatively():
    text = "First line.\t\n\n\n\nSecond   line.   "

    result = normalize_whitespace(text)

    assert result.text == "First line.\n\n\nSecond   line.  "
    assert result.kind == "prose"
    assert result.compressible is True


def test_strict_prose_whitespace_collapses_interior_spaces():
    text = "First   line has     copied spacing.\nSecond\t\tline has tabs."

    result = normalize_whitespace(text, strict_prose=True)

    assert result.text == "First line has copied spacing.\nSecond line has tabs."


def test_strict_prose_whitespace_preserves_markdown_boundaries():
    text = (
        "- item   keeps spacing\n"
        "1. ordered   keeps spacing\n"
        "> quote   keeps spacing\n"
        "| Name   | Value   |\n"
        "| --- | --- |\n"
        "key:   value\n"
        "Name     Value     Notes\n"
        "Paragraph   collapses.  "
    )

    result = normalize_whitespace(text, strict_prose=True)

    assert "- item   keeps spacing" in result.text
    assert "1. ordered   keeps spacing" in result.text
    assert "> quote   keeps spacing" in result.text
    assert "| Name   | Value   |" in result.text
    assert "key:   value" in result.text
    assert "Name     Value     Notes" in result.text
    assert result.text.endswith("Paragraph collapses.  ")


def test_strict_prose_whitespace_preserves_fenced_code_and_hard_breaks():
    fenced = "```python\nvalue   =   1\n```\n"
    text = f"Before   prose.  \n{fenced}After   prose."

    result = normalize_whitespace(text, strict_prose=True)

    assert "Before prose.  \n" in result.text
    assert "value   =   1" in result.text
    assert result.text.endswith("After prose.")


def test_plain_text_preserves_boundary_separator():
    result = normalize_whitespace("Before protected span.   ")

    assert result.text == "Before protected span.  "


def test_markdown_fenced_code_whitespace_is_preserved():
    fenced = "```python\nvalue = 1   \n\nprint(value)\n```\n"
    text = f"Please review:\n\n\n{fenced}\n\n\nPlease review after."

    result = normalize_whitespace(text)

    assert result.text == f"Please review:\n\n{fenced}\n\nPlease review after."
    assert "value = 1   \n\nprint(value)" in result.text
    assert result.kind == "prose"
    assert result.compressible is True


def test_html_whitespace_is_preserved_verbatim_and_protected():
    html = """<pre>  keep

   exact </pre>"""

    result = normalize_whitespace(html)

    assert result.text == html
    assert result.kind == "html"
    assert result.compressible is False


def test_html_inline_element_spacing_is_preserved_verbatim():
    html = "<html>   <a>a</a>   <b>b</b>\n</html>"
    result = normalize_whitespace(html)

    assert result.text == html


def test_html_leading_newline_boundary_is_preserved_verbatim():
    html = "\n\n<html>   <a>a</a>   <b>b</b>\n</html>"
    result = normalize_whitespace(html)

    assert result.text == html


def test_documented_tags_in_prompt_are_not_treated_as_html():
    text = (
        "Use `<blockquote>` only when quoting the user.\n\n"
        "Render <SwitchAgent> as an option, not as literal HTML."
    )

    result = normalize_whitespace(text)

    assert result.kind == "prose"
    assert result.compressible is True
    assert result.text == text


def test_embedded_html_example_does_not_protect_surrounding_prompt():
    text = "The prompt may mention <div>example</div> without becoming an HTML document."

    result = normalize_whitespace(text)

    assert result.kind == "prose"
    assert result.compressible is True


def test_common_html_content_tags_are_not_protected():
    result = normalize_whitespace("<div><p>Please review.</p></div>")

    assert result.kind == "prose"
    assert result.compressible is True


def test_strict_prose_whitespace_requires_tokenizer_positive_savings():
    text = "Summary   has     copied spacing."

    positive = PromptPreprocessor(
        strict_prose_whitespace=True,
        token_estimator=lambda value: TokenEstimate(
            count=10 if value == text else 7,
            estimator="test:stub",
            tokenizer_backed=True,
        ),
        require_tokenizer_backed_gates=True,
        min_whitespace_savings_tokens=2,
        min_whitespace_reduction=0.005,
    ).prepare(text)
    no_savings = PromptPreprocessor(
        strict_prose_whitespace=True,
        token_estimator=lambda _value: TokenEstimate(
            count=10,
            estimator="test:stub",
            tokenizer_backed=True,
        ),
        require_tokenizer_backed_gates=True,
        min_whitespace_savings_tokens=2,
        min_whitespace_reduction=0.005,
    ).prepare(text)

    assert "".join(segment.text for segment in positive) == "Summary has copied spacing."
    assert "".join(segment.text for segment in no_savings) == text


def test_strict_whitespace_preserves_critical_clause_bytes():
    clause = "Keep retry_limit   at 3 unless legal approves a written amendment."
    text = f"Summary   has copied spacing. {clause}"
    preprocessor = PromptPreprocessor(
        strict_prose_whitespace=True,
        token_estimator=lambda value: TokenEstimate(
            count=len(value),
            estimator="test:characters",
            tokenizer_backed=True,
        ),
        require_tokenizer_backed_gates=True,
        min_whitespace_savings_tokens=2,
        min_whitespace_reduction=0.005,
    )

    output = "".join(segment.text for segment in preprocessor.prepare(text))

    assert output.startswith("Summary has copied spacing.")
    assert clause in output
