from tests.pipeline_helpers import RecordingCompressor, build_service_with_pipeline


def test_nocompress_tags_strip_tags_and_skip_model():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    protected = '{"exact": "do not touch", "enabled": true}'
    text = f"Please review before. <nocompress>{protected}</nocompress> Please review after."

    result = service.compress(text, aggressiveness=0.25)

    assert protected in result.compressed_text
    assert "<nocompress>" not in result.compressed_text
    assert "</nocompress>" not in result.compressed_text
    assert compressor.inputs == [
        "Please review before. __CK_KEEP_0000__ Please review after."
    ]
    assert protected not in compressor.inputs[0]
    assert compressor.force_tokens_values[0][0] == "__CK_KEEP_0000__"
    assert compressor.return_word_label_values == [True]


def test_html_preservation_skips_model():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    html = """<pre>  keep

   exact </pre>"""

    result = service.compress(html, aggressiveness=0.25)

    assert compressor.inputs == []
    assert result.compressed_text == html
    assert result.output_sections[0].kind == "html"
    assert result.output_sections[0].compressed is False
    assert result.output_sections[0].protected is True


def test_protected_segments_do_not_duplicate_full_text_as_labels():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    html = "<pre>" + ("x" * 1000) + "</pre>"

    result = service.compress(html, aggressiveness=0.25)

    assert result.compressed_text == html
    assert result.labeled_tokens == []
    assert result.output_sections[0].text == html
    assert result.output_sections[0].labeled_tokens == []


def test_common_html_content_tags_remain_compressible_prose():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    html = "<div><p>Please review this paragraph.</p></div>"

    result = service.compress(html, aggressiveness=0.25)

    assert compressor.inputs == [html]
    assert [section.kind for section in result.output_sections] == ["prose"]
    assert result.output_sections[0].protected is False


def test_html_blocks_are_split_from_surrounding_prose():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = """<html>
  <a>a</a>   <b>b</b>
</html>

Do not remove API keys, URLs, dates, or hard constraints.

<html>
  <a>a</a>   <b>b</b>
</html>

and html?"""

    result = service.compress(text, aggressiveness=0.25)

    assert [section.kind for section in result.output_sections].count("html") == 2
    assert any(
        section.kind == "protected" and section.text == "Do not remove API"
        for section in result.output_sections
    )
    assert compressor.inputs == [
        "__CK_KEEP_0000__\n\n"
        "__CK_KEEP_0001__ keys, URLs, dates, or hard constraints.\n\n"
        "__CK_KEEP_0002__\n\n"
        "and html?"
    ]
    assert compressor.force_tokens_values[0][:3] == [
        "__CK_KEEP_0000__",
        "__CK_KEEP_0001__",
        "__CK_KEEP_0002__",
    ]
    assert "  <a>a</a>   <b>b</b>" in result.compressed_text
    assert "</html>\n\nDo not remove" in result.compressed_text
    assert "constraints.\n\n<html>" in result.compressed_text
    assert "</html>\n\nand html?" in result.compressed_text


def test_markdown_code_fence_is_protected_from_model():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    code = """```python
def render():
    return "<div>  keep spacing  </div>"
```"""
    text = f"Please review this implementation:\n{code}\nPlease review behavior."

    result = service.compress(text, aggressiveness=0.25)

    assert code in result.compressed_text
    assert [section.kind for section in result.output_sections] == [
        "prose",
        "code",
        "prose",
    ]
    assert compressor.inputs == [
        "Please review this implementation:\n"
        "__CK_KEEP_0000__"
        "Please review behavior."
    ]
    assert code not in compressor.inputs[0]
    assert compressor.force_tokens_values[0][0] == "__CK_KEEP_0000__"


def test_html_fence_is_not_split_by_inner_html_tags():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    code = """```html
<div>
  <p>Keep   spacing</p>
</div>
```"""
    text = f"Please review this markup:\n{code}\nPlease review after."

    result = service.compress(text, aggressiveness=0.25)

    assert code in result.compressed_text
    assert [section.kind for section in result.output_sections] == [
        "prose",
        "code",
        "prose",
    ]
    assert compressor.inputs == [
        "Please review this markup:\n__CK_KEEP_0000__Please review after."
    ]
    assert code not in compressor.inputs[0]
    assert compressor.force_tokens_values[0][0] == "__CK_KEEP_0000__"


