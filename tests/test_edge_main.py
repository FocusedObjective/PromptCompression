import json
import sys
from types import SimpleNamespace

from fastapi import Response
from fastapi.testclient import TestClient
import pytest

from app import edge_main
from app import token_estimator
from app.edge_origin import EdgeOriginUnavailable
from app.schemas import V1CompressRequest, V1CompressionSettings
from app.usagetap_authorization import UsageTapAuthorization


class FakeAuthorizationClient:
    def __init__(self) -> None:
        self.validations = 0
        self.authorizations = 0

    def validate_incoming_credential(self, authorization_header: str | None) -> str:
        self.validations += 1
        assert authorization_header == "Bearer cmp-test-key"
        return authorization_header

    def authorize(self, authorization_header: str | None) -> UsageTapAuthorization:
        self.authorizations += 1
        assert authorization_header == "Bearer cmp-test-key"
        return UsageTapAuthorization(
            organization_id="org_edge_test",
            customer_id="customer_edge_test",
        )


class FakeMeteringClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def record_compression_savings(self, **kwargs):
        self.calls.append(kwargs)
        return None


class RecordingOrigin:
    configured = True

    def __init__(self, response: Response | None = None, *, unavailable: bool = False):
        self.response = response or Response(
            content=json.dumps({"source": "gpu"}),
            status_code=200,
            media_type="application/json",
        )
        self.unavailable = unavailable
        self.calls: list[dict] = []

    def forward(self, **kwargs) -> Response:
        self.calls.append(kwargs)
        if self.unavailable:
            raise EdgeOriginUnavailable("origin unavailable")
        return self.response


@pytest.fixture(autouse=True)
def edge_dependencies(monkeypatch):
    authorization = FakeAuthorizationClient()
    metering = FakeMeteringClient()
    origin = RecordingOrigin()
    edge_main.core.compression_response_cache.clear()
    edge_main.core.message_content_cache.clear()
    monkeypatch.setattr(
        edge_main.core,
        "usage_tap_authorization_client",
        authorization,
    )
    monkeypatch.setattr(edge_main.core, "usage_tap_metering_client", metering)
    monkeypatch.setattr(edge_main, "origin_client", origin)
    monkeypatch.setattr(
        edge_main.core.compression_service,
        "gpu_p50_fixed_overhead_ms",
        150.0,
    )
    monkeypatch.setattr(
        edge_main.core.compression_service,
        "gpu_p50_llmlingua_chunk_ms",
        120.0,
    )
    monkeypatch.setattr(
        edge_main.core.compression_service,
        "gpu_p50_token_estimate_ms",
        80.0,
    )
    yield authorization, metering, origin
    edge_main.core.compression_response_cache.clear()
    edge_main.core.message_content_cache.clear()


def test_v1_default_is_local_and_matches_full_api_deterministic(edge_dependencies):
    _authorization, _metering, origin = edge_dependencies
    request = V1CompressRequest(
        model="gpt-test",
        input="A first line.   \n\n\nA second line.",
        compression_settings=V1CompressionSettings(mode="deterministic"),
    )
    expected = edge_main.core.compress_v1(request)

    response = TestClient(edge_main.app).post(
        "/v1/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json=request.model_dump(mode="json"),
    )

    assert response.status_code == 200
    assert response.headers["x-edge-decision"] == "local-deterministic"
    assert origin.calls == []
    body = response.json()
    expected_body = expected.model_dump(mode="json")
    body.pop("compression_time")
    expected_body.pop("compression_time")
    assert body == expected_body


