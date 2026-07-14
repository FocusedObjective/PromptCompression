from app.json_regions import JsonRegionDetector


def test_small_strict_json_is_detected_independently_of_transform_size():
    regions = JsonRegionDetector().detect('prefix {"ok":true} suffix')

    assert [(region.syntax_class, region.parsed_value) for region in regions] == [
        ("strict_json_object", {"ok": True})
    ]


def test_raw_decode_handles_braces_and_escaped_quotes_inside_strings():
    text = 'before {"value":"brace } and \\\"quote\\\"","items":[1,2]} after'
    region = JsonRegionDetector().detect(text)[0]

    assert text[region.start:region.end] == '{"value":"brace } and \\\"quote\\\"","items":[1,2]}'
    assert region.parsed_value == {"value": 'brace } and "quote"', "items": [1, 2]}


def test_templates_markdown_links_and_prose_brackets_are_not_strict_json():
    text = "[label](https://example.com) ${tenant} [plain words]"
    classes = [region.syntax_class for region in JsonRegionDetector().detect(text)]

    assert classes
    assert "strict_json_object" not in classes
    assert "strict_json_array" not in classes


def test_invalid_outer_region_does_not_hide_inner_json_but_blocks_rewrite():
    regions = JsonRegionDetector().detect('[invalid {"ticket":"UT-1042"}]')
    inner = next(region for region in regions if region.parsed_value is not None)

    assert inner.parsed_value == {"ticket": "UT-1042"}
    assert "ambiguous_parent" in inner.context_flags
    assert inner.rewrite_eligible is False


def test_adjacent_and_line_delimited_json_have_distinct_classes():
    detector = JsonRegionDetector()

    adjacent = detector.detect('{"a":1}{"b":2}')
    ndjson = detector.detect('{"a":1}\n{"b":2}')

    assert {region.syntax_class for region in adjacent} == {"concatenated_json"}
    assert {region.syntax_class for region in ndjson} == {"ndjson"}
    assert [region.parsed_value for region in ndjson] == [{"a": 1}, {"b": 2}]


def test_jsonc_javascript_literals_and_duplicate_keys_are_not_rewritable():
    detector = JsonRegionDetector()

    assert detector.detect('{"a":1,}')[0].syntax_class == "jsonc_like"
    assert detector.detect("{'a': 1}")[0].syntax_class == "javascript_object_like"
    duplicate = detector.detect('{"a":1,"a":2}')[0]
    assert duplicate.duplicate_keys == ("a",)
    assert duplicate.rewrite_eligible is False


def test_section_aware_exact_schema_context_blocks_rewrite_beyond_300_chars():
    text = (
        "# Exact output schema\n"
        + ("description text " * 40)
        + '\n{"ticket":"UT-1042"}'
    )
    region = JsonRegionDetector().detect(text)[0]

    assert {"exact_output", "schema"} <= region.context_flags
    assert region.rewrite_eligible is False
