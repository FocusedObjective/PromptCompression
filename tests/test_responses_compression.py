from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app import main
from app.schemas import V1ResponsesCompressRequest
from app.token_estimator import estimate_regex_tokens
from app.usagetap_authorization import UsageTapAuthorization


class FakeCompressionService:
    model_name = "fake-responses-compressor"
    is_loaded = True

    def __init__(self) -> None:
        self.calls: list[tuple[str, float]] = []

    def estimate_compression_tokens(self, text, _tenant_profile=None):
        return estimate_regex_tokens(text)

    def compress(self, *, text, aggressiveness, **_kwargs):
        self.calls.append((text, aggressiveness))
        compressed = text.replace(" are ", " ")
        return SimpleNamespace(
            compressed_text=compressed,
            original_tokens=estimate_regex_tokens(text).count,
            compressed_tokens=estimate_regex_tokens(compressed).count,
            warnings=[],
            model_required=False,
        )


class FakeAuthorizationClient:
    def validate_incoming_credential(self, authorization_header):
        assert authorization_header == "Bearer cmp-test-key"
        return authorization_header

    def authorize(self, authorization_header):
        assert authorization_header == "Bearer cmp-test-key"
        return UsageTapAuthorization(
            organization_id="org_test",
            customer_id="customer_test",
        )


class FakeMeteringClient:
    def __init__(self) -> None:
        self.calls = []

    def record_compression_savings(self, **kwargs):
        self.calls.append(kwargs)


@pytest.fixture(autouse=True)
def responses_dependencies(monkeypatch):
    main.compression_response_cache.clear()
    main.message_content_cache.clear()
    service = FakeCompressionService()
    metering = FakeMeteringClient()
    monkeypatch.setattr(main, "compression_service", service)
    monkeypatch.setattr(
        main, "usage_tap_authorization_client", FakeAuthorizationClient()
    )
    monkeypatch.setattr(main, "usage_tap_metering_client", metering)
    yield service, metering
    main.compression_response_cache.clear()
    main.message_content_cache.clear()


def test_mixed_responses_input_preserves_order_and_non_message_fields(
    responses_dependencies,
):
    service, _metering = responses_dependencies
    function_call = {
        "type": "function_call",
        "id": "fc_123",
        "call_id": "call_123",
        "name": "lookup",
        "arguments": '{"query":"exact value"}',
        "status": "completed",
    }
    function_output = {
        "type": "function_call_output",
        "call_id": "call_123",
        "output": "exact tool output",
    }
    original_input = [
        {
            "type": "message",
            "id": "msg_a",
            "role": "developer",
            "content": "Prompts are code.",
            "cache_control": {"type": "ephemeral"},
            "future_field": {"keep": True},
        },
        function_call,
        function_output,
        {
            "type": "message",
            "id": "msg_d",
            "role": "user",
            "content": "Prompts are code.",
        },
    ]

    response = main.compress_v1_responses(
        V1ResponsesCompressRequest(
            model="gpt-test",
            input=original_input,
            tools=[{"type": "function", "name": "lookup"}],
            metadata={"trace": "trace_123"},
        )
    )

    result = response.compressed_request["input"]
    assert [item["type"] for item in result] == [
        "message",
        "function_call",
        "function_call_output",
        "message",
    ]
    assert result[0]["content"] == "Prompts code."
    assert result[1] == function_call
    assert result[2] == function_output
    assert result[3]["content"] == "Prompts code."
    assert result[0]["id"] == "msg_a"
    assert result[0]["cache_control"] == {"type": "ephemeral"}
    assert result[0]["future_field"] == {"keep": True}
    assert response.compressed_request["tools"] == [
        {"type": "function", "name": "lookup"}
    ]
    assert response.compressed_request["metadata"] == {"trace": "trace_123"}
    assert service.calls == [
        ("Prompts are code.", 0.15),
        ("Prompts are code.", 0.15),
    ]
    assert response.item_stats[0].compressed is True
    assert response.item_stats[1].skipped_reason == "item_type_preserved"
    assert response.item_stats[2].preserved is True
    assert response.item_stats[3].compressed is True
    assert response.tokens_saved == sum(
        stat.tokens_saved for stat in response.item_stats
    )
    assert response.input_tokens == sum(
        stat.original_tokens for stat in response.item_stats
    )
    assert response.output_tokens == sum(
        stat.compressed_tokens for stat in response.item_stats
    )
    assert "Prompts are code." not in response.model_dump_json(include={"item_stats"})


