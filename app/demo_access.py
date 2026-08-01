"""Short-lived, bounded demo authorization for controlled UI testing."""

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import threading
import time
from collections.abc import Callable


DEMO_MODE_ENABLED_ENV = "USAGETAP_DEMO_MODE_ENABLED"
DEMO_SIGNING_KEY_ENV = "USAGETAP_DEMO_SIGNING_KEY"
DEMO_MODE_EXPIRES_AT_ENV = "USAGETAP_DEMO_MODE_EXPIRES_AT"
DEMO_SESSION_TTL_SECONDS_ENV = "USAGETAP_DEMO_SESSION_TTL_SECONDS"
DEMO_MAX_OPERATIONS_ENV = "USAGETAP_DEMO_MAX_OPERATIONS_PER_SESSION"
DEMO_MAX_INPUT_CHARS_ENV = "USAGETAP_DEMO_MAX_INPUT_CHARS_PER_SESSION"
DEMO_MAX_INPUT_CHARS_PER_OPERATION_ENV = (
    "USAGETAP_DEMO_MAX_INPUT_CHARS_PER_OPERATION"
)
DEMO_MAX_ACTIVE_SESSIONS_ENV = "USAGETAP_DEMO_MAX_ACTIVE_SESSIONS"

DEFAULT_DEMO_SESSION_TTL_SECONDS = 600
DEFAULT_DEMO_MAX_OPERATIONS = 5
DEFAULT_DEMO_MAX_INPUT_CHARS = 50_000
DEFAULT_DEMO_MAX_INPUT_CHARS_PER_OPERATION = 20_000
DEFAULT_DEMO_MAX_ACTIVE_SESSIONS = 10
MIN_DEMO_SIGNING_KEY_BYTES = 32
MAX_DEMO_TOKEN_LENGTH = 2048

_BEARER_DEMO_PATTERN = re.compile(
    r"(?i:Bearer) (demo-v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"
)


class DemoAccessError(Exception):
    """A safe error that may be returned to a demo caller."""

    def __init__(self, status_code: int, public_message: str) -> None:
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message


@dataclass(frozen=True, slots=True)
class DemoSession:
    token: str
    expires_at: int
    max_operations: int
    max_input_chars: int
    max_input_chars_per_operation: int


@dataclass(frozen=True, slots=True)
class DemoAuthorization:
    session_id: str
    expires_at: int


@dataclass(slots=True)
class _DemoSessionState:
    expires_at: int
    operations_remaining: int
    input_chars_remaining: int


