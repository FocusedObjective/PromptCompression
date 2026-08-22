"""Fail-closed authorization for UsageTap compression requests."""

from collections.abc import Callable
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import math
import os
import re
import secrets
import threading
import time
from typing import Any

import requests


USAGETAP_API_BASE_URL_ENV = "USAGETAP_API_BASE_URL"
USAGETAP_AUTHORIZATION_TIMEOUT_ENV = "USAGETAP_AUTHORIZATION_TIMEOUT_SECONDS"
USAGETAP_COMPRESSION_KEY_MIN_SUFFIX_LENGTH_ENV = (
    "USAGETAP_COMPRESSION_KEY_MIN_SUFFIX_LENGTH"
)
USAGETAP_COMPRESSION_KEY_MAX_SUFFIX_LENGTH_ENV = (
    "USAGETAP_COMPRESSION_KEY_MAX_SUFFIX_LENGTH"
)
USAGETAP_AUTHORIZATION_FAILURE_CACHE_SECONDS_ENV = (
    "USAGETAP_AUTHORIZATION_FAILURE_CACHE_SECONDS"
)
DEFAULT_USAGETAP_API_BASE_URL = "https://api.usagetap.com"
DEFAULT_USAGETAP_AUTHORIZATION_TIMEOUT_SECONDS = 3.0
COMPRESSION_KEY_SUFFIX_LENGTH = 43
UNIVERSAL_API_KEY_SUFFIX_LENGTH = 43
DEFAULT_COMPRESSION_KEY_MIN_SUFFIX_LENGTH = COMPRESSION_KEY_SUFFIX_LENGTH
DEFAULT_COMPRESSION_KEY_MAX_SUFFIX_LENGTH = COMPRESSION_KEY_SUFFIX_LENGTH
DEFAULT_AUTHORIZATION_FAILURE_CACHE_SECONDS = 5.0
DEFAULT_AUTHORIZATION_FAILURE_CACHE_MAX_ENTRIES = 4096
USAGETAP_COMPRESSION_AUTHORIZATION_PATH = "/v1/compression/authorize"
USAGETAP_ACCEPT_HEADER = "application/vnd.usagetap.v1+json"

_BEARER_CREDENTIAL_PATTERN = re.compile(r"(?i:Bearer) ([^\s]+)")
_COMPRESSION_KEY_PATTERN = re.compile(r"cmp-([A-Za-z0-9_-]+)")
_UNIVERSAL_API_KEY_PATTERN = re.compile(
    rf"utk-([A-Za-z0-9_-]{{{UNIVERSAL_API_KEY_SUFFIX_LENGTH}}})"
)
_COMPRESSION_SESSION_PATTERN = re.compile(
    r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
)
MAX_COMPRESSION_SESSION_LENGTH = 8192


@dataclass(frozen=True, slots=True)
class UsageTapAuthorization:
    """Verified request identity returned by UsageTap (never client supplied)."""

    organization_id: str
    customer_id: str


class UsageTapAuthorizationError(Exception):
    """A safe, caller-facing authorization failure."""

    def __init__(self, status_code: int, public_message: str) -> None:
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message


@dataclass(frozen=True, slots=True)
class _CachedAuthorizationFailure:
    status_code: int
    public_message: str
    expires_at: float


class UsageTapAuthorizationFailureCache:
    """Small process-local cache of irreversible/recent authorization failures."""

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_AUTHORIZATION_FAILURE_CACHE_SECONDS,
        max_entries: int = DEFAULT_AUTHORIZATION_FAILURE_CACHE_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not math.isfinite(ttl_seconds) or ttl_seconds < 0:
            raise ValueError("Authorization failure cache TTL must not be negative")
        if max_entries <= 0:
            raise ValueError("Authorization failure cache size must be positive")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._salt = secrets.token_bytes(32)
        self._entries: OrderedDict[bytes, _CachedAuthorizationFailure] = OrderedDict()
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> "UsageTapAuthorizationFailureCache":
        return cls(
            ttl_seconds=float(
                os.getenv(
                    USAGETAP_AUTHORIZATION_FAILURE_CACHE_SECONDS_ENV,
                    str(DEFAULT_AUTHORIZATION_FAILURE_CACHE_SECONDS),
                )
            )
        )

    def get(self, authorization_header: str) -> UsageTapAuthorizationError | None:
        if self._ttl_seconds == 0:
            return None
        digest = self._digest(authorization_header)
        now = self._clock()
        with self._lock:
            cached = self._entries.get(digest)
            if cached is None:
                return None
            if cached.expires_at <= now:
                del self._entries[digest]
                return None
            self._entries.move_to_end(digest)
            return UsageTapAuthorizationError(
                cached.status_code,
                cached.public_message,
            )

    def record(
        self,
        authorization_header: str,
        error: UsageTapAuthorizationError,
    ) -> None:
        if self._ttl_seconds == 0 or error.status_code not in {401, 402, 403}:
            return
        digest = self._digest(authorization_header)
        cached = _CachedAuthorizationFailure(
            status_code=error.status_code,
            public_message=error.public_message,
            expires_at=self._clock() + self._ttl_seconds,
        )
        with self._lock:
            self._entries[digest] = cached
            self._entries.move_to_end(digest)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def _digest(self, authorization_header: str) -> bytes:
        return hmac.new(
            self._salt,
            authorization_header.encode("utf-8"),
            hashlib.sha256,
        ).digest()


