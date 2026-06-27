from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_AGGRESSIVENESS = 0.15


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
    include_sections: bool = Field(
        default=False,
        description=(
            "Include per-section UI/debug output and word labels. "
            "Disabled by default to reduce latency and payload size."
        ),
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
    elapsed_ms: float
    labeled_tokens: list[LabeledToken] = Field(default_factory=list)
    output_sections: list[OutputSection] = Field(default_factory=list)


class V1CompressionSettings(BaseModel):
    aggressiveness: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="0.0 keeps almost everything; 1.0 is most aggressive.",
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
    tenant_id: str
    compression_profile: str
    compression_profile_source: str
    training_sample_recorded: bool = False
    message_stats: list[V1MessageCompressionStats]
    warnings: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: str
    model: str
    model_loaded: bool


class EvalCaseResponse(BaseModel):
    id: str
    title: str
    category: str
    description: str
    text: str
    default_aggressiveness: float
    required_substrings: list[str] = Field(default_factory=list)
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
