import os
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from app.compression_pipeline import CompressionSegment, PromptPreprocessor
from app.protected_spans import force_tokens_for_text

DEFAULT_MODEL = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"


class CompressionRuntimeError(RuntimeError):
    """Raised when the compression backend is unavailable or fails."""


@dataclass(frozen=True)
class CompressionToken:
    text: str
    kept: bool


@dataclass(frozen=True)
class CompressionOutputSection:
    text: str
    kind: str
    compressed: bool
    protected: bool
    labeled_tokens: list[CompressionToken] = field(default_factory=list)


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
    labeled_tokens: list[CompressionToken]
    output_sections: list[CompressionOutputSection] = field(default_factory=list)


class PromptCompressionService:
    def __init__(self) -> None:
        self.model_name = os.getenv("COMPRESSOR_MODEL", DEFAULT_MODEL)
        self.device = os.getenv("COMPRESSOR_DEVICE", "cpu")
        self.min_rate = float(os.getenv("COMPRESSOR_MIN_RATE", "0.45"))
        self._compressor: Any | None = None
        self._lock = Lock()
        self.preprocessor = PromptPreprocessor()

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

    def parse_word_labels(
        self,
        labeled_prompt: str,
        word_sep: str = "\t\t|\t\t",
        label_sep: str = " ",
    ) -> list[CompressionToken]:
        tokens: list[CompressionToken] = []
        if not labeled_prompt:
            return tokens

        for entry in labeled_prompt.split(word_sep):
            word, separator, label = entry.rpartition(label_sep)
            if not separator or not word:
                continue
            tokens.append(CompressionToken(text=word, kept=label.strip() == "1"))

        return tokens

    def _compress_segment(
        self,
        compressor: Any,
        segment: CompressionSegment,
        target_rate: float,
    ) -> tuple[str, list[CompressionToken]]:
        try:
            raw_result = compressor.compress_prompt_llmlingua2(
                segment.text,
                rate=target_rate,
                force_tokens=force_tokens_for_text(segment.text),
                return_word_label=True,
            )
        except Exception as exc:  # pragma: no cover - model-specific runtime path
            raise CompressionRuntimeError(f"Compression failed: {exc}") from exc

        compressed_text = raw_result.get("compressed_prompt", "")
        labeled_tokens = self.parse_word_labels(
            raw_result.get("fn_labeled_original_prompt", "")
        )
        return compressed_text, labeled_tokens

    def _kept_segment_token(self, segment: CompressionSegment) -> CompressionToken:
        return CompressionToken(text=segment.text, kept=True)

    def compress(self, text: str, aggressiveness: float) -> CompressionResult:
        start = time.perf_counter()
        target_rate = self.target_rate_for_aggressiveness(aggressiveness)
        segments = self.preprocessor.prepare(text)
        compressible_segments = [
            segment for segment in segments if segment.compressible and segment.text.strip()
        ]

        compressor = self._load() if compressible_segments else None
        output_parts: list[str] = []
        labeled_tokens: list[CompressionToken] = []
        output_sections: list[CompressionOutputSection] = []

        for segment in segments:
            if not segment.compressible or not segment.text.strip():
                output_parts.append(segment.text)
                token = self._kept_segment_token(segment)
                labeled_tokens.append(token)
                output_sections.append(
                    CompressionOutputSection(
                        text=segment.text,
                        kind=segment.kind,
                        compressed=segment.kind in {"html", "toon"},
                        protected=not segment.compressible,
                        labeled_tokens=[token],
                    )
                )
                continue

            compressed_part, segment_labels = self._compress_segment(
                compressor,
                segment,
                target_rate,
            )
            output_parts.append(compressed_part)
            labeled_tokens.extend(segment_labels)
            output_sections.append(
                CompressionOutputSection(
                    text=compressed_part,
                    kind=segment.kind,
                    compressed=True,
                    protected=False,
                    labeled_tokens=segment_labels,
                )
            )

        compressed_text = "".join(output_parts)
        original_tokens = len(text.split())
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
            labeled_tokens=labeled_tokens,
            output_sections=output_sections,
        )
