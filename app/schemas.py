from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.token_estimator import REGEX_TOKEN_ESTIMATOR

DEFAULT_AGGRESSIVENESS = 0.15
CompressionMode = Literal["deterministic", "model_auto", "model_force"]


class TenantCompressionSettings(BaseModel):
    profile_id: str | None = Field(
        default=None,
        min_length=1,
        description="Request-supplied tenant profile version or label.",
    )
    default_aggressiveness: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Tenant default used when the request does not explicitly set "
            "aggressiveness."
        ),
    )
    min_rate: float | None = Field(
        default=None,
        ge=0.05,
        le=1.0,
        description=(
            "Tenant-specific lower bound for LLMLingua retention rate. "
            "Higher values preserve more tokens."
        ),
    )
    force_keep_tokens: list[str] = Field(
        default_factory=list,
        description="Exact tokens or short terms that should be forced to survive.",
    )
    force_drop_phrases: list[str] = Field(
        default_factory=list,
        description="Exact compressible boilerplate phrases to drop before model compression.",
    )


class CompressRequest(BaseModel):
    tenant_id: str | None = Field(
        default=None,
        description="Tenant identity for request-scoped compression settings.",
    )
    tenant_profile: TenantCompressionSettings | None = Field(
        default=None,
        description="Tenant-specific compression rules supplied by the API caller.",
    )
    text: str = Field(..., min_length=1, description="Text to compress.")
    aggressiveness: float = Field(
        default=DEFAULT_AGGRESSIVENESS,
        ge=0.0,
        le=1.0,
        description="0.0 keeps almost everything; 1.0 is most aggressive.",
    )
    mode: CompressionMode | None = Field(
        default=None,
        description=(
            "Compression path. Omit on /compress for legacy model-force behavior; "
            "use deterministic to skip LLMLingua."
        ),
    )
    latency_budget_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Optional synchronous latency budget for model_auto gating.",
    )
    allow_cpu_model_auto: bool | None = Field(
        default=None,
        description=(
            "Debug override for /compress: allow explicit model_auto requests "
            "to call LLMLingua on CPU when the ROI gate passes."
        ),
    )
    include_sections: bool = Field(
        default=False,
        description=(
            "Include per-section UI/debug output and word labels. "
            "Disabled by default to reduce latency and payload size."
        ),
    )
    include_diagnostics: bool = Field(
        default=False,
        description="Include phase-level timing and request-shape diagnostics.",
    )


class LabeledToken(BaseModel):
    text: str
    kept: bool


class OutputSection(BaseModel):
    text: str
    kind: str
    compressed: bool
    protected: bool
    labeled_tokens: list[LabeledToken] = Field(default_factory=list)


class CompressionTimingResponse(BaseModel):
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


class CompressionDiagnosticsResponse(BaseModel):
    timings: CompressionTimingResponse
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
    compression_mode: str = "model_force"
    compression_path: str = "unchanged"
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


class CompressResponse(BaseModel):
    compressed_text: str
    original_tokens: int
    compressed_tokens: int
    reduction: float
    aggressiveness: float
    target_rate: float
    model: str
    tenant_id: str
    compression_profile: str
    compression_profile_source: str
    training_sample_recorded: bool = False
    token_estimator: str = Field(default=REGEX_TOKEN_ESTIMATOR)
    compression_mode: str = "model_force"
    compression_path: str = "unchanged"
    warnings: list[str] = Field(default_factory=list)
    elapsed_ms: float
    labeled_tokens: list[LabeledToken] = Field(default_factory=list)
    output_sections: list[OutputSection] = Field(default_factory=list)
    diagnostics: CompressionDiagnosticsResponse | None = None


class V1CompressionSettings(BaseModel):
    mode: CompressionMode | None = Field(
        default=None,
        description=(
            "deterministic skips LLMLingua, model_auto uses the ROI gate, and "
            "model_force runs LLMLingua when safety and segment gates allow."
        ),
    )
    aggressiveness: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="0.0 keeps almost everything; 1.0 is most aggressive.",
    )
    latency_budget_ms: float | None = Field(
        default=None,
        ge=0.0,
        description="Optional synchronous latency budget for model_auto gating.",
    )
    compact_empty_user_messages: bool = Field(
        default=False,
        description="Drop empty user messages before text compression.",
    )
    compact_duplicate_user_text_parts: bool = Field(
        default=False,
        description="Drop exact duplicate user text parts within the same request.",
    )


