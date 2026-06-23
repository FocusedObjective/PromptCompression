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


def test_html_whitespace_normalization_skips_model():
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
    assert result.compressed_text == (
        "<div> <p>Please review</p>  <pre>  keep\n\n   exact </pre> </div>"
    )
    assert result.output_sections[0].kind == "html"
    assert result.output_sections[0].compressed is True
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
    assert "<a>a</a> <b>b</b>" in result.compressed_text
    assert "<a>a</a><b>b</b>" not in result.compressed_text
    assert "</html>\n\nDo not remove" in result.compressed_text
    assert "constraints.\n\n<html>" in result.compressed_text
    assert "</html>\n\nand html?" in result.compressed_text
