from dataclasses import dataclass
import re
from typing import Any

import pytest
import requests

from app.usagetap_authorization import USAGETAP_ACCEPT_HEADER
from app.usagetap_metering import (
    COMPRESSION_METER_SLOT,
    MAX_SAFE_INTEGER,
    UsageTapMeteringClient,
    UsageTapMeteringError,
    compression_metering_idempotency_key,
)


INITIAL_SUCCESS = {
    "result": {
        "status": "ACCEPTED",
        "code": "CUSTOM_METER_SUCCESS",
    },
    "data": {
        "success": True,
        "eventId": "evt_initial",
        "meterSlot": "CUSTOM2",
        "amount": 123,
        "blocked": False,
    },
}

REPLAY_SUCCESS = {
    "result": {
        "status": "ACCEPTED",
        "code": "CUSTOM_METER_ALREADY_RECORDED",
    },
    "data": {
        "success": True,
        "eventId": "evt_initial",
        "meterSlot": "CUSTOM2",
        "amount": 123,
        "idempotent": True,
    },
}

METER_API_KEY = f"ck-{'A' * 43}"


@dataclass
class FakeResponse:
    status_code: int
    payload: Any = None
    json_error: Exception | None = None

    def json(self) -> Any:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class SequencedPost:
    def __init__(self, *outcomes: FakeResponse | Exception) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def build_client(
    *outcomes: FakeResponse | Exception,
    api_key: str | None = METER_API_KEY,
) -> tuple[UsageTapMeteringClient, SequencedPost]:
    post = SequencedPost(*outcomes)
    return (
        UsageTapMeteringClient(
            api_key=api_key,
            api_base_url="https://api.example.test/",
            timeout_seconds=2.5,
            post=post,
        ),
        post,
    )


def record(client: UsageTapMeteringClient):
    return client.record_compression_savings(
        customer_id="customer_authorized",
        operation_id="operation-123",
        input_tokens=500,
        output_tokens=377,
    )


def test_initial_metering_success_sends_exact_contract_without_prompt_text() -> None:
    client, post = build_client(FakeResponse(200, INITIAL_SUCCESS))

    result = record(client)

    assert result is not None
    assert result.event_id == "evt_initial"
    assert result.amount == 123
    assert result.meter_slot == "CUSTOM2"
    assert result.already_recorded is False
    assert re.fullmatch(r"pc-[0-9a-f]{48}", result.idempotency_key)
    assert len(result.idempotency_key) < 100
    assert post.calls == [
        (
            "https://api.example.test/custom_meter",
            {
                "headers": {
                    "Authorization": f"Bearer {METER_API_KEY}",
                    "Accept": USAGETAP_ACCEPT_HEADER,
                    "Content-Type": "application/json",
                    "Idempotency-Key": result.idempotency_key,
                },
                "json": {
                    "customerId": "customer_authorized",
                    "meterSlot": "CUSTOM2",
                    "amount": 123,
                    "feature": "platform.compression",
                    "tags": ["platform-usage", "promptcompression"],
                    "metadata": {
                        "source": "promptcompression",
                        "compressionOperationId": "operation-123",
                        "inputTokens": 500,
                        "outputTokens": 377,
                    },
                },
                "timeout": 2.5,
                "allow_redirects": False,
                "verify": True,
            },
        )
    ]


def test_zero_savings_skips_metering_even_without_platform_key() -> None:
    client, post = build_client(api_key=None)

    result = client.record_compression_savings(
        customer_id="customer_authorized",
        operation_id="operation-zero",
        input_tokens=377,
        output_tokens=500,
    )

    assert result is None
    assert post.calls == []


def test_idempotent_replay_is_accepted_and_verified() -> None:
    client, _ = build_client(FakeResponse(200, REPLAY_SUCCESS))

    result = record(client)

    assert result is not None
    assert result.already_recorded is True
    assert result.event_id == "evt_initial"


@pytest.mark.parametrize("transient_status", [409, 500, 502, 503, 504])
def test_transient_status_retries_once_with_identical_event(
    transient_status: int,
) -> None:
    client, post = build_client(
        FakeResponse(transient_status, {"internal": "not exposed"}),
        FakeResponse(200, REPLAY_SUCCESS),
    )

    result = record(client)

    assert result is not None
    assert result.already_recorded is True
    assert len(post.calls) == 2
    assert post.calls[0] == post.calls[1]


