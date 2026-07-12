import json

from app.structured_json import parse_value_path, transform_tagged_json


def test_parse_value_path_supports_keys_and_array_wildcards():
    assert parse_value_path("$.description") == ("description",)
    assert parse_value_path("$.comments[*].body") == (
        "comments",
        "*",
        "body",
    )
    assert parse_value_path("comments.body") is None
    assert parse_value_path("$.comments[0].body") is None


def test_matching_policy_compresses_only_allowlisted_string_values():
    source = (
        '<compress-json policy="issue-v1">'
        '{"id":"ISSUE-73","description":"Long narrative",'
        '"comments":[{"author":"Ada","body":"Detailed comment"}]}'
        "</compress-json>"
    )

    result = transform_tagged_json(
        source,
        policy_id="issue-v1",
        value_paths=("$.description", "$.comments[*].body"),
        max_values=8,
        compress_value=lambda path, value: f"compressed:{path}:{value}",
    )

    parsed = json.loads(result.text)
    assert parsed["id"] == "ISSUE-73"
    assert parsed["comments"][0]["author"] == "Ada"
    assert parsed["description"] == (
        "compressed:$.description:Long narrative"
    )
    assert parsed["comments"][0]["body"] == (
        "compressed:$.comments[0].body:Detailed comment"
    )
    assert result.compressed_value_count == 2


def test_policy_mismatch_removes_tag_but_does_not_compress_values():
    body = '{"description":"Long narrative"}'
    result = transform_tagged_json(
        f'<compress-json policy="other-v1">{body}</compress-json>',
        policy_id="issue-v1",
        value_paths=("$.description",),
        max_values=8,
        compress_value=lambda _path, _value: "must not run",
    )

    assert result.text == body
    assert result.compressed_value_count == 0
    assert result.warnings == ("json_tag_policy_not_authorized:other-v1",)


def test_invalid_and_duplicate_key_tagged_json_is_protected_verbatim():
    invalid = transform_tagged_json(
        '<compress-json policy="p">{"broken":}</compress-json>',
        policy_id="p",
        value_paths=("$.broken",),
        max_values=8,
        compress_value=lambda _path, value: value,
    )
    duplicate = transform_tagged_json(
        '<compress-json policy="p">{"name":"old","name":"new"}</compress-json>',
        policy_id="p",
        value_paths=("$.name",),
        max_values=8,
        compress_value=lambda _path, value: value,
    )

    assert invalid.text == '<nocompress>{"broken":}</nocompress>'
    assert invalid.warnings == ("json_tag_invalid_json_protected",)
    assert duplicate.text == (
        '<nocompress>{"name":"old","name":"new"}</nocompress>'
    )
    assert duplicate.warnings == ("json_tag_duplicate_keys_protected",)


def test_max_values_limits_selective_compression():
    source = (
        '<compress-json policy="p">'
        '{"comments":[{"body":"one"},{"body":"two"}]}'
        "</compress-json>"
    )
    result = transform_tagged_json(
        source,
        policy_id="p",
        value_paths=("$.comments[*].body",),
        max_values=1,
        compress_value=lambda _path, value: value.upper(),
    )

    assert json.loads(result.text) == {
        "comments": [{"body": "ONE"}, {"body": "two"}]
    }
    assert result.compressed_value_count == 1


def test_tag_like_text_inside_json_string_does_not_end_block_early():
    source = (
        '<compress-json policy="p">'
        '{"description":"Literal </compress-json> text remains inside the value",'
        '"id":"A-1"}'
        "</compress-json>"
    )
    result = transform_tagged_json(
        source,
        policy_id="p",
        value_paths=("$.description",),
        max_values=1,
        compress_value=lambda _path, value: value.replace("Literal", "Short"),
    )

    assert json.loads(result.text) == {
        "description": "Short </compress-json> text remains inside the value",
        "id": "A-1",
    }


def test_scalar_json_root_is_protected_instead_of_selectively_compressed():
    result = transform_tagged_json(
        '<compress-json policy="p">"long string"</compress-json>',
        policy_id="p",
        value_paths=("$",),
        max_values=1,
        compress_value=lambda _path, _value: "short",
    )

    assert result.text == '<nocompress>"long string"</nocompress>'
    assert result.warnings == (
        "json_tag_root_must_be_object_or_array_protected",
    )