class DemoSessionManager:
    """Issue and enforce small process-local demo allowances.

    Cloud Run must use a single instance while this mode is enabled so these
    counters remain authoritative for the deployment.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        signing_key: str | bytes | None = None,
        mode_expires_at: int | None = None,
        session_ttl_seconds: int = DEFAULT_DEMO_SESSION_TTL_SECONDS,
        max_operations: int = DEFAULT_DEMO_MAX_OPERATIONS,
        max_input_chars: int = DEFAULT_DEMO_MAX_INPUT_CHARS,
        max_input_chars_per_operation: int = (
            DEFAULT_DEMO_MAX_INPUT_CHARS_PER_OPERATION
        ),
        max_active_sessions: int = DEFAULT_DEMO_MAX_ACTIVE_SESSIONS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        key_bytes = (
            signing_key.encode("utf-8")
            if isinstance(signing_key, str)
            else signing_key
        )
        if enabled and (key_bytes is None or len(key_bytes) < MIN_DEMO_SIGNING_KEY_BYTES):
            raise ValueError("Demo signing key must contain at least 32 bytes")
        if enabled and mode_expires_at is None:
            raise ValueError("Demo mode expiry is required when demo mode is enabled")
        if session_ttl_seconds <= 0:
            raise ValueError("Demo session TTL must be positive")
        if max_operations <= 0:
            raise ValueError("Demo operation allowance must be positive")
        if max_input_chars <= 0:
            raise ValueError("Demo input allowance must be positive")
        if not 0 < max_input_chars_per_operation <= max_input_chars:
            raise ValueError("Demo per-operation input limit is invalid")
        if max_active_sessions <= 0:
            raise ValueError("Demo active-session limit must be positive")

        self._enabled = enabled
        self._signing_key = key_bytes or b""
        self._mode_expires_at = mode_expires_at
        self._session_ttl_seconds = session_ttl_seconds
        self._max_operations = max_operations
        self._max_input_chars = max_input_chars
        self._max_input_chars_per_operation = max_input_chars_per_operation
        self._max_active_sessions = max_active_sessions
        self._clock = clock
        self._sessions: dict[str, _DemoSessionState] = {}
        self._lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> "DemoSessionManager":
        enabled = os.getenv(DEMO_MODE_ENABLED_ENV, "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        expires_at_value = os.getenv(DEMO_MODE_EXPIRES_AT_ENV, "").strip()
        mode_expires_at = (
            cls._parse_expiry(expires_at_value) if expires_at_value else None
        )
        return cls(
            enabled=enabled,
            signing_key=os.getenv(DEMO_SIGNING_KEY_ENV),
            mode_expires_at=mode_expires_at,
            session_ttl_seconds=int(
                os.getenv(
                    DEMO_SESSION_TTL_SECONDS_ENV,
                    str(DEFAULT_DEMO_SESSION_TTL_SECONDS),
                )
            ),
            max_operations=int(
                os.getenv(DEMO_MAX_OPERATIONS_ENV, str(DEFAULT_DEMO_MAX_OPERATIONS))
            ),
            max_input_chars=int(
                os.getenv(DEMO_MAX_INPUT_CHARS_ENV, str(DEFAULT_DEMO_MAX_INPUT_CHARS))
            ),
            max_input_chars_per_operation=int(
                os.getenv(
                    DEMO_MAX_INPUT_CHARS_PER_OPERATION_ENV,
                    str(DEFAULT_DEMO_MAX_INPUT_CHARS_PER_OPERATION),
                )
            ),
            max_active_sessions=int(
                os.getenv(
                    DEMO_MAX_ACTIVE_SESSIONS_ENV,
                    str(DEFAULT_DEMO_MAX_ACTIVE_SESSIONS),
                )
            ),
        )

    @property
    def enabled(self) -> bool:
        return self._enabled and not self._mode_expired()

    def issue_session(self) -> DemoSession:
        now = self._now()
        self._require_available(now)
        assert self._mode_expires_at is not None
        expires_at = min(now + self._session_ttl_seconds, self._mode_expires_at)
        if expires_at <= now:
            raise self._unavailable()

        with self._lock:
            self._prune_locked(now)
            if len(self._sessions) >= self._max_active_sessions:
                raise DemoAccessError(
                    429,
                    "Demo capacity is temporarily full. Try again later.",
                )
            session_id = secrets.token_urlsafe(18)
            self._sessions[session_id] = _DemoSessionState(
                expires_at=expires_at,
                operations_remaining=self._max_operations,
                input_chars_remaining=self._max_input_chars,
            )

        payload = self._encode_payload(
            {
                "exp": expires_at,
                "iat": now,
                "sid": session_id,
                "v": 1,
            }
        )
        signature = self._sign(payload)
        return DemoSession(
            token=f"demo-v1.{payload}.{signature}",
            expires_at=expires_at,
            max_operations=self._max_operations,
            max_input_chars=self._max_input_chars,
            max_input_chars_per_operation=self._max_input_chars_per_operation,
        )

    def validate_authorization_header(
        self,
        authorization_header: str | None,
    ) -> DemoAuthorization:
        now = self._now()
        self._require_available(now)
        match = (
            _BEARER_DEMO_PATTERN.fullmatch(authorization_header)
            if isinstance(authorization_header, str)
            and len(authorization_header) <= MAX_DEMO_TOKEN_LENGTH + len("Bearer ")
            else None
        )
        if match is None:
            raise self._invalid()

        token = match.group(1)
        _prefix, payload, signature = token.split(".", 2)
        if not hmac.compare_digest(signature, self._sign(payload)):
            raise self._invalid()
        try:
            claims = json.loads(self._decode_payload(payload))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            raise self._invalid() from None
        if not isinstance(claims, dict):
            raise self._invalid()
        session_id = claims.get("sid")
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        if (
            claims.get("v") != 1
            or not isinstance(session_id, str)
            or not session_id
            or isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or issued_at > now
            or expires_at <= now
            or expires_at > now + self._session_ttl_seconds
        ):
            raise self._invalid()

        with self._lock:
            self._prune_locked(now)
            state = self._sessions.get(session_id)
            if state is None or state.expires_at != expires_at:
                raise self._invalid()
        return DemoAuthorization(session_id=session_id, expires_at=expires_at)

    def reserve_operation(
        self,
        authorization: DemoAuthorization,
        *,
        input_chars: int,
    ) -> None:
        if input_chars <= 0:
            raise DemoAccessError(400, "Demo input must not be empty.")
        if input_chars > self._max_input_chars_per_operation:
            raise DemoAccessError(
                413,
                "Demo input is too large for one operation.",
            )
        now = self._now()
        self._require_available(now)
        with self._lock:
            self._prune_locked(now)
            state = self._sessions.get(authorization.session_id)
            if state is None or state.expires_at != authorization.expires_at:
                raise self._invalid()
            if state.operations_remaining <= 0:
                raise DemoAccessError(429, "This demo session has no operations left.")
            if state.input_chars_remaining < input_chars:
                raise DemoAccessError(429, "This demo session has no input allowance left.")
            state.operations_remaining -= 1
            state.input_chars_remaining -= input_chars

    def _require_available(self, now: int) -> None:
        if (
            not self._enabled
            or self._mode_expires_at is None
            or self._mode_expires_at <= now
        ):
            raise self._unavailable()

    def _mode_expired(self) -> bool:
        return self._mode_expires_at is None or self._mode_expires_at <= self._now()

    def _prune_locked(self, now: int) -> None:
        expired = [
            session_id
            for session_id, state in self._sessions.items()
            if state.expires_at <= now
        ]
        for session_id in expired:
            del self._sessions[session_id]

    def _sign(self, payload: str) -> str:
        digest = hmac.new(
            self._signing_key,
            f"demo-v1.{payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        return self._b64encode(digest)

    @classmethod
    def _encode_payload(cls, claims: dict[str, int | str]) -> str:
        raw = json.dumps(
            claims,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return cls._b64encode(raw)

    @staticmethod
    def _decode_payload(payload: str) -> str:
        padding = "=" * (-len(payload) % 4)
        return base64.urlsafe_b64decode(payload + padding).decode("utf-8")

    @staticmethod
    def _b64encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _parse_expiry(value: str) -> int:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            raise ValueError("Demo mode expiry must include a timezone")
        timestamp = parsed.astimezone(timezone.utc).timestamp()
        if not math.isfinite(timestamp):
            raise ValueError("Demo mode expiry is invalid")
        return int(timestamp)

    def _now(self) -> int:
        return int(self._clock())

    @staticmethod
    def _invalid() -> DemoAccessError:
        return DemoAccessError(401, "Invalid or expired demo session.")

    @staticmethod
    def _unavailable() -> DemoAccessError:
        return DemoAccessError(503, "Demo access is not currently available.")
