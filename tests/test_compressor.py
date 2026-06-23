from app.compressor import PromptCompressionService


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
