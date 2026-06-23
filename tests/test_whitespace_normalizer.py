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


def test_html_whitespace_is_normalized_and_protected_tags_are_preserved():
    html = """<div>
  <p>Please   review</p>
  <!-- remove me -->
  <pre>  keep

   exact </pre>
</div>"""

    result = normalize_whitespace(html)

    assert result.text == (
        "<div> <p>Please review</p>  <pre>  keep\n\n   exact </pre> </div>"
    )
    assert result.kind == "html"
    assert result.compressible is False


def test_html_inline_element_spacing_is_preserved():
    result = normalize_whitespace("<html>   <a>a</a>   <b>b</b>\n</html>")

    assert result.text == "<html> <a>a</a> <b>b</b> </html>"
    assert "<a>a</a><b>b</b>" not in result.text


def test_html_leading_newline_boundary_is_preserved():
    result = normalize_whitespace("\n\n<html>   <a>a</a>   <b>b</b>\n</html>")

    assert result.text == "\n<html> <a>a</a> <b>b</b> </html>"
