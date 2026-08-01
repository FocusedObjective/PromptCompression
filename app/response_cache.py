"""Bounded process-local response cache for repeated compression requests."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
import os
from threading import Event, Lock
import time


DEFAULT_RESPONSE_CACHE_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_RESPONSE_CACHE_MAX_ENTRY_BYTES = 1024 * 1024
DEFAULT_RESPONSE_CACHE_MAX_ENTRIES = 4096
DEFAULT_RESPONSE_CACHE_TTL_SECONDS = 300.0
DEFAULT_RESPONSE_CACHE_SINGLE_FLIGHT_TIMEOUT_SECONDS = 30.0

_ENTRY_OVERHEAD_BYTES = 256


@dataclass(frozen=True, slots=True)
class CacheLookup:
    payload: bytes
    status: str
    cacheable: bool = False


@dataclass(slots=True)
class _CacheEntry:
    payload: bytes
    expires_at: float
    size_bytes: int


@dataclass(slots=True)
class _Flight:
    event: Event
    payload: bytes | None = None
    cacheable: bool = False
    error: BaseException | None = None


class LocalResponseCache:
    """TTL/LRU byte cache with per-key single-flight request coalescing."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        max_bytes: int = DEFAULT_RESPONSE_CACHE_MAX_BYTES,
        max_entry_bytes: int = DEFAULT_RESPONSE_CACHE_MAX_ENTRY_BYTES,
        max_entries: int = DEFAULT_RESPONSE_CACHE_MAX_ENTRIES,
        ttl_seconds: float = DEFAULT_RESPONSE_CACHE_TTL_SECONDS,
        single_flight_timeout_seconds: float = (
            DEFAULT_RESPONSE_CACHE_SINGLE_FLIGHT_TIMEOUT_SECONDS
        ),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.enabled = enabled
        self.max_bytes = max(0, max_bytes)
        self.max_entry_bytes = max(0, max_entry_bytes)
        self.max_entries = max(0, max_entries)
        self.ttl_seconds = max(0.0, ttl_seconds)
        self.single_flight_timeout_seconds = max(
            0.0,
            single_flight_timeout_seconds,
        )
        self._clock = clock
        self._entries: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._total_bytes = 0
        self._lock = Lock()
        self._flights: dict[str, _Flight] = {}
        self._flight_lock = Lock()
        self._hits = 0
        self._misses = 0
        self._stores = 0
        self._evictions = 0
        self._shared = 0

    @classmethod
    def from_environment(cls) -> LocalResponseCache:
        return cls(
            enabled=_environment_bool("RESPONSE_CACHE_ENABLED", True),
            max_bytes=_environment_int(
                "RESPONSE_CACHE_MAX_BYTES",
                DEFAULT_RESPONSE_CACHE_MAX_BYTES,
            ),
            max_entry_bytes=_environment_int(
                "RESPONSE_CACHE_MAX_ENTRY_BYTES",
                DEFAULT_RESPONSE_CACHE_MAX_ENTRY_BYTES,
            ),
            max_entries=_environment_int(
                "RESPONSE_CACHE_MAX_ENTRIES",
                DEFAULT_RESPONSE_CACHE_MAX_ENTRIES,
            ),
            ttl_seconds=_environment_float(
                "RESPONSE_CACHE_TTL_SECONDS",
                DEFAULT_RESPONSE_CACHE_TTL_SECONDS,
            ),
            single_flight_timeout_seconds=_environment_float(
                "RESPONSE_CACHE_SINGLE_FLIGHT_TIMEOUT_SECONDS",
                DEFAULT_RESPONSE_CACHE_SINGLE_FLIGHT_TIMEOUT_SECONDS,
            ),
        )

    def get_or_compute(
        self,
        key: str,
        compute: Callable[[], tuple[bytes, bool]],
        *,
        store_result: bool = True,
    ) -> CacheLookup:
        """Return cached bytes or compute once for concurrent callers.

        ``compute`` returns serialized response bytes and whether the completed
        response is eligible for TTL storage. Ineligible responses can still be
        shared by requests already waiting on the same in-flight computation.
        """
        if not self._usable:
            payload, _ = compute()
            return CacheLookup(payload=payload, status="disabled", cacheable=False)

        cached = self.get(key)
        if cached is not None:
            return CacheLookup(payload=cached, status="hit", cacheable=True)

        with self._flight_lock:
            flight = self._flights.get(key)
            if flight is None:
                flight = _Flight(event=Event())
                self._flights[key] = flight
                owner = True
            else:
                owner = False

        if not owner:
            completed = flight.event.wait(self.single_flight_timeout_seconds)
            if completed:
                if flight.error is not None:
                    raise flight.error
                if flight.payload is not None:
                    with self._lock:
                        self._shared += 1
                    return CacheLookup(
                        payload=flight.payload,
                        status="shared",
                        cacheable=flight.cacheable,
                    )
            payload, _ = compute()
            return CacheLookup(payload=payload, status="bypass", cacheable=False)

        try:
            payload, cacheable = compute()
            stored = cacheable and store_result and self.put(key, payload)
            flight.payload = payload
            flight.cacheable = cacheable
            return CacheLookup(
                payload=payload,
                status=(
                    "store"
                    if stored
                    else "miss" if cacheable and not store_result else "bypass"
                ),
                cacheable=cacheable,
            )
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            flight.event.set()
            with self._flight_lock:
                self._flights.pop(key, None)

    def get(self, key: str) -> bytes | None:
        if not self._usable:
            return None
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if entry.expires_at <= now:
                self._remove_locked(key)
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry.payload

    def put(self, key: str, payload: bytes) -> bool:
        if not self._usable:
            return False
        size_bytes = len(key.encode("utf-8")) + len(payload) + _ENTRY_OVERHEAD_BYTES
        if size_bytes > self.max_entry_bytes or size_bytes > self.max_bytes:
            return False

        expires_at = self._clock() + self.ttl_seconds
        with self._lock:
            self._remove_locked(key)
            self._entries[key] = _CacheEntry(
                payload=payload,
                expires_at=expires_at,
                size_bytes=size_bytes,
            )
            self._total_bytes += size_bytes
            self._stores += 1
            while (
                self._total_bytes > self.max_bytes
                or len(self._entries) > self.max_entries
            ):
                oldest_key = next(iter(self._entries))
                self._remove_locked(oldest_key)
                self._evictions += 1
        return True

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._total_bytes = 0

    def stats(self) -> dict[str, int | float | bool]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "entries": len(self._entries),
                "bytes": self._total_bytes,
                "max_bytes": self.max_bytes,
                "max_entry_bytes": self.max_entry_bytes,
                "max_entries": self.max_entries,
                "ttl_seconds": self.ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "stores": self._stores,
                "evictions": self._evictions,
                "shared": self._shared,
            }

    @property
    def _usable(self) -> bool:
        return (
            self.enabled
            and self.max_bytes > 0
            and self.max_entry_bytes > 0
            and self.max_entries > 0
            and self.ttl_seconds > 0
        )

    def _remove_locked(self, key: str) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._total_bytes -= entry.size_bytes


def _environment_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().casefold() not in {"0", "false", "no", "off"}


def _environment_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return max(0, int(value))
    except ValueError:
        return default


def _environment_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        return default
