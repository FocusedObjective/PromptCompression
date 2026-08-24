"""Bounded cache for independently reusable message text compression results."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from typing import Any, Callable

from app.gpu_policy import GPU_COMPRESSION_POLICY
from app.response_cache import LocalResponseCache
from app.tenant_profiles import TenantCompressionProfile


DEFAULT_CONTENT_CACHE_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_CONTENT_CACHE_MAX_ENTRY_BYTES = 256 * 1024
DEFAULT_CONTENT_CACHE_MAX_ENTRIES = 8192
DEFAULT_CONTENT_CACHE_TTL_SECONDS = 300.0
DEFAULT_CONTENT_CACHE_SINGLE_FLIGHT_TIMEOUT_SECONDS = 30.0

_TRANSIENT_WARNING_FRAGMENTS = (
    "cold_model",
    "missing_latency_baseline",
    "timeout",
    "unavailable",
    "fallback",
    "output_rejected_integrity",
)


@dataclass(frozen=True, slots=True)
class CachedTextCompression:
    text: str
    original_tokens: int
    compressed_tokens: int
    changed: bool
    warnings: tuple[str, ...]
    cache_status: str = "bypass"


class ContentCompressionCache:
    """Exact-content cache scoped by every output-affecting behavior setting."""

    def __init__(self, cache: LocalResponseCache) -> None:
        self._cache = cache

    @classmethod
    def from_environment(cls) -> ContentCompressionCache:
        return cls(
            LocalResponseCache(
                enabled=_environment_bool("CONTENT_CACHE_ENABLED", True),
                max_bytes=_environment_int(
                    "CONTENT_CACHE_MAX_BYTES",
                    DEFAULT_CONTENT_CACHE_MAX_BYTES,
                ),
                max_entry_bytes=_environment_int(
                    "CONTENT_CACHE_MAX_ENTRY_BYTES",
                    DEFAULT_CONTENT_CACHE_MAX_ENTRY_BYTES,
                ),
                max_entries=_environment_int(
                    "CONTENT_CACHE_MAX_ENTRIES",
                    DEFAULT_CONTENT_CACHE_MAX_ENTRIES,
                ),
                ttl_seconds=_environment_float(
                    "CONTENT_CACHE_TTL_SECONDS",
                    DEFAULT_CONTENT_CACHE_TTL_SECONDS,
                ),
                single_flight_timeout_seconds=_environment_float(
                    "CONTENT_CACHE_SINGLE_FLIGHT_TIMEOUT_SECONDS",
                    DEFAULT_CONTENT_CACHE_SINGLE_FLIGHT_TIMEOUT_SECONDS,
                ),
            )
        )

    def compress(
        self,
        *,
        text: str,
        role: str,
        model_name: str,
        aggressiveness: float,
        mode: str | None,
        latency_budget_ms: float | None,
        protection_mode: str = "hybrid",
        tenant_profile: TenantCompressionProfile | None,
        compute: Callable[[], CachedTextCompression],
    ) -> CachedTextCompression:
        key = self._key(
            text=text,
            role=role,
            model_name=model_name,
            aggressiveness=aggressiveness,
            mode=mode,
            latency_budget_ms=latency_budget_ms,
            protection_mode=protection_mode,
            tenant_profile=tenant_profile,
        )

        def serialized_compute() -> tuple[bytes, bool]:
            result = compute()
            payload = json.dumps(
                {
                    "text": result.text,
                    "original_tokens": result.original_tokens,
                    "compressed_tokens": result.compressed_tokens,
                    "changed": result.changed,
                    "warnings": list(result.warnings),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            return payload, _is_cacheable(result)

        lookup = self._cache.get_or_compute(key, serialized_compute)
        data = json.loads(lookup.payload.decode("utf-8"))
        return CachedTextCompression(
            text=str(data["text"]),
            original_tokens=int(data["original_tokens"]),
            compressed_tokens=int(data["compressed_tokens"]),
            changed=bool(data["changed"]),
            warnings=tuple(str(warning) for warning in data.get("warnings", [])),
            cache_status=lookup.status,
        )

    def clear(self) -> None:
        self._cache.clear()

    def stats(self) -> dict[str, int | float | bool]:
        return self._cache.stats()

    @staticmethod
    def _key(
        *,
        text: str,
        role: str,
        model_name: str,
        aggressiveness: float,
        mode: str | None,
        latency_budget_ms: float | None,
        protection_mode: str = "hybrid",
        tenant_profile: TenantCompressionProfile | None,
    ) -> str:
        identity: dict[str, Any] = {
            "schema": "message-content-cache-v1",
            "compression_policy": GPU_COMPRESSION_POLICY.schema_version,
            "behavior_version": os.getenv(
                "COMPRESSOR_BEHAVIOR_VERSION",
                GPU_COMPRESSION_POLICY.schema_version,
            ),
            "source_sha256": os.getenv("COMPRESSOR_SOURCE_SHA256", "unknown"),
            "model": model_name,
            "role": role,
            "aggressiveness": aggressiveness,
            "mode": mode,
            "latency_budget_ms": latency_budget_ms,
            "protection_mode": protection_mode,
            "tenant_profile": (
                None if tenant_profile is None else asdict(tenant_profile)
            ),
            "content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        canonical = json.dumps(
            identity,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


def _is_cacheable(result: CachedTextCompression) -> bool:
    return result.changed and not any(
        fragment in warning.casefold()
        for warning in result.warnings
        for fragment in _TRANSIENT_WARNING_FRAGMENTS
    )


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
