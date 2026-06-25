from typing import Any

from app.compression_pipeline import PromptPreprocessor
from app.compressor import PromptCompressionService


class RecordingCompressor:
    def __init__(self) -> None:
        self.inputs: list[str] = []
        self.force_tokens_values: list[list[str]] = []
        self.return_word_label_values: list[bool] = []

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
            "fn_labeled_original_prompt": "Review 1",
        }


def fake_toon_encoder(value: Any) -> str:
    assert isinstance(value, dict)
    return "users[3]{id,name,role}:\n  1,Alice,admin\n  2,Bob,user\n  3,Cora,user"


def build_service_with_pipeline(
    compressor: RecordingCompressor,
    toon_encoder=fake_toon_encoder,
) -> PromptCompressionService:
    service = PromptCompressionService()
    service._compressor = compressor
    service.preprocessor = PromptPreprocessor(
        toon_encoder=toon_encoder,
        min_json_chars=1,
        min_json_lines=1,
        min_toon_savings=0.0,
    )
    service.min_segment_chars = 1
    service.min_segment_tokens = 1
    return service
