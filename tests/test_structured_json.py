import base64
import json

from app.structured_json import parse_value_path, transform_tagged_json


def _transform(source: str, **overrides: object):
    options = {
        "policy_id": None,
        "value_paths": (),
        "max_values": 8,
        "compress_value": lambda path, value: f"compressed:{path}:{value}",
        "allow_inline_paths": False,
    }
    options.update(overrides)
    return transform_tagged_json(source, **options)


def _unwrapped_json(result_text: str) -> object:
    prefix = "<protected-json>"
    suffix = "</protected-json>"
    assert result_text.startswith(prefix)
    assert result_text.endswith(suffix)
    encoded = result_text[len(prefix) : -len(suffix)]
    return json.loads(base64.b64decode(encoded).decode("utf-8"))


def test_parse_value_path_supports_keys_and_array_wildcards():
    assert parse_value_path("$.description") == ("description",)
    assert parse_value_path("$.comments[*].body") == (
        "comments",
        "*",
        "body",
    )
    assert parse_value_path("comments.body") is None
    assert parse_value_path("$.comments[0].body") is None


def test_inline_paths_compress_only_selected_strings_when_explicitly_allowed():
    source = (
        '<compress-json paths="$.description,$.comments[*].body">'
        '{"id":"ISSUE-73","title":"Exact title","description":"Narrative",'
        '"comments":[{"author":"Ada","body":"Detailed comment"}]}'
        "</compress-json>"
    )

    result = _transform(source, allow_inline_paths=True)
    parsed = _unwrapped_json(result.text)

    assert parsed == {
        "id": "ISSUE-73",
        "title": "Exact title",
        "description": "compressed:$.description:Narrative",
        "comments": [
            {
                "author": "Ada",
                "body": "compressed:$.comments[0].body:Detailed comment",
            }
        ],
    }
    assert result.compressed_value_count == 2


def test_inline_paths_are_fail_closed_without_profiler_opt_in():
    source = (
        '<compress-json paths="$.description">'
        '{"description":"Narrative"}'
        "</compress-json>"
    )

    result = _transform(source)

    assert _unwrapped_json(result.text) == {"description": "Narrative"}
    assert result.compressed_value_count == 0
    assert result.warnings == ("json_tag_inline_paths_not_authorized",)


def test_policy_and_inline_paths_use_only_the_authorized_intersection():
    source = (
        '<compress-json paths="$.title,$.description" policy="issue-v1">'
        '{"title":"Exact title","description":"Narrative"}'
        "</compress-json>"
    )

    result = _transform(
        source,
        policy_id="issue-v1",
        value_paths=("$.description", "$.comments[*].body"),
    )

    assert _unwrapped_json(result.text) == {
        "title": "Exact title",
        "description": "compressed:$.description:Narrative",
    }
    assert result.compressed_value_count == 1


def test_matching_policy_without_inline_paths_uses_all_tenant_paths():
    source = (
        '<compress-json policy="issue-v1">'
        '{"description":"Narrative","comments":[{"body":"Comment"}]}'
        "</compress-json>"
    )

    result = _transform(
        source,
        policy_id="issue-v1",
        value_paths=("$.description", "$.comments[*].body"),
    )

    parsed = _unwrapped_json(result.text)
    assert parsed["description"].startswith("compressed:$.description:")
    assert parsed["comments"][0]["body"].startswith(
        "compressed:$.comments[0].body:"
    )


def test_policy_mismatch_cannot_fall_back_to_inline_authorization():
    source = (
        '<compress-json policy="other-v1" paths="$.description">'
        '{"description":"Narrative"}'
        "</compress-json>"
    )

    result = _transform(
        source,
        policy_id="issue-v1",
        value_paths=("$.description",),
        allow_inline_paths=True,
    )

    assert _unwrapped_json(result.text) == {"description": "Narrative"}
    assert result.warnings == ("json_tag_policy_not_authorized:other-v1",)


def test_invalid_and_duplicate_key_tagged_json_are_protected_verbatim():
    invalid = _transform(
        '<compress-json paths="$.broken">{"broken":}</compress-json>',
        allow_inline_paths=True,
    )
    duplicate = _transform(
        '<compress-json paths="$.name">'
        '{"name":"old","name":"new"}'
        "</compress-json>",
        allow_inline_paths=True,
    )

    assert invalid.text == '<nocompress>{"broken":}</nocompress>'
    assert invalid.warnings == ("json_tag_invalid_json_protected",)
    assert duplicate.text == (
        '<nocompress>{"name":"old","name":"new"}</nocompress>'
    )
    assert duplicate.warnings == ("json_tag_duplicate_keys_protected",)


def test_max_values_limits_inline_compression():
    source = (
        '<compress-json paths="$.comments[*].body">'
        '{"comments":[{"body":"one"},{"body":"two"}]}'
        "</compress-json>"
    )
    result = _transform(
        source,
        max_values=1,
        allow_inline_paths=True,
        compress_value=lambda _path, value: value.upper(),
    )

    assert _unwrapped_json(result.text) == {
        "comments": [{"body": "ONE"}, {"body": "two"}]
    }
    assert result.compressed_value_count == 1


def test_internal_protection_wrapper_cannot_be_closed_by_json_string_content():
    source = (
        '<compress-json paths="$.description">'
        '{"description":"Literal </protected-json> text"}'
        "</compress-json>"
    )

    result = _transform(
        source,
        allow_inline_paths=True,
        compress_value=lambda _path, value: value.replace("Literal", "Short"),
    )

    assert _unwrapped_json(result.text) == {
        "description": "Short </protected-json> text"
    }
