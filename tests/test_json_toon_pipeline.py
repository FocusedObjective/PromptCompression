from typing import Any

from app.compression_pipeline import PromptPreprocessor
from app.compressor import PromptCompressionService
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
    assert [section.kind for section in result.output_sections] == [
        "prose",
        "json",
        "prose",
    ]
    assert compressor.inputs == [
        "Return exactly this JSON shape, preserving valid JSON syntax:\n"
        "__CK_KEEP_0000__"
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


def test_small_json_does_not_trigger_structured_pipeline():
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
    text = 'Please review {"ok": true} after.'

    service.compress(text, aggressiveness=0.25)

    assert compressor.inputs == [text]


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
