"""Short-lived demo authorization with persistent rate and daily quotas."""

from dataclasses import dataclass
from datetime import datetime, timezone
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol


DEMO_MODE_ENABLED_ENV = "USAGETAP_DEMO_MODE_ENABLED"
DEMO_SIGNING_KEY_ENV = "USAGETAP_DEMO_SIGNING_KEY"
DEMO_SESSION_TTL_SECONDS_ENV = "USAGETAP_DEMO_SESSION_TTL_SECONDS"
DEMO_MAX_OPERATIONS_ENV = "USAGETAP_DEMO_MAX_OPERATIONS_PER_SESSION"
DEMO_MAX_INPUT_CHARS_ENV = "USAGETAP_DEMO_MAX_INPUT_CHARS_PER_SESSION"
DEMO_MAX_INPUT_CHARS_PER_OPERATION_ENV = (
    "USAGETAP_DEMO_MAX_INPUT_CHARS_PER_OPERATION"
)
DEMO_RATE_LIMIT_SESSIONS_ENV = "USAGETAP_DEMO_RATE_LIMIT_SESSIONS"
DEMO_RATE_LIMIT_WINDOW_SECONDS_ENV = "USAGETAP_DEMO_RATE_LIMIT_WINDOW_SECONDS"
DEMO_MAX_SESSIONS_PER_CLIENT_PER_DAY_ENV = (
    "USAGETAP_DEMO_MAX_SESSIONS_PER_CLIENT_PER_DAY"
)
DEMO_MAX_OPERATIONS_PER_CLIENT_PER_DAY_ENV = (
    "USAGETAP_DEMO_MAX_OPERATIONS_PER_CLIENT_PER_DAY"
)
DEMO_MAX_INPUT_CHARS_PER_CLIENT_PER_DAY_ENV = (
    "USAGETAP_DEMO_MAX_INPUT_CHARS_PER_CLIENT_PER_DAY"
)
DEMO_MAX_SESSIONS_PER_DAY_ENV = "USAGETAP_DEMO_MAX_SESSIONS_PER_DAY"
DEMO_MAX_OPERATIONS_PER_DAY_ENV = "USAGETAP_DEMO_MAX_OPERATIONS_PER_DAY"
DEMO_MAX_INPUT_CHARS_PER_DAY_ENV = "USAGETAP_DEMO_MAX_INPUT_CHARS_PER_DAY"
DEMO_STORAGE_BACKEND_ENV = "USAGETAP_DEMO_STORAGE_BACKEND"
DEMO_FIRESTORE_PROJECT_ENV = "USAGETAP_DEMO_FIRESTORE_PROJECT"
DEMO_FIRESTORE_DATABASE_ENV = "USAGETAP_DEMO_FIRESTORE_DATABASE"
DEMO_FIRESTORE_COLLECTION_ENV = "USAGETAP_DEMO_FIRESTORE_COLLECTION"

DEFAULT_DEMO_SESSION_TTL_SECONDS = 600
DEFAULT_DEMO_MAX_OPERATIONS = 5
DEFAULT_DEMO_MAX_INPUT_CHARS = 50_000
DEFAULT_DEMO_MAX_INPUT_CHARS_PER_OPERATION = 20_000
DEFAULT_DEMO_RATE_LIMIT_SESSIONS = 2
DEFAULT_DEMO_RATE_LIMIT_WINDOW_SECONDS = 3_600
DEFAULT_DEMO_MAX_SESSIONS_PER_CLIENT_PER_DAY = 5
DEFAULT_DEMO_MAX_OPERATIONS_PER_CLIENT_PER_DAY = 25
DEFAULT_DEMO_MAX_INPUT_CHARS_PER_CLIENT_PER_DAY = 100_000
DEFAULT_DEMO_MAX_SESSIONS_PER_DAY = 100
DEFAULT_DEMO_MAX_OPERATIONS_PER_DAY = 250
DEFAULT_DEMO_MAX_INPUT_CHARS_PER_DAY = 2_000_000
DEFAULT_DEMO_STORAGE_BACKEND = "memory"
DEFAULT_DEMO_FIRESTORE_DATABASE = "(default)"
DEFAULT_DEMO_FIRESTORE_COLLECTION = "prompt_compression_demo_v1"
MIN_DEMO_SIGNING_KEY_BYTES = 32
MAX_DEMO_TOKEN_LENGTH = 2048
SECONDS_PER_DAY = 86_400

