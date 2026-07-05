import os
import time
from dataclasses import dataclass, field
import logging
from pathlib import Path
import re
from threading import Lock
from typing import Any

from app.compression_pipeline import CompressionSegment, PromptPreprocessor
from app.protected_spans import (
    ProtectedSpan,
    force_tokens_for_text,
    protected_spans_for_text,
)
from app.tenant_profiles import (
    DEFAULT_PROFILE_ID,
    DEFAULT_PROFILE_SOURCE,
    DEFAULT_TENANT_ID,
    TenantCompressionProfile,
)
from app.token_estimator import (
    REGEX_TOKEN_ESTIMATOR,
    TokenEstimate,
    estimate_huggingface_tokens,
    merge_token_estimator_names,
)

DEFAULT_MODEL = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"
LOGGER = logging.getLogger(__name__)
MIN_SEGMENT_CHARS = int(os.getenv("COMPRESSOR_MIN_SEGMENT_CHARS", "160"))
MIN_SEGMENT_TOKENS = int(os.getenv("COMPRESSOR_MIN_SEGMENT_TOKENS", "24"))
PLACEHOLDER_PREFIX = "__CK_KEEP_"
PLACEHOLDER_SUFFIX = "__"
DEFAULT_MAX_FORCE_TOKENS = 100
DEFAULT_PLACEHOLDER_CHUNK_TARGET = int(
    os.getenv("COMPRESSOR_PLACEHOLDER_CHUNK_TARGET", "80")
)
DEFAULT_MODEL_CHUNK_CHARS = int(os.getenv("COMPRESSOR_MODEL_CHUNK_CHARS", "24000"))
PROTECTED_SPAN_COALESCE_GAP_CHARS = int(
    os.getenv("COMPRESSOR_PROTECTED_SPAN_COALESCE_GAP_CHARS", "24")
)
PROTECTED_SPAN_COALESCE_MAX_CHARS = int(
    os.getenv("COMPRESSOR_PROTECTED_SPAN_COALESCE_MAX_CHARS", "240")
)
BASE_SLOT_ID = "base"
ADAPTER_SLOT_ENV = "COMPRESSOR_ADAPTER_SLOTS"
ADAPTER_ROOT_ENV = "COMPRESSOR_ADAPTER_ROOT"
PRELOAD_SLOT_ENV = "COMPRESSOR_PRELOAD_SLOTS"
ADAPTER_MODEL_FILENAMES = ("adapter_model.safetensors", "adapter_model.bin")
ADAPTER_SLOT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
TIMED_PHASES = (
    "target_rate_ms",
    "preprocessing_ms",
    "force_drop_ms",
    "segment_selection_ms",
    "model_load_ms",
    "model_input_ms",
    "force_tokens_ms",
    "llmlingua_ms",
    "placeholder_validation_ms",
    "model_expand_ms",
    "uncompressed_expand_ms",
    "token_estimate_ms",
)


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
    tenant_id: str = DEFAULT_TENANT_ID
    compression_profile: str = DEFAULT_PROFILE_ID
    compression_profile_source: str = DEFAULT_PROFILE_SOURCE
    training_sample_recorded: bool = False
    token_estimator: str = REGEX_TOKEN_ESTIMATOR
    diagnostics: "CompressionDiagnostics | None" = None


@dataclass(frozen=True)
class CompressionTiming:
    total_ms: float
    target_rate_ms: float
    preprocessing_ms: float
    force_drop_ms: float
    segment_selection_ms: float
    model_load_ms: float
    model_input_ms: float
    force_tokens_ms: float
    llmlingua_ms: float
    placeholder_validation_ms: float
    model_expand_ms: float
    uncompressed_expand_ms: float
    token_estimate_ms: float
    other_ms: float


@dataclass(frozen=True)
class CompressionDiagnostics:
    timings: CompressionTiming
    input_chars: int
    output_chars: int
    segment_count: int
    compressible_segment_count: int
    model_segment_count: int
    skipped_segment_count: int
    placeholder_count: int
    model_input_chars: int
    segment_kinds: dict[str, int]
    llmlingua_called: bool
    fallback_used: bool
    fallback_reason: str | None = None
    model_chunk_count: int = 0
    llmlingua_call_count: int = 0
    skipped_model_chunk_count: int = 0
    chunk_placeholder_max: int = 0
    chunk_placeholder_avg: float = 0.0
    chunk_chars_max: int = 0


