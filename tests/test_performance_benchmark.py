import statistics

from scripts.benchmark_performance import (
    DEFAULT_TARGET_TOKENS,
    build_case,
    build_summary_rows,
)


def test_default_benchmark_sizes_have_requested_shape():
    assert max(DEFAULT_TARGET_TOKENS) == 200_000
    assert statistics.median(DEFAULT_TARGET_TOKENS) == 3_000


def test_build_case_generates_json_share_metadata():
    case = build_case(1_000, 0.5)

    assert case.target_tokens == 1_000
    assert case.json_ratio_target == 0.5
    assert case.html_ratio_target == 0.0
    assert case.synthetic_input_tokens >= 1_000
    assert case.synthetic_json_tokens > 0
    assert case.synthetic_html_tokens == 0
    assert case.json_chars > 0
    assert case.html_chars == 0
    assert "Customer telemetry JSON" in case.text


def test_build_case_generates_html_share_metadata():
    case = build_case(1_000, 0.25, 0.25)

    assert case.case_id == "tok1000_json0p25_html0p25"
    assert case.target_tokens == 1_000
    assert case.json_ratio_target == 0.25
    assert case.html_ratio_target == 0.25
    assert case.synthetic_input_tokens >= 1_000
    assert case.synthetic_json_tokens > 0
    assert case.synthetic_html_tokens > 0
    assert case.json_chars > 0
    assert case.html_chars > 0
    assert "Customer telemetry JSON" in case.text
    assert "Downloaded incident HTML page" in case.text
    assert "<!doctype html>" in case.text
    assert "Benchmark HTML Incident Page" in case.text


def test_summary_rows_group_latency_distributions():
    rows = [
        {
            "status": "ok",
            "target_tokens": 1_000,
            "json_ratio_target": 0.0,
            "client_wall_ms": 100.0,
            "server_elapsed_ms": 80.0,
            "timing_total_ms": 80.0,
            "timing_preprocessing_ms": 10.0,
            "timing_segment_selection_ms": 20.0,
            "timing_model_load_ms": 0.0,
            "timing_llmlingua_ms": 40.0,
            "timing_token_estimate_ms": 10.0,
            "synthetic_input_tokens": 1_010,
            "synthetic_json_tokens": 0,
            "response_original_tokens": 1_000,
            "response_compressed_tokens": 700,
            "response_tokens_saved": 300,
            "reduction": 0.3,
            "input_chars": 4_000,
            "output_chars": 3_000,
            "model_input_chars": 4_000,
        },
        {
            "status": "ok",
            "target_tokens": 1_000,
            "json_ratio_target": 0.0,
            "client_wall_ms": 200.0,
            "server_elapsed_ms": 160.0,
            "timing_total_ms": 160.0,
            "timing_preprocessing_ms": 20.0,
            "timing_segment_selection_ms": 30.0,
            "timing_model_load_ms": 0.0,
            "timing_llmlingua_ms": 90.0,
            "timing_token_estimate_ms": 20.0,
            "synthetic_input_tokens": 1_020,
            "synthetic_json_tokens": 0,
            "response_original_tokens": 1_010,
            "response_compressed_tokens": 710,
            "response_tokens_saved": 300,
            "reduction": 0.297,
            "input_chars": 4_100,
            "output_chars": 3_100,
            "model_input_chars": 4_100,
        },
    ]

    summary_rows = build_summary_rows(rows)
    overall = next(row for row in summary_rows if row["group_type"] == "overall")

    assert overall["success_count"] == 2
    assert overall["client_wall_ms_p50"] == 150.0
    assert overall["timing_llmlingua_ms_mean"] == 65.0
