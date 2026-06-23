from app import main
from app.compressor import CompressionResult, CompressionToken
from app.schemas import CompressRequest


class FakeCompressionService:
    model_name = "fake-model"
    is_loaded = True

    def compress(self, text: str, aggressiveness: float) -> CompressionResult:
        return CompressionResult(
            compressed_text="Prompts code.",
            original_tokens=4,
            compressed_tokens=2,
            reduction=0.5,
            aggressiveness=aggressiveness,
            target_rate=0.75,
            model=self.model_name,
            elapsed_ms=12.5,
            labeled_tokens=[
                CompressionToken(text="Prompts", kept=True),
                CompressionToken(text="are", kept=False),
                CompressionToken(text="code.", kept=True),
            ],
        )


def test_index_returns_prompt_compression_ui():
    response = main.index()

    assert "Prompt Compression" in response
    assert "Dropped Words Highlighted" in response


def test_compress_response_includes_labeled_tokens(monkeypatch):
    monkeypatch.setattr(main, "compression_service", FakeCompressionService())

    response = main.compress(
        CompressRequest(text="Prompts are code.", aggressiveness=0.25)
    )

    assert [token.model_dump() for token in response.labeled_tokens] == [
        {"text": "Prompts", "kept": True},
        {"text": "are", "kept": False},
        {"text": "code.", "kept": True},
    ]