class V1CompressRequest(BaseModel):
    tenant_id: str | None = Field(
        default=None,
        description="Tenant identity. X-Tenant-ID may be used instead.",
    )
    tenant_profile: TenantCompressionSettings | None = Field(
        default=None,
        description="Tenant-specific compression rules supplied by the API caller.",
    )
    model: str = Field(default="bear-2", description="Accepted for request compatibility.")
    input: str = Field(..., min_length=1, description="Text to compress.")
    compression_settings: V1CompressionSettings | None = None


class V1CompressResponse(BaseModel):
    output: str
    output_tokens: int
    input_tokens: int
    original_input_tokens: int
    tokens_saved: int
    compression_ratio: float
    token_estimator: str = Field(default=REGEX_TOKEN_ESTIMATOR)
    downstream_estimated_input_tokens: int | None = None
    downstream_estimated_output_tokens: int | None = None
    downstream_token_estimator: str | None = None
    compression_time: float
    tenant_id: str
    compression_profile: str
    compression_profile_source: str
    training_sample_recorded: bool = False
    warnings: list[str] = Field(default_factory=list)


class V1Message(BaseModel):
    role: str = Field(..., description="Vendor-style chat message role.")
    content: Any = Field(default=None, description="String content or content parts.")

    model_config = ConfigDict(extra="allow")


class V1MessagesCompressRequest(BaseModel):
    tenant_id: str | None = Field(
        default=None,
        description="Tenant identity. X-Tenant-ID may be used instead.",
    )
    tenant_profile: TenantCompressionSettings | None = Field(
        default=None,
        description="Tenant-specific compression rules supplied by the API caller.",
    )
    model: str = Field(
        default="bear-2",
        description="Accepted for request compatibility and preserved in output.",
    )
    messages: list[V1Message] = Field(..., min_length=1)
    compression_settings: V1CompressionSettings | None = None

    model_config = ConfigDict(extra="allow")


class V1MessageCompressionStats(BaseModel):
    index: int
    role: str
    original_tokens: int
    compressed_tokens: int
    tokens_saved: int
    compression_applied: bool
    compressed: bool
    text_parts: int
    compressed_text_parts: int
    skipped_reason: str | None = None


class V1MessagesCompressResponse(BaseModel):
    compressed_request: dict[str, Any]
    messages: list[dict[str, Any]]
    input_tokens: int
    output_tokens: int
    original_input_tokens: int
    tokens_saved: int
    compression_ratio: float
    compression_time: float
    user_input_tokens: int
    user_output_tokens: int
    user_tokens_saved: int
    non_user_tokens_preserved: int
    token_estimator: str = Field(default=REGEX_TOKEN_ESTIMATOR)
    downstream_estimated_input_tokens: int | None = None
    downstream_estimated_output_tokens: int | None = None
    downstream_token_estimator: str | None = None
    tenant_id: str
    compression_profile: str
    compression_profile_source: str
    training_sample_recorded: bool = False
    message_stats: list[V1MessageCompressionStats]
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    deployment_version: str
    deployment_timestamp: str
    model: str
    model_loaded: bool


class TokenEstimateRequest(BaseModel):
    text: str = Field(default="", description="Text to estimate.")
    model: str | None = Field(
        default=None,
        description=(
            "Optional downstream model name. Omit to use the compression model "
            "tokenizer/fallback."
        ),
    )


class TokenEstimateResponse(BaseModel):
    tokens: int
    token_estimator: str
    tokenizer_backed: bool


class EvalCaseResponse(BaseModel):
    id: str
    title: str
    category: str
    description: str
    text: str
    default_aggressiveness: float
    required_substrings: list[str] = Field(default_factory=list)
    required_whitespace_insensitive_substrings: list[str] = Field(default_factory=list)
    forbidden_substrings: list[str] = Field(default_factory=list)
    expected_section_kinds: list[str] = Field(default_factory=list)
    target_min_reduction: float | None = None
    max_elapsed_ms: float | None = None


class EvalRunRequest(BaseModel):
    case_ids: list[str] | None = Field(
        default=None,
        description="Case ids to run. Runs every case when omitted or empty.",
    )
    aggressiveness: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional aggressiveness override for every selected case.",
    )


class EvalQualityCheckResponse(BaseModel):
    id: str
    label: str
    passed: bool
    severity: str
    detail: str


class EvalRunCaseResponse(BaseModel):
    case_id: str
    title: str
    category: str
    passed: bool
    compressed_text: str
    original_tokens: int
    compressed_tokens: int
    reduction: float
    aggressiveness: float
    target_rate: float
    model: str
    elapsed_ms: float
    checks: list[EvalQualityCheckResponse]
    output_sections: list[OutputSection] = Field(default_factory=list)


class EvalRunResponse(BaseModel):
    passed: bool
    total_cases: int
    passed_cases: int
    failed_cases: int
    results: list[EvalRunCaseResponse]
