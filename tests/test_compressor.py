from app.compressor import (
    COMPRESSION_MODE_DETERMINISTIC,
    COMPRESSION_MODE_MODEL_AUTO,
    PromptCompressionService,
    _parse_adapter_slots,
    build_token_savings,
)
from app.tenant_profiles import build_tenant_profile
from app.token_estimator import estimate_huggingface_tokens, estimate_token_count
from tests.pipeline_helpers import RecordingCompressor, build_service_with_pipeline


class DroppingPlaceholderCompressor(RecordingCompressor):
    def compress_prompt_llmlingua2(
        self,
        text: str,
        rate: float,
        force_tokens: list[str],
        return_word_label: bool,
    ) -> dict[str, str | int]:
        self.inputs.append(text)
        self.force_tokens_values.append(force_tokens)
        self.return_word_label_values.append(return_word_label)
        return {
            "compressed_prompt": text.replace("__CK_KEEP_0000__", ""),
            "origin_tokens": len(text.split()),
            "compressed_tokens": len(text.split()),
            "fn_labeled_original_prompt": "",
        }


class LowForceTokenCompressor(RecordingCompressor):
    max_force_token = 1


class ZeroForceTokenCompressor(RecordingCompressor):
    max_force_token = 0


class LabelingPlaceholderCompressor(RecordingCompressor):
    def compress_prompt_llmlingua2(
        self,
        text: str,
        rate: float,
        force_tokens: list[str],
        return_word_label: bool,
    ) -> dict[str, str | int]:
        self.inputs.append(text)
        self.force_tokens_values.append(force_tokens)
        self.return_word_label_values.append(return_word_label)
        return {
            "compressed_prompt": text.replace("Please review", "Review"),
            "origin_tokens": len(text.split()),
            "compressed_tokens": len(text.split()),
            "fn_labeled_original_prompt": (
                "Please 0\t\t|\t\treview 1\t\t|\t\tbefore. 1\t\t|\t\t"
                "__CK_KEEP_0000__ 1\t\t|\t\t"
                "Please 0\t\t|\t\treview 1\t\t|\t\tafter. 1"
            ),
        }


class ManglingProtectedTextCompressor(RecordingCompressor):
    def compress_prompt_llmlingua2(
        self,
        text: str,
        rate: float,
        force_tokens: list[str],
        return_word_label: bool,
    ) -> dict[str, str | int]:
        self.inputs.append(text)
        self.force_tokens_values.append(force_tokens)
        self.return_word_label_values.append(return_word_label)
        return {
            "compressed_prompt": text,
            "origin_tokens": len(text.split()),
            "compressed_tokens": len(text.split()),
            "fn_labeled_original_prompt": (
                " ".join(f"{word} 1" for word in text.split())
            ),
        }


class FakeTokenizer:
    name_or_path = "fake-tokenizer"

    def __call__(
        self,
        text: str,
        add_special_tokens: bool,
        return_attention_mask: bool,
        return_token_type_ids: bool,
    ) -> dict[str, list[int]]:
        assert add_special_tokens is False
        assert return_attention_mask is False
        assert return_token_type_ids is False
        return {"input_ids": [1 for part in text.split("|") if part]}


def test_aggressiveness_zero_keeps_full_rate():
    service = PromptCompressionService()
    assert service.target_rate_for_aggressiveness(0.0) == 1.0


def test_aggressiveness_one_uses_min_rate():
    service = PromptCompressionService()
    assert service.target_rate_for_aggressiveness(1.0) == service.min_rate


def test_aggressiveness_is_bounded():
    service = PromptCompressionService()
    assert service.target_rate_for_aggressiveness(-1.0) == 1.0
    assert service.target_rate_for_aggressiveness(2.0) == service.min_rate


def test_parse_word_labels_marks_kept_and_dropped_tokens():
    service = PromptCompressionService()

    tokens = service.parse_word_labels("Prompts 1\t\t|\t\tare 0\t\t|\t\tcode 1")

    assert [token.text for token in tokens] == ["Prompts", "are", "code"]
    assert [token.kept for token in tokens] == [True, False, True]


