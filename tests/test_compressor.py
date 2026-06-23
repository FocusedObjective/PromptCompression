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
