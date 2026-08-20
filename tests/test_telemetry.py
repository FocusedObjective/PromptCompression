import logging

from app.telemetry import CompressionTelemetry


def test_telemetry_records_bounded_content_free_metrics(caplog):
    telemetry = CompressionTelemetry()
    secret = "private-prompt-must-not-appear"

    with caplog.at_level(logging.INFO, logger="promptcompression.telemetry"):
        telemetry.record(
            route="/v1/messages/compress",
            mode="model_auto",
            cache_status="miss",
            input_tokens=10_000,
            output_tokens=9_000,
            elapsed_ms=321.5,
            warnings=["tool_result_shadow", f"diagnostic {secret}"],
            content_cache_hits=3,
            content_cache_misses=1,
            tool_actions=["shadow"],
        )

    snapshot = telemetry.snapshot()
    assert snapshot["requests"] == 1
    assert snapshot["saved_tokens"] == 1_000
    assert snapshot["content_cache_hits"] == 3
    assert snapshot["tool_actions"] == {"shadow": 1}
    assert secret not in caplog.text
    assert "prompt_compression_request" in caplog.text
