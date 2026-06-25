from app.whitespace_normalizer import normalize_whitespace


def test_plain_text_whitespace_is_normalized_conservatively():
    text = "First line.\t\n\n\n\nSecond   line.   "

    result = normalize_whitespace(text)

    assert result.text == "First line.\n\n\nSecond   line.  "
    assert result.kind == "prose"
    assert result.compressible is True


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
