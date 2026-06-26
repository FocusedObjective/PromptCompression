from pydantic import BaseModel, Field

DEFAULT_AGGRESSIVENESS = 0.15


class CompressRequest(BaseModel):
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
