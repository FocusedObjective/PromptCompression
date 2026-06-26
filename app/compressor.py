import os
import time
from dataclasses import dataclass, field
import logging
from threading import Lock
from typing import Any

from app.compression_pipeline import CompressionSegment, PromptPreprocessor
from app.protected_spans import force_tokens_for_text, protected_spans_for_text
from app.token_estimator import estimate_token_count

DEFAULT_MODEL = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"
LOGGER = logging.getLogger(__name__)
MIN_SEGMENT_CHARS = int(os.getenv("COMPRESSOR_MIN_SEGMENT_CHARS", "160"))
MIN_SEGMENT_TOKENS = int(os.getenv("COMPRESSOR_MIN_SEGMENT_TOKENS", "24"))
PLACEHOLDER_PREFIX = "__CK_KEEP_"
PLACEHOLDER_SUFFIX = "__"
DEFAULT_MAX_FORCE_TOKENS = 100


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


@dataclass(frozen=True)
class _CompressionPlaceholder:
    token: str
    segment: CompressionSegment


@dataclass(frozen=True)
class _PreparedModelInput:
    text: str
    placeholders: list[_CompressionPlaceholder]


@dataclass(frozen=True)
class _ExpandedCompression:
    text: str
    labeled_tokens: list[CompressionToken]
    output_sections: list[CompressionOutputSection]


