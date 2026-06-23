import os
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

from app.protected_spans import force_tokens_for_text

DEFAULT_MODEL = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"


class CompressionRuntimeError(RuntimeError):
    """Raised when the compression backend is unavailable or fails."""


@dataclass(frozen=True)
class CompressionResult:
    compressed_text: str
    original_tokens: int
    compressed_tokens: int
    reduction: float
    aggressiveness: float
    target_rate: float
    model: str
    elapsed_ms: float


class PromptCompressionService:
    def __init__(self) -> None:
        self.model_name = os.getenv("COMPRESSOR_MODEL", DEFAULT_MODEL)
        self.device = os.getenv("COMPRESSOR_DEVICE", "cpu")
        self.min_rate = float(os.getenv("COMPRESSOR_MIN_RATE", "0.45"))
        self._compressor: Any | None = None
        self._lock = Lock()

    @property
    def is_loaded(self) -> bool:
        return self._compressor is not None

    def _load(self) -> Any:
        if self._compressor is not None:
            return self._compressor

        with self._lock:
            if self._compressor is not None:
                return self._compressor

            try:
                from llmlingua import PromptCompressor
            except ImportError as exc:
                raise CompressionRuntimeError(
                    "llmlingua is not installed. Run `pip install -r requirements.txt`."
                ) from exc

            try:
                self._compressor = PromptCompressor(
                    model_name=self.model_name,
                    device_map=self.device,
                    use_llmlingua2=True,
                )
            except Exception as exc:  # pragma: no cover - depends on network/model cache
                raise CompressionRuntimeError(
                    "Failed to load the compression model. The first run needs network access "
                    "to download the Hugging Face checkpoint."
                ) from exc

            return self._compressor

    def target_rate_for_aggressiveness(self, aggressiveness: float) -> float:
        bounded = max(0.0, min(1.0, aggressiveness))
        min_rate = max(0.05, min(1.0, self.min_rate))
        if bounded == 0.0:
            return 1.0
        if bounded == 1.0:
            return min_rate
        return 1.0 - bounded * (1.0 - min_rate)

    def compress(self, text: str, aggressiveness: float) -> CompressionResult:
        start = time.perf_counter()
        compressor = self._load()
        target_rate = self.target_rate_for_aggressiveness(aggressiveness)

        try:
            raw_result = compressor.compress_prompt_llmlingua2(
                text,
                rate=target_rate,
                force_tokens=force_tokens_for_text(text),
                return_word_label=True,
            )
        except Exception as exc:  # pragma: no cover - model-specific runtime path
            raise CompressionRuntimeError(f"Compression failed: {exc}") from exc

        compressed_text = raw_result.get("compressed_prompt", "")
        original_tokens = int(raw_result.get("origin_tokens", 0))
        compressed_tokens = int(raw_result.get("compressed_tokens", 0))

        if original_tokens <= 0:
            original_tokens = len(text.split())
        if compressed_tokens <= 0:
            compressed_tokens = len(compressed_text.split())

        reduction = 0.0
        if original_tokens:
            reduction = max(0.0, 1.0 - (compressed_tokens / original_tokens))

        return CompressionResult(
            compressed_text=compressed_text,
            original_tokens=original_tokens,
            compressed_tokens=compressed_tokens,
            reduction=reduction,
            aggressiveness=max(0.0, min(1.0, aggressiveness)),
            target_rate=target_rate,
            model=self.model_name,
            elapsed_ms=(time.perf_counter() - start) * 1000,
        )
