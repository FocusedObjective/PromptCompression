from typing import Any

import pytest

from app.compression_pipeline import PromptPreprocessor
from app.compressor import PromptCompressionService
from app.tenant_profiles import build_tenant_profile
from app.toon_adapter import ToonEncodingError
from tests.pipeline_helpers import (
    RecordingCompressor,
    build_service_with_pipeline,
    fake_toon_encoder,
)


def test_medium_large_json_is_toonified_and_not_sent_to_model():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = """Please review this customer data:
{
  "users": [
    {"id": 1, "name": "Alice", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"},
    {"id": 3, "name": "Cora", "role": "user"}
  ]
}
Please review the risk summary."""

    result = service.compress(text, aggressiveness=0.25)

    assert "users[3]{id,name,role}" in result.compressed_text
    assert '"users"' not in result.compressed_text
    assert [section.kind for section in result.output_sections] == [
        "prose",
        "toon",
        "prose",
    ]
    assert result.output_sections[1].compressed is True
    assert result.output_sections[1].protected is True
    assert "users[3]{id,name,role}" in result.output_sections[1].text
    assert compressor.inputs == [
        "Please review this customer data:\n"
        "__CK_KEEP_0000__\n"
        "Please review the risk summary."
    ]
    assert "users[3]{id,name,role}" not in compressor.inputs[0]
    assert compressor.force_tokens_values[0][0] == "__CK_KEEP_0000__"


def test_toonified_json_keeps_boundary_before_following_html():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = """Please review:
{
  "users": [
    {"id": 1, "name": "Alice", "role": "admin"},
    {"id": 2, "name": "Bob", "role": "user"},
    {"id": 3, "name": "Cora", "role": "user"}
  ]
}

<html>   
  <a>a</a>   <b>b</b>
</html>"""

    result = service.compress(text, aggressiveness=0.25)

    assert "  3,Cora,user\n<html>   " in result.compressed_text
    assert "  <a>a</a>   <b>b</b>" in result.compressed_text


def test_generic_unchanged_context_still_allows_toon_conversion():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = """Keep customer IDs, incident dates, URLs, and exact retry limits unchanged.

Customer data:
{
  "account": {
    "id": "acct_2048",
    "plan": "enterprise",
    "region": "us-west-2"
  },
  "incidents": [
    {"id": "INC-1001", "date": "2026-06-18", "severity": "high", "status": "open"},
    {"id": "INC-1002", "date": "2026-06-20", "severity": "medium", "status": "monitoring"},
    {"id": "INC-1003", "date": "2026-06-22", "severity": "low", "status": "resolved"}
  ]
}

Please review next steps."""

    result = service.compress(text, aggressiveness=0.25)

    assert "users[3]{id,name,role}" in result.compressed_text
    assert any(section.kind == "toon" for section in result.output_sections)
    assert not any(section.kind == "json" for section in result.output_sections)


def test_fenced_json_template_is_preserved_verbatim():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    json_fence = """```json
{
  "status": "ok",
  "items": [
    {"id": "A1", "label": "Alpha"},
    {"id": "B2", "label": "Beta"}
  ]
}
```"""
    text = (
        "Return exactly this JSON shape, preserving valid JSON syntax:\n"
        f"{json_fence}\n"
        "Please review after."
    )

    result = service.compress(text, aggressiveness=0.25)

    assert json_fence in result.compressed_text
    assert "```toon" not in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert any(section.kind == "protected" for section in result.output_sections)
    assert compressor.inputs == [
        "Return exactly this __CK_KEEP_0000__ shape, preserving valid "
        "__CK_KEEP_0001__ syntax:\n"
        "__CK_KEEP_0002__"
        "Please review after."
    ]
    assert json_fence not in compressor.inputs[0]
    assert compressor.force_tokens_values[0][0] == "__CK_KEEP_0000__"


def test_raw_json_exact_context_is_not_toonified():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    json_block = """{
  "status": "ok",
  "items": [
    {"id": "A1", "label": "Alpha"},
    {"id": "B2", "label": "Beta"}
  ]
}"""
    text = f"Return exactly this JSON shape:\n{json_block}\nPlease review after."

    result = service.compress(text, aggressiveness=0.25)

    assert json_block in result.compressed_text
    assert "users[3]{id,name,role}" not in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert all(json_block not in seen for seen in compressor.inputs)


