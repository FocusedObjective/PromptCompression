"""Lightweight deterministic CPU edge for the prompt-compression API.

The module deliberately reuses the production Python compressor and HTTP
response builders.  Model packages are optional because PromptCompressionService
loads Torch and LLMLingua lazily, and this application only invokes it in
deterministic mode.  Explicit model requests are passed through to the GPU API.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import uuid
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

# Keep Transformers in tokenizer-only mode.  These flags are set before the
# shared API module is imported so an accidentally installed ML framework is
# not discovered or loaded by the CPU edge process.
os.environ.setdefault("USE_TORCH", "0")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("USE_FLAX", "0")

from app import main as core
from app.edge_origin import EdgeOriginClient, EdgeOriginUnavailable
from app.schemas import (
    CompressRequest,
    CompressResponse,
    HealthResponse,
    TokenEstimateRequest,
    TokenEstimateResponse,
    V1CompressRequest,
    V1CompressResponse,
    V1CompressionSettings,
    V1MessagesCompressRequest,
    V1MessagesCompressResponse,
    V1ResponsesCompressRequest,
    V1ResponsesCompressResponse,
)
from app.tenant_profiles import TenantCompressionProfile
from app.usagetap_authorization import UsageTapAuthorizationError
from app.version import DEPLOYMENT_TIMESTAMP, DEPLOYMENT_VERSION


LOGGER = logging.getLogger(__name__)
MODEL_AUTO = "model_auto"
MODEL_FORCE = "model_force"
DEFAULT_MAX_BODY_BYTES = 1_048_576

origin_client = EdgeOriginClient.from_environment()
max_body_bytes = int(os.getenv("EDGE_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES)))
preload_tokenizer = os.getenv("EDGE_PRELOAD_TOKENIZER", "true").strip().casefold() in {
    "1",
    "true",
    "yes",
    "on",
}
_warm_token_estimator = "not-loaded"


def warm_tokenizer() -> None:
    """Load the tokenizer without loading Torch or the compression model."""
    global _warm_token_estimator
    if not preload_tokenizer:
        _warm_token_estimator = "disabled"
        return
    estimate = core.compression_service.estimate_compression_tokens(
        "edge tokenizer warmup",
        TenantCompressionProfile(),
    )
    _warm_token_estimator = estimate.estimator
    if not estimate.tokenizer_backed:
        LOGGER.warning("CPU edge tokenizer warmup used fallback estimator %s", estimate.estimator)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    warm_tokenizer()
    yield


app = FastAPI(
    title="Prompt Compression CPU Edge",
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    # Do not call PromptCompressionService.runtime_info() here: that method
    # intentionally probes Torch for the GPU service.  The edge health path
    # must remain model-runtime-free even if a developer happens to have Torch
    # installed in the surrounding environment.
    runtime: dict[str, Any] = {
        "service_role": "deterministic-cpu-edge",
        "device": "cpu",
        "model_runtime": "none",
        "origin_configured": origin_client.configured,
        "max_body_bytes": max_body_bytes,
        "tokenizer_estimator": _warm_token_estimator,
        "response_cache": core.compression_response_cache.stats(),
    }
    return HealthResponse(
        status="ok",
        deployment_version=DEPLOYMENT_VERSION,
        deployment_timestamp=DEPLOYMENT_TIMESTAMP,
        model=core.compression_service.model_name,
        model_loaded=core.compression_service.is_loaded,
        runtime=runtime,
    )


@app.post("/tokens/estimate", response_model=TokenEstimateResponse)
def estimate_tokens(request: TokenEstimateRequest) -> TokenEstimateResponse:
    return core.estimate_tokens(request)


@app.post(
    "/compress",
    response_model=CompressResponse,
    response_model_exclude_none=True,
)
def compress_http(
    http_request: Request,
    http_response: Response,
    request: CompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    cache_control: Annotated[str | None, Header(alias="Cache-Control")] = None,
) -> CompressResponse | Response:
    _enforce_body_limit(request.model_dump_json(exclude_none=True).encode("utf-8"))
    mode = core._resolve_compress_mode(request)
    fallback_warning: str | None = None
    if mode == MODEL_FORCE:
        origin_response = _try_forward_model_request(
            path="/compress",
            payload=request.model_dump_json(exclude_none=True).encode("utf-8"),
            http_request=http_request,
            request_id=x_request_id,
        )
        if origin_response is not None:
            return origin_response
        fallback_warning = "edge_origin_unavailable_deterministic_fallback"
    elif mode == MODEL_AUTO:
        _validate_model_request_credential(http_request)
        planned_response, model_required = core.plan_compress_model_auto(
            request,
            x_tenant_id=x_tenant_id,
            x_request_id=x_request_id,
        )
        if model_required:
            origin_response = _try_forward_model_request(
                path="/compress",
                payload=request.model_dump_json(exclude_none=True).encode("utf-8"),
                http_request=http_request,
                request_id=x_request_id,
                credential_validated=True,
            )
            if origin_response is not None:
                return origin_response
            planned_response.warnings.append(
                "edge_origin_unavailable_deterministic_fallback"
            )
        return _finalize_local_model_auto(
            http_request=http_request,
            http_response=http_response,
            response=planned_response,
            route="/compress",
            input_tokens=planned_response.original_tokens,
            output_tokens=planned_response.compressed_tokens,
            elapsed_ms=planned_response.elapsed_ms,
            fallback=model_required,
        )

    local_request = request.model_copy(update={"mode": "deterministic"})
    pending = core.start_usage_tap_compression_authorization(
        http_request,
        http_request.headers.get("Authorization"),
    )
    response = core.compress_http(
        http_request=http_request,
        http_response=http_response,
        request=local_request,
        pending_authorization=pending,
        x_tenant_id=x_tenant_id,
        x_request_id=x_request_id,
        cache_control=cache_control,
    )
    if fallback_warning is not None:
        response.warnings.append(fallback_warning)
        http_response.headers["X-Edge-Decision"] = "fallback-deterministic"
    else:
        http_response.headers["X-Edge-Decision"] = "local-deterministic"
    return response


@app.post("/v1/compress", response_model=V1CompressResponse)
def compress_v1_http(
    http_request: Request,
    http_response: Response,
    request: V1CompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    cache_control: Annotated[str | None, Header(alias="Cache-Control")] = None,
) -> V1CompressResponse | Response:
    _enforce_body_limit(request.model_dump_json(exclude_none=True).encode("utf-8"))
    mode = core._resolve_v1_mode(request.compression_settings)
    fallback_warning: str | None = None
    if mode == MODEL_FORCE:
        origin_response = _try_forward_model_request(
            path="/v1/compress",
            payload=request.model_dump_json(exclude_none=True).encode("utf-8"),
            http_request=http_request,
            request_id=x_request_id,
        )
        if origin_response is not None:
            return origin_response
        fallback_warning = "edge_origin_unavailable_deterministic_fallback"
    elif mode == MODEL_AUTO:
        _validate_model_request_credential(http_request)
        planned_response, model_required = core.plan_v1_compress_model_auto(
            request,
            x_tenant_id=x_tenant_id,
        )
        if model_required:
            origin_response = _try_forward_model_request(
                path="/v1/compress",
                payload=request.model_dump_json(exclude_none=True).encode("utf-8"),
                http_request=http_request,
                request_id=x_request_id,
                credential_validated=True,
            )
            if origin_response is not None:
                return origin_response
            planned_response.warnings.append(
                "edge_origin_unavailable_deterministic_fallback"
            )
        return _finalize_local_model_auto(
            http_request=http_request,
            http_response=http_response,
            response=planned_response,
            route="/v1/compress",
            input_tokens=planned_response.input_tokens,
            output_tokens=planned_response.output_tokens,
            elapsed_ms=planned_response.compression_time,
            fallback=model_required,
        )

    local_request = request.model_copy(
        update={
            "compression_settings": _deterministic_settings(
                request.compression_settings
            )
        }
    )
    pending = core.start_usage_tap_compression_authorization(
        http_request,
        http_request.headers.get("Authorization"),
    )
    response = core.compress_v1_http(
        http_request=http_request,
        http_response=http_response,
        request=local_request,
        pending_authorization=pending,
        x_tenant_id=x_tenant_id,
        cache_control=cache_control,
    )
    if fallback_warning is not None:
        response.warnings.append(fallback_warning)
        http_response.headers["X-Edge-Decision"] = "fallback-deterministic"
    else:
        http_response.headers["X-Edge-Decision"] = "local-deterministic"
    return response


@app.post("/v1/messages/compress", response_model=V1MessagesCompressResponse)
def compress_v1_messages_http(
    http_request: Request,
    http_response: Response,
    request: V1MessagesCompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    cache_control: Annotated[str | None, Header(alias="Cache-Control")] = None,
) -> V1MessagesCompressResponse | Response:
    _enforce_body_limit(request.model_dump_json(exclude_none=True).encode("utf-8"))
    mode = core._resolve_v1_mode(request.compression_settings)
    fallback_warning: str | None = None
    if mode == MODEL_FORCE:
        origin_response = _try_forward_model_request(
            path="/v1/messages/compress",
            payload=request.model_dump_json(exclude_none=True).encode("utf-8"),
            http_request=http_request,
            request_id=x_request_id,
        )
        if origin_response is not None:
            return origin_response
        fallback_warning = "edge_origin_unavailable_deterministic_fallback"
    elif mode == MODEL_AUTO:
        _validate_model_request_credential(http_request)
        planned_response, model_required = core.plan_v1_messages_model_auto(
            request,
            x_tenant_id=x_tenant_id,
        )
        if model_required:
            origin_response = _try_forward_model_request(
                path="/v1/messages/compress",
                payload=request.model_dump_json(exclude_none=True).encode("utf-8"),
                http_request=http_request,
                request_id=x_request_id,
                credential_validated=True,
            )
            if origin_response is not None:
                return origin_response
            planned_response.warnings.append(
                "edge_origin_unavailable_deterministic_fallback"
            )
        response = _finalize_local_model_auto(
            http_request=http_request,
            http_response=http_response,
            response=planned_response,
            route="/v1/messages/compress",
            input_tokens=planned_response.input_tokens,
            output_tokens=planned_response.output_tokens,
            elapsed_ms=planned_response.compression_time,
            fallback=model_required,
        )
        http_response.headers["X-Compression-Content-Cache"] = (
            "hits=0; misses=0; stores=0"
        )
        if planned_response.fail_open_used:
            http_response.headers["Cache-Control"] = "no-store"
        return response

    local_request = request.model_copy(
        update={
            "compression_settings": _deterministic_settings(
                request.compression_settings
            )
        }
    )
    pending = core.start_usage_tap_compression_authorization(
        http_request,
        http_request.headers.get("Authorization"),
    )
    response = core.compress_v1_messages_http(
        http_request=http_request,
        http_response=http_response,
        request=local_request,
        pending_authorization=pending,
        x_tenant_id=x_tenant_id,
        cache_control=cache_control,
    )
    if fallback_warning is not None:
        response.warnings.append(fallback_warning)
        http_response.headers["X-Edge-Decision"] = "fallback-deterministic"
    else:
        http_response.headers["X-Edge-Decision"] = "local-deterministic"
    return response


@app.post("/v1/responses/compress", response_model=V1ResponsesCompressResponse)
def compress_v1_responses_http(
    http_request: Request,
    http_response: Response,
    request: V1ResponsesCompressRequest,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
    cache_control: Annotated[str | None, Header(alias="Cache-Control")] = None,
) -> V1ResponsesCompressResponse | Response:
    _enforce_body_limit(request.model_dump_json(exclude_none=True).encode("utf-8"))
    mode = core._resolve_v1_mode(request.compression_settings)
    fallback_warning: str | None = None
    if mode == MODEL_FORCE:
        origin_response = _try_forward_model_request(
            path="/v1/responses/compress",
            payload=request.model_dump_json(exclude_none=True).encode("utf-8"),
            http_request=http_request,
            request_id=x_request_id,
        )
        if origin_response is not None:
            return origin_response
        fallback_warning = "edge_origin_unavailable_deterministic_fallback"
    elif mode == MODEL_AUTO:
        _validate_model_request_credential(http_request)
        planned_response, model_required = core.plan_v1_responses_model_auto(
            request,
            x_tenant_id=x_tenant_id,
        )
        if model_required:
            origin_response = _try_forward_model_request(
                path="/v1/responses/compress",
                payload=request.model_dump_json(exclude_none=True).encode("utf-8"),
                http_request=http_request,
                request_id=x_request_id,
                credential_validated=True,
            )
            if origin_response is not None:
                return origin_response
            planned_response.warnings.append(
                "edge_origin_unavailable_deterministic_fallback"
            )
        response = _finalize_local_model_auto(
            http_request=http_request,
            http_response=http_response,
            response=planned_response,
            route="/v1/responses/compress",
            input_tokens=planned_response.input_tokens,
            output_tokens=planned_response.output_tokens,
            elapsed_ms=planned_response.compression_time,
            fallback=model_required,
        )
        http_response.headers["X-Compression-Content-Cache"] = (
            "hits=0; misses=0; stores=0"
        )
        if planned_response.fail_open_used:
            http_response.headers["Cache-Control"] = "no-store"
        return response

    local_request = request.model_copy(
        update={
            "compression_settings": _deterministic_settings(
                request.compression_settings
            )
        }
    )
    pending = core.start_usage_tap_compression_authorization(
        http_request,
        http_request.headers.get("Authorization"),
    )
    response = core.compress_v1_responses_http(
        http_request=http_request,
        http_response=http_response,
        request=local_request,
        pending_authorization=pending,
        x_tenant_id=x_tenant_id,
        cache_control=cache_control,
    )
    if fallback_warning is not None:
        response.warnings.append(fallback_warning)
        http_response.headers["X-Edge-Decision"] = "fallback-deterministic"
    else:
        http_response.headers["X-Edge-Decision"] = "local-deterministic"
    return response


def _deterministic_settings(
    settings: V1CompressionSettings | None,
) -> V1CompressionSettings:
    resolved = settings or V1CompressionSettings()
    return resolved.model_copy(update={"mode": "deterministic"})


def _try_forward_model_request(
    *,
    path: str,
    payload: bytes,
    http_request: Request,
    request_id: str | None,
    credential_validated: bool = False,
) -> Response | None:
    _enforce_body_limit(payload)
    if not credential_validated:
        # Reject malformed credentials at the edge without performing the live
        # authorization twice.  The GPU origin remains the authoritative
        # authorization and metering boundary for forwarded requests.
        _validate_model_request_credential(http_request)

    resolved_request_id = request_id or str(uuid.uuid4())
    try:
        response = origin_client.forward(
            path=path,
            body=payload,
            incoming_headers=http_request.headers,
            request_id=resolved_request_id,
        )
    except EdgeOriginUnavailable as exc:
        LOGGER.warning(
            "GPU origin unavailable; using deterministic fallback path=%s request_id=%s error=%s",
            path,
            resolved_request_id,
            type(exc).__name__,
        )
        return None
    if response.status_code >= 500:
        LOGGER.warning(
            "GPU origin returned server error; using deterministic fallback path=%s request_id=%s status=%s",
            path,
            resolved_request_id,
            response.status_code,
        )
        return None
    return response


def _validate_model_request_credential(http_request: Request) -> None:
    authorization = http_request.headers.get("Authorization")
    try:
        core.usage_tap_authorization_client.validate_incoming_credential(
            authorization
        )
    except UsageTapAuthorizationError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.public_message,
        ) from exc


def _finalize_local_model_auto(
    *,
    http_request: Request,
    http_response: Response,
    response: CompressResponse | V1CompressResponse | V1MessagesCompressResponse,
    route: str,
    input_tokens: int,
    output_tokens: int,
    elapsed_ms: float,
    fallback: bool,
) -> CompressResponse | V1CompressResponse | V1MessagesCompressResponse:
    pending = core.start_usage_tap_compression_authorization(
        http_request,
        http_request.headers.get("Authorization"),
    )
    core.complete_usage_tap_compression_authorization(http_request, pending)
    core.record_usage_tap_compression_metering(
        http_request,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    http_response.headers["X-Compression-Cache"] = "bypass"
    http_response.headers["X-Edge-Decision"] = (
        "fallback-deterministic" if fallback else "local-model-auto"
    )
    core.compression_telemetry.record(
        route=route,
        mode=MODEL_AUTO,
        cache_status="bypass",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        elapsed_ms=elapsed_ms,
        warnings=response.warnings,
        fail_open_used=(
            response.fail_open_used
            if isinstance(response, V1MessagesCompressResponse)
            else False
        ),
    )
    return response


def _enforce_body_limit(payload: bytes) -> None:
    if max_body_bytes <= 0:
        raise HTTPException(status_code=503, detail="The edge body limit is invalid.")
    if len(payload) > max_body_bytes:
        raise HTTPException(status_code=413, detail="Request body exceeds the edge limit.")
