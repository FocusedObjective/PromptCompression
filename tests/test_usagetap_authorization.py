from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
import pytest
import requests

from app import main
from app.usagetap_authorization import (
    USAGETAP_ACCEPT_HEADER,
    UsageTapAuthorization,
    UsageTapAuthorizationClient,
    UsageTapAuthorizationError,
    UsageTapAuthorizationFailureCache,
)


AUTHORIZED_PAYLOAD = {
    "result": {
        "status": "ACCEPTED",
        "code": "COMPRESSION_AUTHORIZED",
    },
    "data": {
        "authorized": True,
        "organizationId": "org_server",
        "customerId": "customer_server",
    },
}


@dataclass
class FakeResponse:
    status_code: int
    payload: Any = None
    json_error: Exception | None = None

    def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class RecordingPost:
    def __init__(self, response: FakeResponse | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class CompressionMustNotRun:
    model_name = "must-not-run"
    is_loaded = False

    def __init__(self) -> None:
        self.calls = 0

    def compress(self, *args: Any, **kwargs: Any) -> None:
        self.calls += 1
        raise AssertionError("compression ran before authorization")


def build_client(
    response: FakeResponse | Exception,
) -> tuple[UsageTapAuthorizationClient, RecordingPost]:
    post = RecordingPost(response)
    return (
        UsageTapAuthorizationClient(
            api_base_url="https://api.example.test/",
            timeout_seconds=2.75,
            min_key_suffix_length=1,
            post=post,
        ),
        post,
    )


def assert_authorization_error(
    client: UsageTapAuthorizationClient,
    authorization: str | None,
    expected_status: int,
) -> UsageTapAuthorizationError:
    with pytest.raises(UsageTapAuthorizationError) as caught:
        client.authorize(authorization)
    assert caught.value.status_code == expected_status
    return caught.value


def test_valid_cmp_authorization_forwards_only_required_headers() -> None:
    client, post = build_client(FakeResponse(200, AUTHORIZED_PAYLOAD))
    incoming = "Bearer cmp-exact-key-value"

    result = client.authorize(incoming)

    assert result == UsageTapAuthorization(
        organization_id="org_server",
        customer_id="customer_server",
    )
    assert post.calls == [
        (
            "https://api.example.test/v1/compression/authorize",
            {
                "headers": {
                    "Authorization": incoming,
                    "Accept": USAGETAP_ACCEPT_HEADER,
                },
                "timeout": 2.75,
                "allow_redirects": False,
                "verify": True,
            },
        )
    ]


def test_valid_universal_key_is_forwarded_for_permission_authorization() -> None:
    client, post = build_client(FakeResponse(200, AUTHORIZED_PAYLOAD))
    incoming = f"Bearer utk-{'A' * 43}"

    result = client.authorize(incoming)

    assert result.customer_id == "customer_server"
    assert post.calls[0][1]["headers"]["Authorization"] == incoming


def test_short_lived_session_is_forwarded_without_local_claim_trust() -> None:
    client, post = build_client(FakeResponse(200, AUTHORIZED_PAYLOAD))
    incoming = "Bearer eyJ0eXAiOiJjb21wcmVzc2lvbiJ9.eyJleHAiOjF9.signature"

    result = client.authorize(incoming)

    assert result.customer_id == "customer_server"
    assert post.calls[0][1]["headers"]["Authorization"] == incoming


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "cmp-raw-key",
        "Bearer ck-wrong-prefix",
        "Bearer other-key",
        "Basic cmp-wrong-scheme",
        "Bearer cmp-",
        "Bearer  cmp-extra-space",
        "Bearer cmp-key trailing",
        "Bearer jwt.has whitespace",
        "Bearer jwt.only-two-parts",
        "Bearer jwt.has.invalid.character+",
    ],
)
def test_invalid_incoming_credentials_are_rejected_before_usagetap(
    authorization: str | None,
) -> None:
    client, post = build_client(FakeResponse(200, AUTHORIZED_PAYLOAD))

    error = assert_authorization_error(client, authorization, 401)

    assert post.calls == []
    assert "cmp-" not in str(error)


def test_strict_sanity_gate_enforces_suffix_length_and_alphabet() -> None:
    client = UsageTapAuthorizationClient(
        min_key_suffix_length=16,
        max_key_suffix_length=20,
        post=lambda *args, **kwargs: FakeResponse(200, AUTHORIZED_PAYLOAD),
    )

    assert (
        client.validate_incoming_credential("Bearer cmp-AbCdEf012345_-xy")
        == "Bearer cmp-AbCdEf012345_-xy"
    )
    for invalid in (
        "Bearer cmp-too-short",
        "Bearer cmp-AbCdEf0123456789012345",
        "Bearer cmp-AbCdEf012345+xyz",
        "Bearer cmp-AbCdEf012345.xyz",
    ):
        assert_authorization_error(client, invalid, 401)