@dataclass(frozen=True)
class _CompressionPlaceholder:
    token: str
    segment: CompressionSegment


@dataclass(frozen=True)
class _PreparedModelInput:
    text: str
    placeholders: list[_CompressionPlaceholder]


@dataclass(frozen=True)
class _ModelInputChunk:
    text: str
    placeholders: list[_CompressionPlaceholder]


@dataclass(frozen=True)
class _ChunkedCompressionStats:
    chunk_count: int = 0
    llmlingua_call_count: int = 0
    skipped_chunk_count: int = 0
    placeholder_counts: tuple[int, ...] = ()
    char_counts: tuple[int, ...] = ()
    fallback_reason: str | None = None


@dataclass(frozen=True)
class _ChunkedCompressionResult:
    expanded: "_ExpandedCompression"
    stats: _ChunkedCompressionStats


@dataclass(frozen=True)
class _ExpandedCompression:
    text: str
    labeled_tokens: list[CompressionToken]
    output_sections: list[CompressionOutputSection]


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def _add_timing(timings: dict[str, float], key: str, elapsed_ms: float) -> None:
    timings[key] = timings.get(key, 0.0) + elapsed_ms


class PromptCompressionService:
    def __init__(self) -> None:
        self.model_name = os.getenv("COMPRESSOR_MODEL", DEFAULT_MODEL)
        self.device = os.getenv("COMPRESSOR_DEVICE", "cpu")
        self.min_rate = float(os.getenv("COMPRESSOR_MIN_RATE", "0.45"))
        self.min_segment_chars = max(0, MIN_SEGMENT_CHARS)
        self.min_segment_tokens = max(0, MIN_SEGMENT_TOKENS)
        self._compressor: Any | None = None
        self._adapter_compressors: dict[str, Any] = {}
        self._adapter_slots = _parse_adapter_slots(os.getenv(ADAPTER_SLOT_ENV, ""))
        self._adapter_root = _parse_adapter_root(os.getenv(ADAPTER_ROOT_ENV, ""))
        self._preload_slots = _parse_slot_list(os.getenv(PRELOAD_SLOT_ENV, ""))
        self._lock = Lock()
        self.preprocessor = PromptPreprocessor()
        self.placeholder_chunk_target = max(0, DEFAULT_PLACEHOLDER_CHUNK_TARGET)
        self.max_model_chunk_chars = max(0, DEFAULT_MODEL_CHUNK_CHARS)
        self.protected_span_coalesce_gap_chars = max(
            0,
            PROTECTED_SPAN_COALESCE_GAP_CHARS,
        )
        self.protected_span_coalesce_max_chars = max(
            0,
            PROTECTED_SPAN_COALESCE_MAX_CHARS,
        )

    @property
    def is_loaded(self) -> bool:
        return self._compressor is not None or bool(self._adapter_compressors)

    def preload_configured_slots(self) -> None:
        if not self._preload_slots:
            return

        slot_ids = self._preload_slots
        if "all" in slot_ids:
            slot_ids = (BASE_SLOT_ID, *self._adapter_slots.keys())

        for slot_id in slot_ids:
            if slot_id == BASE_SLOT_ID:
                self._load()
            elif slot_id in self._adapter_slots:
                self._load_adapter_slot(slot_id)
            else:
                LOGGER.warning("Skipping unknown compressor preload slot %s", slot_id)

    def _build_prompt_compressor(self) -> Any:
        try:
            from llmlingua import PromptCompressor
        except ImportError as exc:
            LOGGER.exception("Failed to import llmlingua")
            raise CompressionRuntimeError(
                "llmlingua is not installed. Run `pip install -r requirements.txt`."
            ) from exc

        try:
            return PromptCompressor(
                model_name=self.model_name,
                device_map=self.device,
                use_llmlingua2=True,
            )
        except Exception as exc:  # pragma: no cover - depends on network/model cache
            LOGGER.exception("Failed to load compression model %s", self.model_name)
            raise CompressionRuntimeError(
                "Failed to load the compression model. The first run needs network "
                "access to download the Hugging Face checkpoint."
            ) from exc

    def _load(self) -> Any:
        if self._compressor is not None:
            return self._compressor

        with self._lock:
            if self._compressor is not None:
                return self._compressor

            self._compressor = self._build_prompt_compressor()

            return self._compressor

    def _load_adapter_slot(self, slot_id: str) -> Any:
        if slot_id in self._adapter_compressors:
            return self._adapter_compressors[slot_id]

        adapter_path = self._adapter_slots[slot_id]
        with self._lock:
            if slot_id in self._adapter_compressors:
                return self._adapter_compressors[slot_id]

            try:
                from peft import PeftModel
            except ImportError as exc:
                LOGGER.exception("Failed to import peft")
                raise CompressionRuntimeError(
                    "peft is not installed. Runtime LoRA adapter slots require "
                    "`peft` in requirements.txt."
                ) from exc

            compressor = self._build_prompt_compressor()
            try:
                compressor.model = PeftModel.from_pretrained(
                    compressor.model,
                    adapter_path,
                    is_trainable=False,
                )
                compressor.model.eval()
            except Exception as exc:  # pragma: no cover - model-specific runtime path
                LOGGER.exception(
                    "Failed to load LoRA adapter slot %s from %s",
                    slot_id,
                    adapter_path,
                )
                raise CompressionRuntimeError(
                    f"Failed to load LoRA adapter slot {slot_id!r} from "
                    f"{adapter_path!r}."
                ) from exc

            self._adapter_compressors[slot_id] = compressor
            LOGGER.info("Loaded LoRA adapter slot %s from %s", slot_id, adapter_path)
            return compressor

    def _load_for_profile(self, profile: TenantCompressionProfile) -> Any:
        slot_id = profile.tenant_id
        if slot_id in self._adapter_slots:
            return self._load_adapter_slot(slot_id)

        adapter_path = self._discover_adapter_slot(slot_id)
        if adapter_path is not None:
            with self._lock:
                self._adapter_slots.setdefault(slot_id, adapter_path)
            return self._load_adapter_slot(slot_id)

        return self._load()

    def _discover_adapter_slot(self, slot_id: str) -> str | None:
        if self._adapter_root is None or not _is_safe_adapter_slot_id(slot_id):
            return None

        adapter_path = self._adapter_root / slot_id
        if not _is_valid_adapter_dir(adapter_path):
            return None

        try:
            adapter_path.resolve().relative_to(self._adapter_root.resolve())
        except ValueError:
            LOGGER.warning("Ignoring adapter slot outside adapter root: %s", slot_id)
            return None

        LOGGER.info("Discovered LoRA adapter slot %s at %s", slot_id, adapter_path)
        return str(adapter_path)

    def estimate_compression_tokens(
        self,
        text: str,
        tenant_profile: TenantCompressionProfile | None = None,
    ) -> TokenEstimate:
        return estimate_huggingface_tokens(
            text,
            self.model_name,
            tokenizer=self._loaded_tokenizer_for_profile(tenant_profile),
        )

    def _loaded_tokenizer_for_profile(
        self,
        tenant_profile: TenantCompressionProfile | None,
    ) -> Any | None:
        slot_id = DEFAULT_TENANT_ID if tenant_profile is None else tenant_profile.tenant_id
        compressor = None
        if slot_id in self._adapter_compressors:
            compressor = self._adapter_compressors[slot_id]
        elif self._compressor is not None:
            compressor = self._compressor

        if compressor is None:
            return None

        tokenizer = getattr(compressor, "tokenizer", None)
        if tokenizer is not None:
            return tokenizer

        model = getattr(compressor, "model", None)
        return getattr(model, "tokenizer", None)

    def target_rate_for_aggressiveness(
        self,
        aggressiveness: float,
        min_rate_override: float | None = None,
    ) -> float:
        bounded = max(0.0, min(1.0, aggressiveness))
        configured_min_rate = (
            self.min_rate
            if min_rate_override is None
            else min_rate_override
        )
        min_rate = max(0.05, min(1.0, configured_min_rate))
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

    def _placeholder_chunk_limit(self, max_force_tokens: int) -> int:
        if max_force_tokens <= 0:
            return 0
        if self.placeholder_chunk_target <= 0:
            return max_force_tokens
        return min(max_force_tokens, self.placeholder_chunk_target)

    def _protected_spans_for_model_input(self, text: str) -> list[ProtectedSpan]:
        spans = protected_spans_for_text(text)
        if not spans:
            return spans
        return self._coalesce_protected_spans(text, spans)

    def _coalesce_protected_spans(
        self,
        text: str,
        spans: list[ProtectedSpan],
    ) -> list[ProtectedSpan]:
        if (
            not spans
            or self.protected_span_coalesce_gap_chars <= 0
            or self.protected_span_coalesce_max_chars <= 0
        ):
            return spans

        coalesced: list[ProtectedSpan] = []
        current = spans[0]
        for span in spans[1:]:
            gap = text[current.end : span.start]
            merged_text = text[current.start : span.end]
            if (
                len(gap) <= self.protected_span_coalesce_gap_chars
                and len(merged_text) <= self.protected_span_coalesce_max_chars
                and "\n" not in gap
                and re.search(r"[.!?]\s", gap) is None
            ):
                current = ProtectedSpan(
                    start=current.start,
                    end=span.end,
                    text=merged_text,
                    kind="protected_group",
                )
                continue

            coalesced.append(current)
            current = span

        coalesced.append(current)
        return coalesced

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
                for span in self._protected_spans_for_model_input(segment.text):
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

    def _split_prepared_model_input(
        self,
        prepared: _PreparedModelInput,
        placeholder_limit: int,
    ) -> list[_ModelInputChunk]:
        chunks: list[_ModelInputChunk] = []
        parts: list[str] = []
        placeholders: list[_CompressionPlaceholder] = []
        char_count = 0

        def flush() -> None:
            nonlocal parts, placeholders, char_count
            if not parts:
                return
            chunks.append(
                _ModelInputChunk(
                    text="".join(parts),
                    placeholders=placeholders,
                )
            )
            parts = []
            placeholders = []
            char_count = 0

        def append_unit(
            text: str,
            unit_placeholders: list[_CompressionPlaceholder],
        ) -> None:
            nonlocal char_count
            if not text:
                return

            exceeds_placeholder_limit = (
                bool(unit_placeholders)
                and len(placeholders) + len(unit_placeholders) > placeholder_limit
            )
            exceeds_char_limit = (
                self.max_model_chunk_chars > 0
                and char_count + len(text) > self.max_model_chunk_chars
            )
            current_exceeds_placeholder_limit = (
                len(placeholders) > placeholder_limit
            )
            if parts and (
                exceeds_placeholder_limit
                or exceeds_char_limit
                or current_exceeds_placeholder_limit
            ):
                flush()

            parts.append(text)
            placeholders.extend(unit_placeholders)
            char_count += len(text)

        cursor = 0
        for placeholder in prepared.placeholders:
            position = prepared.text.find(placeholder.token, cursor)
            if position < cursor:
                continue

            for text_part in self._split_text_for_model_chunks(
                prepared.text[cursor:position]
            ):
                append_unit(text_part, [])

            append_unit(placeholder.token, [placeholder])
            cursor = position + len(placeholder.token)

        for text_part in self._split_text_for_model_chunks(prepared.text[cursor:]):
            append_unit(text_part, [])

        flush()
        return chunks

    def _split_text_for_model_chunks(self, text: str) -> list[str]:
        if (
            not text
            or self.max_model_chunk_chars <= 0
            or len(text) <= self.max_model_chunk_chars
        ):
            return [text] if text else []

        chunks: list[str] = []
        cursor = 0
        while cursor < len(text):
            end = min(len(text), cursor + self.max_model_chunk_chars)
            if end < len(text):
                newline_break = text.rfind("\n", cursor + 1, end)
                space_break = text.rfind(" ", cursor + 1, end)
                break_at = max(newline_break, space_break)
                if break_at > cursor:
                    end = break_at + 1
            chunks.append(text[cursor:end])
            cursor = end
        return chunks

    def _force_tokens_for_model_input(
        self,
        text: str,
        required_tokens: list[str],
        max_tokens: int,
        priority_tokens: list[str] | None = None,
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

        for token in priority_tokens or []:
            add_token(token)

        for token in force_tokens_for_text(text, max_tokens=max_tokens):
            add_token(token)

        return tokens

    def _apply_force_drop_phrases(
        self,
        segments: list[CompressionSegment],
        tenant_profile: TenantCompressionProfile,
    ) -> list[CompressionSegment]:
        if not tenant_profile.force_drop_phrases:
            return segments

        updated_segments: list[CompressionSegment] = []
        for segment in segments:
            if not segment.compressible:
                updated_segments.append(segment)
                continue

            text = segment.text
            for phrase in tenant_profile.force_drop_phrases:
                text = self._remove_force_drop_phrase(text, phrase)
            updated_segments.append(
                CompressionSegment(
                    text=text,
                    compressible=segment.compressible,
                    kind=segment.kind,
                )
            )

        return updated_segments

    def _remove_force_drop_phrase(self, text: str, phrase: str) -> str:
        while True:
            index = text.find(phrase)
            if index < 0:
                return text

            start = index
            end = index + len(phrase)
            if not text[:start].strip():
                start = 0
                while end < len(text) and text[end].isspace():
                    end += 1
            if not text[end:].strip():
                end = len(text)
                while start > 0 and text[start - 1].isspace():
                    start -= 1
            elif (
                start > 0
                and text[start - 1].isspace()
                and end < len(text)
                and text[end].isspace()
            ):
                end += 1
            text = text[:start] + text[end:]

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

    def _append_uncompressed_prose_section(
        self,
        chunk: str,
        labeled_tokens: list[CompressionToken],
        output_sections: list[CompressionOutputSection],
        include_sections: bool,
    ) -> None:
        if not include_sections or not chunk:
            return
        labels = (
            [CompressionToken(text=chunk, kept=True)]
            if chunk.strip()
            else []
        )
        labeled_tokens.extend(labels)
        output_sections.append(
            CompressionOutputSection(
                text=chunk,
                kind="prose",
                compressed=False,
                protected=False,
                labeled_tokens=labels,
            )
        )

    def _expand_prepared_chunk_uncompressed(
        self,
        chunk: _ModelInputChunk,
        include_sections: bool,
    ) -> _ExpandedCompression:
        if not chunk.placeholders:
            labeled_tokens: list[CompressionToken] = []
            output_sections: list[CompressionOutputSection] = []
            self._append_uncompressed_prose_section(
                chunk.text,
                labeled_tokens,
                output_sections,
                include_sections=include_sections,
            )
            return _ExpandedCompression(
                text=chunk.text,
                labeled_tokens=labeled_tokens,
                output_sections=output_sections,
            )

        output_parts: list[str] = []
        labeled_tokens = []
        output_sections = []
        cursor = 0

        for placeholder in chunk.placeholders:
            position = chunk.text.find(placeholder.token, cursor)
            if position < cursor:
                continue

            prose = chunk.text[cursor:position]
            output_parts.append(prose)
            self._append_uncompressed_prose_section(
                prose,
                labeled_tokens,
                output_sections,
                include_sections=include_sections,
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

        tail = chunk.text[cursor:]
        output_parts.append(tail)
        self._append_uncompressed_prose_section(
            tail,
            labeled_tokens,
            output_sections,
            include_sections=include_sections,
        )

        return _ExpandedCompression(
            text="".join(output_parts),
            labeled_tokens=labeled_tokens,
            output_sections=output_sections,
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
        tenant_profile: TenantCompressionProfile,
        timings: dict[str, float] | None = None,
    ) -> _ChunkedCompressionResult:
        max_force_tokens = self._max_force_tokens(compressor)
        placeholder_limit = self._placeholder_chunk_limit(max_force_tokens)
        chunks = self._split_prepared_model_input(
            prepared,
            placeholder_limit=placeholder_limit,
        )

        output_parts: list[str] = []
        labeled_tokens: list[CompressionToken] = []
        output_sections: list[CompressionOutputSection] = []
        placeholder_counts: list[int] = []
        char_counts: list[int] = []
        llmlingua_call_count = 0
        skipped_chunk_count = 0
        fallback_reason: str | None = None

        for chunk in chunks:
            placeholder_count = len(chunk.placeholders)
            placeholder_counts.append(placeholder_count)
            char_counts.append(len(chunk.text))
            required_tokens = [placeholder.token for placeholder in chunk.placeholders]

            if len(required_tokens) > max_force_tokens:
                LOGGER.warning(
                    "Skipping model chunk because %s placeholders exceed "
                    "max_force_token=%s",
                    len(required_tokens),
                    max_force_tokens,
                )
                skipped_chunk_count += 1
                fallback_reason = fallback_reason or "too_many_placeholders"
                fallback_expand_start = time.perf_counter()
                expanded_chunk = self._expand_prepared_chunk_uncompressed(
                    chunk,
                    include_sections=include_sections,
                )
                if timings is not None:
                    _add_timing(
                        timings,
                        "uncompressed_expand_ms",
                        _elapsed_ms(fallback_expand_start),
                    )
                self._append_expanded_chunk(
                    expanded_chunk,
                    output_parts,
                    labeled_tokens,
                    output_sections,
                )
                continue

            llmlingua_call_count += 1
            expanded_chunk = self._compress_prepared_model_chunk(
                compressor=compressor,
                chunk=chunk,
                target_rate=target_rate,
                include_sections=include_sections,
                tenant_profile=tenant_profile,
                max_force_tokens=max_force_tokens,
                timings=timings,
            )
            if expanded_chunk is None:
                skipped_chunk_count += 1
                if fallback_reason is None:
                    fallback_reason = "placeholder_validation_failed"
                fallback_expand_start = time.perf_counter()
                expanded_chunk = self._expand_prepared_chunk_uncompressed(
                    chunk,
                    include_sections=include_sections,
                )
                if timings is not None:
                    _add_timing(
                        timings,
                        "uncompressed_expand_ms",
                        _elapsed_ms(fallback_expand_start),
                    )
            self._append_expanded_chunk(
                expanded_chunk,
                output_parts,
                labeled_tokens,
                output_sections,
            )

        return _ChunkedCompressionResult(
            expanded=_ExpandedCompression(
                text="".join(output_parts),
                labeled_tokens=labeled_tokens,
                output_sections=output_sections,
            ),
            stats=_ChunkedCompressionStats(
                chunk_count=len(chunks),
                llmlingua_call_count=llmlingua_call_count,
                skipped_chunk_count=skipped_chunk_count,
                placeholder_counts=tuple(placeholder_counts),
                char_counts=tuple(char_counts),
                fallback_reason=fallback_reason,
            ),
        )

    def _append_expanded_chunk(
        self,
        expanded_chunk: _ExpandedCompression,
        output_parts: list[str],
        labeled_tokens: list[CompressionToken],
        output_sections: list[CompressionOutputSection],
    ) -> None:
        output_parts.append(expanded_chunk.text)
        labeled_tokens.extend(expanded_chunk.labeled_tokens)
        output_sections.extend(expanded_chunk.output_sections)

    def _compress_prepared_model_chunk(
        self,
        *,
        compressor: Any,
        chunk: _ModelInputChunk,
        target_rate: float,
        include_sections: bool,
        tenant_profile: TenantCompressionProfile,
        max_force_tokens: int,
        timings: dict[str, float] | None,
    ) -> _ExpandedCompression | None:
        required_tokens = [placeholder.token for placeholder in chunk.placeholders]

        force_tokens_start = time.perf_counter()
        force_tokens = self._force_tokens_for_model_input(
            chunk.text,
            required_tokens=required_tokens,
            max_tokens=max_force_tokens,
            priority_tokens=list(tenant_profile.force_keep_tokens),
        )
        if timings is not None:
            _add_timing(timings, "force_tokens_ms", _elapsed_ms(force_tokens_start))

        try:
            llmlingua_start = time.perf_counter()
            raw_result = compressor.compress_prompt_llmlingua2(
                chunk.text,
                rate=target_rate,
                force_tokens=force_tokens,
                return_word_label=include_sections,
            )
            if timings is not None:
                _add_timing(timings, "llmlingua_ms", _elapsed_ms(llmlingua_start))
        except Exception as exc:  # pragma: no cover - model-specific runtime path
            message = str(exc) or exc.__class__.__name__
            raise CompressionRuntimeError(f"Compression failed: {message}") from exc

        compressed_model_text = raw_result.get("compressed_prompt", "")
        placeholder_validation_start = time.perf_counter()
        if not self._has_valid_placeholders(
            compressed_model_text,
            chunk.placeholders,
        ):
            if timings is not None:
                _add_timing(
                    timings,
                    "placeholder_validation_ms",
                    _elapsed_ms(placeholder_validation_start),
                )
            LOGGER.warning("Skipping compressed output because placeholders changed")
            return None
        if timings is not None:
            _add_timing(
                timings,
                "placeholder_validation_ms",
                _elapsed_ms(placeholder_validation_start),
            )

        expand_start = time.perf_counter()
        model_labeled_tokens: list[CompressionToken] = []
        if include_sections:
            model_labeled_tokens = self.parse_word_labels(
                raw_result.get("fn_labeled_original_prompt", "")
            )

        expanded = self._expand_compressed_model_text(
            compressed_model_text,
            chunk.placeholders,
            include_sections=include_sections,
            model_labeled_tokens=model_labeled_tokens,
        )
        if timings is not None:
            _add_timing(timings, "model_expand_ms", _elapsed_ms(expand_start))
        return expanded

    def _should_compress_segment(
        self,
        segment: CompressionSegment,
        target_rate: float,
        tenant_profile: TenantCompressionProfile,
    ) -> bool:
        if not segment.compressible or not segment.text.strip():
            return False
        if target_rate >= 1.0:
            return False
        if len(segment.text.strip()) < self.min_segment_chars:
            return False
        return (
            self.estimate_compression_tokens(segment.text, tenant_profile).count
            >= self.min_segment_tokens
        )

    def compress(
        self,
        text: str,
        aggressiveness: float,
        include_sections: bool = True,
        tenant_profile: TenantCompressionProfile | None = None,
    ) -> CompressionResult:
        start = time.perf_counter()
        timings = dict.fromkeys(TIMED_PHASES, 0.0)
        profile = tenant_profile or TenantCompressionProfile()

        phase_start = time.perf_counter()
        target_rate = self.target_rate_for_aggressiveness(
            aggressiveness,
            min_rate_override=profile.min_rate,
        )
        timings["target_rate_ms"] = _elapsed_ms(phase_start)

        phase_start = time.perf_counter()
        prepared_segments = self.preprocessor.prepare(text)
        timings["preprocessing_ms"] = _elapsed_ms(phase_start)

        phase_start = time.perf_counter()
        segments = self._apply_force_drop_phrases(prepared_segments, profile)
        timings["force_drop_ms"] = _elapsed_ms(phase_start)

        phase_start = time.perf_counter()
        should_compress_segments = [
            self._should_compress_segment(segment, target_rate, profile)
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
        timings["segment_selection_ms"] = _elapsed_ms(phase_start)

        prepared: _PreparedModelInput | None = None
        chunk_stats: _ChunkedCompressionStats | None = None
        fallback_used = False
        fallback_reason = None
        llmlingua_called = False
        if compressible_segments:
            phase_start = time.perf_counter()
            compressor = self._load_for_profile(profile)
            timings["model_load_ms"] = _elapsed_ms(phase_start)

            phase_start = time.perf_counter()
            prepared = self._prepare_model_input(segments, should_compress_segments)
            timings["model_input_ms"] = _elapsed_ms(phase_start)

            chunked_result = self._compress_prepared_model_input(
                compressor,
                prepared,
                target_rate,
                include_sections=include_sections,
                tenant_profile=profile,
                timings=timings,
            )
            expanded = chunked_result.expanded
            chunk_stats = chunked_result.stats
            llmlingua_called = chunk_stats.llmlingua_call_count > 0
            if chunk_stats.skipped_chunk_count:
                fallback_used = True
                fallback_reason = chunk_stats.fallback_reason
        else:
            phase_start = time.perf_counter()
            expanded = self._uncompressed_result_parts(
                segments,
                include_sections=include_sections,
            )
            timings["uncompressed_expand_ms"] = _elapsed_ms(phase_start)

        compressed_text = expanded.text
        phase_start = time.perf_counter()
        original_estimate = self.estimate_compression_tokens(text, profile)
        compressed_estimate = (
            original_estimate
            if compressed_text == text
            else self.estimate_compression_tokens(compressed_text, profile)
        )
        timings["token_estimate_ms"] = _elapsed_ms(phase_start)

        reduction = 0.0
        if original_estimate.count:
            reduction = max(
                0.0,
                1.0 - (compressed_estimate.count / original_estimate.count),
            )
        total_ms = _elapsed_ms(start)
        diagnostics = self._build_diagnostics(
            timings=timings,
            total_ms=total_ms,
            input_text=text,
            compressed_text=compressed_text,
            segments=segments,
            should_compress_segments=should_compress_segments,
            prepared=prepared,
            chunk_stats=chunk_stats,
            llmlingua_called=llmlingua_called,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
        )

        return CompressionResult(
            compressed_text=compressed_text,
            original_tokens=original_estimate.count,
            compressed_tokens=compressed_estimate.count,
            reduction=reduction,
            aggressiveness=max(0.0, min(1.0, aggressiveness)),
            target_rate=target_rate,
            model=self.model_name,
            elapsed_ms=total_ms,
            labeled_tokens=expanded.labeled_tokens,
            output_sections=expanded.output_sections,
            tenant_id=profile.tenant_id,
            compression_profile=profile.profile_id,
            compression_profile_source=profile.source,
            training_sample_recorded=False,
            token_estimator=merge_token_estimator_names(
                [original_estimate.estimator, compressed_estimate.estimator]
            ),
            diagnostics=diagnostics,
        )

    def _build_diagnostics(
        self,
        *,
        timings: dict[str, float],
        total_ms: float,
        input_text: str,
        compressed_text: str,
        segments: list[CompressionSegment],
        should_compress_segments: list[bool],
        prepared: _PreparedModelInput | None,
        chunk_stats: _ChunkedCompressionStats | None,
        llmlingua_called: bool,
        fallback_used: bool,
        fallback_reason: str | None,
    ) -> CompressionDiagnostics:
        segment_kinds: dict[str, int] = {}
        for segment in segments:
            segment_kinds[segment.kind] = segment_kinds.get(segment.kind, 0) + 1

        accounted_ms = sum(timings.get(phase, 0.0) for phase in TIMED_PHASES)
        timing = CompressionTiming(
            total_ms=total_ms,
            target_rate_ms=timings["target_rate_ms"],
            preprocessing_ms=timings["preprocessing_ms"],
            force_drop_ms=timings["force_drop_ms"],
            segment_selection_ms=timings["segment_selection_ms"],
            model_load_ms=timings["model_load_ms"],
            model_input_ms=timings["model_input_ms"],
            force_tokens_ms=timings["force_tokens_ms"],
            llmlingua_ms=timings["llmlingua_ms"],
            placeholder_validation_ms=timings["placeholder_validation_ms"],
            model_expand_ms=timings["model_expand_ms"],
            uncompressed_expand_ms=timings["uncompressed_expand_ms"],
            token_estimate_ms=timings["token_estimate_ms"],
            other_ms=max(0.0, total_ms - accounted_ms),
        )

        model_segment_count = sum(
            1 for should_compress in should_compress_segments if should_compress
        )
        placeholder_counts = (
            chunk_stats.placeholder_counts if chunk_stats is not None else ()
        )
        char_counts = chunk_stats.char_counts if chunk_stats is not None else ()
        return CompressionDiagnostics(
            timings=timing,
            input_chars=len(input_text),
            output_chars=len(compressed_text),
            segment_count=len(segments),
            compressible_segment_count=sum(
                1 for segment in segments if segment.compressible
            ),
            model_segment_count=model_segment_count,
            skipped_segment_count=len(segments) - model_segment_count,
            placeholder_count=0 if prepared is None else len(prepared.placeholders),
            model_input_chars=0 if prepared is None else len(prepared.text),
            segment_kinds=segment_kinds,
            llmlingua_called=llmlingua_called,
            fallback_used=fallback_used,
            fallback_reason=fallback_reason,
            model_chunk_count=0 if chunk_stats is None else chunk_stats.chunk_count,
            llmlingua_call_count=(
                0 if chunk_stats is None else chunk_stats.llmlingua_call_count
            ),
            skipped_model_chunk_count=(
                0 if chunk_stats is None else chunk_stats.skipped_chunk_count
            ),
            chunk_placeholder_max=max(placeholder_counts, default=0),
            chunk_placeholder_avg=(
                0.0
                if not placeholder_counts
                else sum(placeholder_counts) / len(placeholder_counts)
            ),
            chunk_chars_max=max(char_counts, default=0),
        )


def _parse_adapter_slots(raw_value: str) -> dict[str, str]:
    slots: dict[str, str] = {}
    for entry in _split_env_list(raw_value):
        slot_id, separator, adapter_path = entry.partition("=")
        slot_id = slot_id.strip()
        adapter_path = adapter_path.strip()
        if not separator or not slot_id or not adapter_path:
            LOGGER.warning("Ignoring malformed adapter slot entry %r", entry)
            continue
        if slot_id == BASE_SLOT_ID:
            LOGGER.warning("Ignoring reserved adapter slot id %r", slot_id)
            continue
        slots[slot_id] = adapter_path
    return slots


def _parse_adapter_root(raw_value: str) -> Path | None:
    root = raw_value.strip()
    return Path(root) if root else None


def _is_safe_adapter_slot_id(slot_id: str) -> bool:
    return slot_id not in {BASE_SLOT_ID, DEFAULT_TENANT_ID} and bool(
        ADAPTER_SLOT_ID_RE.fullmatch(slot_id)
    )


def _is_valid_adapter_dir(adapter_path: Path) -> bool:
    return (
        adapter_path.is_dir()
        and (adapter_path / "adapter_config.json").is_file()
        and any((adapter_path / name).is_file() for name in ADAPTER_MODEL_FILENAMES)
    )


def _parse_slot_list(raw_value: str) -> tuple[str, ...]:
    return tuple(_split_env_list(raw_value))


def _split_env_list(raw_value: str) -> list[str]:
    return [
        entry.strip()
        for entry in re.split(r"[;,]", raw_value)
        if entry.strip()
    ]