class PromptCompressionService:
    def __init__(self) -> None:
        self.model_name = os.getenv("COMPRESSOR_MODEL", DEFAULT_MODEL)
        self.device = os.getenv("COMPRESSOR_DEVICE", "cpu")
        self.min_rate = float(os.getenv("COMPRESSOR_MIN_RATE", "0.45"))
        self.min_segment_chars = max(0, MIN_SEGMENT_CHARS)
        self.min_segment_tokens = max(0, MIN_SEGMENT_TOKENS)
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
                LOGGER.exception("Failed to import llmlingua")
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
                LOGGER.exception("Failed to load compression model %s", self.model_name)
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

    def _kept_segment_token(self, segment: CompressionSegment) -> CompressionToken:
        return CompressionToken(text=segment.text, kept=True)

    def _max_force_tokens(self, compressor: Any) -> int:
        try:
            return max(
                0,
                int(getattr(compressor, "max_force_token", DEFAULT_MAX_FORCE_TOKENS)),
            )
        except (TypeError, ValueError):
            return DEFAULT_MAX_FORCE_TOKENS

    def _placeholder_for_index(self, index: int) -> str:
        return f"{PLACEHOLDER_PREFIX}{index:04d}{PLACEHOLDER_SUFFIX}"

    def _prepare_model_input(
        self,
        segments: list[CompressionSegment],
        should_compress_segments: list[bool],
    ) -> _PreparedModelInput:
        source_text = "".join(segment.text for segment in segments)
        parts: list[str] = []
        placeholders: list[_CompressionPlaceholder] = []
        next_placeholder_index = 0

        def next_placeholder() -> str:
            nonlocal next_placeholder_index
            placeholder = self._placeholder_for_index(next_placeholder_index)
            while placeholder in source_text:
                next_placeholder_index += 1
                placeholder = self._placeholder_for_index(next_placeholder_index)
            next_placeholder_index += 1
            return placeholder

        def append_placeholder(segment: CompressionSegment) -> None:
            placeholder = next_placeholder()
            parts.append(placeholder)
            placeholders.append(_CompressionPlaceholder(placeholder, segment))

        for segment, should_compress in zip(
            segments,
            should_compress_segments,
            strict=True,
        ):
            if should_compress:
                cursor = 0
                for span in protected_spans_for_text(segment.text):
                    parts.append(segment.text[cursor:span.start])
                    append_placeholder(
                        CompressionSegment(
                            text=span.text,
                            compressible=False,
                            kind="protected",
                        )
                    )
                    cursor = span.end
                parts.append(segment.text[cursor:])
                continue

            append_placeholder(segment)

        return _PreparedModelInput(
            text="".join(parts),
            placeholders=placeholders,
        )

    def _force_tokens_for_model_input(
        self,
        text: str,
        required_tokens: list[str],
        max_tokens: int,
    ) -> list[str]:
        tokens: list[str] = []
        seen: set[str] = set()

        def add_token(value: str) -> None:
            if len(tokens) >= max_tokens:
                return
            if value and value not in seen:
                seen.add(value)
                tokens.append(value)

        for token in required_tokens:
            add_token(token)

        for token in force_tokens_for_text(text, max_tokens=max_tokens):
            add_token(token)

        return tokens

    def _has_valid_placeholders(
        self,
        compressed_text: str,
        placeholders: list[_CompressionPlaceholder],
    ) -> bool:
        cursor = 0
        for placeholder in placeholders:
            if compressed_text.count(placeholder.token) != 1:
                return False
            position = compressed_text.find(placeholder.token, cursor)
            if position < cursor:
                return False
            cursor = position + len(placeholder.token)
        return True

    def _section_for_uncompressed_segment(
        self,
        segment: CompressionSegment,
        segment_labels: list[CompressionToken],
    ) -> CompressionOutputSection:
        return CompressionOutputSection(
            text=segment.text,
            kind=segment.kind,
            compressed=segment.kind == "toon",
            protected=not segment.compressible,
            labeled_tokens=segment_labels,
        )

    def _uncompressed_segment_labels(
        self,
        segment: CompressionSegment,
        include_sections: bool,
    ) -> list[CompressionToken]:
        if (
            include_sections
            and segment.text.strip()
            and (segment.compressible or segment.kind == "protected")
        ):
            return [self._kept_segment_token(segment)]
        return []

    def _uncompressed_result_parts(
        self,
        segments: list[CompressionSegment],
        include_sections: bool,
    ) -> _ExpandedCompression:
        output_parts: list[str] = []
        labeled_tokens: list[CompressionToken] = []
        output_sections: list[CompressionOutputSection] = []

        for segment in segments:
            output_parts.append(segment.text)
            segment_labels = self._uncompressed_segment_labels(
                segment,
                include_sections=include_sections,
            )
            labeled_tokens.extend(segment_labels)
            if include_sections:
                output_sections.append(
                    self._section_for_uncompressed_segment(segment, segment_labels)
                )

        return _ExpandedCompression(
            text="".join(output_parts),
            labeled_tokens=labeled_tokens,
            output_sections=output_sections,
        )

    def _label_chunks_for_placeholders(
        self,
        labeled_tokens: list[CompressionToken],
        placeholders: list[_CompressionPlaceholder],
    ) -> list[list[CompressionToken]] | None:
        chunks: list[list[CompressionToken]] = []
        cursor = 0

        for placeholder in placeholders:
            position = next(
                (
                    index
                    for index in range(cursor, len(labeled_tokens))
                    if labeled_tokens[index].text == placeholder.token
                ),
                None,
            )
            if position is None:
                return None
            chunks.append(labeled_tokens[cursor:position])
            cursor = position + 1

        chunks.append(labeled_tokens[cursor:])
        return chunks

    def _append_compressed_prose_section(
        self,
        chunk: str,
        labeled_tokens: list[CompressionToken],
        output_sections: list[CompressionOutputSection],
        include_sections: bool,
        section_labels: list[CompressionToken] | None = None,
    ) -> None:
        if not include_sections or not chunk:
            return
        labels = section_labels or [CompressionToken(text=chunk, kept=True)]
        labeled_tokens.extend(labels)
        output_sections.append(
            CompressionOutputSection(
                text=chunk,
                kind="prose",
                compressed=True,
                protected=False,
                labeled_tokens=labels,
            )
        )

    def _expand_compressed_model_text(
        self,
        compressed_text: str,
        placeholders: list[_CompressionPlaceholder],
        include_sections: bool,
        model_labeled_tokens: list[CompressionToken],
    ) -> _ExpandedCompression:
        if not placeholders:
            labeled_tokens: list[CompressionToken] = []
            output_sections: list[CompressionOutputSection] = []
            self._append_compressed_prose_section(
                compressed_text,
                labeled_tokens,
                output_sections,
                include_sections=include_sections,
                section_labels=model_labeled_tokens,
            )
            return _ExpandedCompression(
                text=compressed_text,
                labeled_tokens=labeled_tokens,
                output_sections=output_sections,
            )

        output_parts: list[str] = []
        labeled_tokens = []
        output_sections = []
        label_chunks = self._label_chunks_for_placeholders(
            model_labeled_tokens,
            placeholders,
        )
        cursor = 0

        for index, placeholder in enumerate(placeholders):
            position = compressed_text.find(placeholder.token, cursor)
            chunk = compressed_text[cursor:position]
            output_parts.append(chunk)
            self._append_compressed_prose_section(
                chunk,
                labeled_tokens,
                output_sections,
                include_sections=include_sections,
                section_labels=(
                    None if label_chunks is None else label_chunks[index]
                ),
            )

            segment = placeholder.segment
            output_parts.append(segment.text)
            segment_labels = self._uncompressed_segment_labels(
                segment,
                include_sections=include_sections,
            )
            labeled_tokens.extend(segment_labels)
            if include_sections:
                output_sections.append(
                    self._section_for_uncompressed_segment(segment, segment_labels)
                )
            cursor = position + len(placeholder.token)

        tail = compressed_text[cursor:]
        output_parts.append(tail)
        self._append_compressed_prose_section(
            tail,
            labeled_tokens,
            output_sections,
            include_sections=include_sections,
            section_labels=None if label_chunks is None else label_chunks[-1],
        )

        return _ExpandedCompression(
            text="".join(output_parts),
            labeled_tokens=labeled_tokens,
            output_sections=output_sections,
        )

    def _compress_prepared_model_input(
        self,
        compressor: Any,
        prepared: _PreparedModelInput,
        target_rate: float,
        include_sections: bool,
    ) -> _ExpandedCompression | None:
        required_tokens = [placeholder.token for placeholder in prepared.placeholders]
        max_force_tokens = self._max_force_tokens(compressor)
        if len(required_tokens) > max_force_tokens:
            LOGGER.warning(
                "Skipping model compression because %s placeholders exceed "
                "max_force_token=%s",
                len(required_tokens),
                max_force_tokens,
            )
            return None

        force_tokens = self._force_tokens_for_model_input(
            prepared.text,
            required_tokens=required_tokens,
            max_tokens=max_force_tokens,
        )

        try:
            raw_result = compressor.compress_prompt_llmlingua2(
                prepared.text,
                rate=target_rate,
                force_tokens=force_tokens,
                return_word_label=include_sections,
            )
        except Exception as exc:  # pragma: no cover - model-specific runtime path
            message = str(exc) or exc.__class__.__name__
            raise CompressionRuntimeError(f"Compression failed: {message}") from exc

        compressed_model_text = raw_result.get("compressed_prompt", "")
        if not self._has_valid_placeholders(
            compressed_model_text,
            prepared.placeholders,
        ):
            LOGGER.warning("Skipping compressed output because placeholders changed")
            return None

        model_labeled_tokens: list[CompressionToken] = []
        if include_sections:
            model_labeled_tokens = self.parse_word_labels(
                raw_result.get("fn_labeled_original_prompt", "")
            )

        return self._expand_compressed_model_text(
            compressed_model_text,
            prepared.placeholders,
            include_sections=include_sections,
            model_labeled_tokens=model_labeled_tokens,
        )

    def _should_compress_segment(
        self,
        segment: CompressionSegment,
        target_rate: float,
    ) -> bool:
        if not segment.compressible or not segment.text.strip():
            return False
        if target_rate >= 1.0:
            return False
        if len(segment.text.strip()) < self.min_segment_chars:
            return False
        return estimate_token_count(segment.text) >= self.min_segment_tokens

    def compress(
        self,
        text: str,
        aggressiveness: float,
        include_sections: bool = True,
    ) -> CompressionResult:
        start = time.perf_counter()
        target_rate = self.target_rate_for_aggressiveness(aggressiveness)
        segments = self.preprocessor.prepare(text)
        should_compress_segments = [
            self._should_compress_segment(segment, target_rate)
            for segment in segments
        ]
        compressible_segments = [
            segment
            for segment, should_compress in zip(
                segments,
                should_compress_segments,
                strict=True,
            )
            if should_compress
        ]

        if compressible_segments:
            compressor = self._load()
            prepared = self._prepare_model_input(segments, should_compress_segments)
            expanded = self._compress_prepared_model_input(
                compressor,
                prepared,
                target_rate,
                include_sections=include_sections,
            )
            if expanded is None:
                expanded = self._uncompressed_result_parts(
                    segments,
                    include_sections=include_sections,
                )
        else:
            expanded = self._uncompressed_result_parts(
                segments,
                include_sections=include_sections,
            )

        compressed_text = expanded.text
        original_tokens = estimate_token_count(text)
        compressed_tokens = (
            original_tokens
            if compressed_text == text
            else estimate_token_count(compressed_text)
        )

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
            labeled_tokens=expanded.labeled_tokens,
            output_sections=expanded.output_sections,
        )
