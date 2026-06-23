from app.compressor import PromptCompressionService
from app.token_estimator import estimate_token_count


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