def test_duplicate_key_json_is_not_toonified():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    json_block = """{
  "feature": "old",
  "feature": "new",
  "cases": [
    {"id": 1, "value": 0},
    {"id": 2, "value": 1}
  ]
}"""
    text = f"Please review this data:\n{json_block}\nPlease review after."

    result = service.compress(text, aggressiveness=0.25)

    assert json_block in result.compressed_text
    assert "users[3]{id,name,role}" not in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert all(json_block not in seen for seen in compressor.inputs)


def test_toon_unavailable_preserves_json_and_still_skips_model():
    def unavailable_toon_encoder(value: Any) -> str:
        raise ToonEncodingError("missing")

    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor, unavailable_toon_encoder)
    json_block = """{
  "items": [
    {"sku": "A1", "qty": 2},
    {"sku": "B2", "qty": 4}
  ]
}"""
    text = f"Please review:\n{json_block}\nPlease review totals."

    result = service.compress(text, aggressiveness=0.25)

    assert json_block in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert all(json_block not in seen for seen in compressor.inputs)


def test_json_minify_fallback_applies_only_when_enabled_and_token_positive():
    def unavailable_toon_encoder(value: Any) -> str:
        raise ToonEncodingError("missing")

    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.preprocessor = PromptPreprocessor(
        toon_encoder=unavailable_toon_encoder,
        min_json_chars=1,
        min_json_lines=1,
        min_toon_savings=0.0,
        enable_json_minify=True,
        min_json_minify_savings=0.0,
    )
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    json_block = """{
  "items": [
    {"sku": "A1", "qty": 2},
    {"sku": "B2", "qty": 4}
  ]
}"""
    text = f"Please review:\n{json_block}\nPlease review totals."

    result = service.compress(text, aggressiveness=0.25)

    assert '{"items":[{"sku":"A1","qty":2},{"sku":"B2","qty":4}]}' in (
        result.compressed_text
    )
    assert any(section.kind == "json_minified" for section in result.output_sections)
    assert all(json_block not in seen for seen in compressor.inputs)
    assert result.diagnostics is not None
    assert result.diagnostics.json_minify_tokens_saved >= 0
    assert result.diagnostics.json_minified_segment_count == 1


def test_json_minify_fallback_never_minifies_duplicate_key_json():
    def unavailable_toon_encoder(value: Any) -> str:
        raise ToonEncodingError("missing")

    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.preprocessor = PromptPreprocessor(
        toon_encoder=unavailable_toon_encoder,
        min_json_chars=1,
        min_json_lines=1,
        min_toon_savings=0.0,
        enable_json_minify=True,
        min_json_minify_savings=0.0,
    )
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    json_block = """{
  "feature": "old",
  "feature": "new",
  "items": [
    {"id": 1},
    {"id": 2}
  ]
}"""
    text = f"Please review this data:\n{json_block}\nPlease review after."

    result = service.compress(text, aggressiveness=0.25)

    assert json_block in result.compressed_text
    assert '"items":[{"id":1},{"id":2}]' not in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert not any(
        section.kind == "json_minified" for section in result.output_sections
    )


@pytest.mark.parametrize("json_value", ['{"ok": true}', "[]", "{}"])
def test_small_json_is_protected_verbatim_without_attempting_toon(
    json_value: str,
):
    def unexpected_toon_encoder(_value: Any) -> str:
        raise AssertionError("small JSON should not be sent to the TOON encoder")

    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.preprocessor = PromptPreprocessor(
        toon_encoder=unexpected_toon_encoder,
        min_json_chars=100,
        min_json_lines=4,
        min_toon_savings=0.0,
    )
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    text = f"Please review {json_value} after."

    result = service.compress(text, aggressiveness=0.25)

    assert compressor.inputs == ["Please review __CK_KEEP_0000__ after."]
    assert result.compressed_text == f"Review {json_value} after."
    assert [section.kind for section in result.output_sections] == [
        "prose",
        "json",
        "prose",
    ]
    assert result.output_sections[1].protected is True
    assert result.output_sections[1].compressed is False