def test_model_request_is_forwarded_without_duplicate_live_authorization(
    edge_dependencies,
):
    authorization, _metering, origin = edge_dependencies

    response = TestClient(edge_main.app).post(
        "/v1/compress",
        headers={
            "Authorization": "Bearer cmp-test-key",
            "X-Request-ID": "request-edge-1",
        },
        json={
            "model": "gpt-test",
            "input": "Send this to the model service.",
            "compression_settings": {"mode": "model_force"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {"source": "gpu"}
    assert len(origin.calls) == 1
    assert origin.calls[0]["path"] == "/v1/compress"
    assert origin.calls[0]["request_id"] == "request-edge-1"
    assert authorization.validations == 1
    assert authorization.authorizations == 0


def test_model_auto_skips_gpu_after_local_deterministic_plan(edge_dependencies):
    authorization, metering, origin = edge_dependencies

    response = TestClient(edge_main.app).post(
        "/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={
            "text": "A first line.   \n\n\nA second line.",
            "mode": "model_auto",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-edge-decision"] == "local-model-auto"
    assert response.headers["x-compression-cache"] == "bypass"
    assert origin.calls == []
    assert authorization.validations == 2
    assert authorization.authorizations == 1
    assert len(metering.calls) == 1
    body = response.json()
    assert body["compression_mode"] == "model_auto"
    assert body["compression_path"] in {"unchanged", "deterministic_only"}
    assert "llmlingua_skipped_no_candidate_prose" in body["warnings"]


def test_model_auto_forwards_only_when_gpu_gate_runs(edge_dependencies):
    authorization, metering, origin = edge_dependencies
    text = (
        "Carefully review the service behavior and summarize the operational "
        "outcome for the customer. "
    ) * 220

    response = TestClient(edge_main.app).post(
        "/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={"text": text, "mode": "model_auto"},
    )

    assert response.status_code == 200
    assert response.json() == {"source": "gpu"}
    assert len(origin.calls) == 1
    assert origin.calls[0]["path"] == "/compress"
    assert authorization.validations == 1
    assert authorization.authorizations == 0
    assert metering.calls == []


def test_missing_gpu_returns_schema_compatible_deterministic_fallback(
    monkeypatch,
    edge_dependencies,
):
    _authorization, metering, _origin = edge_dependencies
    failed_origin = RecordingOrigin(unavailable=True)
    monkeypatch.setattr(edge_main, "origin_client", failed_origin)

    response = TestClient(edge_main.app).post(
        "/v1/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={
            "model": "gpt-test",
            "input": "A first line.   \n\n\nA second line.",
            "compression_settings": {"mode": "model_force"},
        },
    )

    assert response.status_code == 200
    assert response.headers["x-edge-decision"] == "fallback-deterministic"
    body = response.json()
    assert "output" in body
    assert "tokens_saved" in body
    assert "edge_origin_unavailable_deterministic_fallback" in body["warnings"]
    assert len(metering.calls) == 1


def test_gpu_5xx_returns_deterministic_fallback(monkeypatch, edge_dependencies):
    failed_origin = RecordingOrigin(response=Response(status_code=503))
    monkeypatch.setattr(edge_main, "origin_client", failed_origin)

    response = TestClient(edge_main.app).post(
        "/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={
            "text": (
                "Carefully review the service behavior and summarize the "
                "operational outcome for the customer. "
            )
            * 220,
            "mode": "model_auto",
        },
    )

    assert response.status_code == 200
    assert response.headers["x-edge-decision"] == "fallback-deterministic"
    body = response.json()
    assert body["compression_mode"] == "model_auto"
    assert body["compression_path"] in {"unchanged", "deterministic_only"}
    assert "edge_origin_unavailable_deterministic_fallback" in body["warnings"]


def test_messages_gpu_failure_keeps_vendor_response_schema(
    monkeypatch,
    edge_dependencies,
):
    failed_origin = RecordingOrigin(unavailable=True)
    monkeypatch.setattr(edge_main, "origin_client", failed_origin)

    response = TestClient(edge_main.app).post(
        "/v1/messages/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={
            "model": "gpt-test",
            "messages": [
                {"role": "system", "content": "Preserve this."},
                {
                    "role": "user",
                    "content": (
                        "Carefully review the service behavior and summarize "
                        "the operational outcome for the customer. "
                    )
                    * 220,
                },
            ],
            "compression_settings": {"mode": "model_auto"},
        },
    )

    assert response.status_code == 200
    assert response.headers["x-edge-decision"] == "fallback-deterministic"
    body = response.json()
    assert "compressed_request" in body
    assert "messages" in body
    assert "message_stats" in body
    assert "edge_origin_unavailable_deterministic_fallback" in body["warnings"]


def test_gpu_client_error_is_passed_through_without_fallback(
    monkeypatch,
    edge_dependencies,
):
    denied = RecordingOrigin(
        response=Response(
            content=json.dumps({"detail": "Compression credit is unavailable."}),
            status_code=402,
            media_type="application/json",
        )
    )
    monkeypatch.setattr(edge_main, "origin_client", denied)

    response = TestClient(edge_main.app).post(
        "/v1/compress",
        headers={"Authorization": "Bearer cmp-test-key"},
        json={
            "model": "gpt-test",
            "input": "Do not hide billing failures.",
            "compression_settings": {"mode": "model_force"},
        },
    )

    assert response.status_code == 402
    assert response.json()["detail"] == "Compression credit is unavailable."


def test_health_identifies_model_free_edge(edge_dependencies):
    response = TestClient(edge_main.app).get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["runtime"]["service_role"] == "deterministic-cpu-edge"
    assert body["model_loaded"] is False


def test_shared_routes_use_the_same_request_and_success_response_schemas():
    edge_spec = edge_main.app.openapi()
    gpu_spec = edge_main.core.app.openapi()

    route_methods = {
        "/health": "get",
        "/tokens/estimate": "post",
        "/compress": "post",
        "/v1/compress": "post",
        "/v1/messages/compress": "post",
        "/v1/responses/compress": "post",
    }
    for path, method in route_methods.items():
        edge_operation = edge_spec["paths"][path][method]
        gpu_operation = gpu_spec["paths"][path][method]
        assert edge_operation.get("requestBody") == gpu_operation.get("requestBody")
        assert (
            edge_operation["responses"]["200"]["content"]
            == gpu_operation["responses"]["200"]["content"]
        )


def test_baked_tokenizer_path_preserves_public_estimator_name(monkeypatch):
    model_name = "example/compression-model"
    tokenizer_path = "/app/tokenizer"
    calls: list[tuple[str, dict]] = []

    class FakeAutoTokenizer:
        @staticmethod
        def from_pretrained(source: str, **kwargs):
            calls.append((source, kwargs))
            return SimpleNamespace(name_or_path=source)

    monkeypatch.setenv("COMPRESSOR_MODEL", model_name)
    monkeypatch.setenv("COMPRESSOR_TOKENIZER_PATH", tokenizer_path)
    monkeypatch.setitem(
        sys.modules,
        "transformers",
        SimpleNamespace(AutoTokenizer=FakeAutoTokenizer),
    )
    token_estimator._HF_TOKENIZER_CACHE.pop(model_name, None)
    token_estimator._HF_TOKENIZER_FAILURES.discard(model_name)

    tokenizer = token_estimator._load_huggingface_tokenizer(model_name)

    assert calls == [
        (
            tokenizer_path,
            {"use_fast": True, "local_files_only": True},
        )
    ]
    assert token_estimator._tokenizer_name(tokenizer, model_name) == model_name
    token_estimator._HF_TOKENIZER_CACHE.pop(model_name, None)