_BEARER_DEMO_PATTERN = re.compile(
    r"(?i:Bearer) (demo-v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"
)


class DemoAccessError(Exception):
    """A safe error that may be returned to a demo caller."""

    def __init__(
        self,
        status_code: int,
        public_message: str,
        *,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(public_message)
        self.status_code = status_code
        self.public_message = public_message
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class DemoSession:
    token: str
    expires_at: int
    max_operations: int
    max_input_chars: int
    max_input_chars_per_operation: int
    daily_sessions_remaining: int
    daily_operations_remaining: int
    daily_input_chars_remaining: int


@dataclass(frozen=True, slots=True)
class DemoAuthorization:
    session_id: str
    expires_at: int
    client_key: str


@dataclass(frozen=True, slots=True)
class DemoQuotaPolicy:
    rate_limit_sessions: int
    rate_limit_window_seconds: int
    max_sessions_per_client_per_day: int
    max_operations_per_client_per_day: int
    max_input_chars_per_client_per_day: int
    max_sessions_per_day: int
    max_operations_per_day: int
    max_input_chars_per_day: int


@dataclass(frozen=True, slots=True)
class DemoQuotaSnapshot:
    client_sessions: int
    client_operations: int
    client_input_chars: int


@dataclass(slots=True)
class _DemoSessionState:
    client_key: str
    expires_at: int
    operations_remaining: int
    input_chars_remaining: int


@dataclass(slots=True)
class _DailyUsage:
    sessions: int = 0
    operations: int = 0
    input_chars: int = 0


class DemoAccessStore(Protocol):
    def issue_session(
        self,
        *,
        session_id: str,
        client_key: str,
        expires_at: int,
        operations_remaining: int,
        input_chars_remaining: int,
        now: int,
        policy: DemoQuotaPolicy,
    ) -> DemoQuotaSnapshot: ...

    def reserve_operation(
        self,
        *,
        session_id: str,
        expires_at: int,
        client_key: str,
        input_chars: int,
        now: int,
        policy: DemoQuotaPolicy,
    ) -> None: ...


class InMemoryDemoAccessStore:
    """Thread-safe local store used for development and unit tests."""

    def __init__(self) -> None:
        self._sessions: dict[str, _DemoSessionState] = {}
        self._rate_windows: dict[tuple[int, str], int] = {}
        self._client_days: dict[tuple[int, str], _DailyUsage] = {}
        self._global_days: dict[int, _DailyUsage] = {}
        self._lock = threading.Lock()

    def issue_session(
        self,
        *,
        session_id: str,
        client_key: str,
        expires_at: int,
        operations_remaining: int,
        input_chars_remaining: int,
        now: int,
        policy: DemoQuotaPolicy,
    ) -> DemoQuotaSnapshot:
        day = now // SECONDS_PER_DAY
        rate_window = now // policy.rate_limit_window_seconds
        rate_key = (rate_window, client_key)
        client_day_key = (day, client_key)
        with self._lock:
            self._prune_locked(now, policy)
            rate_count = self._rate_windows.get(rate_key, 0)
            if rate_count >= policy.rate_limit_sessions:
                retry_after = (
                    (rate_window + 1) * policy.rate_limit_window_seconds - now
                )
                raise _client_rate_limited(retry_after)

            client_usage = self._client_days.setdefault(
                client_day_key,
                _DailyUsage(),
            )
            global_usage = self._global_days.setdefault(day, _DailyUsage())
            _require_session_quota(client_usage, global_usage, now, policy)

            self._rate_windows[rate_key] = rate_count + 1
            client_usage.sessions += 1
            global_usage.sessions += 1
            self._sessions[session_id] = _DemoSessionState(
                client_key=client_key,
                expires_at=expires_at,
                operations_remaining=operations_remaining,
                input_chars_remaining=input_chars_remaining,
            )
            return DemoQuotaSnapshot(
                client_sessions=client_usage.sessions,
                client_operations=client_usage.operations,
                client_input_chars=client_usage.input_chars,
            )

    def reserve_operation(
        self,
        *,
        session_id: str,
        expires_at: int,
        client_key: str,
        input_chars: int,
        now: int,
        policy: DemoQuotaPolicy,
    ) -> None:
        day = now // SECONDS_PER_DAY
        with self._lock:
            self._prune_locked(now, policy)
            state = self._sessions.get(session_id)
            if (
                state is None
                or state.expires_at != expires_at
                or state.client_key != client_key
            ):
                raise _invalid_demo_session()
            if state.operations_remaining <= 0:
                raise DemoAccessError(429, "This demo session has no operations left.")
            if state.input_chars_remaining < input_chars:
                raise DemoAccessError(429, "This demo session has no input allowance left.")

            client_usage = self._client_days.setdefault(
                (day, client_key),
                _DailyUsage(),
            )
            global_usage = self._global_days.setdefault(day, _DailyUsage())
            _require_operation_quota(
                client_usage,
                global_usage,
                input_chars,
                now,
                policy,
            )

            state.operations_remaining -= 1
            state.input_chars_remaining -= input_chars
            client_usage.operations += 1
            client_usage.input_chars += input_chars
            global_usage.operations += 1
            global_usage.input_chars += input_chars

    def _prune_locked(self, now: int, policy: DemoQuotaPolicy) -> None:
        day = now // SECONDS_PER_DAY
        rate_window = now // policy.rate_limit_window_seconds
        self._sessions = {
            key: value
            for key, value in self._sessions.items()
            if value.expires_at > now
        }
        self._rate_windows = {
            key: value
            for key, value in self._rate_windows.items()
            if key[0] >= rate_window
        }
        self._client_days = {
            key: value for key, value in self._client_days.items() if key[0] >= day
        }
        self._global_days = {
            key: value for key, value in self._global_days.items() if key >= day
        }


class FirestoreDemoAccessStore:
    """Persistent Firestore store for production demo sessions and quotas."""

    def __init__(
        self,
        *,
        project: str | None = None,
        database: str = DEFAULT_DEMO_FIRESTORE_DATABASE,
        collection: str = DEFAULT_DEMO_FIRESTORE_COLLECTION,
    ) -> None:
        try:
            from google.api_core.exceptions import GoogleAPICallError, RetryError
            from google.cloud import firestore
        except ImportError as exc:
            raise ValueError(
                "Firestore demo storage requires google-cloud-firestore"
            ) from exc
        if not database.strip() or not collection.strip():
            raise ValueError("Firestore demo database and collection must not be empty")
        self._firestore = firestore
        self._storage_errors = (GoogleAPICallError, RetryError)
        self._client = firestore.Client(
            project=project or None,
            database=database.strip(),
        )
        self._collection = self._client.collection(collection.strip())

    def issue_session(
        self,
        *,
        session_id: str,
        client_key: str,
        expires_at: int,
        operations_remaining: int,
        input_chars_remaining: int,
        now: int,
        policy: DemoQuotaPolicy,
    ) -> DemoQuotaSnapshot:
        day = now // SECONDS_PER_DAY
        rate_window = now // policy.rate_limit_window_seconds
        session_ref = self._collection.document(f"session:{session_id}")
        rate_ref = self._collection.document(f"rate:{rate_window}:{client_key}")
        client_ref = self._collection.document(f"client:{day}:{client_key}")
        global_ref = self._collection.document(f"global:{day}")

        @self._firestore.transactional
        def apply(transaction: Any) -> DemoQuotaSnapshot:
            documents = _batch_snapshot_data(
                self._client,
                [rate_ref, client_ref, global_ref],
                transaction,
            )
            rate_data = documents.get(rate_ref.id, {})
            client_data = documents.get(client_ref.id, {})
            global_data = documents.get(global_ref.id, {})
            rate_count = _stored_int(rate_data, "sessions")
            if rate_count >= policy.rate_limit_sessions:
                retry_after = (
                    (rate_window + 1) * policy.rate_limit_window_seconds - now
                )
                raise _client_rate_limited(retry_after)

            client_usage = _stored_daily_usage(client_data)
            global_usage = _stored_daily_usage(global_data)
            _require_session_quota(client_usage, global_usage, now, policy)
            client_usage.sessions += 1
            global_usage.sessions += 1

            transaction.set(
                rate_ref,
                {
                    "sessions": rate_count + 1,
                    "expireAt": _utc_datetime(
                        (rate_window + 1) * policy.rate_limit_window_seconds
                        + SECONDS_PER_DAY
                    ),
                },
            )
            transaction.set(client_ref, _daily_document(client_usage, day))
            transaction.set(global_ref, _daily_document(global_usage, day))
            transaction.set(
                session_ref,
                {
                    "clientKey": client_key,
                    "expiresAtEpoch": expires_at,
                    "operationsRemaining": operations_remaining,
                    "inputCharsRemaining": input_chars_remaining,
                    "expireAt": _utc_datetime(expires_at + SECONDS_PER_DAY),
                },
            )
            return DemoQuotaSnapshot(
                client_sessions=client_usage.sessions,
                client_operations=client_usage.operations,
                client_input_chars=client_usage.input_chars,
            )

        try:
            return apply(self._client.transaction())
        except DemoAccessError:
            raise
        except self._storage_errors:
            raise _storage_unavailable() from None

    def reserve_operation(
        self,
        *,
        session_id: str,
        expires_at: int,
        client_key: str,
        input_chars: int,
        now: int,
        policy: DemoQuotaPolicy,
    ) -> None:
        day = now // SECONDS_PER_DAY
        session_ref = self._collection.document(f"session:{session_id}")
        client_ref = self._collection.document(f"client:{day}:{client_key}")
        global_ref = self._collection.document(f"global:{day}")

        @self._firestore.transactional
        def apply(transaction: Any) -> None:
            documents = _batch_snapshot_data(
                self._client,
                [session_ref, client_ref, global_ref],
                transaction,
            )
            session_data = documents.get(session_ref.id, {})
            if (
                session_data.get("clientKey") != client_key
                or _stored_int(session_data, "expiresAtEpoch") != expires_at
                or expires_at <= now
            ):
                raise _invalid_demo_session()
            operations_remaining = _stored_int(
                session_data,
                "operationsRemaining",
            )
            input_chars_remaining = _stored_int(
                session_data,
                "inputCharsRemaining",
            )
            if operations_remaining <= 0:
                raise DemoAccessError(429, "This demo session has no operations left.")
            if input_chars_remaining < input_chars:
                raise DemoAccessError(429, "This demo session has no input allowance left.")

            client_usage = _stored_daily_usage(
                documents.get(client_ref.id, {})
            )
            global_usage = _stored_daily_usage(
                documents.get(global_ref.id, {})
            )
            _require_operation_quota(
                client_usage,
                global_usage,
                input_chars,
                now,
                policy,
            )
            client_usage.operations += 1
            client_usage.input_chars += input_chars
            global_usage.operations += 1
            global_usage.input_chars += input_chars

            transaction.update(
                session_ref,
                {
                    "operationsRemaining": operations_remaining - 1,
                    "inputCharsRemaining": input_chars_remaining - input_chars,
                },
            )
            transaction.set(client_ref, _daily_document(client_usage, day))
            transaction.set(global_ref, _daily_document(global_usage, day))

        try:
            apply(self._client.transaction())
        except DemoAccessError:
            raise
        except self._storage_errors:
            raise _storage_unavailable() from None


class DemoSessionManager:
    """Issue signed demo credentials and enforce persistent bounded allowances."""

    def __init__(
        self,
        *,
        enabled: bool = False,
        signing_key: str | bytes | None = None,
        session_ttl_seconds: int = DEFAULT_DEMO_SESSION_TTL_SECONDS,
        max_operations: int = DEFAULT_DEMO_MAX_OPERATIONS,
        max_input_chars: int = DEFAULT_DEMO_MAX_INPUT_CHARS,
        max_input_chars_per_operation: int = (
            DEFAULT_DEMO_MAX_INPUT_CHARS_PER_OPERATION
        ),
        rate_limit_sessions: int = DEFAULT_DEMO_RATE_LIMIT_SESSIONS,
        rate_limit_window_seconds: int = DEFAULT_DEMO_RATE_LIMIT_WINDOW_SECONDS,
        max_sessions_per_client_per_day: int = (
            DEFAULT_DEMO_MAX_SESSIONS_PER_CLIENT_PER_DAY
        ),
        max_operations_per_client_per_day: int = (
            DEFAULT_DEMO_MAX_OPERATIONS_PER_CLIENT_PER_DAY
        ),
        max_input_chars_per_client_per_day: int = (
            DEFAULT_DEMO_MAX_INPUT_CHARS_PER_CLIENT_PER_DAY
        ),
        max_sessions_per_day: int = DEFAULT_DEMO_MAX_SESSIONS_PER_DAY,
        max_operations_per_day: int = DEFAULT_DEMO_MAX_OPERATIONS_PER_DAY,
        max_input_chars_per_day: int = DEFAULT_DEMO_MAX_INPUT_CHARS_PER_DAY,
        store: DemoAccessStore | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        key_bytes = (
            signing_key.encode("utf-8")
            if isinstance(signing_key, str)
            else signing_key
        )
        if enabled and (key_bytes is None or len(key_bytes) < MIN_DEMO_SIGNING_KEY_BYTES):
            raise ValueError("Demo signing key must contain at least 32 bytes")
        positive_values = {
            "session TTL": session_ttl_seconds,
            "operation allowance": max_operations,
            "input allowance": max_input_chars,
            "per-operation input limit": max_input_chars_per_operation,
            "rate-limit session allowance": rate_limit_sessions,
            "rate-limit window": rate_limit_window_seconds,
            "daily client session allowance": max_sessions_per_client_per_day,
            "daily client operation allowance": max_operations_per_client_per_day,
            "daily client input allowance": max_input_chars_per_client_per_day,
            "daily global session allowance": max_sessions_per_day,
            "daily global operation allowance": max_operations_per_day,
            "daily global input allowance": max_input_chars_per_day,
        }
        for label, value in positive_values.items():
            if value <= 0:
                raise ValueError(f"Demo {label} must be positive")
        if max_input_chars_per_operation > max_input_chars:
            raise ValueError("Demo per-operation input limit is invalid")
        if max_sessions_per_client_per_day > max_sessions_per_day:
            raise ValueError("Demo client session quota exceeds the global quota")
        if max_operations_per_client_per_day > max_operations_per_day:
            raise ValueError("Demo client operation quota exceeds the global quota")
        if max_input_chars_per_client_per_day > max_input_chars_per_day:
            raise ValueError("Demo client input quota exceeds the global quota")

        self._enabled = enabled
        self._signing_key = key_bytes or b""
        self._session_ttl_seconds = session_ttl_seconds
        self._max_operations = max_operations
        self._max_input_chars = max_input_chars
        self._max_input_chars_per_operation = max_input_chars_per_operation
        self._policy = DemoQuotaPolicy(
            rate_limit_sessions=rate_limit_sessions,
            rate_limit_window_seconds=rate_limit_window_seconds,
            max_sessions_per_client_per_day=max_sessions_per_client_per_day,
            max_operations_per_client_per_day=max_operations_per_client_per_day,
            max_input_chars_per_client_per_day=max_input_chars_per_client_per_day,
            max_sessions_per_day=max_sessions_per_day,
            max_operations_per_day=max_operations_per_day,
            max_input_chars_per_day=max_input_chars_per_day,
        )
        self._store = store or InMemoryDemoAccessStore()
        self._clock = clock

    @classmethod
    def from_environment(cls) -> "DemoSessionManager":
        enabled = os.getenv(DEMO_MODE_ENABLED_ENV, "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        backend = os.getenv(
            DEMO_STORAGE_BACKEND_ENV,
            DEFAULT_DEMO_STORAGE_BACKEND,
        ).strip().lower()
        if backend == "memory":
            store: DemoAccessStore = InMemoryDemoAccessStore()
        elif backend == "firestore":
            store = FirestoreDemoAccessStore(
                project=os.getenv(DEMO_FIRESTORE_PROJECT_ENV),
                database=os.getenv(
                    DEMO_FIRESTORE_DATABASE_ENV,
                    DEFAULT_DEMO_FIRESTORE_DATABASE,
                ),
                collection=os.getenv(
                    DEMO_FIRESTORE_COLLECTION_ENV,
                    DEFAULT_DEMO_FIRESTORE_COLLECTION,
                ),
            )
        else:
            raise ValueError("Demo storage backend must be memory or firestore")
        return cls(
            enabled=enabled,
            signing_key=os.getenv(DEMO_SIGNING_KEY_ENV),
            session_ttl_seconds=_int_env(
                DEMO_SESSION_TTL_SECONDS_ENV,
                DEFAULT_DEMO_SESSION_TTL_SECONDS,
            ),
            max_operations=_int_env(
                DEMO_MAX_OPERATIONS_ENV,
                DEFAULT_DEMO_MAX_OPERATIONS,
            ),
            max_input_chars=_int_env(
                DEMO_MAX_INPUT_CHARS_ENV,
                DEFAULT_DEMO_MAX_INPUT_CHARS,
            ),
            max_input_chars_per_operation=_int_env(
                DEMO_MAX_INPUT_CHARS_PER_OPERATION_ENV,
                DEFAULT_DEMO_MAX_INPUT_CHARS_PER_OPERATION,
            ),
            rate_limit_sessions=_int_env(
                DEMO_RATE_LIMIT_SESSIONS_ENV,
                DEFAULT_DEMO_RATE_LIMIT_SESSIONS,
            ),
            rate_limit_window_seconds=_int_env(
                DEMO_RATE_LIMIT_WINDOW_SECONDS_ENV,
                DEFAULT_DEMO_RATE_LIMIT_WINDOW_SECONDS,
            ),
            max_sessions_per_client_per_day=_int_env(
                DEMO_MAX_SESSIONS_PER_CLIENT_PER_DAY_ENV,
                DEFAULT_DEMO_MAX_SESSIONS_PER_CLIENT_PER_DAY,
            ),
            max_operations_per_client_per_day=_int_env(
                DEMO_MAX_OPERATIONS_PER_CLIENT_PER_DAY_ENV,
                DEFAULT_DEMO_MAX_OPERATIONS_PER_CLIENT_PER_DAY,
            ),
            max_input_chars_per_client_per_day=_int_env(
                DEMO_MAX_INPUT_CHARS_PER_CLIENT_PER_DAY_ENV,
                DEFAULT_DEMO_MAX_INPUT_CHARS_PER_CLIENT_PER_DAY,
            ),
            max_sessions_per_day=_int_env(
                DEMO_MAX_SESSIONS_PER_DAY_ENV,
                DEFAULT_DEMO_MAX_SESSIONS_PER_DAY,
            ),
            max_operations_per_day=_int_env(
                DEMO_MAX_OPERATIONS_PER_DAY_ENV,
                DEFAULT_DEMO_MAX_OPERATIONS_PER_DAY,
            ),
            max_input_chars_per_day=_int_env(
                DEMO_MAX_INPUT_CHARS_PER_DAY_ENV,
                DEFAULT_DEMO_MAX_INPUT_CHARS_PER_DAY,
            ),
            store=store,
        )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def issue_session(self, client_identifier: str = "unknown") -> DemoSession:
        now = self._now()
        self._require_available()
        expires_at = now + self._session_ttl_seconds
        session_id = secrets.token_urlsafe(18)
        client_key = self._client_key(client_identifier)
        quota = self._store.issue_session(
            session_id=session_id,
            client_key=client_key,
            expires_at=expires_at,
            operations_remaining=self._max_operations,
            input_chars_remaining=self._max_input_chars,
            now=now,
            policy=self._policy,
        )

        payload = self._encode_payload(
            {
                "exp": expires_at,
                "iat": now,
                "sid": session_id,
                "cid": client_key,
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
            daily_sessions_remaining=max(
                0,
                self._policy.max_sessions_per_client_per_day
                - quota.client_sessions,
            ),
            daily_operations_remaining=max(
                0,
                self._policy.max_operations_per_client_per_day
                - quota.client_operations,
            ),
            daily_input_chars_remaining=max(
                0,
                self._policy.max_input_chars_per_client_per_day
                - quota.client_input_chars,
            ),
        )

    def validate_authorization_header(
        self,
        authorization_header: str | None,
    ) -> DemoAuthorization:
        now = self._now()
        self._require_available()
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
        client_key = claims.get("cid")
        issued_at = claims.get("iat")
        expires_at = claims.get("exp")
        if (
            claims.get("v") != 1
            or not isinstance(session_id, str)
            or not session_id
            or not isinstance(client_key, str)
            or re.fullmatch(r"[0-9a-f]{64}", client_key) is None
            or isinstance(issued_at, bool)
            or not isinstance(issued_at, int)
            or isinstance(expires_at, bool)
            or not isinstance(expires_at, int)
            or issued_at > now
            or expires_at <= now
            or expires_at > now + self._session_ttl_seconds
        ):
            raise self._invalid()

        return DemoAuthorization(
            session_id=session_id,
            expires_at=expires_at,
            client_key=client_key,
        )

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
        self._require_available()
        self._store.reserve_operation(
            session_id=authorization.session_id,
            expires_at=authorization.expires_at,
            client_key=authorization.client_key,
            input_chars=input_chars,
            now=now,
            policy=self._policy,
        )

    def _client_key(self, client_identifier: str) -> str:
        normalized = client_identifier.strip() or "unknown"
        return hmac.new(
            self._signing_key,
            f"demo-client-v1\n{normalized}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _require_available(self) -> None:
        if not self._enabled:
            raise self._unavailable()

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

    def _now(self) -> int:
        return int(self._clock())

    @staticmethod
    def _invalid() -> DemoAccessError:
        return _invalid_demo_session()

    @staticmethod
    def _unavailable() -> DemoAccessError:
        return DemoAccessError(503, "Demo access is not currently available.")


def demo_client_identifier(
    forwarded_for: str | None,
    direct_host: str | None,
    *,
    trust_forwarded_for: bool,
) -> str:
    """Return the trusted Cloud Run client address without retaining raw headers."""
    if trust_forwarded_for and isinstance(forwarded_for, str):
        parts = [part.strip() for part in forwarded_for.split(",")]
        if len(parts) >= 2:
            try:
                client_ip = ipaddress.ip_address(parts[-2])
                ipaddress.ip_address(parts[-1])
            except ValueError:
                pass
            else:
                return client_ip.compressed
    normalized_direct = (direct_host or "unknown").strip()
    return normalized_direct[:255] or "unknown"


def _require_session_quota(
    client_usage: _DailyUsage,
    global_usage: _DailyUsage,
    now: int,
    policy: DemoQuotaPolicy,
) -> None:
    retry_after = _seconds_until_next_utc_day(now)
    if client_usage.sessions >= policy.max_sessions_per_client_per_day:
        raise DemoAccessError(
            429,
            "This network has reached today's demo session quota.",
            retry_after_seconds=retry_after,
        )
    if global_usage.sessions >= policy.max_sessions_per_day:
        raise DemoAccessError(
            429,
            "Today's demo capacity has been reached.",
            retry_after_seconds=retry_after,
        )


def _require_operation_quota(
    client_usage: _DailyUsage,
    global_usage: _DailyUsage,
    input_chars: int,
    now: int,
    policy: DemoQuotaPolicy,
) -> None:
    retry_after = _seconds_until_next_utc_day(now)
    if client_usage.operations >= policy.max_operations_per_client_per_day:
        raise DemoAccessError(
            429,
            "This network has reached today's demo operation quota.",
            retry_after_seconds=retry_after,
        )
    if client_usage.input_chars + input_chars > policy.max_input_chars_per_client_per_day:
        raise DemoAccessError(
            429,
            "This network has reached today's demo input quota.",
            retry_after_seconds=retry_after,
        )
    if global_usage.operations >= policy.max_operations_per_day:
        raise DemoAccessError(
            429,
            "Today's demo operation capacity has been reached.",
            retry_after_seconds=retry_after,
        )
    if global_usage.input_chars + input_chars > policy.max_input_chars_per_day:
        raise DemoAccessError(
            429,
            "Today's demo input capacity has been reached.",
            retry_after_seconds=retry_after,
        )


def _client_rate_limited(retry_after_seconds: int) -> DemoAccessError:
    return DemoAccessError(
        429,
        "Too many demo sessions from this network. Try again later.",
        retry_after_seconds=max(1, retry_after_seconds),
    )


def _invalid_demo_session() -> DemoAccessError:
    return DemoAccessError(401, "Invalid or expired demo session.")


def _storage_unavailable() -> DemoAccessError:
    return DemoAccessError(503, "Demo access is temporarily unavailable.")


def _seconds_until_next_utc_day(now: int) -> int:
    return max(1, ((now // SECONDS_PER_DAY) + 1) * SECONDS_PER_DAY - now)


def _utc_datetime(epoch_seconds: int) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, timezone.utc)


def _snapshot_data(snapshot: Any) -> dict[str, Any]:
    if not getattr(snapshot, "exists", False):
        return {}
    data = snapshot.to_dict()
    return data if isinstance(data, dict) else {}


def _batch_snapshot_data(
    client: Any,
    references: list[Any],
    transaction: Any,
) -> dict[str, dict[str, Any]]:
    return {
        snapshot.reference.id: _snapshot_data(snapshot)
        for snapshot in client.get_all(references, transaction=transaction)
    }


def _stored_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key, 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _stored_daily_usage(data: dict[str, Any]) -> _DailyUsage:
    return _DailyUsage(
        sessions=_stored_int(data, "sessions"),
        operations=_stored_int(data, "operations"),
        input_chars=_stored_int(data, "inputChars"),
    )


def _daily_document(usage: _DailyUsage, day: int) -> dict[str, Any]:
    return {
        "sessions": usage.sessions,
        "operations": usage.operations,
        "inputChars": usage.input_chars,
        "expireAt": _utc_datetime((day + 3) * SECONDS_PER_DAY),
    }


def _int_env(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))
