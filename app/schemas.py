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