def test_concise_responses_messages_without_type_are_compressed_in_place(
    responses_dependencies,
):
    service, _metering = responses_dependencies
    original_input = [
        {
            "role": "developer",
            "content": [{"type": "input_text", "text": "Prompts are code."}],
        },
        {
            "type": "function_call",
            "call_id": "call_123",
            "name": "lookup",
            "arguments": "{}",
        },
        {
            "type": "function_call_output",
            "call_id": "call_123",
            "output": "Exact tool output.",
        },
        {
            "type": "reasoning",
            "role": "user",
            "content": "Message-like fields must not override an explicit type.",
        },
        {"role": "user", "content": "Prompts are code."},
        {"role": "assistant", "content": "Assistant stays unchanged."},
    ]

    response = main.compress_v1_responses(
        V1ResponsesCompressRequest(model="gpt-test", input=original_input)
    )

    result = response.compressed_request["input"]
    assert result[0]["content"][0]["text"] == "Prompts code."
    assert result[1] == original_input[1]
    assert result[2] == original_input[2]
    assert result[3] == original_input[3]
    assert result[4]["content"] == "Prompts code."
    assert result[5] == original_input[5]
    assert all("type" not in result[index] for index in (0, 4, 5))
    assert service.calls == [
        ("Prompts are code.", 0.15),
        ("Prompts are code.", 0.15),
    ]
    assert [response.item_stats[index].type for index in (0, 4, 5)] == [
        "message",
        "message",
        "message",
    ]
    assert response.item_stats[1].type == "function_call"
    assert response.item_stats[2].type == "function_call_output"
    assert response.item_stats[3].type == "reasoning"
    assert all(response.item_stats[index].preserved for index in (1, 2, 3, 5))
    assert response.item_stats[0].compressed is True
    assert response.item_stats[4].compressed is True
    assert response.item_stats[5].skipped_reason == "role_not_compressible"


@pytest.mark.parametrize("function_index", [2, 4])
def test_function_call_at_gateway_reported_indexes_is_preserved(
    responses_dependencies,
    function_index,
):
    items = [
        {"type": "message", "role": "user", "content": "Prompts are code."},
        {"type": "reasoning", "id": "rs_1", "summary": []},
        {"type": "reference", "id": "ref_1"},
        {"type": "custom_future_item", "payload": {"exact": [1, 2, 3]}},
        {"type": "message", "role": "system", "content": "Prompts are code."},
    ]
    function_call = {
        "type": "function_call",
        "id": "fc_indexed",
        "call_id": "call_indexed",
        "name": "indexed_tool",
        "arguments": '{"unchanged":true}',
    }
    items.insert(function_index, function_call)

    response = main.compress_v1_responses(
        V1ResponsesCompressRequest(model="gpt-test", input=items)
    )

    assert response.input[function_index] == function_call
    assert response.item_stats[function_index].index == function_index
    assert response.item_stats[function_index].type == "function_call"
    assert response.item_stats[function_index].preserved is True


def test_mixed_input_text_image_unknown_parts_and_breakpoints_are_preserved(
    responses_dependencies,
):
    image_part = {
        "type": "input_image",
        "image_url": "data:image/png;base64,exact",
        "detail": "high",
        "cache_control": {"type": "ephemeral"},
    }
    unknown_part = {"type": "future_part", "text": "must not compress", "x": 1}
    input_text = {
        "type": "input_text",
        "text": "Prompts are code.",
        "cache_control": {"type": "ephemeral"},
        "annotations": [{"kind": "reference", "id": "ref_1"}],
    }
    item = {
        "type": "message",
        "id": "msg_mixed",
        "role": "system",
        "content": [input_text, image_part, unknown_part],
        "cache_control": {"type": "ephemeral"},
    }

    response = main.compress_v1_responses(
        V1ResponsesCompressRequest(model="gpt-test", input=[item])
    )
    result = response.input[0]

    assert result["content"][0] == {**input_text, "text": "Prompts code."}
    assert result["content"][1] == image_part
    assert result["content"][2] == unknown_part
    assert result["cache_control"] == {"type": "ephemeral"}
    part_stats = response.item_stats[0].content_parts
    assert [stat.type for stat in part_stats] == [
        "input_text",
        "input_image",
        "future_part",
    ]
    assert part_stats[0].compressed is True
    assert part_stats[1].skipped_reason == "content_part_type_preserved"
    assert part_stats[2].skipped_reason == "content_part_type_preserved"


