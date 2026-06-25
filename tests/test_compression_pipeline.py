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
    assert compressor.inputs == ["Please review before. ", " Please review after."]


def test_html_preservation_skips_model():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    html = """<div>
  <p>Please   review</p>
  <!-- remove me -->
  <pre>  keep

   exact </pre>
</div>"""

    result = service.compress(html, aggressiveness=0.25)

    assert compressor.inputs == []
    assert result.compressed_text == html
    assert result.output_sections[0].kind == "html"
    assert result.output_sections[0].compressed is False
    assert result.output_sections[0].protected is True


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

    assert [section.kind for section in result.output_sections] == [
        "html",
        "prose",
        "html",
        "prose",
    ]
    assert compressor.inputs == [
        "\n\nDo not remove API keys, URLs, dates, or hard constraints.\n\n",
        "\n\nand html?",
    ]
    assert "  <a>a</a>   <b>b</b>" in result.compressed_text
    assert "</html>\n\nDo not remove" in result.compressed_text
    assert "constraints.\n\n<html>" in result.compressed_text
    assert "</html>\n\nand html?" in result.compressed_text


def test_documented_tag_examples_remain_compressible_prose():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = (
        "Use `<blockquote>` only when quoting the user.\n\n"
        "Render <SwitchAgent> as an option, not as literal HTML."
    )

    result = service.compress(text, aggressiveness=0.25)

    assert compressor.inputs == [text]
    assert [section.kind for section in result.output_sections] == ["prose"]
    assert result.output_sections[0].protected is False


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
    assert compressor.inputs == ["Please review before.\n\n", "Please review after."]


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
        "Please review before.\n\nPayload: ",
        "\n\nPlease review after.",
    ]


def test_labeled_invalid_json_is_not_repaired_or_toonified():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = """Please review before.

Payload: {"_id":"card_123","description":"Line 1\\
Line 2","checklists":[]}

Please review after."""

    result = service.compress(text, aggressiveness=0.25)

    assert "users[3]{id,name,role}" not in result.compressed_text
    assert [section.kind for section in result.output_sections] == ["prose"]
    assert compressor.inputs == [text]