def test_network_failure_retries_once_with_identical_event() -> None:
    client, post = build_client(
        requests.Timeout("first attempt failed"),
        FakeResponse(200, REPLAY_SUCCESS),
    )

    result = record(client)

    assert result is not None
    assert len(post.calls) == 2
    assert post.calls[0] == post.calls[1]


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 406, 422])
def test_permanent_error_does_not_retry_or_expose_upstream(status_code: int) -> None:
    client, post = build_client(
        FakeResponse(status_code, {"code": "SUBSCRIPTION_NOT_FOUND", "key": "secret"})
    )

    with pytest.raises(UsageTapMeteringError) as caught:
        record(client)

    assert len(post.calls) == 1
    assert "SUBSCRIPTION_NOT_FOUND" not in str(caught.value)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"result": {"code": "CUSTOM_METER_SUCCESS"}, "data": {"success": False}},
        {
            "result": {"code": "CUSTOM_METER_SUCCESS"},
            "data": {"success": True, "eventId": "evt", "meterSlot": "CUSTOM1", "amount": 123},
        },
        {
            "result": {"code": "CUSTOM_METER_SUCCESS"},
            "data": {"success": True, "eventId": "evt", "meterSlot": "CUSTOM2", "amount": 122},
        },
        {
            "result": {"code": "CUSTOM_METER_SUCCESS"},
            "data": {"success": True, "eventId": "", "meterSlot": "CUSTOM2", "amount": 123},
        },
        {
            "result": {"code": "CUSTOM_METER_ALREADY_RECORDED"},
            "data": {"success": True, "eventId": "evt", "meterSlot": "CUSTOM2", "amount": 123},
        },
        {
            "result": {"code": "UNEXPECTED_SUCCESS"},
            "data": {"success": True, "eventId": "evt", "meterSlot": "CUSTOM2", "amount": 123},
        },
    ],
)
def test_malformed_or_mismatched_success_response_fails_closed(payload: Any) -> None:
    client, _ = build_client(FakeResponse(200, payload))

    with pytest.raises(UsageTapMeteringError):
        record(client)


def test_malformed_json_fails_closed() -> None:
    client, _ = build_client(FakeResponse(200, json_error=ValueError("bad json")))

    with pytest.raises(UsageTapMeteringError):
        record(client)


def test_idempotency_key_is_stable_and_changes_with_event_identity() -> None:
    base = compression_metering_idempotency_key(
        customer_id="customer-a",
        operation_id="operation-a",
        amount=1,
    )

    assert base == compression_metering_idempotency_key(
        customer_id="customer-a",
        operation_id="operation-a",
        amount=1,
    )
    assert base != compression_metering_idempotency_key(
        customer_id="customer-b",
        operation_id="operation-a",
        amount=1,
    )
    assert base != compression_metering_idempotency_key(
        customer_id="customer-a",
        operation_id="operation-b",
        amount=1,
    )
    assert base != compression_metering_idempotency_key(
        customer_id="customer-a",
        operation_id="operation-a",
        amount=2,
    )


@pytest.mark.parametrize(
    ("input_tokens", "output_tokens"),
    [
        (-1, 0),
        (True, 0),
        (1.5, 0),
        (MAX_SAFE_INTEGER + 1, 0),
        (1, -1),
    ],
)
def test_invalid_or_unsafe_token_counts_are_rejected(
    input_tokens: Any,
    output_tokens: Any,
) -> None:
    client, post = build_client(FakeResponse(200, INITIAL_SUCCESS))

    with pytest.raises(UsageTapMeteringError):
        client.record_compression_savings(
            customer_id="customer_authorized",
            operation_id="operation-invalid",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    assert post.calls == []


def test_missing_platform_key_fails_without_calling_upstream() -> None:
    client, post = build_client(FakeResponse(200, INITIAL_SUCCESS), api_key=None)

    with pytest.raises(UsageTapMeteringError):
        record(client)

    assert post.calls == []


@pytest.mark.parametrize("prefix", ["ck-", "cmp-"])
def test_meter_client_accepts_legacy_exact_key_formats(prefix: str) -> None:
    UsageTapMeteringClient(api_key=f"{prefix}{'A' * 43}")


def test_meter_client_accepts_universal_key_with_track_usage_permission() -> None:
    UsageTapMeteringClient(api_key=f"utk-{'A' * 43}")


@pytest.mark.parametrize(
    "invalid_key",
    [
        f"ck-{'A' * 42}",
        f"ck-{'A' * 44}",
        f"cmp-{'A' * 42}",
        f"sk-{'A' * 43}",
        f"ck-{'A' * 42}.",
    ],
)
def test_meter_client_rejects_unexpected_key_format_without_echoing_key(
    invalid_key: str,
) -> None:
    with pytest.raises(ValueError) as caught:
        UsageTapMeteringClient(api_key=invalid_key)

    assert invalid_key not in str(caught.value)


def test_platform_key_never_appears_in_logs_or_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = f"ck-{'N' * 43}"
    client, _ = build_client(
        requests.Timeout(f"failed with {secret}"),
        requests.Timeout(f"failed again with {secret}"),
        api_key=secret,
    )

    with pytest.raises(UsageTapMeteringError) as caught:
        record(client)

    assert secret not in caplog.text
    assert secret not in str(caught.value)
    assert "Authorization" not in str(caught.value)


def test_meter_slot_constant_is_custom2() -> None:
    assert COMPRESSION_METER_SLOT == "CUSTOM2"
