import os
import time
from dataclasses import dataclass, field
import logging
import math
from pathlib import Path
import re
from threading import Lock
from typing import Any

from app.analytics import DetailedAnalytics, build_detailed_analytics
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
COMPRESSION_MODE_DETERMINISTIC = "deterministic"
COMPRESSION_MODE_MODEL_AUTO = "model_auto"
COMPRESSION_MODE_MODEL_FORCE = "model_force"
COMPRESSION_MODES = {
    COMPRESSION_MODE_DETERMINISTIC,
    COMPRESSION_MODE_MODEL_AUTO,
    COMPRESSION_MODE_MODEL_FORCE,
}
COMPRESSION_PATH_UNCHANGED = "unchanged"
COMPRESSION_PATH_DETERMINISTIC_ONLY = "deterministic_only"
COMPRESSION_PATH_DETERMINISTIC_PLUS_MODEL = "deterministic_plus_model"
DEFAULT_MAX_FORCE_TOKENS = 100
DEFAULT_PLACEHOLDER_CHUNK_TARGET = int(
    os.getenv("COMPRESSOR_PLACEHOLDER_CHUNK_TARGET", "80")
)
DEFAULT_MODEL_CHUNK_CHARS = int(os.getenv("COMPRESSOR_MODEL_CHUNK_CHARS", "24000"))
DEFAULT_MODEL_AUTO_ENABLED = os.getenv(
    "COMPRESSOR_MODEL_AUTO_ENABLED",
    "false",
).lower() in {"1", "true", "yes", "on"}
DEFAULT_ALLOW_CPU_MODEL_AUTO = os.getenv(
    "COMPRESSOR_ALLOW_CPU_MODEL_AUTO",
    "false",
).lower() in {"1", "true", "yes", "on"}
DEFAULT_MIN_MODEL_CANDIDATE_TOKENS = int(
    os.getenv("COMPRESSOR_MIN_MODEL_CANDIDATE_TOKENS", "20000")
)
DEFAULT_MIN_MODEL_INCREMENTAL_SAVINGS_TOKENS = int(
    os.getenv("COMPRESSOR_MIN_MODEL_INCREMENTAL_SAVINGS_TOKENS", "2000")
)
DEFAULT_MIN_MODEL_INCREMENTAL_REDUCTION = float(
    os.getenv("COMPRESSOR_MIN_MODEL_INCREMENTAL_REDUCTION", "0.05")
)
DEFAULT_MAX_MODEL_PROJECTED_LATENCY_MS = float(
    os.getenv("COMPRESSOR_MAX_MODEL_PROJECTED_LATENCY_MS", "2500")
)
DEFAULT_MAX_MODEL_AUTO_PLACEHOLDERS = int(
    os.getenv("COMPRESSOR_MAX_MODEL_AUTO_PLACEHOLDERS", "400")
)
DEFAULT_COLD_MODEL_TIGHT_LATENCY_BUDGET_MS = float(
    os.getenv("COMPRESSOR_COLD_MODEL_TIGHT_LATENCY_BUDGET_MS", "1000")
)
DEFAULT_MIN_DUPLICATE_BLOCK_TOKENS = int(
    os.getenv("COMPRESSOR_MIN_DUPLICATE_BLOCK_TOKENS", "32")
)
DEFAULT_ENABLE_LITERAL_PLACEHOLDERING = os.getenv(
    "COMPRESSOR_ENABLE_LITERAL_PLACEHOLDERING",
    "false",
).lower() in {"1", "true", "yes", "on"}
DEFAULT_MIN_LITERAL_PLACEHOLDER_SAVINGS_TOKENS = int(
    os.getenv("COMPRESSOR_MIN_LITERAL_PLACEHOLDER_SAVINGS_TOKENS", "50")
)
DEFAULT_MIN_LITERAL_PLACEHOLDER_REDUCTION = float(
    os.getenv("COMPRESSOR_MIN_LITERAL_PLACEHOLDER_REDUCTION", "0.05")
)
DEFAULT_MAX_PROTECTED_DENSITY = float(
    os.getenv("COMPRESSOR_MAX_PROTECTED_DENSITY", "0.20")
)
DEFAULT_MAX_STRUCTURED_DENSITY = float(
    os.getenv("COMPRESSOR_MAX_STRUCTURED_DENSITY", "0.35")
)
DEFAULT_SKIP_MODEL_IF_DETERMINISTIC_REDUCTION_GTE = float(
    os.getenv("COMPRESSOR_SKIP_MODEL_IF_DETERMINISTIC_REDUCTION_GTE", "0.12")
)
DEFAULT_GPU_P50_FIXED_OVERHEAD_MS = os.getenv(
    "COMPRESSOR_GPU_P50_FIXED_OVERHEAD_MS",
)
DEFAULT_GPU_P50_LINGUA_CHUNK_MS = os.getenv(
    "COMPRESSOR_GPU_P50_LLMLINGUA_CHUNK_MS",
)
DEFAULT_GPU_P50_TOKEN_ESTIMATE_MS = os.getenv(
    "COMPRESSOR_GPU_P50_TOKEN_ESTIMATE_MS",
)
DEFAULT_CPU_P50_FIXED_OVERHEAD_MS = os.getenv(
    "COMPRESSOR_CPU_P50_FIXED_OVERHEAD_MS",
)
DEFAULT_CPU_P50_LINGUA_CHUNK_MS = os.getenv(
    "COMPRESSOR_CPU_P50_LLMLINGUA_CHUNK_MS",
)
DEFAULT_CPU_P50_TOKEN_ESTIMATE_MS = os.getenv(
    "COMPRESSOR_CPU_P50_TOKEN_ESTIMATE_MS",
)
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
    "model_gate_ms",
    "diagnostics_ms",
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
class TokenSavings:
    original_tokens: int
    after_deterministic_tokens: int
    final_tokens: int
    deterministic_tokens_saved: int
    model_incremental_tokens_saved: int
    total_tokens_saved: int
    deterministic_reduction: float
    model_incremental_reduction: float
    total_reduction: float
    model_stage: str
    model_ran: bool
    fallback_used: bool
    attribution_residual_tokens: int
    token_estimator: str


