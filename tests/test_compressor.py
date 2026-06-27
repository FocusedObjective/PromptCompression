from app.compressor import PromptCompressionService
from app.tenant_profiles import build_tenant_profile
from app.token_estimator import estimate_token_count
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
    assert compressor.force_tokens_values[0][:4] == [
        "__CK_KEEP_0000__",
        "__CK_KEEP_0001__",
        "__CK_KEEP_0002__",
        "__CK_KEEP_0003__",
    ]
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


def test_too_many_placeholders_skip_model_for_safety():
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
        "Please review before. KEEP A Please review middle. KEEP B Please review after."
    )
    assert compressor.inputs == []


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