def test_default_sanity_gate_requires_exact_43_character_cmp_suffix() -> None:
    client = UsageTapAuthorizationClient(
        post=lambda *args, **kwargs: FakeResponse(200, AUTHORIZED_PAYLOAD),
    )
    valid = f"Bearer cmp-{'A' * 43}"

    assert client.validate_incoming_credential(valid) == valid
    assert_authorization_error(client, f"Bearer cmp-{'A' * 42}", 401)
    assert_authorization_error(client, f"Bearer cmp-{'A' * 44}", 401)
    assert_authorization_error(client, f"Bearer ck-{'A' * 43}", 401)


def test_universal_key_sanity_gate_requires_exact_43_character_suffix() -> None:
    client = UsageTapAuthorizationClient(
        post=lambda *args, **kwargs: FakeResponse(200, AUTHORIZED_PAYLOAD),
    )
    valid = f"Bearer utk-{'A' * 43}"

    assert client.validate_incoming_credential(valid) == valid
    assert_authorization_error(client, f"Bearer utk-{'A' * 42}", 401)
    assert_authorization_error(client, f"Bearer utk-{'A' * 44}", 401)
    assert_authorization_error(client, f"Bearer utk-{'A' * 42}.", 401)


def test_failure_cache_uses_salted_digest_and_expires() -> None:
    now = [100.0]
    cache = UsageTapAuthorizationFailureCache(
        ttl_seconds=5,
        clock=lambda: now[0],
    )
    authorization = "Bearer cmp-sensitive-customer-key"
    cache.record(
        authorization,
        UsageTapAuthorizationError(403, "Compression key lacks required permissions."),
    )

    cached = cache.get(authorization)

    assert cached is not None
    assert cached.status_code == 403
    assert all(
        authorization.encode("utf-8") != digest
        for digest in cache._entries
    )
    now[0] += 5
    assert cache.get(authorization) is None


def test_failure_cache_ignores_availability_failures() -> None:
    cache = UsageTapAuthorizationFailureCache(ttl_seconds=5)
    authorization = "Bearer cmp-sensitive-customer-key"

    cache.record(
        authorization,
        UsageTapAuthorizationError(
            503,
            "Compression authorization is temporarily unavailable.",
        ),
    )

    assert cache.get(authorization) is None


@pytest.mark.parametrize("status_code", [401, 402, 403])
def test_usagetap_authorization_status_is_preserved(status_code: int) -> None:
    client, _ = build_client(FakeResponse(status_code, {"internal": "not exposed"}))

    error = assert_authorization_error(client, "Bearer cmp-key", status_code)

    assert "internal" not in str(error)


@pytest.mark.parametrize("status_code", [500, 503, 599, 302, 429])
def test_non_authorization_statuses_fail_closed_as_unavailable(
    status_code: int,
) -> None:
    client, _ = build_client(FakeResponse(status_code, {}))

    assert_authorization_error(client, "Bearer cmp-key", 503)


def test_timeout_fails_closed_as_unavailable() -> None:
    client, _ = build_client(requests.Timeout("upstream timeout"))

    assert_authorization_error(client, "Bearer cmp-key", 503)


def test_malformed_json_fails_closed() -> None:
    client, _ = build_client(
        FakeResponse(200, json_error=ValueError("malformed response"))
    )

    assert_authorization_error(client, "Bearer cmp-key", 503)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"data": {"authorized": False, "organizationId": "org", "customerId": "c"}},
        {"data": {"authorized": 1, "organizationId": "org", "customerId": "c"}},
        {"data": {"authorized": True, "organizationId": "", "customerId": "c"}},
        {"data": {"authorized": True, "organizationId": "   ", "customerId": "c"}},
        {"data": {"authorized": True, "organizationId": "org", "customerId": ""}},
        {"data": {"authorized": True, "organizationId": "org", "customerId": None}},
    ],
)
def test_incomplete_or_unauthorized_200_response_fails_closed(payload: Any) -> None:
    client, _ = build_client(FakeResponse(200, payload))

    assert_authorization_error(client, "Bearer cmp-key", 503)


ROUTE_PAYLOADS = [
    ("/compress", {"text": "Compress this prompt."}),
    ("/v1/compress", {"input": "Compress this prompt."}),
    (
        "/v1/messages/compress",
        {"messages": [{"role": "user", "content": "Compress this prompt."}]},
    ),
]


@pytest.mark.parametrize(("path", "payload"), ROUTE_PAYLOADS)
def test_every_compression_route_requires_authorization_and_never_compresses(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict[str, Any],
) -> None:
    service = CompressionMustNotRun()
    client, post = build_client(FakeResponse(200, AUTHORIZED_PAYLOAD))
    monkeypatch.setattr(main, "compression_service", service)
    monkeypatch.setattr(main, "usage_tap_authorization_client", client)

    response = TestClient(main.app).post(path, json=payload)

    assert response.status_code == 401
    assert service.calls == 0
    assert post.calls == []