def test_estimate_token_count_matches_unicode_word_or_non_space_pattern():
    text = "Open-source CI/CD .test.yaml user_message"

    assert estimate_token_count(text) == 13


def test_estimate_token_count_groups_unicode_letters_and_numbers():
    text = "cafe42 naïve 2026-06-23"

    assert estimate_token_count(text) == 7


def test_huggingface_estimate_uses_supplied_tokenizer_without_loading_model():
    estimate = estimate_huggingface_tokens(
        "alpha|beta|gamma",
        "ignored-model",
        tokenizer=FakeTokenizer(),
    )

    assert estimate.count == 3
    assert estimate.estimator == "huggingface:fake-tokenizer"
    assert estimate.tokenizer_backed is True


def test_short_segments_skip_model_for_speed():
    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.min_segment_chars = 80
    service.min_segment_tokens = 12

    result = service.compress("Please review this short prompt.", aggressiveness=0.25)

    assert compressor.inputs == []
    assert result.compressed_text == "Please review this short prompt."
    assert result.output_sections[0].compressed is False


def test_aggressiveness_zero_skips_model():
    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.min_segment_chars = 1
    service.min_segment_tokens = 1

    result = service.compress("Please review this longer prompt.", aggressiveness=0.0)

    assert compressor.inputs == []
    assert result.compressed_text == "Please review this longer prompt."
    assert result.reduction == 0.0


def test_deterministic_mode_never_calls_model():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = (
        "Please review before. "
        "<nocompress>KEEP EXACT</nocompress> "
        "Please review after."
    )

    result = service.compress(
        text,
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_DETERMINISTIC,
    )

    assert compressor.inputs == []
    assert result.compressed_text == (
        "Please review before. KEEP EXACT Please review after."
    )
    assert result.compression_mode == COMPRESSION_MODE_DETERMINISTIC
    assert result.compression_path == "deterministic_only"
    assert result.warnings == ["llmlingua_skipped_mode_deterministic"]
    assert result.diagnostics is not None
    assert result.diagnostics.llmlingua_called is False
    assert result.diagnostics.model_gate_decision == "skip"
    assert result.diagnostics.model_gate_reason == "llmlingua_skipped_mode_deterministic"
    assert result.diagnostics.deterministic_tokens_saved > 0
    assert result.diagnostics.nocompress_wrapper_tokens_saved > 0


