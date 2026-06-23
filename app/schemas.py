from pydantic import BaseModel, Field


class CompressRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Text to compress.")
    aggressiveness: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="0.0 keeps almost everything; 1.0 is most aggressive.",
    )


class LabeledToken(BaseModel):
    text: str
    kept: bool


class CompressResponse(BaseModel):
    compressed_text: str
    original_tokens: int
    compressed_tokens: int
    reduction: float
    aggressiveness: float
    target_rate: float
    model: str
    elapsed_ms: float
    labeled_tokens: list[LabeledToken] = []


class HealthResponse(BaseModel):
    status: str
    model: str
    model_loaded: bool
