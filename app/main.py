from fastapi import FastAPI, HTTPException

from app.compressor import CompressionRuntimeError, PromptCompressionService
from app.schemas import CompressRequest, CompressResponse, HealthResponse

app = FastAPI(
    title="Prompt Compression MVP",
    version="0.1.0",
    description="Fast prompt compression API backed by a token-classification model.",
)

compression_service = PromptCompressionService()


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        model=compression_service.model_name,
        model_loaded=compression_service.is_loaded,
    )


@app.post("/compress", response_model=CompressResponse)
def compress(request: CompressRequest) -> CompressResponse:
    try:
        result = compression_service.compress(
            text=request.text,
            aggressiveness=request.aggressiveness,
        )
    except CompressionRuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return CompressResponse(**result.__dict__)