@pytest.mark.parametrize("upstream_status", [401, 402, 403, 503])
def test_route_discards_speculative_compression_on_usagetap_failure(
    monkeypatch: pytest.MonkeyPatch,
    upstream_status: int,
) -> None:
    compression_calls = []

    def speculative_compress(*args: Any, **kwargs: Any) -> SimpleNamespace:
        compression_calls.append((args, kwargs))
        return SimpleNamespace(original_tokens=10, compressed_tokens=5)

    client, _ = build_client(FakeResponse(upstream_status, {"secret": "upstream"}))
    monkeypatch.setattr(main, "compress", speculative_compress)
    monkeypatch.setattr(main, "usage_tap_authorization_client", client)
    monkeypatch.setattr(
        main,
        "usage_tap_authorization_failure_cache",
        UsageTapAuthorizationFailureCache(ttl_seconds=5),
    )

    response = TestClient(main.app).post(
        "/compress",
        headers={"Authorization": "Bearer cmp-route-key"},
        json={"text": "Compress this prompt."},
    )

    assert response.status_code == upstream_status
    assert len(compression_calls) == 1
    assert "upstream" not in response.text
    assert "cmp-route-key" not in response.text


def test_recent_definitive_failure_blocks_inference_and_second_upstream_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compression_calls = []

    def speculative_compress(*args: Any, **kwargs: Any) -> SimpleNamespace:
        compression_calls.append((args, kwargs))
        return SimpleNamespace(original_tokens=10, compressed_tokens=5)

    client, post = build_client(FakeResponse(401, {}))
    monkeypatch.setattr(main, "compress", speculative_compress)
    monkeypatch.setattr(main, "usage_tap_authorization_client", client)
    monkeypatch.setattr(
        main,
        "usage_tap_authorization_failure_cache",
        UsageTapAuthorizationFailureCache(ttl_seconds=5),
    )
    test_client = TestClient(main.app)
    request = {
        "headers": {"Authorization": "Bearer cmp-recently-failed-key"},
        "json": {"text": "Compress this prompt."},
    }

    first = test_client.post("/compress", **request)
    second = test_client.post("/compress", **request)

    assert first.status_code == 401
    assert second.status_code == 401
    assert len(compression_calls) == 1
    assert len(post.calls) == 1


def identity_probe_app() -> FastAPI:
    probe = FastAPI()

    @probe.post("/probe")
    def identity_probe(
        request: Request,
        _authorization: Annotated[
            UsageTapAuthorization,
            Depends(main.require_usage_tap_compression_authorization),
        ],
    ) -> dict[str, str]:
        verified = request.state.usagetap_authorization
        return {
            "organizationId": verified.organization_id,
            "customerId": request.state.usagetap_customer_id,
        }

    return probe


def test_customer_identity_comes_only_from_usagetap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = build_client(FakeResponse(200, AUTHORIZED_PAYLOAD))
    monkeypatch.setattr(main, "usage_tap_authorization_client", client)

    response = TestClient(identity_probe_app()).post(
        "/probe",
        headers={"Authorization": "Bearer cmp-key"},
        json={
            "customerId": "customer_attacker",
            "organizationId": "org_attacker",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "organizationId": "org_server",
        "customerId": "customer_server",
    }


def test_concurrent_requests_retain_their_verified_customer_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def identity_post(url: str, **kwargs: Any) -> FakeResponse:
        del url
        key = kwargs["headers"]["Authorization"].removeprefix("Bearer cmp-")
        return FakeResponse(
            200,
            {
                "data": {
                    "authorized": True,
                    "organizationId": f"org_{key}",
                    "customerId": f"customer_{key}",
                }
            },
        )

    monkeypatch.setattr(
        main,
        "usage_tap_authorization_client",
        UsageTapAuthorizationClient(
            post=identity_post,
            min_key_suffix_length=1,
        ),
    )
    probe = identity_probe_app()

    def send(key: str) -> tuple[str, dict[str, str]]:
        response = TestClient(probe).post(
            "/probe",
            headers={"Authorization": f"Bearer cmp-{key}"},
        )
        assert response.status_code == 200
        return key, response.json()

    keys = [f"request-{index}" for index in range(20)]
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = dict(executor.map(send, keys))

    for key in keys:
        assert results[key] == {
            "organizationId": f"org_{key}",
            "customerId": f"customer_{key}",
        }


def test_authorization_value_never_appears_in_logs_or_errors(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    incoming = "Bearer cmp-never-log-this-value"
    client, _ = build_client(requests.Timeout(f"timeout while sending {incoming}"))
    monkeypatch.setattr(main, "usage_tap_authorization_client", client)

    response = TestClient(main.app).post(
        "/compress",
        headers={"Authorization": incoming},
        json={"text": "Compress this prompt."},
    )

    assert response.status_code == 503
    assert incoming not in caplog.text
    assert "cmp-never-log-this-value" not in caplog.text
    assert incoming not in response.text
    assert "cmp-never-log-this-value" not in response.text