def test_model_auto_protects_small_json_when_model_gate_runs():
    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.preprocessor = PromptPreprocessor(
        toon_encoder=fake_toon_encoder,
        min_json_chars=100,
        min_json_lines=4,
        min_toon_savings=0.0,
    )
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    service.device = "cuda"
    service.model_auto_enabled = True
    service.min_model_candidate_tokens = 1
    service.min_model_incremental_savings_tokens = 0
    service.min_model_incremental_reduction = 0.0
    service.max_model_projected_latency_ms = 1000.0
    service.max_protected_density = 1.0
    service.max_structured_density = 1.0
    service.skip_model_if_deterministic_reduction_gte = 1.0
    service.gpu_p50_fixed_overhead_ms = 1.0
    service.gpu_p50_llmlingua_chunk_ms = 1.0
    service.gpu_p50_token_estimate_ms = 1.0
    text = 'Please review {"ok": true} after.'

    result = service.compress(
        text,
        aggressiveness=0.25,
        mode="model_auto",
    )

    assert compressor.inputs == ["Please review __CK_KEEP_0000__ after."]
    assert result.compressed_text == 'Review {"ok": true} after.'
    assert result.diagnostics is not None
    assert result.diagnostics.model_gate_decision == "run"


def test_tagged_json_compresses_allowlisted_strings_then_protects_structure():
    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.preprocessor = PromptPreprocessor(
        toon_encoder=fake_toon_encoder,
        min_json_chars=10_000,
        min_json_lines=100,
    )
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    profile = build_tenant_profile(
        tenant_id="tenant_123",
        json_compression_policy_id="issue-v1",
        json_value_compression_paths=["$.description"],
        json_value_min_tokens=1,
        json_value_max_reduction=0.9,
    )
    text = (
        "Please review tagged data.\n"
        '<compress-json policy="issue-v1">'
        '{"id":"ISSUE-73","title":"Please review exact title",'
        '"description":"Please review this detailed narrative before launch."}'
        "</compress-json>\n"
        "Please review after."
    )

    result = service.compress(
        text,
        aggressiveness=0.25,
        tenant_profile=profile,
        mode="model_force",
    )

    assert compressor.inputs[0] == (
        "Please review this detailed narrative before launch."
    )
    assert compressor.inputs[-1] == (
        "Please review tagged data.\n__CK_KEEP_0000__\nPlease review after."
    )
    assert "<compress-json" not in result.compressed_text
    assert '"id":"ISSUE-73"' in result.compressed_text
    assert '"title":"Please review exact title"' in result.compressed_text
    assert '"description":"Review this detailed narrative before launch."' in (
        result.compressed_text
    )


def test_tagged_json_rejects_value_compression_over_reduction_limit():
    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.preprocessor = PromptPreprocessor(
        toon_encoder=fake_toon_encoder,
        min_json_chars=10_000,
        min_json_lines=100,
    )
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    profile = build_tenant_profile(
        tenant_id="tenant_123",
        json_compression_policy_id="issue-v1",
        json_value_compression_paths=["$.description"],
        json_value_min_tokens=1,
        json_value_max_reduction=0.0,
    )
    original_value = "Please review this detailed narrative before launch."
    text = (
        '<compress-json policy="issue-v1">'
        f'{{"description":"{original_value}"}}'
        "</compress-json>"
    )

    result = service.compress(
        text,
        aggressiveness=0.25,
        tenant_profile=profile,
        mode="model_force",
    )

    assert f'"description":"{original_value}"' in result.compressed_text


def test_tagged_json_selective_values_are_rebuilt_before_toon_protection():
    encoded_values: list[Any] = []

    def recording_toon_encoder(value: Any) -> str:
        encoded_values.append(value)
        return "i{id,d}:\n  ISSUE-73,Review"

    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.preprocessor = PromptPreprocessor(
        toon_encoder=recording_toon_encoder,
        min_json_chars=1,
        min_json_lines=1,
        min_toon_savings=0.0,
    )
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    profile = build_tenant_profile(
        tenant_id="tenant_123",
        json_compression_policy_id="issue-v1",
        json_value_compression_paths=["$.description"],
        json_value_min_tokens=1,
        json_value_max_reduction=0.9,
    )
    text = (
        "Please review before.\n"
        '<compress-json policy="issue-v1">'
        '{"id":"ISSUE-73","description":"Please review narrative"}'
        "</compress-json>\n"
        "Please review after."
    )

    result = service.compress(
        text,
        aggressiveness=0.25,
        tenant_profile=profile,
        mode="model_force",
    )

    assert encoded_values == [
        {"id": "ISSUE-73", "description": "Review narrative"}
    ]
    assert "i{id,d}" in result.compressed_text
    assert compressor.inputs[-1] == (
        "Please review before.\n__CK_KEEP_0000__\nPlease review after."
    )
    assert "ISSUE-73" not in compressor.inputs[-1]