def build_token_savings(
    *,
    original_tokens: int,
    after_deterministic_tokens: int,
    final_tokens: int,
    model_ran: bool,
    fallback_used: bool,
    token_estimator: str,
) -> TokenSavings:
    deterministic_tokens_saved = max(
        0,
        original_tokens - after_deterministic_tokens,
    )
    model_incremental_tokens_saved = max(
        0,
        after_deterministic_tokens - final_tokens,
    )
    total_tokens_saved = max(0, original_tokens - final_tokens)
    deterministic_reduction = (
        0.0
        if original_tokens <= 0
        else deterministic_tokens_saved / original_tokens
    )
    model_incremental_reduction = (
        0.0
        if after_deterministic_tokens <= 0
        else model_incremental_tokens_saved / after_deterministic_tokens
    )
    total_reduction = (
        0.0 if original_tokens <= 0 else total_tokens_saved / original_tokens
    )
    return TokenSavings(
        original_tokens=original_tokens,
        after_deterministic_tokens=after_deterministic_tokens,
        final_tokens=final_tokens,
        deterministic_tokens_saved=deterministic_tokens_saved,
        model_incremental_tokens_saved=model_incremental_tokens_saved,
        total_tokens_saved=total_tokens_saved,
        deterministic_reduction=deterministic_reduction,
        model_incremental_reduction=model_incremental_reduction,
        total_reduction=total_reduction,
        model_stage="llmlingua2",
        model_ran=model_ran,
        fallback_used=fallback_used,
        attribution_residual_tokens=(
            total_tokens_saved
            - deterministic_tokens_saved
            - model_incremental_tokens_saved
        ),
        token_estimator=token_estimator,
    )


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
    compression_mode: str = COMPRESSION_MODE_MODEL_FORCE
    compression_path: str = COMPRESSION_PATH_UNCHANGED
    warnings: list[str] = field(default_factory=list)
    token_savings: TokenSavings | None = None


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
    model_gate_ms: float = 0.0
    diagnostics_ms: float = 0.0
    other_ms: float = 0.0


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
    deterministic_original_tokens: int = 0
    deterministic_output_tokens: int = 0
    deterministic_tokens_saved: int = 0
    deterministic_reduction: float = 0.0
    deterministic_input_chars: int = 0
    deterministic_output_chars: int = 0
    preprocessing_tokens_saved: int = 0
    force_drop_tokens_saved: int = 0
    whitespace_tokens_saved: int = 0
    toon_tokens_saved: int = 0
    json_minify_tokens_saved: int = 0
    html_markdown_tokens_saved: int = 0
    nocompress_wrapper_tokens_saved: int = 0
    skipped_model_candidate_tokens: int = 0
    literal_placeholder_count: int = 0
    literal_placeholder_tokens_saved: int = 0
    model_incremental_tokens_saved: int = 0
    model_incremental_reduction: float = 0.0
    protected_segment_count: int = 0
    toon_segment_count: int = 0
    json_minified_segment_count: int = 0
    duplicate_block_candidate_count: int = 0
    duplicate_block_candidate_tokens: int = 0
    compression_mode: str = COMPRESSION_MODE_MODEL_FORCE
    compression_path: str = COMPRESSION_PATH_UNCHANGED
    model_gate_decision: str = "run"
    model_gate_reason: str | None = None
    model_candidate_tokens: int = 0
    model_candidate_chars: int = 0
    model_expected_incremental_savings_tokens: int = 0
    model_expected_incremental_reduction: float = 0.0
    model_projected_latency_ms: float | None = None
    model_projected_chunk_count: int = 0
    protected_density: float = 0.0
    structured_density: float = 0.0
    identifier_density: float = 0.0
    analytics: DetailedAnalytics | None = None


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
    model_output_text: str = ""
    force_token_count: int = 0


@dataclass(frozen=True)
class _ChunkedCompressionResult:
    expanded: "_ExpandedCompression"
    stats: _ChunkedCompressionStats


@dataclass(frozen=True)
class _CompressedModelChunkResult:
    expanded: "_ExpandedCompression | None"
    model_output_text: str
    force_token_count: int


@dataclass(frozen=True)
class _ExpandedCompression:
    text: str
    labeled_tokens: list[CompressionToken]
    output_sections: list[CompressionOutputSection]


@dataclass(frozen=True)
class _SegmentCompressionCandidate:
    should_compress: bool
    token_count: int


@dataclass(frozen=True)
class _ModelGateEvaluation:
    should_run: bool
    decision: str
    reason: str | None
    candidate_tokens: int
    candidate_chars: int
    expected_incremental_savings_tokens: int
    expected_incremental_reduction: float
    projected_latency_ms: float | None
    projected_chunk_count: int
    protected_density: float
    structured_density: float
    placeholder_count: int
    identifier_density: float


@dataclass(frozen=True)
class _DeterministicComponentSavings:
    whitespace_tokens_saved: int = 0
    toon_tokens_saved: int = 0
    json_minify_tokens_saved: int = 0
    html_markdown_tokens_saved: int = 0
    nocompress_wrapper_tokens_saved: int = 0


@dataclass(frozen=True)
class _DuplicateBlockDiagnostics:
    candidate_count: int = 0
    candidate_tokens: int = 0


