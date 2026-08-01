"""Idempotent UsageTap metering for verified compression savings."""

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import math
import os
import re
from typing import Any

import requests

from app.usagetap_authorization import (
    DEFAULT_USAGETAP_API_BASE_URL,
    USAGETAP_ACCEPT_HEADER,
    USAGETAP_API_BASE_URL_ENV,
)


USAGETAP_METERING_API_KEY_ENV = "USAGETAP_METERING_API_KEY"
USAGETAP_METERING_TIMEOUT_ENV = "USAGETAP_METERING_TIMEOUT_SECONDS"
DEFAULT_USAGETAP_METERING_TIMEOUT_SECONDS = 3.0
USAGETAP_CUSTOM_METER_PATH = "/custom_meter"
COMPRESSION_METER_SLOT = "CUSTOM2"
COMPRESSION_FEATURE = "platform.compression"
COMPRESSION_TAGS = ("platform-usage", "promptcompression")
MAX_SAFE_INTEGER = 9_007_199_254_740_991

_METER_KEY_PATTERN = re.compile(r"(?:ck-|cmp-)[A-Za-z0-9_-]{43}")

_SUCCESS_CODES = {
    "CUSTOM_METER_SUCCESS",
    "CUSTOM_METER_ALREADY_RECORDED",
}
_TRANSIENT_STATUS_CODES = {409, 500, 502, 503, 504}


@dataclass(frozen=True, slots=True)
class UsageTapMeteringResult:
    event_id: str
    amount: int
    meter_slot: str
    idempotency_key: str
    already_recorded: bool


class UsageTapMeteringError(Exception):
    """A metering failure with no platform credential or upstream details."""

    def __init__(self, public_message: str = "Compression metering is unavailable.") -> None:
        super().__init__(public_message)
        self.public_message = public_message


def compression_metering_idempotency_key(
    *,
    customer_id: str,
    operation_id: str,
    amount: int,
) -> str:
    """Return a stable key unique to identity, operation, slot, and amount."""
    material = (
        f"{customer_id}\n{operation_id}\n{COMPRESSION_METER_SLOT}\n{amount}"
    ).encode("utf-8")
    return f"pc-{hashlib.sha256(material).hexdigest()[:48]}"


class UsageTapMeteringClient:
    """Write one compression-savings event to UsageTap."""

    def __init__(
        self,
        *,
        api_key: str | None,
        api_base_url: str = DEFAULT_USAGETAP_API_BASE_URL,
        timeout_seconds: float = DEFAULT_USAGETAP_METERING_TIMEOUT_SECONDS,
        post: Callable[..., Any] = requests.post,
    ) -> None:
        if not api_base_url.strip():
            raise ValueError("UsageTap API base URL must not be empty")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("UsageTap metering timeout must be positive")
        self._api_key = api_key.strip() if isinstance(api_key, str) else None
        if self._api_key and _METER_KEY_PATTERN.fullmatch(self._api_key) is None:
            raise ValueError("UsageTap meter key has an unexpected format")
        self._metering_url = f"{api_base_url.rstrip('/')}{USAGETAP_CUSTOM_METER_PATH}"
        self._timeout_seconds = timeout_seconds
        self._post = post

    @classmethod
    def from_environment(cls) -> "UsageTapMeteringClient":
        return cls(
            api_key=os.getenv(USAGETAP_METERING_API_KEY_ENV),
            api_base_url=os.getenv(
                USAGETAP_API_BASE_URL_ENV,
                DEFAULT_USAGETAP_API_BASE_URL,
            ),
            timeout_seconds=float(
                os.getenv(
                    USAGETAP_METERING_TIMEOUT_ENV,
                    str(DEFAULT_USAGETAP_METERING_TIMEOUT_SECONDS),
                )
            ),
        )

    def record_compression_savings(
        self,
        *,
        customer_id: str,
        operation_id: str,
        input_tokens: int,
        output_tokens: int,
    ) -> UsageTapMeteringResult | None:
        self._validate_identity(customer_id, operation_id)
        self._validate_token_count(input_tokens)
        self._validate_token_count(output_tokens)

        amount = max(0, input_tokens - output_tokens)
        if amount == 0:
            return None
        if amount > MAX_SAFE_INTEGER:
            raise UsageTapMeteringError()
        if not self._api_key:
            raise UsageTapMeteringError()

        idempotency_key = compression_metering_idempotency_key(
            customer_id=customer_id,
            operation_id=operation_id,
            amount=amount,
        )
        payload = {
            "customerId": customer_id,
            "meterSlot": COMPRESSION_METER_SLOT,
            "amount": amount,
            "feature": COMPRESSION_FEATURE,
            "tags": list(COMPRESSION_TAGS),
            "metadata": {
                "source": "promptcompression",
                "compressionOperationId": operation_id,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
            },
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Accept": USAGETAP_ACCEPT_HEADER,
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }

        response = None
        for attempt in range(2):
            try:
                response = self._post(
                    self._metering_url,
                    headers=headers,
                    json=payload,
                    timeout=self._timeout_seconds,
                    allow_redirects=False,
                    verify=True,
                )
            except requests.RequestException:
                if attempt == 0:
                    continue
                raise UsageTapMeteringError() from None

            if response.status_code in _TRANSIENT_STATUS_CODES and attempt == 0:
                continue
            break

        if response is None or response.status_code != 200:
            raise UsageTapMeteringError()

        try:
            body = response.json()
        except (TypeError, ValueError):
            raise UsageTapMeteringError() from None

        return self._validated_result(
            body,
            intended_amount=amount,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _validate_identity(customer_id: str, operation_id: str) -> None:
        if not isinstance(customer_id, str) or not customer_id.strip():
            raise UsageTapMeteringError()
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise UsageTapMeteringError()

    @staticmethod
    def _validate_token_count(value: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise UsageTapMeteringError()
        if value > MAX_SAFE_INTEGER:
            raise UsageTapMeteringError()

    @staticmethod
    def _validated_result(
        body: object,
        *,
        intended_amount: int,
        idempotency_key: str,
    ) -> UsageTapMeteringResult:
        if not isinstance(body, dict):
            raise UsageTapMeteringError()
        result = body.get("result")
        data = body.get("data")
        if not isinstance(result, dict) or not isinstance(data, dict):
            raise UsageTapMeteringError()

        code = result.get("code")
        if code not in _SUCCESS_CODES or data.get("success") is not True:
            raise UsageTapMeteringError()
        if data.get("meterSlot") != COMPRESSION_METER_SLOT:
            raise UsageTapMeteringError()
        amount = data.get("amount")
        if isinstance(amount, bool) or amount != intended_amount:
            raise UsageTapMeteringError()
        event_id = data.get("eventId")
        if not isinstance(event_id, str) or not event_id.strip():
            raise UsageTapMeteringError()

        already_recorded = code == "CUSTOM_METER_ALREADY_RECORDED"
        if already_recorded and data.get("idempotent") is not True:
            raise UsageTapMeteringError()

        return UsageTapMeteringResult(
            event_id=event_id.strip(),
            amount=intended_amount,
            meter_slot=COMPRESSION_METER_SLOT,
            idempotency_key=idempotency_key,
            already_recorded=already_recorded,
        )