class UsageTapAuthorizationClient:
    """Validate a compression credential against UsageTap for one operation."""

    def __init__(
        self,
        *,
        api_base_url: str = DEFAULT_USAGETAP_API_BASE_URL,
        timeout_seconds: float = DEFAULT_USAGETAP_AUTHORIZATION_TIMEOUT_SECONDS,
        min_key_suffix_length: int = DEFAULT_COMPRESSION_KEY_MIN_SUFFIX_LENGTH,
        max_key_suffix_length: int = DEFAULT_COMPRESSION_KEY_MAX_SUFFIX_LENGTH,
        post: Callable[..., Any] = requests.post,
    ) -> None:
        if not api_base_url.strip():
            raise ValueError("UsageTap API base URL must not be empty")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("UsageTap authorization timeout must be positive")
        if min_key_suffix_length <= 0:
            raise ValueError("Compression key minimum suffix length must be positive")
        if max_key_suffix_length < min_key_suffix_length:
            raise ValueError("Compression key maximum must not be below minimum")
        self._authorization_url = (
            f"{api_base_url.rstrip('/')}{USAGETAP_COMPRESSION_AUTHORIZATION_PATH}"
        )
        self._timeout_seconds = timeout_seconds
        self._min_key_suffix_length = min_key_suffix_length
        self._max_key_suffix_length = max_key_suffix_length
        self._post = post

    @classmethod
    def from_environment(cls) -> "UsageTapAuthorizationClient":
        base_url = os.getenv(
            USAGETAP_API_BASE_URL_ENV,
            DEFAULT_USAGETAP_API_BASE_URL,
        )
        timeout_seconds = float(
            os.getenv(
                USAGETAP_AUTHORIZATION_TIMEOUT_ENV,
                str(DEFAULT_USAGETAP_AUTHORIZATION_TIMEOUT_SECONDS),
            )
        )
        return cls(
            api_base_url=base_url,
            timeout_seconds=timeout_seconds,
            min_key_suffix_length=int(
                os.getenv(
                    USAGETAP_COMPRESSION_KEY_MIN_SUFFIX_LENGTH_ENV,
                    str(DEFAULT_COMPRESSION_KEY_MIN_SUFFIX_LENGTH),
                )
            ),
            max_key_suffix_length=int(
                os.getenv(
                    USAGETAP_COMPRESSION_KEY_MAX_SUFFIX_LENGTH_ENV,
                    str(DEFAULT_COMPRESSION_KEY_MAX_SUFFIX_LENGTH),
                )
            ),
        )

    def authorize(self, authorization_header: str | None) -> UsageTapAuthorization:
        self.validate_incoming_credential(authorization_header)
        assert authorization_header is not None

        try:
            response = self._post(
                self._authorization_url,
                headers={
                    "Authorization": authorization_header,
                    "Accept": USAGETAP_ACCEPT_HEADER,
                },
                timeout=self._timeout_seconds,
                allow_redirects=False,
                verify=True,
            )
        except requests.RequestException:
            raise self._unavailable() from None

        if response.status_code == 401:
            raise UsageTapAuthorizationError(
                401,
                "Invalid or missing compression credentials.",
            )
        if response.status_code == 402:
            raise UsageTapAuthorizationError(
                402,
                "Compression credit is unavailable.",
            )
        if response.status_code == 403:
            raise UsageTapAuthorizationError(
                403,
                "Compression key lacks required permissions.",
            )
        if response.status_code != 200:
            raise self._unavailable()

        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise self._unavailable() from None

        if not isinstance(payload, dict):
            raise self._unavailable()
        data = payload.get("data")
        if not isinstance(data, dict) or data.get("authorized") is not True:
            raise self._unavailable()

        organization_id = data.get("organizationId")
        customer_id = data.get("customerId")
        if not self._non_empty_string(organization_id):
            raise self._unavailable()
        if not self._non_empty_string(customer_id):
            raise self._unavailable()

        return UsageTapAuthorization(
            organization_id=organization_id.strip(),
            customer_id=customer_id.strip(),
        )

    def validate_incoming_credential(self, authorization_header: str | None) -> str:
        bearer_match = (
            _BEARER_CREDENTIAL_PATTERN.fullmatch(authorization_header)
            if isinstance(authorization_header, str)
            else None
        )
        credential = bearer_match.group(1) if bearer_match is not None else ""
        key_match = _COMPRESSION_KEY_PATTERN.fullmatch(credential)
        universal_key_match = _UNIVERSAL_API_KEY_PATTERN.fullmatch(credential)
        suffix_length = len(key_match.group(1)) if key_match is not None else 0
        valid_api_key = bool(
            key_match is not None
            and self._min_key_suffix_length
            <= suffix_length
            <= self._max_key_suffix_length
        )
        valid_universal_api_key = universal_key_match is not None
        valid_session = bool(
            credential
            and len(credential) <= MAX_COMPRESSION_SESSION_LENGTH
            and _COMPRESSION_SESSION_PATTERN.fullmatch(credential)
        )
        if not (valid_api_key or valid_universal_api_key or valid_session):
            raise UsageTapAuthorizationError(
                401,
                "Invalid or missing compression credentials.",
            )
        assert authorization_header is not None
        return authorization_header

    @staticmethod
    def _non_empty_string(value: object) -> bool:
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _unavailable() -> UsageTapAuthorizationError:
        return UsageTapAuthorizationError(
            503,
            "Compression authorization is temporarily unavailable.",
        )