@dataclass(frozen=True)
class _LiteralPlaceholderingResult:
    segments: list[CompressionSegment]
    placeholder_count: int = 0
    tokens_saved: int = 0


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
        self.model_auto_enabled = DEFAULT_MODEL_AUTO_ENABLED
        self.allow_cpu_model_auto = DEFAULT_ALLOW_CPU_MODEL_AUTO
        self.min_model_candidate_tokens = max(0, DEFAULT_MIN_MODEL_CANDIDATE_TOKENS)
        self.min_model_incremental_savings_tokens = max(
            0,
            DEFAULT_MIN_MODEL_INCREMENTAL_SAVINGS_TOKENS,
        )
        self.min_model_incremental_reduction = max(
            0.0,
            DEFAULT_MIN_MODEL_INCREMENTAL_REDUCTION,
        )
        self.max_model_projected_latency_ms = max(
            0.0,
            DEFAULT_MAX_MODEL_PROJECTED_LATENCY_MS,
        )
        self.max_model_auto_placeholders = max(0, DEFAULT_MAX_MODEL_AUTO_PLACEHOLDERS)
        self.cold_model_tight_latency_budget_ms = max(
            0.0,
            DEFAULT_COLD_MODEL_TIGHT_LATENCY_BUDGET_MS,
        )
        self.max_protected_density = max(0.0, DEFAULT_MAX_PROTECTED_DENSITY)
        self.max_structured_density = max(0.0, DEFAULT_MAX_STRUCTURED_DENSITY)
        self.skip_model_if_deterministic_reduction_gte = max(
            0.0,
            DEFAULT_SKIP_MODEL_IF_DETERMINISTIC_REDUCTION_GTE,
        )
        self.min_duplicate_block_tokens = max(1, DEFAULT_MIN_DUPLICATE_BLOCK_TOKENS)
        self.literal_placeholdering_enabled = DEFAULT_ENABLE_LITERAL_PLACEHOLDERING
        self.min_literal_placeholder_savings_tokens = max(
            0,
            DEFAULT_MIN_LITERAL_PLACEHOLDER_SAVINGS_TOKENS,
        )
        self.min_literal_placeholder_reduction = max(
            0.0,
            DEFAULT_MIN_LITERAL_PLACEHOLDER_REDUCTION,
        )
        self.gpu_p50_fixed_overhead_ms = _parse_optional_float(
            DEFAULT_GPU_P50_FIXED_OVERHEAD_MS,
        )
        self.gpu_p50_llmlingua_chunk_ms = _parse_optional_float(
            DEFAULT_GPU_P50_LINGUA_CHUNK_MS,
        )
        self.gpu_p50_token_estimate_ms = _parse_optional_float(
            DEFAULT_GPU_P50_TOKEN_ESTIMATE_MS,
        )
        self.cpu_p50_fixed_overhead_ms = _parse_optional_float(
            DEFAULT_CPU_P50_FIXED_OVERHEAD_MS,
        )
        self.cpu_p50_llmlingua_chunk_ms = _parse_optional_float(
            DEFAULT_CPU_P50_LINGUA_CHUNK_MS,
        )
        self.cpu_p50_token_estimate_ms = _parse_optional_float(
            DEFAULT_CPU_P50_TOKEN_ESTIMATE_MS,
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
                    source_text=segment.source_text,
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
            compressed=segment.kind in {"json_minified", "toon"},
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
        model_output_parts: list[str] = []
        force_token_count = 0

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
                model_output_parts.append(chunk.text)
                continue

            llmlingua_call_count += 1
            compressed_chunk = self._compress_prepared_model_chunk(
                compressor=compressor,
                chunk=chunk,
                target_rate=target_rate,
                include_sections=include_sections,
                tenant_profile=tenant_profile,
                max_force_tokens=max_force_tokens,
                timings=timings,
            )
            model_output_parts.append(compressed_chunk.model_output_text)
            force_token_count += compressed_chunk.force_token_count
            expanded_chunk = compressed_chunk.expanded
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
                model_output_text="".join(model_output_parts),
                force_token_count=force_token_count,
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
    ) -> _CompressedModelChunkResult:
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
            return _CompressedModelChunkResult(
                expanded=None,
                model_output_text=compressed_model_text,
                force_token_count=len(force_tokens),
            )
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
        return _CompressedModelChunkResult(
            expanded=expanded,
            model_output_text=compressed_model_text,
            force_token_count=len(force_tokens),
        )

    def _should_compress_segment(
        self,
        segment: CompressionSegment,
        target_rate: float,
        tenant_profile: TenantCompressionProfile,
    ) -> bool:
        return self._compression_candidate_for_segment(
            segment,
            target_rate,
            tenant_profile,
        ).should_compress

    def _compression_candidate_for_segment(
        self,
        segment: CompressionSegment,
        target_rate: float,
        tenant_profile: TenantCompressionProfile,
    ) -> _SegmentCompressionCandidate:
        if not segment.compressible or not segment.text.strip():
            return _SegmentCompressionCandidate(False, 0)
        if target_rate >= 1.0:
            return _SegmentCompressionCandidate(False, 0)
        if len(segment.text.strip()) < self.min_segment_chars:
            return _SegmentCompressionCandidate(False, 0)

        token_count = self.estimate_compression_tokens(
            segment.text,
            tenant_profile,
        ).count
        return _SegmentCompressionCandidate(
            should_compress=token_count >= self.min_segment_tokens,
            token_count=token_count,
        )

    def _evaluate_model_gate(
        self,
        *,
        mode: str,
        text: str,
        segments: list[CompressionSegment],
        should_compress_segments: list[bool],
        segment_candidates: list[_SegmentCompressionCandidate],
        prepared: _PreparedModelInput | None,
        target_rate: float,
        deterministic_reduction: float,
        latency_budget_ms: float | None,
        allow_cpu_model_auto: bool | None,
    ) -> _ModelGateEvaluation:
        candidate_tokens = sum(
            candidate.token_count
            for candidate, should_compress in zip(
                segment_candidates,
                should_compress_segments,
                strict=True,
            )
            if should_compress
        )
        candidate_chars = sum(
            len(segment.text)
            for segment, should_compress in zip(
                segments,
                should_compress_segments,
                strict=True,
            )
            if should_compress
        )
        model_input_chars = 0 if prepared is None else len(prepared.text)
        placeholder_count = 0 if prepared is None else len(prepared.placeholders)
        projected_chunk_count = 0
        projected_latency_ms: float | None = None
        protected_density = 0.0
        identifier_density = 0.0
        structured_density = 0.0
        expected_incremental_reduction = 0.0
        expected_incremental_savings_tokens = 0

        def skip(reason: str) -> _ModelGateEvaluation:
            return _ModelGateEvaluation(
                should_run=False,
                decision="skip",
                reason=reason,
                candidate_tokens=candidate_tokens,
                candidate_chars=candidate_chars,
                expected_incremental_savings_tokens=(
                    expected_incremental_savings_tokens
                ),
                expected_incremental_reduction=expected_incremental_reduction,
                projected_latency_ms=projected_latency_ms,
                projected_chunk_count=projected_chunk_count,
                protected_density=protected_density,
                structured_density=structured_density,
                placeholder_count=placeholder_count,
                identifier_density=identifier_density,
            )

        def run() -> _ModelGateEvaluation:
            return _ModelGateEvaluation(
                should_run=True,
                decision="run",
                reason=None,
                candidate_tokens=candidate_tokens,
                candidate_chars=candidate_chars,
                expected_incremental_savings_tokens=(
                    expected_incremental_savings_tokens
                ),
                expected_incremental_reduction=expected_incremental_reduction,
                projected_latency_ms=projected_latency_ms,
                projected_chunk_count=projected_chunk_count,
                protected_density=protected_density,
                structured_density=structured_density,
                placeholder_count=placeholder_count,
                identifier_density=identifier_density,
            )

        if mode == COMPRESSION_MODE_DETERMINISTIC:
            return skip("llmlingua_skipped_mode_deterministic")
        if target_rate >= 1.0:
            return skip("llmlingua_skipped_aggressiveness_zero")
        if candidate_tokens <= 0:
            return skip("llmlingua_skipped_no_candidate_prose")
        if self._request_requires_exact_output(text):
            return skip("llmlingua_skipped_exact_output_context")

        cpu_model_auto_allowed = (
            self.allow_cpu_model_auto
            if allow_cpu_model_auto is None
            else allow_cpu_model_auto
        )
        if (
            mode == COMPRESSION_MODE_MODEL_AUTO
            and self._device_is_cpu()
            and not cpu_model_auto_allowed
        ):
            return skip("llmlingua_skipped_cpu_auto_disabled")
        if (
            mode == COMPRESSION_MODE_MODEL_AUTO
            and candidate_tokens < self.min_model_candidate_tokens
        ):
            return skip("llmlingua_skipped_low_candidate_tokens")
        if mode == COMPRESSION_MODE_MODEL_AUTO and (
            deterministic_reduction
            >= self.skip_model_if_deterministic_reduction_gte
        ):
            return skip("llmlingua_skipped_deterministic_savings_sufficient")

        protected_density = self._protected_density_for_model_candidates(
            segments,
            should_compress_segments,
        )
        identifier_density = self._identifier_density_for_model_candidates(
            segments,
            should_compress_segments,
        )
        structured_density = self._structured_density(segments)
        expected_incremental_reduction = self._expected_model_reduction(
            candidate_tokens=candidate_tokens,
            candidate_segment_count=sum(should_compress_segments),
            deterministic_reduction=deterministic_reduction,
            protected_density=protected_density,
            identifier_density=identifier_density,
            structured_density=structured_density,
        )
        expected_incremental_savings_tokens = int(
            candidate_tokens * expected_incremental_reduction
        )
        projected_chunk_count = self._projected_model_chunk_count(model_input_chars)
        projected_latency_ms = self._project_model_latency_ms(projected_chunk_count)

        if mode == COMPRESSION_MODE_MODEL_FORCE:
            return run()

        if protected_density > self.max_protected_density:
            return skip("llmlingua_skipped_high_protected_density")
        if structured_density > self.max_structured_density:
            return skip("llmlingua_skipped_high_structured_density")
        if placeholder_count > self.max_model_auto_placeholders:
            return skip("llmlingua_skipped_high_placeholder_count")
        if (
            latency_budget_ms is not None
            and not self.is_loaded
            and latency_budget_ms <= self.cold_model_tight_latency_budget_ms
        ):
            return skip("llmlingua_skipped_cold_model_tight_latency_budget")
        if projected_latency_ms is None:
            return skip("llmlingua_skipped_missing_latency_baseline")
        max_latency_ms = (
            self.max_model_projected_latency_ms
            if latency_budget_ms is None
            else min(self.max_model_projected_latency_ms, max(0.0, latency_budget_ms))
        )
        if projected_latency_ms > max_latency_ms:
            return skip("llmlingua_skipped_high_projected_latency")
        if (
            expected_incremental_savings_tokens
            < self.min_model_incremental_savings_tokens
            or expected_incremental_reduction
            < self.min_model_incremental_reduction
        ):
            return skip("llmlingua_skipped_low_expected_incremental_savings")

        return run()

    def _projected_model_chunk_count(self, model_input_chars: int) -> int:
        if model_input_chars <= 0:
            return 0
        if self.max_model_chunk_chars <= 0:
            return 1
        return max(1, math.ceil(model_input_chars / self.max_model_chunk_chars))

    def _project_model_latency_ms(self, projected_chunk_count: int) -> float | None:
        if projected_chunk_count <= 0:
            return 0.0

        fixed_ms: float | None
        chunk_ms: float | None
        token_ms: float | None
        if self._device_is_cpu():
            fixed_ms = self.cpu_p50_fixed_overhead_ms
            chunk_ms = self.cpu_p50_llmlingua_chunk_ms
            token_ms = self.cpu_p50_token_estimate_ms
        else:
            fixed_ms = self.gpu_p50_fixed_overhead_ms
            chunk_ms = self.gpu_p50_llmlingua_chunk_ms
            token_ms = self.gpu_p50_token_estimate_ms

        if fixed_ms is None or chunk_ms is None or token_ms is None:
            return None
        return fixed_ms + projected_chunk_count * chunk_ms + token_ms

    def _protected_density_for_model_candidates(
        self,
        segments: list[CompressionSegment],
        should_compress_segments: list[bool],
    ) -> float:
        candidate_chars = 0
        protected_chars = 0
        for segment, should_compress in zip(
            segments,
            should_compress_segments,
            strict=True,
        ):
            if not should_compress:
                continue
            candidate_chars += len(segment.text)
            protected_chars += sum(
                len(span.text)
                for span in self._protected_spans_for_model_input(segment.text)
            )

        if candidate_chars <= 0:
            return 0.0
        return protected_chars / candidate_chars

    def _identifier_density_for_model_candidates(
        self,
        segments: list[CompressionSegment],
        should_compress_segments: list[bool],
    ) -> float:
        candidate_chars = 0
        identifier_chars = 0
        identifier_kinds = {
            "constant",
            "email",
            "identifier",
            "money",
            "number",
            "url",
        }
        for segment, should_compress in zip(
            segments,
            should_compress_segments,
            strict=True,
        ):
            if not should_compress:
                continue
            candidate_chars += len(segment.text)
            identifier_chars += sum(
                len(span.text)
                for span in protected_spans_for_text(segment.text)
                if span.kind in identifier_kinds
            )

        if candidate_chars <= 0:
            return 0.0
        return identifier_chars / candidate_chars

    def _structured_density(self, segments: list[CompressionSegment]) -> float:
        total_chars = sum(len(segment.text) for segment in segments)
        if total_chars <= 0:
            return 0.0

        structured_chars = sum(
            len(segment.text)
            for segment in segments
            if not segment.compressible
            or segment.kind
            in {
                "code",
                "html",
                "html_markdown",
                "json",
                "nocompress",
                "toon",
                "verbatim",
            }
        )
        return structured_chars / total_chars

    def _expected_model_reduction(
        self,
        *,
        candidate_tokens: int,
        candidate_segment_count: int,
        deterministic_reduction: float,
        protected_density: float,
        identifier_density: float,
        structured_density: float,
    ) -> float:
        if candidate_tokens <= 0:
            return 0.0

        expected = 0.08 if structured_density < 0.10 else 0.05
        if protected_density > 0.10:
            expected -= 0.02
        if identifier_density > 0.10:
            expected -= 0.02
        average_segment_tokens = (
            candidate_tokens / candidate_segment_count
            if candidate_segment_count
            else 0.0
        )
        if average_segment_tokens and average_segment_tokens < 120:
            expected -= 0.02
        if deterministic_reduction >= 0.10:
            expected -= 0.02
        return max(0.0, expected)

    def _request_requires_exact_output(self, text: str) -> bool:
        lowered = text.lower()
        exact_terms = (
            "byte-stable",
            "byte stable",
            "byte-exact",
            "byte exact",
            "return exactly as written",
            "return exactly as provided",
            "return the input exactly",
            "output the input exactly",
            "preserve formatting exactly",
            "preserve whitespace exactly",
            "verbatim output",
            "do not modify the text",
            "do not change the text",
        )
        return any(term in lowered for term in exact_terms)

    def _device_is_cpu(self) -> bool:
        return self.device.strip().lower() in {"", "cpu"}

    def _normalize_compression_mode(self, mode: str | None) -> str:
        resolved = mode or COMPRESSION_MODE_MODEL_FORCE
        if resolved not in COMPRESSION_MODES:
            raise ValueError(
                "compression mode must be one of: "
                + ", ".join(sorted(COMPRESSION_MODES))
            )
        return resolved

    def _compression_path(
        self,
        *,
        input_text: str,
        deterministic_text: str,
        llmlingua_called: bool,
    ) -> str:
        if llmlingua_called:
            return COMPRESSION_PATH_DETERMINISTIC_PLUS_MODEL
        if deterministic_text == input_text:
            return COMPRESSION_PATH_UNCHANGED
        return COMPRESSION_PATH_DETERMINISTIC_ONLY

    def _deterministic_component_savings(
        self,
        segments: list[CompressionSegment],
        tenant_profile: TenantCompressionProfile,
    ) -> _DeterministicComponentSavings:
        source_by_category: dict[str, list[str]] = {
            "json_minify": [],
            "html_markdown": [],
            "nocompress_wrapper": [],
            "toon": [],
            "whitespace": [],
        }
        output_by_category: dict[str, list[str]] = {
            "json_minify": [],
            "html_markdown": [],
            "nocompress_wrapper": [],
            "toon": [],
            "whitespace": [],
        }

        for segment in segments:
            source_text = segment.source_text
            if source_text is None or source_text == segment.text:
                continue

            category = self._deterministic_savings_category(segment)
            if category is None:
                continue

            source_by_category[category].append(source_text)
            output_by_category[category].append(segment.text)

        return _DeterministicComponentSavings(
            whitespace_tokens_saved=self._token_savings_between_texts(
                "".join(source_by_category["whitespace"]),
                "".join(output_by_category["whitespace"]),
                tenant_profile,
            ),
            toon_tokens_saved=self._token_savings_between_texts(
                "".join(source_by_category["toon"]),
                "".join(output_by_category["toon"]),
                tenant_profile,
            ),
            json_minify_tokens_saved=self._token_savings_between_texts(
                "".join(source_by_category["json_minify"]),
                "".join(output_by_category["json_minify"]),
                tenant_profile,
            ),
            html_markdown_tokens_saved=self._token_savings_between_texts(
                "".join(source_by_category["html_markdown"]),
                "".join(output_by_category["html_markdown"]),
                tenant_profile,
            ),
            nocompress_wrapper_tokens_saved=self._token_savings_between_texts(
                "".join(source_by_category["nocompress_wrapper"]),
                "".join(output_by_category["nocompress_wrapper"]),
                tenant_profile,
            ),
        )

    def _deterministic_savings_category(
        self,
        segment: CompressionSegment,
    ) -> str | None:
        if segment.kind == "toon":
            return "toon"
        if segment.kind == "json_minified":
            return "json_minify"
        if segment.kind == "html_markdown":
            return "html_markdown"
        if segment.kind == "nocompress":
            return "nocompress_wrapper"
        if segment.kind == "prose":
            return "whitespace"
        return None

    def _token_savings_between_texts(
        self,
        original_text: str,
        compressed_text: str,
        tenant_profile: TenantCompressionProfile,
    ) -> int:
        if not original_text or original_text == compressed_text:
            return 0

        original_tokens = self.estimate_compression_tokens(
            original_text,
            tenant_profile,
        ).count
        compressed_tokens = self.estimate_compression_tokens(
            compressed_text,
            tenant_profile,
        ).count
        return max(0, original_tokens - compressed_tokens)

    def _duplicate_block_diagnostics(
        self,
        segments: list[CompressionSegment],
        tenant_profile: TenantCompressionProfile,
    ) -> _DuplicateBlockDiagnostics:
        seen: dict[str, int] = {}
        duplicate_count = 0
        duplicate_tokens = 0

        for segment in segments:
            if not segment.compressible:
                continue
            for block in self._duplicate_candidate_blocks(segment.text):
                normalized = self._normalize_duplicate_block(block)
                if not normalized:
                    continue
                token_count = self.estimate_compression_tokens(
                    normalized,
                    tenant_profile,
                ).count
                if token_count < self.min_duplicate_block_tokens:
                    continue
                previous_count = seen.get(normalized, 0)
                seen[normalized] = previous_count + 1
                if previous_count:
                    duplicate_count += 1
                    duplicate_tokens += token_count

        return _DuplicateBlockDiagnostics(
            candidate_count=duplicate_count,
            candidate_tokens=duplicate_tokens,
        )

    def _duplicate_candidate_blocks(self, text: str) -> list[str]:
        blocks = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", text)
            if paragraph.strip()
        ]
        if len(blocks) <= 1:
            blocks.extend(line.strip() for line in text.splitlines() if line.strip())
        return blocks

    def _normalize_duplicate_block(self, block: str) -> str:
        return re.sub(r"\s+", " ", block).strip()

    def _apply_repeated_literal_placeholdering(
        self,
        segments: list[CompressionSegment],
        tenant_profile: TenantCompressionProfile,
        *,
        exact_output_context: bool,
    ) -> _LiteralPlaceholderingResult:
        if not self.literal_placeholdering_enabled or exact_output_context:
            return _LiteralPlaceholderingResult(segments=segments)

        source_text = "".join(segment.text for segment in segments)
        candidates = self._literal_placeholder_candidates(segments)
        if not candidates:
            return _LiteralPlaceholderingResult(segments=segments)

        placeholder_map: dict[str, str] = {}
        used_placeholders: set[str] = set()
        for index, literal in enumerate(candidates):
            placeholder = self._literal_placeholder_for_index(index)
            while placeholder in source_text or placeholder in used_placeholders:
                index += 1
                placeholder = self._literal_placeholder_for_index(index)
            placeholder_map[literal] = placeholder
            used_placeholders.add(placeholder)

        legend = "".join(
            f"{placeholder}={literal}\n"
            for literal, placeholder in placeholder_map.items()
        )
        updated_segments = [
            CompressionSegment(
                text=legend,
                compressible=False,
                kind="literal_map",
                source_text="",
            )
        ]
        for segment in segments:
            if not segment.compressible:
                updated_segments.append(segment)
                continue

            updated_text = segment.text
            for literal, placeholder in placeholder_map.items():
                updated_text = updated_text.replace(literal, placeholder)

            if updated_text == segment.text:
                updated_segments.append(segment)
                continue

            updated_segments.append(
                CompressionSegment(
                    text=updated_text,
                    compressible=False,
                    kind="literal_placeholdered",
                    source_text=segment.text,
                )
            )

        updated_text = "".join(segment.text for segment in updated_segments)
        original_tokens = self.estimate_compression_tokens(
            source_text,
            tenant_profile,
        ).count
        updated_tokens = self.estimate_compression_tokens(
            updated_text,
            tenant_profile,
        ).count
        tokens_saved = max(0, original_tokens - updated_tokens)
        reduction = 0.0 if original_tokens <= 0 else tokens_saved / original_tokens
        if (
            tokens_saved < self.min_literal_placeholder_savings_tokens
            or reduction < self.min_literal_placeholder_reduction
        ):
            return _LiteralPlaceholderingResult(segments=segments)

        return _LiteralPlaceholderingResult(
            segments=updated_segments,
            placeholder_count=len(placeholder_map),
            tokens_saved=tokens_saved,
        )

    def _literal_placeholder_candidates(
        self,
        segments: list[CompressionSegment],
    ) -> list[str]:
        counts: dict[str, int] = {}
        first_seen: dict[str, int] = {}
        order = 0
        for segment in segments:
            if not segment.compressible:
                continue
            for literal in self._literal_candidates_for_text(segment.text):
                counts[literal] = counts.get(literal, 0) + 1
                if literal not in first_seen:
                    first_seen[literal] = order
                    order += 1

        return [
            literal
            for literal in sorted(counts, key=lambda item: first_seen[item])
            if self._literal_placeholder_min_occurrences(literal) <= counts[literal]
        ]

    def _literal_candidates_for_text(self, text: str) -> list[str]:
        candidates: list[str] = []
        for span in protected_spans_for_text(text):
            if span.kind == "url" and len(span.text) >= 32:
                candidates.append(span.text)
            elif span.kind in {"constant", "identifier"} and len(span.text) >= 16:
                candidates.append(span.text)
        return candidates

    def _literal_placeholder_min_occurrences(self, literal: str) -> int:
        if literal.lower().startswith(("http://", "https://")):
            return 2
        return 3

    def _literal_placeholder_for_index(self, index: int) -> str:
        label = ""
        current = index
        while True:
            label = chr(ord("A") + (current % 26)) + label
            current = current // 26 - 1
            if current < 0:
                break
        return f"[{label}]"

    def compress(
        self,
        text: str,
        aggressiveness: float,
        include_sections: bool = True,
        tenant_profile: TenantCompressionProfile | None = None,
        mode: str | None = None,
        latency_budget_ms: float | None = None,
        allow_cpu_model_auto: bool | None = None,
        collect_diagnostics: bool = True,
        apply_deterministic_transforms: bool = True,
        evaluate_disabled_transforms: bool = False,
        evaluation_constraints: dict[str, list[str]] | None = None,
        request_id: str | None = None,
    ) -> CompressionResult:
        start = time.perf_counter()
        timings = dict.fromkeys(TIMED_PHASES, 0.0)
        profile = tenant_profile or TenantCompressionProfile()
        model_was_loaded = self.is_loaded
        compression_mode = self._normalize_compression_mode(mode)

        phase_start = time.perf_counter()
        target_rate = self.target_rate_for_aggressiveness(
            aggressiveness,
            min_rate_override=profile.min_rate,
        )
        timings["target_rate_ms"] = _elapsed_ms(phase_start)

        phase_start = time.perf_counter()
        prepared_segments = (
            self.preprocessor.prepare(text)
            if apply_deterministic_transforms
            else [CompressionSegment(text=text, compressible=True, kind="prose", source_text=text)]
        )
        timings["preprocessing_ms"] = _elapsed_ms(phase_start)
        preprocessed_text = "".join(segment.text for segment in prepared_segments)

        phase_start = time.perf_counter()
        segments = (
            self._apply_force_drop_phrases(prepared_segments, profile)
            if apply_deterministic_transforms
            else prepared_segments
        )
        timings["force_drop_ms"] = _elapsed_ms(phase_start)
        force_dropped_text = "".join(segment.text for segment in segments)
        exact_output_context = self._request_requires_exact_output(text)
        literal_placeholdering = (
            self._apply_repeated_literal_placeholdering(
                segments,
                profile,
                exact_output_context=exact_output_context,
            )
            if apply_deterministic_transforms
            else _LiteralPlaceholderingResult(segments=segments)
        )
        segments = literal_placeholdering.segments
        deterministic_text = "".join(segment.text for segment in segments)

        phase_start = time.perf_counter()
        segment_candidates = [
            self._compression_candidate_for_segment(segment, target_rate, profile)
            for segment in segments
        ]
        should_compress_segments = [
            candidate.should_compress for candidate in segment_candidates
        ]
        timings["segment_selection_ms"] = _elapsed_ms(phase_start)

        phase_start = time.perf_counter()
        original_estimate = self.estimate_compression_tokens(text, profile)
        preprocessed_estimate = (
            original_estimate
            if preprocessed_text == text
            else self.estimate_compression_tokens(preprocessed_text, profile)
        )
        force_dropped_estimate = (
            preprocessed_estimate
            if force_dropped_text == preprocessed_text
            else self.estimate_compression_tokens(force_dropped_text, profile)
        )
        deterministic_estimate = (
            force_dropped_estimate
            if deterministic_text == force_dropped_text
            else self.estimate_compression_tokens(deterministic_text, profile)
        )
        timings["token_estimate_ms"] = _elapsed_ms(phase_start)

        deterministic_reduction = 0.0
        if original_estimate.count:
            deterministic_reduction = max(
                0.0,
                1.0 - (deterministic_estimate.count / original_estimate.count),
            )
        preprocessing_tokens_saved = max(
            0,
            original_estimate.count - preprocessed_estimate.count,
        )
        force_drop_tokens_saved = max(
            0,
            preprocessed_estimate.count - force_dropped_estimate.count,
        )
        phase_start = time.perf_counter()
        if collect_diagnostics:
            component_savings = self._deterministic_component_savings(
                prepared_segments,
                profile,
            )
            duplicate_blocks = self._duplicate_block_diagnostics(segments, profile)
        else:
            component_savings = _DeterministicComponentSavings()
            duplicate_blocks = _DuplicateBlockDiagnostics()
        timings["diagnostics_ms"] += _elapsed_ms(phase_start)

        prepared: _PreparedModelInput | None = None
        chunk_stats: _ChunkedCompressionStats | None = None
        fallback_used = False
        fallback_reason = None
        llmlingua_called = False
        if any(should_compress_segments):
            phase_start = time.perf_counter()
            prepared = self._prepare_model_input(segments, should_compress_segments)
            timings["model_input_ms"] = _elapsed_ms(phase_start)

        phase_start = time.perf_counter()
        model_gate = self._evaluate_model_gate(
            mode=compression_mode,
            text=text,
            segments=segments,
            should_compress_segments=should_compress_segments,
            segment_candidates=segment_candidates,
            prepared=prepared,
            target_rate=target_rate,
            deterministic_reduction=deterministic_reduction,
            latency_budget_ms=latency_budget_ms,
            allow_cpu_model_auto=allow_cpu_model_auto,
        )
        timings["model_gate_ms"] = _elapsed_ms(phase_start)

        if model_gate.should_run:
            phase_start = time.perf_counter()
            compressor = self._load_for_profile(profile)
            timings["model_load_ms"] = _elapsed_ms(phase_start)

            if prepared is None:
                phase_start = time.perf_counter()
                prepared = self._prepare_model_input(
                    segments,
                    should_compress_segments,
                )
                timings["model_input_ms"] += _elapsed_ms(phase_start)

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
        compressed_estimate = (
            deterministic_estimate
            if compressed_text == deterministic_text
            else self.estimate_compression_tokens(compressed_text, profile)
        )
        timings["token_estimate_ms"] += _elapsed_ms(phase_start)

        reduction = 0.0
        if original_estimate.count:
            reduction = max(
                0.0,
                1.0 - (compressed_estimate.count / original_estimate.count),
            )
        total_ms = _elapsed_ms(start)
        compression_path = self._compression_path(
            input_text=text,
            deterministic_text=deterministic_text,
            llmlingua_called=llmlingua_called,
        )
        token_estimator = merge_token_estimator_names(
            [
                original_estimate.estimator,
                preprocessed_estimate.estimator,
                force_dropped_estimate.estimator,
                deterministic_estimate.estimator,
                compressed_estimate.estimator,
            ]
        )
        token_savings = build_token_savings(
            original_tokens=original_estimate.count,
            after_deterministic_tokens=deterministic_estimate.count,
            final_tokens=compressed_estimate.count,
            model_ran=llmlingua_called,
            fallback_used=fallback_used,
            token_estimator=token_estimator,
        )
        diagnostics = None
        if collect_diagnostics:
            phase_start = time.perf_counter()
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
                deterministic_original_tokens=original_estimate.count,
                deterministic_output_tokens=deterministic_estimate.count,
                deterministic_tokens_saved=(
                    token_savings.deterministic_tokens_saved
                ),
                deterministic_reduction=token_savings.deterministic_reduction,
                deterministic_input_chars=len(text),
                deterministic_output_chars=len(deterministic_text),
                preprocessing_tokens_saved=preprocessing_tokens_saved,
                force_drop_tokens_saved=force_drop_tokens_saved,
                whitespace_tokens_saved=component_savings.whitespace_tokens_saved,
                toon_tokens_saved=component_savings.toon_tokens_saved,
                json_minify_tokens_saved=component_savings.json_minify_tokens_saved,
                html_markdown_tokens_saved=(
                    component_savings.html_markdown_tokens_saved
                ),
                nocompress_wrapper_tokens_saved=(
                    component_savings.nocompress_wrapper_tokens_saved
                ),
                skipped_model_candidate_tokens=(
                    0 if model_gate.should_run else model_gate.candidate_tokens
                ),
                literal_placeholder_count=literal_placeholdering.placeholder_count,
                literal_placeholder_tokens_saved=literal_placeholdering.tokens_saved,
                model_incremental_tokens_saved=(
                    token_savings.model_incremental_tokens_saved
                ),
                model_incremental_reduction=(
                    token_savings.model_incremental_reduction
                ),
                duplicate_block_candidate_count=duplicate_blocks.candidate_count,
                duplicate_block_candidate_tokens=duplicate_blocks.candidate_tokens,
                compression_mode=compression_mode,
                compression_path=compression_path,
                model_gate=model_gate,
                analytics=build_detailed_analytics(
                    service=self,
                    input_text=text,
                    preprocessed_text=preprocessed_text,
                    force_dropped_text=force_dropped_text,
                    pipeline_text=deterministic_text,
                    deterministic_text=(
                        prepared.text
                        if llmlingua_called and prepared is not None
                        else deterministic_text
                    ),
                    model_output_text=(
                        chunk_stats.model_output_text
                        if llmlingua_called and chunk_stats is not None
                        else deterministic_text
                    ),
                    final_text=compressed_text,
                    prepared_segments=prepared_segments,
                    final_segments=segments,
                    profile=profile,
                    token_estimator=token_estimator,
                    original_tokens=original_estimate.count,
                    deterministic_tokens=(
                        self.estimate_compression_tokens(prepared.text, profile).count
                        if llmlingua_called and prepared is not None
                        else deterministic_estimate.count
                    ),
                    post_deterministic_tokens=deterministic_estimate.count,
                    model_output_tokens=(
                        self.estimate_compression_tokens(
                            chunk_stats.model_output_text,
                            profile,
                        ).count
                        if llmlingua_called and chunk_stats is not None
                        else deterministic_estimate.count
                    ),
                    final_tokens=compressed_estimate.count,
                    target_rate=target_rate,
                    model_called=llmlingua_called,
                    model_call_count=(
                        0 if chunk_stats is None else chunk_stats.llmlingua_call_count
                    ),
                    model_chunk_count=(
                        0 if chunk_stats is None else chunk_stats.chunk_count
                    ),
                    model_reason=fallback_reason or model_gate.reason,
                    placeholder_tokens=(
                        []
                        if prepared is None
                        else [item.token for item in prepared.placeholders]
                    ),
                    force_token_count=(
                        chunk_stats.force_token_count
                        if chunk_stats is not None
                        else (
                            len(force_tokens_for_text(prepared.text))
                            if prepared is not None
                            else len(force_tokens_for_text(deterministic_text))
                        ) + len(profile.force_keep_tokens)
                    ),
                    duplicate_candidate_count=duplicate_blocks.candidate_count,
                    duplicate_candidate_tokens=duplicate_blocks.candidate_tokens,
                    attribution_residual_tokens=(
                        token_savings.attribution_residual_tokens
                    ),
                    evaluate_disabled_transforms=evaluate_disabled_transforms,
                    evaluation_constraints=evaluation_constraints,
                    request_id=request_id,
                    cold_model_load=llmlingua_called and not model_was_loaded,
                ),
            )
            timings["diagnostics_ms"] += _elapsed_ms(phase_start)
        warnings = (
            [model_gate.reason]
            if model_gate.decision == "skip" and model_gate.reason is not None
            else []
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
            token_estimator=token_estimator,
            diagnostics=diagnostics,
            compression_mode=compression_mode,
            compression_path=compression_path,
            warnings=warnings,
            token_savings=token_savings,
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
        deterministic_original_tokens: int,
        deterministic_output_tokens: int,
        deterministic_tokens_saved: int,
        deterministic_reduction: float,
        deterministic_input_chars: int,
        deterministic_output_chars: int,
        preprocessing_tokens_saved: int,
        force_drop_tokens_saved: int,
        whitespace_tokens_saved: int,
        toon_tokens_saved: int,
        json_minify_tokens_saved: int,
        html_markdown_tokens_saved: int,
        nocompress_wrapper_tokens_saved: int,
        skipped_model_candidate_tokens: int,
        literal_placeholder_count: int,
        literal_placeholder_tokens_saved: int,
        model_incremental_tokens_saved: int,
        model_incremental_reduction: float,
        duplicate_block_candidate_count: int,
        duplicate_block_candidate_tokens: int,
        compression_mode: str,
        compression_path: str,
        model_gate: _ModelGateEvaluation,
        analytics: DetailedAnalytics | None = None,
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
            model_gate_ms=timings["model_gate_ms"],
            diagnostics_ms=timings["diagnostics_ms"],
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
            deterministic_original_tokens=deterministic_original_tokens,
            deterministic_output_tokens=deterministic_output_tokens,
            deterministic_tokens_saved=deterministic_tokens_saved,
            deterministic_reduction=deterministic_reduction,
            deterministic_input_chars=deterministic_input_chars,
            deterministic_output_chars=deterministic_output_chars,
            preprocessing_tokens_saved=preprocessing_tokens_saved,
            force_drop_tokens_saved=force_drop_tokens_saved,
            whitespace_tokens_saved=whitespace_tokens_saved,
            toon_tokens_saved=toon_tokens_saved,
            json_minify_tokens_saved=json_minify_tokens_saved,
            html_markdown_tokens_saved=html_markdown_tokens_saved,
            nocompress_wrapper_tokens_saved=nocompress_wrapper_tokens_saved,
            skipped_model_candidate_tokens=skipped_model_candidate_tokens,
            literal_placeholder_count=literal_placeholder_count,
            literal_placeholder_tokens_saved=literal_placeholder_tokens_saved,
            model_incremental_tokens_saved=model_incremental_tokens_saved,
            model_incremental_reduction=model_incremental_reduction,
            protected_segment_count=sum(
                1 for segment in segments if not segment.compressible
            ),
            toon_segment_count=segment_kinds.get("toon", 0),
            json_minified_segment_count=segment_kinds.get("json_minified", 0),
            duplicate_block_candidate_count=duplicate_block_candidate_count,
            duplicate_block_candidate_tokens=duplicate_block_candidate_tokens,
            compression_mode=compression_mode,
            compression_path=compression_path,
            model_gate_decision=model_gate.decision,
            model_gate_reason=model_gate.reason,
            model_candidate_tokens=model_gate.candidate_tokens,
            model_candidate_chars=model_gate.candidate_chars,
            model_expected_incremental_savings_tokens=(
                model_gate.expected_incremental_savings_tokens
            ),
            model_expected_incremental_reduction=(
                model_gate.expected_incremental_reduction
            ),
            model_projected_latency_ms=model_gate.projected_latency_ms,
            model_projected_chunk_count=model_gate.projected_chunk_count,
            protected_density=model_gate.protected_density,
            structured_density=model_gate.structured_density,
            identifier_density=model_gate.identifier_density,
            analytics=analytics,
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


def _parse_optional_float(raw_value: str | None) -> float | None:
    if raw_value is None or not raw_value.strip():
        return None
    try:
        return float(raw_value)
    except ValueError:
        LOGGER.warning("Ignoring invalid numeric compressor config %r", raw_value)
        return None


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
