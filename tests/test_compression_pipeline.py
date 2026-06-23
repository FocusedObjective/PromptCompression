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