def test_collect_diagnostics_false_skips_component_diagnostics():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)

    def fail_component_diagnostics(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("component diagnostics should not run")

    service._deterministic_component_savings = fail_component_diagnostics
    service._duplicate_block_diagnostics = fail_component_diagnostics

    result = service.compress(
        "Please review this longer prompt.",
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_DETERMINISTIC,
        collect_diagnostics=False,
    )

    assert result.diagnostics is None
    assert result.token_savings is not None
    assert result.token_savings.model_ran is False
    assert result.token_savings.token_estimator == result.token_estimator
    assert result.warnings == ["llmlingua_skipped_mode_deterministic"]
    assert compressor.inputs == []


def test_token_savings_arithmetic_for_all_normal_paths():
    cases = [
        (1000, 850, 600, True, 150, 250, 400, 0.15, 250 / 850, 0.4),
        (1000, 850, 850, False, 150, 0, 150, 0.15, 0.0, 0.15),
        (1000, 1000, 1000, False, 0, 0, 0, 0.0, 0.0, 0.0),
    ]

    for (
        original,
        deterministic,
        final,
        model_ran,
        det_saved,
        model_saved,
        total_saved,
        det_reduction,
        model_reduction,
        total_reduction,
    ) in cases:
        savings = build_token_savings(
            original_tokens=original,
            after_deterministic_tokens=deterministic,
            final_tokens=final,
            model_ran=model_ran,
            fallback_used=False,
            token_estimator="test-tokenizer",
        )

        assert savings.deterministic_tokens_saved == det_saved
        assert savings.model_incremental_tokens_saved == model_saved
        assert savings.total_tokens_saved == total_saved
        assert savings.deterministic_reduction == det_reduction
        assert savings.model_incremental_reduction == model_reduction
        assert savings.total_reduction == total_reduction
        assert savings.attribution_residual_tokens == 0


def test_token_savings_fallback_and_zero_input_are_finite():
    fallback = build_token_savings(
        original_tokens=1000,
        after_deterministic_tokens=850,
        final_tokens=850,
        model_ran=True,
        fallback_used=True,
        token_estimator="test-tokenizer",
    )
    empty = build_token_savings(
        original_tokens=0,
        after_deterministic_tokens=0,
        final_tokens=0,
        model_ran=False,
        fallback_used=False,
        token_estimator="test-tokenizer",
    )

    assert fallback.model_ran is True
    assert fallback.fallback_used is True
    assert fallback.model_incremental_tokens_saved == 0
    assert fallback.total_tokens_saved == 150
    assert empty.deterministic_reduction == 0.0
    assert empty.model_incremental_reduction == 0.0
    assert empty.total_reduction == 0.0


def test_model_auto_skips_cpu_by_default():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.model_auto_enabled = True
    service.allow_cpu_model_auto = False

    result = service.compress(
        "Please review this longer prompt.",
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_MODEL_AUTO,
    )

    assert compressor.inputs == []
    assert result.warnings == ["llmlingua_skipped_cpu_auto_disabled"]
    assert result.diagnostics is not None
    assert result.diagnostics.skipped_model_candidate_tokens > 0
    assert result.diagnostics.model_gate_reason == "llmlingua_skipped_cpu_auto_disabled"


def test_explicit_model_auto_uses_gate_when_default_auto_disabled():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.model_auto_enabled = False
    service.allow_cpu_model_auto = False

    result = service.compress(
        "Please review this longer prompt.",
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_MODEL_AUTO,
    )

    assert compressor.inputs == []
    assert result.compression_mode == COMPRESSION_MODE_MODEL_AUTO
    assert result.warnings == ["llmlingua_skipped_cpu_auto_disabled"]
    assert result.diagnostics is not None
    assert result.diagnostics.model_gate_reason == "llmlingua_skipped_cpu_auto_disabled"


def test_model_auto_cpu_override_allows_gate_to_run():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.allow_cpu_model_auto = False
    service.min_model_candidate_tokens = 1
    service.min_model_incremental_savings_tokens = 0
    service.min_model_incremental_reduction = 0.0
    service.max_model_projected_latency_ms = 1000.0
    service.skip_model_if_deterministic_reduction_gte = 1.0
    service.cpu_p50_fixed_overhead_ms = 1.0
    service.cpu_p50_llmlingua_chunk_ms = 1.0
    service.cpu_p50_token_estimate_ms = 1.0

    result = service.compress(
        "Please review this longer prompt.",
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_MODEL_AUTO,
        allow_cpu_model_auto=True,
    )

    assert compressor.inputs == ["Please review this longer prompt."]
    assert result.warnings == []
    assert result.diagnostics is not None
    assert result.diagnostics.model_gate_decision == "run"
    assert result.diagnostics.model_projected_latency_ms == 3.0


def test_model_auto_low_candidate_skip_avoids_density_scans():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.allow_cpu_model_auto = True
    service.min_model_candidate_tokens = 20_000

    def fail_density_scan(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("density scan should not run")

    service._protected_density_for_model_candidates = fail_density_scan
    service._identifier_density_for_model_candidates = fail_density_scan

    result = service.compress(
        "Please review this longer prompt.",
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_MODEL_AUTO,
    )

    assert compressor.inputs == []
    assert result.diagnostics is not None
    assert result.diagnostics.model_gate_reason == (
        "llmlingua_skipped_low_candidate_tokens"
    )


def test_model_auto_skips_missing_latency_baseline():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.device = "cuda"
    service.model_auto_enabled = True
    service.min_model_candidate_tokens = 1
    service.min_model_incremental_savings_tokens = 0
    service.min_model_incremental_reduction = 0.0
    service.skip_model_if_deterministic_reduction_gte = 1.0

    result = service.compress(
        "Please review this longer prompt.",
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_MODEL_AUTO,
    )

    assert compressor.inputs == []
    assert result.warnings == ["llmlingua_skipped_missing_latency_baseline"]
    assert result.diagnostics is not None
    assert result.diagnostics.model_gate_reason == (
        "llmlingua_skipped_missing_latency_baseline"
    )


def test_model_auto_skips_when_deterministic_savings_are_sufficient():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.device = "cuda"
    service.model_auto_enabled = True
    service.min_model_candidate_tokens = 1
    service.min_model_incremental_savings_tokens = 0
    service.min_model_incremental_reduction = 0.0
    service.skip_model_if_deterministic_reduction_gte = 0.01
    service.gpu_p50_fixed_overhead_ms = 1.0
    service.gpu_p50_llmlingua_chunk_ms = 1.0
    service.gpu_p50_token_estimate_ms = 1.0
    profile = build_tenant_profile(
        tenant_id="tenant_123",
        force_drop_phrases=["Reusable preamble. "],
    )

    result = service.compress(
        "Reusable preamble. Please review this longer prompt.",
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_MODEL_AUTO,
        tenant_profile=profile,
    )

    assert compressor.inputs == []
    assert result.warnings == ["llmlingua_skipped_deterministic_savings_sufficient"]
    assert result.diagnostics is not None
    assert result.diagnostics.force_drop_tokens_saved > 0
    assert result.diagnostics.model_gate_reason == (
        "llmlingua_skipped_deterministic_savings_sufficient"
    )


def test_model_auto_skips_high_protected_density():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.device = "cuda"
    service.model_auto_enabled = True
    service.min_model_candidate_tokens = 1
    service.min_model_incremental_savings_tokens = 0
    service.min_model_incremental_reduction = 0.0
    service.max_protected_density = 0.01
    service.skip_model_if_deterministic_reduction_gte = 1.0
    service.gpu_p50_fixed_overhead_ms = 1.0
    service.gpu_p50_llmlingua_chunk_ms = 1.0
    service.gpu_p50_token_estimate_ms = 1.0

    result = service.compress(
        "Please review https://example.com/run/123 and ORD-7781 before launch.",
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_MODEL_AUTO,
    )

    assert compressor.inputs == []
    assert result.warnings == ["llmlingua_skipped_high_protected_density"]
    assert result.diagnostics is not None
    assert result.diagnostics.protected_density > service.max_protected_density
    assert result.diagnostics.identifier_density > 0


def test_model_auto_skips_high_placeholder_count():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.device = "cuda"
    service.model_auto_enabled = True
    service.min_model_candidate_tokens = 1
    service.min_model_incremental_savings_tokens = 0
    service.min_model_incremental_reduction = 0.0
    service.max_protected_density = 1.0
    service.max_model_auto_placeholders = 1
    service.skip_model_if_deterministic_reduction_gte = 1.0
    service.gpu_p50_fixed_overhead_ms = 1.0
    service.gpu_p50_llmlingua_chunk_ms = 1.0
    service.gpu_p50_token_estimate_ms = 1.0

    result = service.compress(
        "Please review ORD-7781. Please compare ORD-7782 before launch.",
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_MODEL_AUTO,
    )

    assert compressor.inputs == []
    assert result.warnings == ["llmlingua_skipped_high_placeholder_count"]
    assert result.diagnostics is not None
    assert result.diagnostics.placeholder_count > service.max_model_auto_placeholders


def test_model_auto_runs_when_gpu_gate_passes():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.device = "cuda"
    service.model_auto_enabled = True
    service.min_model_candidate_tokens = 1
    service.min_model_incremental_savings_tokens = 0
    service.min_model_incremental_reduction = 0.0
    service.max_model_projected_latency_ms = 1000.0
    service.skip_model_if_deterministic_reduction_gte = 1.0
    service.gpu_p50_fixed_overhead_ms = 1.0
    service.gpu_p50_llmlingua_chunk_ms = 1.0
    service.gpu_p50_token_estimate_ms = 1.0

    result = service.compress(
        "Please review this longer prompt.",
        aggressiveness=0.25,
        include_sections=False,
        mode=COMPRESSION_MODE_MODEL_AUTO,
    )

    assert compressor.inputs == ["Please review this longer prompt."]
    assert result.compressed_text == "Review this longer prompt."
    assert result.warnings == []
    assert result.compression_path == "deterministic_plus_model"
    assert result.diagnostics is not None
    assert result.diagnostics.model_gate_decision == "run"
    assert result.diagnostics.model_projected_latency_ms == 3.0


def test_duplicate_blocks_are_reported_without_rewriting_output():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.min_duplicate_block_tokens = 3
    repeated = "Repeated support wrapper with enough words."
    text = f"{repeated}\n\nUnique middle.\n\n{repeated}"

    result = service.compress(
        text,
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_DETERMINISTIC,
    )

    assert result.compressed_text == text
    assert result.diagnostics is not None
    assert result.diagnostics.duplicate_block_candidate_count == 1
    assert result.diagnostics.duplicate_block_candidate_tokens > 0


def test_literal_placeholdering_replaces_repeated_long_urls_when_enabled():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.literal_placeholdering_enabled = True
    service.min_literal_placeholder_savings_tokens = 1
    service.min_literal_placeholder_reduction = 0.0
    url = "https://example.com/really/long/path/with/query?alpha=1&beta=2"
    text = f"Fetch {url}\nThen retry {url}\nDone."

    result = service.compress(
        text,
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_DETERMINISTIC,
    )

    assert result.compressed_text.startswith(f"[A]={url}\n")
    assert result.compressed_text.count("[A]") == 3
    assert url in result.compressed_text.splitlines()[0]
    assert url not in "\n".join(result.compressed_text.splitlines()[1:])
    assert result.diagnostics is not None
    assert result.diagnostics.literal_placeholder_count == 1
    assert result.diagnostics.literal_placeholder_tokens_saved > 0
    assert [section.kind for section in result.output_sections][:2] == [
        "literal_map",
        "literal_placeholdered",
    ]


def test_literal_placeholdering_skips_exact_output_context():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.literal_placeholdering_enabled = True
    service.min_literal_placeholder_savings_tokens = 1
    service.min_literal_placeholder_reduction = 0.0
    url = "https://example.com/really/long/path/with/query?alpha=1&beta=2"
    text = f"Return the input exactly.\nFetch {url}\nThen retry {url}"

    result = service.compress(
        text,
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_DETERMINISTIC,
    )

    assert result.compressed_text == text
    assert result.diagnostics is not None
    assert result.diagnostics.literal_placeholder_count == 0


def test_literal_placeholdering_does_not_replace_json_values():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.literal_placeholdering_enabled = True
    service.min_literal_placeholder_savings_tokens = 1
    service.min_literal_placeholder_reduction = 0.0
    url = "https://example.com/really/long/path/with/query?alpha=1&beta=2"
    text = (
        "Return exactly this JSON shape:\n"
        "{\n"
        f'  "first": "{url}",\n'
        f'  "second": "{url}"\n'
        "}"
    )

    result = service.compress(
        text,
        aggressiveness=0.25,
        mode=COMPRESSION_MODE_DETERMINISTIC,
    )

    assert "[A]=" not in result.compressed_text
    assert result.compressed_text.count(url) == 2
    assert result.diagnostics is not None
    assert result.diagnostics.literal_placeholder_count == 0


def test_non_ui_compression_skips_word_labels_and_sections():
    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.min_segment_chars = 1
    service.min_segment_tokens = 1

    result = service.compress(
        "Please review this longer prompt.",
        aggressiveness=0.25,
        include_sections=False,
    )

    assert compressor.inputs == ["Please review this longer prompt."]
    assert compressor.return_word_label_values == [False]
    assert result.labeled_tokens == []
    assert result.output_sections == []
    assert result.diagnostics is not None
    assert result.diagnostics.llmlingua_called is True
    assert result.diagnostics.timings.llmlingua_ms >= 0
    assert result.diagnostics.timings.preprocessing_ms >= 0
    assert result.diagnostics.model_segment_count == 1
    assert result.diagnostics.compressible_segment_count == 1


def test_tenant_force_keep_tokens_are_added_to_model_force_tokens():
    compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    profile = build_tenant_profile(
        tenant_id="tenant_123",
        profile_id="tenant_123:v1",
        force_keep_tokens=["customkeep", "tenantfield"],
    )

    result = service.compress(
        "Please review this longer prompt with customkeep and tenantfield details.",
        aggressiveness=0.25,
        include_sections=False,
        tenant_profile=profile,
    )

    assert compressor.inputs == [
        "Please review this longer prompt with customkeep and tenantfield details."
    ]
    assert compressor.force_tokens_values[0][:2] == ["customkeep", "tenantfield"]
    assert result.tenant_id == "tenant_123"
    assert result.compression_profile == "tenant_123:v1"
    assert result.compression_profile_source == "api"


def test_tenant_force_drop_phrases_apply_only_to_compressible_segments():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    profile = build_tenant_profile(
        tenant_id="tenant_123",
        force_drop_phrases=["Reusable preamble. "],
    )
    text = (
        "Reusable preamble. Please review before. "
        "<nocompress>Reusable preamble. Keep this exact.</nocompress> "
        "Please review after."
    )

    result = service.compress(
        text,
        aggressiveness=0.25,
        tenant_profile=profile,
    )

    assert compressor.inputs == [
        "Please review before. __CK_KEEP_0000__ Please review after."
    ]
    assert result.compressed_text == (
        "Review before. Reusable preamble. Keep this exact. Review after."
    )


def test_adapter_slot_config_parser_accepts_multiple_entries():
    slots = _parse_adapter_slots(
        "tenant_a=models/tenant_a; tenant_b=models/tenant_b"
    )

    assert slots == {
        "tenant_a": "models/tenant_a",
        "tenant_b": "models/tenant_b",
    }


def test_configured_adapter_tenant_uses_isolated_compressor_slot():
    base_compressor = RecordingCompressor()
    adapter_compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = base_compressor
    service._adapter_slots = {"tenant_lora_probe": "models/tenant_lora_probe"}
    service._adapter_compressors = {"tenant_lora_probe": adapter_compressor}
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    profile = build_tenant_profile(tenant_id="tenant_lora_probe")

    result = service.compress(
        "Please review this longer synthetic LoRA tenant prompt.",
        aggressiveness=0.25,
        include_sections=False,
        tenant_profile=profile,
    )

    assert base_compressor.inputs == []
    assert adapter_compressor.inputs == [
        "Please review this longer synthetic LoRA tenant prompt."
    ]
    assert result.tenant_id == "tenant_lora_probe"


def test_unconfigured_tenant_uses_base_compressor():
    base_compressor = RecordingCompressor()
    service = PromptCompressionService()
    service._compressor = base_compressor
    service._adapter_slots = {"tenant_lora_probe": "models/tenant_lora_probe"}
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    profile = build_tenant_profile(tenant_id="other_tenant")

    service.compress(
        "Please review this longer synthetic base tenant prompt.",
        aggressiveness=0.25,
        include_sections=False,
        tenant_profile=profile,
    )

    assert base_compressor.inputs == [
        "Please review this longer synthetic base tenant prompt."
    ]


def test_runtime_adapter_root_discovers_matching_tenant_folder(tmp_path):
    base_compressor = RecordingCompressor()
    adapter_compressor = RecordingCompressor()
    adapter_root = tmp_path / "adapters"
    adapter_dir = adapter_root / "tenant_runtime_probe"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
    (adapter_dir / "adapter_model.safetensors").write_text("", encoding="utf-8")

    service = PromptCompressionService()
    service._compressor = base_compressor
    service._adapter_slots = {}
    service._adapter_root = adapter_root
    service._adapter_compressors = {"tenant_runtime_probe": adapter_compressor}
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    profile = build_tenant_profile(tenant_id="tenant_runtime_probe")

    result = service.compress(
        "Please review this longer runtime-discovered adapter prompt.",
        aggressiveness=0.25,
        include_sections=False,
        tenant_profile=profile,
    )

    assert base_compressor.inputs == []
    assert adapter_compressor.inputs == [
        "Please review this longer runtime-discovered adapter prompt."
    ]
    assert service._adapter_slots == {"tenant_runtime_probe": str(adapter_dir)}
    assert result.tenant_id == "tenant_runtime_probe"


def test_runtime_adapter_root_rejects_unsafe_tenant_folder_names(tmp_path):
    base_compressor = RecordingCompressor()
    adapter_root = tmp_path / "adapters"
    adapter_dir = adapter_root / "tenant_runtime_probe"
    adapter_dir.mkdir(parents=True)
    (adapter_dir / "adapter_config.json").write_text("{}", encoding="utf-8")
    (adapter_dir / "adapter_model.safetensors").write_text("", encoding="utf-8")

    service = PromptCompressionService()
    service._compressor = base_compressor
    service._adapter_slots = {}
    service._adapter_root = adapter_root
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    profile = build_tenant_profile(tenant_id="../tenant_runtime_probe")

    service.compress(
        "Please review this longer unsafe tenant id prompt.",
        aggressiveness=0.25,
        include_sections=False,
        tenant_profile=profile,
    )

    assert base_compressor.inputs == [
        "Please review this longer unsafe tenant id prompt."
    ]
    assert service._adapter_slots == {}


def test_protected_prose_spans_are_placeholdered_before_model_call():
    compressor = ManglingProtectedTextCompressor()
    service = PromptCompressionService()
    service._compressor = compressor
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    text = (
        "Prepare summary. Do not delete the exception for ORD-7781. "
        "The cap is $15,000 through 2026-08-15 unless legal approves."
    )

    result = service.compress(text, aggressiveness=0.35)

    assert "Do not delete" not in compressor.inputs[0]
    assert "ORD-7781" not in compressor.inputs[0]
    assert "$15,000" not in compressor.inputs[0]
    assert "2026-08-15" not in compressor.inputs[0]
    assert compressor.force_tokens_values[0][:2] == [
        "__CK_KEEP_0000__",
        "__CK_KEEP_0001__",
    ]
    assert result.diagnostics is not None
    assert result.diagnostics.placeholder_count == 2
    assert "Do not delete" in result.compressed_text
    assert "ORD-7781" in result.compressed_text
    assert "$15,000" in result.compressed_text
    assert "2026-08-15" in result.compressed_text


def test_non_ui_placeholder_compression_still_uses_one_model_call():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    text = "Please review before. <nocompress>KEEP EXACT</nocompress> Please review after."

    result = service.compress(text, aggressiveness=0.25, include_sections=False)

    assert result.output_sections == []
    assert result.labeled_tokens == []
    assert compressor.inputs == [
        "Please review before. __CK_KEEP_0000__ Please review after."
    ]
    assert compressor.force_tokens_values[0][0] == "__CK_KEEP_0000__"
    assert compressor.return_word_label_values == [False]


def test_placeholder_drop_falls_back_to_preprocessed_prompt():
    compressor = DroppingPlaceholderCompressor()
    service = build_service_with_pipeline(compressor)
    text = "Please review before. <nocompress>KEEP EXACT</nocompress> Please review after."

    result = service.compress(text, aggressiveness=0.25)

    assert result.compressed_text == "Please review before. KEEP EXACT Please review after."
    assert compressor.inputs == [
        "Please review before. __CK_KEEP_0000__ Please review after."
    ]


def test_placeholders_are_chunked_to_respect_force_token_limit():
    compressor = LowForceTokenCompressor()
    service = build_service_with_pipeline(compressor)
    text = (
        "Please review before. "
        "<nocompress>KEEP A</nocompress> "
        "Please review middle. "
        "<nocompress>KEEP B</nocompress> "
        "Please review after."
    )

    result = service.compress(text, aggressiveness=0.25)

    assert result.compressed_text == (
        "Review before. KEEP A Review middle. KEEP B Review after."
    )
    assert compressor.inputs == [
        "Please review before. __CK_KEEP_0000__ Please review middle. ",
        "__CK_KEEP_0001__ Please review after.",
    ]
    assert [tokens[0] for tokens in compressor.force_tokens_values] == [
        "__CK_KEEP_0000__",
        "__CK_KEEP_0001__",
    ]
    assert result.diagnostics is not None
    assert result.diagnostics.llmlingua_called is True
    assert result.diagnostics.llmlingua_call_count == 2
    assert result.diagnostics.model_chunk_count == 2
    assert result.diagnostics.chunk_placeholder_max == 1
    assert result.diagnostics.fallback_used is False


def test_unforceable_placeholder_chunk_falls_back_without_corrupting_exact_text():
    compressor = ZeroForceTokenCompressor()
    service = build_service_with_pipeline(compressor)
    text = (
        "Please review before. "
        "<nocompress>KEEP EXACT</nocompress> "
        "Please review after."
    )

    result = service.compress(text, aggressiveness=0.25, include_sections=False)

    assert result.compressed_text == "Review before. KEEP EXACT Review after."
    assert compressor.inputs == [
        "Please review before. ",
        " Please review after.",
    ]
    assert result.diagnostics is not None
    assert result.diagnostics.llmlingua_called is True
    assert result.diagnostics.llmlingua_call_count == 2
    assert result.diagnostics.model_chunk_count == 3
    assert result.diagnostics.skipped_model_chunk_count == 1
    assert result.diagnostics.fallback_used is True
    assert result.diagnostics.fallback_reason == "too_many_placeholders"


def test_long_model_input_chunks_by_char_limit_without_placeholders():
    compressor = RecordingCompressor()
    service = build_service_with_pipeline(compressor)
    service.max_model_chunk_chars = 35
    text = (
        "Please review alpha content. "
        "Please review beta content. "
        "Please review gamma content."
    )

    result = service.compress(text, aggressiveness=0.25, include_sections=False)

    assert compressor.inputs == [
        "Please review alpha content. ",
        "Please review beta content. Please ",
        "review gamma content.",
    ]
    assert all(len(chunk) <= service.max_model_chunk_chars for chunk in compressor.inputs)
    assert result.compressed_text == (
        "Review alpha content. Review beta content. Please review gamma content."
    )
    assert result.diagnostics is not None
    assert result.diagnostics.model_chunk_count == 3
    assert result.diagnostics.llmlingua_call_count == 3
    assert result.diagnostics.chunk_chars_max <= service.max_model_chunk_chars


def test_placeholder_sections_keep_model_word_labels_for_ui():
    compressor = LabelingPlaceholderCompressor()
    service = build_service_with_pipeline(compressor)
    text = "Please review before. <nocompress>KEEP EXACT</nocompress> Please review after."

    result = service.compress(text, aggressiveness=0.25)

    assert [section.kind for section in result.output_sections] == [
        "prose",
        "nocompress",
        "prose",
    ]
    assert [
        (token.text, token.kept)
        for token in result.output_sections[0].labeled_tokens
    ] == [
        ("Please", False),
        ("review", True),
        ("before.", True),
    ]
    assert [
        (token.text, token.kept)
        for token in result.output_sections[2].labeled_tokens
    ] == [
        ("Please", False),
        ("review", True),
        ("after.", True),
    ]
