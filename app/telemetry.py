"""Content-free operational telemetry for compression rollout decisions."""

from __future__ import annotations

from collections import Counter
import json
import logging
from threading import Lock
from typing import Any


LOGGER = logging.getLogger("promptcompression.telemetry")


class CompressionTelemetry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._requests = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._saved_tokens = 0
        self._elapsed_ms = 0.0
        self._routes: Counter[str] = Counter()
        self._modes: Counter[str] = Counter()
        self._cache: Counter[str] = Counter()
        self._warning_classes: Counter[str] = Counter()
        self._fail_open = 0
        self._content_cache_hits = 0
        self._content_cache_misses = 0
        self._content_cache_stores = 0
        self._tool_actions: Counter[str] = Counter()

    def record(
        self,
        *,
        route: str,
        mode: str,
        cache_status: str,
        input_tokens: int,
        output_tokens: int,
        elapsed_ms: float,
        warnings: list[str],
        fail_open_used: bool = False,
        content_cache_hits: int = 0,
        content_cache_misses: int = 0,
        content_cache_stores: int = 0,
        tool_actions: list[str] | None = None,
    ) -> None:
        saved_tokens = max(0, input_tokens - output_tokens)
        warning_classes = sorted({_warning_class(warning) for warning in warnings})
        resolved_tool_actions = [action for action in tool_actions or [] if action]
        event = {
            "event": "prompt_compression_request",
            "route": route,
            "mode": mode,
            "cache_status": cache_status,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "saved_tokens": saved_tokens,
            "elapsed_ms": round(max(0.0, elapsed_ms), 3),
            "warning_classes": warning_classes,
            "fail_open_used": fail_open_used,
            "content_cache_hits": content_cache_hits,
            "content_cache_misses": content_cache_misses,
            "content_cache_stores": content_cache_stores,
            "tool_actions": resolved_tool_actions,
        }
        LOGGER.info(json.dumps(event, separators=(",", ":"), sort_keys=True))

        with self._lock:
            self._requests += 1
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens
            self._saved_tokens += saved_tokens
            self._elapsed_ms += max(0.0, elapsed_ms)
            self._routes[route] += 1
            self._modes[mode] += 1
            self._cache[cache_status] += 1
            self._warning_classes.update(warning_classes)
            self._fail_open += int(fail_open_used)
            self._content_cache_hits += content_cache_hits
            self._content_cache_misses += content_cache_misses
            self._content_cache_stores += content_cache_stores
            self._tool_actions.update(resolved_tool_actions)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "requests": self._requests,
                "input_tokens": self._input_tokens,
                "output_tokens": self._output_tokens,
                "saved_tokens": self._saved_tokens,
                "average_elapsed_ms": (
                    0.0 if self._requests == 0 else self._elapsed_ms / self._requests
                ),
                "routes": dict(self._routes),
                "modes": dict(self._modes),
                "cache": dict(self._cache),
                "warning_classes": dict(self._warning_classes),
                "fail_open": self._fail_open,
                "content_cache_hits": self._content_cache_hits,
                "content_cache_misses": self._content_cache_misses,
                "content_cache_stores": self._content_cache_stores,
                "tool_actions": dict(self._tool_actions),
            }

    def clear(self) -> None:
        with self._lock:
            self._requests = 0
            self._input_tokens = 0
            self._output_tokens = 0
            self._saved_tokens = 0
            self._elapsed_ms = 0.0
            self._routes.clear()
            self._modes.clear()
            self._cache.clear()
            self._warning_classes.clear()
            self._fail_open = 0
            self._content_cache_hits = 0
            self._content_cache_misses = 0
            self._content_cache_stores = 0
            self._tool_actions.clear()


def _warning_class(warning: str) -> str:
    lowered = warning.casefold()
    if "output_rejected_integrity" in lowered or "rollback" in lowered:
        return "rollback"
    if "fail_open" in lowered:
        return "fail_open"
    if "timeout" in lowered:
        return "timeout"
    if "unavailable" in lowered or "fallback" in lowered:
        return "fallback"
    if "tool_result_shadow" in lowered:
        return "tool_shadow"
    if "llmlingua_skipped" in lowered:
        return "model_gate_skip"
    return "other"