def test_llm_tool_call_json_is_not_toonified():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    tool_call = """[
  {
    "role": "assistant",
    "tool_calls": [
      {
        "id": "call_123",
        "type": "function",
        "function": {
          "name": "lookup_user",
          "arguments": "{\\"userId\\":\\"usr_9934812\\"}"
        }
      }
    ]
  }
]"""
    text = f"Please review this exchange:\n{tool_call}\nPlease review the outcome."

    result = service.compress(text, aggressiveness=0.25)

    assert tool_call in result.compressed_text
    assert "tool_calls[1]" not in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert all(tool_call not in seen for seen in compressor.inputs)


def test_llm_tool_response_json_is_not_toonified():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    tool_response = """{
  "role": "tool",
  "tool_call_id": "call_123",
  "name": "lookup_user",
  "content": "{\\"userId\\":\\"usr_9934812\\",\\"plan\\":\\"pro\\"}"
}"""
    text = f"Please review this tool response:\n{tool_response}\nPlease review next steps."

    result = service.compress(text, aggressiveness=0.25)

    assert tool_response in result.compressed_text
    assert "tool_call_id:" not in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert all(tool_response not in seen for seen in compressor.inputs)


def test_openai_responses_function_call_json_is_not_toonified():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    openai_call = """{
  "output": [
    {
      "type": "function_call",
      "call_id": "call_abc",
      "name": "get_weather",
      "arguments": "{\\"city\\":\\"Seattle\\"}"
    },
    {
      "type": "function_call_output",
      "call_id": "call_abc",
      "output": "{\\"temperature\\":\\"55F\\"}"
    }
  ]
}"""
    text = f"Please review this OpenAI response:\n{openai_call}\nPlease review next steps."

    result = service.compress(text, aggressiveness=0.25)

    assert openai_call in result.compressed_text
    assert "function_call:" not in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert all(openai_call not in seen for seen in compressor.inputs)


def test_anthropic_tool_use_and_result_json_is_not_toonified():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    anthropic_exchange = """[
  {
    "role": "assistant",
    "content": [
      {
        "type": "tool_use",
        "id": "toolu_01ABC",
        "name": "lookup_order",
        "input": {"order_id": "ord_123"}
      }
    ]
  },
  {
    "role": "user",
    "content": [
      {
        "type": "tool_result",
        "tool_use_id": "toolu_01ABC",
        "content": "{\\"status\\":\\"shipped\\"}"
      }
    ]
  }
]"""
    text = f"Please review this Anthropic exchange:\n{anthropic_exchange}\nPlease review next steps."

    result = service.compress(text, aggressiveness=0.25)

    assert anthropic_exchange in result.compressed_text
    assert "tool_use:" not in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert all(anthropic_exchange not in seen for seen in compressor.inputs)


def test_google_function_call_and_response_json_is_not_toonified():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    google_exchange = """[
  {
    "role": "model",
    "parts": [
      {
        "functionCall": {
          "name": "lookupInventory",
          "args": {"sku": "A1"}
        }
      }
    ]
  },
  {
    "role": "function",
    "parts": [
      {
        "functionResponse": {
          "name": "lookupInventory",
          "response": {"available": true, "qty": 12}
        }
      }
    ]
  }
]"""
    text = f"Please review this Gemini exchange:\n{google_exchange}\nPlease review next steps."

    result = service.compress(text, aggressiveness=0.25)

    assert google_exchange in result.compressed_text
    assert "functionCall:" not in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert all(google_exchange not in seen for seen in compressor.inputs)


def test_grok_xai_tool_call_json_is_not_toonified():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    grok_call = """{
  "id": "chatcmpl_123",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "tool_calls": [
          {
            "id": "call_xai_123",
            "type": "function",
            "function": {
              "name": "search_web",
              "arguments": "{\\"query\\":\\"latest status\\"}"
            }
          }
        ]
      }
    }
  ]
}"""
    text = f"Please review this Grok response:\n{grok_call}\nPlease review next steps."

    result = service.compress(text, aggressiveness=0.25)

    assert grok_call in result.compressed_text
    assert "tool_calls[1]" not in result.compressed_text
    assert any(section.kind == "json" for section in result.output_sections)
    assert all(grok_call not in seen for seen in compressor.inputs)