def test_no_eligible_text_and_unknown_items_succeed_unchanged(
    responses_dependencies,
):
    service, _metering = responses_dependencies
    original = [
        {"type": "function_call", "call_id": "call_1", "arguments": "{}"},
        {"type": "reasoning", "id": "reasoning_1", "encrypted_content": "abc"},
        {"type": "unknown_future", "payload": "exact"},
    ]

    response = main.compress_v1_responses(
        V1ResponsesCompressRequest(model="gpt-test", input=original)
    )

    assert response.input == original
    assert response.compressed_request["input"] == original
    assert response.tokens_saved == 0
    assert response.input_tokens == response.output_tokens
    assert service.calls == []
    assert all(stat.preserved for stat in response.item_stats)


def test_no_positive_savings_rolls_back_exact_original_content(
    responses_dependencies,
):
    original = {
        "type": "message",
        "role": "user",
        "content": [{"type": "input_text", "text": "Hi"}],
    }

    response = main.compress_v1_responses(
        V1ResponsesCompressRequest(model="gpt-test", input=[original])
    )

    assert response.input == [original]
    assert response.tokens_saved == 0
    assert response.item_stats[0].compression_applied is True
    assert response.item_stats[0].compressed is False
    assert response.item_stats[0].skipped_reason == "no_positive_savings"
    assert response.item_stats[0].content_parts[0].skipped_reason == (
        "no_positive_savings"
    )


def test_http_contract_accepts_string_input_and_returns_responses_shape(
    responses_dependencies,
):
    _service, metering = responses_dependencies
    response = TestClient(main.app).post(
        "/v1/responses/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={
            "model": "gpt-test",
            "input": "Prompts are code.",
            "temperature": 0.2,
            "compression_settings": {"aggressiveness": 0.4},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["input"] == "Prompts code."
    assert body["compressed_request"] == {
        "model": "gpt-test",
        "input": "Prompts code.",
        "temperature": 0.2,
    }
    assert "messages" not in body["compressed_request"]
    assert body["tokens_saved"] > 0
    assert metering.calls[-1]["input_tokens"] == body["input_tokens"]
    assert metering.calls[-1]["output_tokens"] == body["output_tokens"]


def test_messages_endpoint_skips_standalone_function_items_individually(
    responses_dependencies,
):
    function_call = {
        "type": "function_call",
        "id": "fc_messages",
        "call_id": "call_messages",
        "name": "lookup",
        "arguments": '{"exact":true}',
    }
    function_output = {
        "type": "function_call_output",
        "call_id": "call_messages",
        "output": "exact output",
    }
    response = TestClient(main.app).post(
        "/v1/messages/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={
            "model": "gpt-test",
            "messages": [
                {"role": "user", "content": "Prompts are code."},
                function_call,
                function_output,
                {"role": "user", "content": "Prompts are code."},
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["messages"] == [
        {"role": "user", "content": "Prompts code."},
        function_call,
        function_output,
        {"role": "user", "content": "Prompts code."},
    ]
    assert body["compressed_request"]["messages"] == body["messages"]
    assert body["message_stats"][1] == {
        "index": 1,
        "role": "",
        "item_type": "function_call",
        "original_tokens": body["message_stats"][1]["original_tokens"],
        "compressed_tokens": body["message_stats"][1]["original_tokens"],
        "tokens_saved": 0,
        "compression_applied": False,
        "compressed": False,
        "text_parts": 0,
        "compressed_text_parts": 0,
        "skipped_reason": "item_type_preserved",
        "content_cache_hits": 0,
        "content_cache_misses": 0,
        "content_cache_stores": 0,
        "candidate_tokens_saved": 0,
        "candidate_reduction": 0.0,
        "tool_result_action": None,
    }
    assert body["message_stats"][1]["original_tokens"] > 0
    assert body["message_stats"][2]["item_type"] == "function_call_output"
    assert body["message_stats"][2]["skipped_reason"] == "item_type_preserved"


@pytest.mark.parametrize("function_index", [2, 4])
def test_messages_endpoint_preserves_function_call_at_mixed_chain_indexes(
    responses_dependencies,
    function_index,
):
    items = [
        {"role": "system", "content": "System stays."},
        {"role": "user", "content": "Prompts are code."},
        {"type": "reasoning", "id": "reason_1"},
        {"role": "assistant", "content": "Assistant stays."},
        {"role": "user", "content": "Prompts are code."},
    ]
    function_call = {
        "type": "function_call",
        "call_id": "call_indexed",
        "name": "lookup",
        "arguments": "{}",
    }
    items.insert(function_index, function_call)

    response = TestClient(main.app).post(
        "/v1/messages/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={"model": "gpt-test", "messages": items},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["messages"][function_index] == function_call
    assert body["message_stats"][function_index]["index"] == function_index
    assert body["message_stats"][function_index]["item_type"] == "function_call"
