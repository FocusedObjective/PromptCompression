#!/usr/bin/env python3
"""Compare benchmark JSONL outputs without retaining prompt or response text."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare one baseline raw benchmark JSONL file with one or more "
            "candidate files. Candidates use LABEL=PATH syntax."
        )
    )
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        action="append",
        default=[],
        metavar="LABEL=PATH",
        help="Candidate label and JSONL path. Repeat for multiple candidates.",
    )
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args()


def load_records(path: Path) -> dict[tuple[str, int, str], dict[str, Any]]:
    records: dict[tuple[str, int, str], dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            item = json.loads(line)
            key = (
                str(item["case_id"]),
                int(item["repeat"]),
                str(item.get("condition_id", "")),
            )
            if key in records:
                raise ValueError(f"{path}:{line_number}: duplicate record key {key}")
            final_text = str(item.get("final_text", ""))
            analytics = item.get("analytics") or {}
            records[key] = {
                "target_tokens": int(item.get("target_tokens", 0)),
                "prompt_sha256": str(item.get("prompt_sha256", "")),
                "final_sha256": hashlib.sha256(final_text.encode("utf-8")).hexdigest(),
                "compressed_tokens": int(
                    item.get("response_compressed_tokens", item.get("compressed_tokens", 0))
                ),
                "reduction": float(item.get("reduction", 0.0)),
                "status": str(item.get("status", "")),
                "fallback_reason": item.get("fallback_reason"),
                "rollback_reason": item.get(
                    "rollback_reason",
                    analytics.get(
                        "outputRollbackReason",
                        analytics.get("output_rollback_reason"),
                    ),
                ),
            }
    return records


def summarize(
    baseline: dict[tuple[str, int, str], dict[str, Any]],
    candidate: dict[tuple[str, int, str], dict[str, Any]],
) -> dict[str, Any]:
    common_keys = sorted(set(baseline) & set(candidate))
    buckets: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "matched_records": 0,
            "exact_output_matches": 0,
            "output_differences": 0,
            "prompt_hash_mismatches": 0,
            "status_mismatches": 0,
            "fallback_mismatches": 0,
            "rollback_mismatches": 0,
            "token_deltas": [],
            "reduction_deltas": [],
        }
    )

    for key in common_keys:
        left = baseline[key]
        right = candidate[key]
        bucket = buckets[int(left["target_tokens"])]
        bucket["matched_records"] += 1
        if left["final_sha256"] == right["final_sha256"]:
            bucket["exact_output_matches"] += 1
        else:
            bucket["output_differences"] += 1
        if left["prompt_sha256"] != right["prompt_sha256"]:
            bucket["prompt_hash_mismatches"] += 1
        if left["status"] != right["status"]:
            bucket["status_mismatches"] += 1
        if left["fallback_reason"] != right["fallback_reason"]:
            bucket["fallback_mismatches"] += 1
        if left["rollback_reason"] != right["rollback_reason"]:
            bucket["rollback_mismatches"] += 1
        bucket["token_deltas"].append(
            right["compressed_tokens"] - left["compressed_tokens"]
        )
        bucket["reduction_deltas"].append(right["reduction"] - left["reduction"])

    by_target: dict[str, Any] = {}
    totals = {
        "matched_records": 0,
        "exact_output_matches": 0,
        "output_differences": 0,
        "prompt_hash_mismatches": 0,
        "status_mismatches": 0,
        "fallback_mismatches": 0,
        "rollback_mismatches": 0,
    }
    for target, bucket in sorted(buckets.items()):
        token_deltas = bucket.pop("token_deltas")
        reduction_deltas = bucket.pop("reduction_deltas")
        row = {
            **bucket,
            "compressed_token_delta": {
                "min": min(token_deltas),
                "median": statistics.median(token_deltas),
                "mean": statistics.fmean(token_deltas),
                "max": max(token_deltas),
            },
            "reduction_delta": {
                "min": min(reduction_deltas),
                "median": statistics.median(reduction_deltas),
                "mean": statistics.fmean(reduction_deltas),
                "max": max(reduction_deltas),
            },
        }
        by_target[str(target)] = row
        for field in totals:
            totals[field] += int(row[field])

    return {
        "baseline_records": len(baseline),
        "candidate_records": len(candidate),
        "matched_records": len(common_keys),
        "missing_from_candidate": len(set(baseline) - set(candidate)),
        "extra_in_candidate": len(set(candidate) - set(baseline)),
        "totals": totals,
        "by_target_tokens": by_target,
    }


def main() -> int:
    args = parse_args()
    if not args.candidate:
        raise SystemExit("At least one --candidate LABEL=PATH is required.")

    baseline = load_records(args.baseline)
    candidates: dict[str, Any] = {}
    for value in args.candidate:
        label, separator, raw_path = value.partition("=")
        if not separator or not label or not raw_path:
            raise SystemExit(f"Invalid --candidate value: {value!r}")
        if label in candidates:
            raise SystemExit(f"Duplicate candidate label: {label}")
        candidates[label] = summarize(baseline, load_records(Path(raw_path)))

    result = {
        "schema_version": 1,
        "baseline": str(args.baseline),
        "candidates": candidates,
        "notes": [
            "Output equality is SHA-256 equality of final_text.",
            "No prompt or response text is written to this summary.",
            "Record identity is case_id + repeat + condition_id.",
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