def test_script_blocks_are_preserved_verbatim():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    script = """<script>
const defaults = {enabled: true, label: "Keep   spaces"};
</script>"""
    text = f"Please review this page:\n{script}\nPlease review after."

    result = service.compress(text, aggressiveness=0.25)

    assert script in result.compressed_text
    assert [section.kind for section in result.output_sections] == [
        "prose",
        "html",
        "prose",
    ]
    assert compressor.inputs == [
        "Please review this page:\n__CK_KEEP_0000__\nPlease review after."
    ]
    assert script not in compressor.inputs[0]
    assert compressor.force_tokens_values[0][0] == "__CK_KEEP_0000__"


def test_documented_tag_examples_remain_compressible_prose():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = (
        "Use `<blockquote>` only when quoting the user.\n\n"
        "Render <SwitchAgent> as an option, not as literal HTML."
    )

    result = service.compress(text, aggressiveness=0.25)

    assert "<SwitchAgent>" in compressor.inputs[0]
    assert "`<blockquote>`" not in compressor.inputs[0]
    assert "html" not in [section.kind for section in result.output_sections]
    assert "code" not in [section.kind for section in result.output_sections]
    assert any(section.kind == "protected" for section in result.output_sections)


def test_ui_rendering_contract_sections_are_verbatim():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    contract = """# UI RENDERING CONTRACT (RENDER ONLY)

Output structure:

- Output plain text for the transcript.
- If present, the UI block MUST appear after the transcript text.

UI block wrapper (exact tokens):
[UI]
{ "schemaVersion": 1, "components": [ ... ] }
[/UI]

# CONTENT FORMATS

Card descriptions render through the Quill editor. All generated descriptions must be valid Quill-compatible HTML.

RIGHT:
```
<p>First point.</p><p>Second point.</p>
```

# FAILURE MODES

1. Card data is missing or empty: ask one clarifying question before proceeding.

---
"""
    text = f"Please review before.\n\n{contract}\nPlease review after."

    result = service.compress(text, aggressiveness=0.25)

    assert contract in result.compressed_text
    assert "[UI]" in result.compressed_text
    assert "[ UI ]" not in result.compressed_text
    assert "<p>First point.</p><p>Second point.</p>" in result.compressed_text
    assert [section.kind for section in result.output_sections] == [
        "prose",
        "verbatim",
        "prose",
    ]
    assert compressor.inputs == [
        "Please review before.\n\n__CK_KEEP_0000__Please review after."
    ]
    assert contract not in compressor.inputs[0]
    assert compressor.force_tokens_values[0][0] == "__CK_KEEP_0000__"


def test_labeled_valid_json_uses_generic_json_pipeline():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = """Please review before.

Payload: {
  "users": [
    {"id": 1, "name": "Alice", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"},
    {"id": 3, "name": "Cora", "role": "user"}
  ]
}

Please review after."""

    result = service.compress(text, aggressiveness=0.25)

    assert "Payload:" in result.compressed_text
    assert "users[3]{id,name,role}" in result.compressed_text
    assert [section.kind for section in result.output_sections] == [
        "prose",
        "toon",
        "prose",
    ]
    assert compressor.inputs == [
        "Please review before.\n\nPayload: __CK_KEEP_0000__\n\n"
        "Please review after."
    ]
    assert "users[3]{id,name,role}" not in compressor.inputs[0]
    assert compressor.force_tokens_values[0][0] == "__CK_KEEP_0000__"


def test_labeled_invalid_json_is_not_repaired_or_toonified():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = """Please review before.

Payload: {"_id":"card_123","description":"Line 1\\
Line 2","checklists":[]}

Please review after."""

    result = service.compress(text, aggressiveness=0.25)

    assert "users[3]{id,name,role}" not in result.compressed_text
    assert {section.kind for section in result.output_sections} == {
        "prose",
        "protected",
    }
    assert "Line __CK_KEEP_0000__" in compressor.inputs[0]
    assert "Line __CK_KEEP_0001__" in compressor.inputs[0]
