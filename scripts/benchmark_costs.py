"""Explicit API-equivalent cost scenarios for subscription-run benchmarks."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


def load_projection_prices(path: Path) -> dict[str, Any]:
    catalog = json.loads(path.read_text(encoding="utf-8"))
    if not catalog.get("as_of") or catalog.get("currency") != "USD":
        raise ValueError("Projection prices need a date and USD currency")
    seen = set()
    if not catalog.get("models"):
        raise ValueError("Projection prices need model profiles")
    for profile in catalog["models"]:
        model = profile.get("model")
        if not model or model in seen or not profile.get("sources"):
            raise ValueError("Projection models must be unique and sourced")
        seen.add(model)
        for key in ("input_per_million", "cached_input_per_million", "output_per_million"):
            value = profile.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"Invalid projection price: {key}")
        for key in ("long_context_threshold", "long_context_input_multiplier", "long_context_output_multiplier"):
            if key in profile and (not isinstance(profile[key], (int, float)) or isinstance(profile[key], bool) or not math.isfinite(profile[key]) or profile[key] <= 0):
                raise ValueError(f"Invalid projection context setting: {key}")
    return catalog


def project_ledger(ledger: list[dict[str, Any]], profiles: list[dict[str, Any]], compression_rate: float) -> list[dict[str, Any]]:
    """Reprice frozen counts only; never invoke a model or change recorded usage."""
    projected = []
    for profile in profiles:
        for row in ledger:
            # No cache writes in this explicitly cold, uncached scenario.
            cold = {**profile, "cache_write_30m_per_million": 0, "cache_write_12h_per_million": 0}
            before = api_cost(row["before_prompt_tokens"], row["before_output_tokens"], cold)
            after = api_cost(row["after_prompt_tokens"], row["after_output_tokens"], cold)
            meter = row["compression_meter_saved_tokens"]
            fee = meter * compression_rate / 1e6 if meter is not None else None
            total = after["total"] + fee if fee is not None else None
            saved = before["total"] - total if total is not None else None
            projected.append({
                "model": profile["model"], "label": profile.get("label", profile["model"]),
                **{key: row[key] for key in ("case_id", "condition", "repeat", "before_prompt_tokens", "after_prompt_tokens", "before_output_tokens", "after_output_tokens", "compression_meter_saved_tokens")},
                "before_input_usd": before["input_total"], "before_output_usd": before["output"], "before_total_usd": before["total"],
                "after_input_usd": after["input_total"], "after_output_usd": after["output"], "after_api_usd": after["total"],
                "compression_usd": fee, "after_including_compression_usd": total, "net_saving_usd": saved,
                "net_saving_fraction": saved / before["total"] if saved is not None and before["total"] else None,
            })
    return projected


def load_pricing(path: Path, model: str) -> dict[str, Any]:
    pricing = json.loads(path.read_text(encoding="utf-8"))
    if pricing.get("model") != model:
        raise ValueError("Pricing model must match the measured benchmark model")
    for key in (
        "input_per_million",
        "cached_input_per_million",
        "output_per_million",
        "cache_write_30m_per_million",
        "cache_write_12h_per_million",
        "compression_saved_tokens_per_million",
    ):
        value = pricing.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"Invalid price: {key}")
    if not pricing.get("sources") or not pricing.get("as_of"):
        raise ValueError("Prices need dated, inspectable sources")
    return pricing


def api_cost(
    input_tokens: float,
    output_tokens: float,
    pricing: dict[str, Any],
    *,
    cached_tokens: float = 0,
    write_30m_tokens: float = 0,
    write_12h_tokens: float = 0,
) -> dict[str, float]:
    counts = (
        input_tokens,
        output_tokens,
        cached_tokens,
        write_30m_tokens,
        write_12h_tokens,
    )
    if any(not math.isfinite(value) or value < 0 for value in counts):
        raise ValueError("Token counts must be finite and nonnegative")
    ordinary = input_tokens - cached_tokens - write_30m_tokens - write_12h_tokens
    if ordinary < -1e-8:
        raise ValueError("Cache reads/writes must be disjoint subsets of total input")
    ordinary = max(0, ordinary)
    long = input_tokens > pricing.get("long_context_threshold", float("inf"))
    in_multiplier = pricing.get("long_context_input_multiplier", 1) if long else 1
    out_multiplier = pricing.get("long_context_output_multiplier", 1) if long else 1
    costs = {
        "ordinary_input": ordinary * pricing["input_per_million"] * in_multiplier / 1e6,
        "cached_input": cached_tokens
        * pricing["cached_input_per_million"]
        * in_multiplier
        / 1e6,
        "cache_write_30m": write_30m_tokens
        * pricing["cache_write_30m_per_million"]
        * in_multiplier
        / 1e6,
        "cache_write_12h": write_12h_tokens
        * pricing["cache_write_12h_per_million"]
        * in_multiplier
        / 1e6,
        "output": output_tokens * pricing["output_per_million"] * out_multiplier / 1e6,
    }
    costs["input_total"] = sum(
        costs[key]
        for key in (
            "ordinary_input",
            "cached_input",
            "cache_write_30m",
            "cache_write_12h",
        )
    )
    costs["total"] = costs["input_total"] + costs["output"]
    return costs


def compression_meter(variant: dict[str, Any]) -> int | None:
    if variant["condition"] in {"unchanged", "json_minified"}:
        return 0
    details = variant["compression"]
    savings = details.get("token_savings") or {}
    if isinstance(savings.get("total_tokens_saved"), int):
        return max(0, savings["total_tokens_saved"])
    original, compressed = (
        details.get("original_tokens"),
        details.get("compressed_tokens"),
    )
    if isinstance(original, int) and isinstance(compressed, int):
        return max(0, original - compressed)
    return None


def paired_costs(
    before_prompt: int,
    after_prompt: int,
    before_output: int,
    after_output: int,
    metered_savings: int | None,
    pricing: dict[str, Any],
    *,
    before_cached_share: float = 0,
    after_cached_share: float = 0,
    write_share: float = 0,
    retention: str = "30m",
    allowance: bool = False,
    reuse_count: int = 1,
) -> dict[str, Any]:
    if any(
        not 0 <= value <= 1
        for value in (before_cached_share, after_cached_share, write_share)
    ):
        raise ValueError("Cache shares must be between zero and one")
    if retention not in {"30m", "12h"} or reuse_count < 1:
        raise ValueError("Invalid retention or compression reuse count")

    def cost(prompt: int, output: int, cached: float) -> dict[str, float]:
        return api_cost(
            prompt,
            output,
            pricing,
            cached_tokens=prompt * cached,
            write_30m_tokens=prompt * write_share if retention == "30m" else 0,
            write_12h_tokens=prompt * write_share if retention == "12h" else 0,
        )

    before = cost(before_prompt, before_output, before_cached_share)
    after = cost(after_prompt, after_output, after_cached_share)
    fee = (
        metered_savings
        * pricing["compression_saved_tokens_per_million"]
        / 1e6
        / reuse_count
        if metered_savings is not None
        else None
    )
    if allowance:
        fee = 0.0
    total = after["total"] + fee if fee is not None else None
    return {
        "before": before,
        "after": after,
        "compression": fee,
        "after_total": total,
        "gross_saving": before["total"] - after["total"],
        "net_saving": before["total"] - total if total is not None else None,
    }
