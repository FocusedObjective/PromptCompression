#!/usr/bin/env python3
"""Render a portable HTML benchmark report, including API costs and exact text diffs."""

from __future__ import annotations

import argparse
import base64
import csv
import difflib
import gzip
import html
import json
import re
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.benchmark_costs import (  # noqa: E402
    api_cost,
    compression_meter,
    load_pricing,
    load_projection_prices,
    paired_costs,
    project_ledger,
)
from scripts.benchmark_scoring import normalize_answer  # noqa: E402
from scripts.benchmark_sources import canonical_hash, sha256, write_json  # noqa: E402
from scripts.codex_benchmark import load_attempts, load_manifest, utc_now  # noqa: E402


@lru_cache(maxsize=64)
def text_diff(before: str, after: str) -> tuple[str, str]:
    """Every original/output character survives in its respective HTML column."""
    if before == after:
        return html.escape(before), html.escape(after)

    def lex(text: str) -> tuple[list[str], list[int]]:
        matches = list(re.finditer(r"\w+|[^\w\s]", text))
        if not matches:
            return ([""] if text else []), ([0, len(text)] if text else [0])
        return [m.group() for m in matches], [
            0,
            *(m.start() for m in matches[1:]),
            len(text),
        ]

    left, left_offsets = lex(before)
    right, right_offsets = lex(after)
    old, new = [], []
    # Keep frequent words and JSON keys eligible for matching. Whitespace stays
    # attached to adjacent tokens so both exact texts remain recoverable.
    for tag, i, j, a, b in difflib.SequenceMatcher(
        None, left, right, autojunk=False
    ).get_opcodes():
        old_text, new_text = (
            html.escape(before[left_offsets[i] : left_offsets[j]]),
            html.escape(after[right_offsets[a] : right_offsets[b]]),
        )
        if tag == "equal":
            old.append(old_text)
            new.append(new_text)
        else:
            if old_text:
                old.append(f'<del class="token drop">{old_text}</del>')
            if new_text:
                new.append(f'<ins class="token added">{new_text}</ins>')
    return "".join(old), "".join(new)


def normalized_em(answer: str, references: list[str]) -> float:
    # Supplementary only. Never replace the official benchmark score.
    return float(
        any(normalize_answer(answer) == normalize_answer(ref) for ref in references)
    )


def read_answer(directory: Path, result: dict[str, Any]) -> str | None:
    if result.get("status") != "ok":
        return None
    path = (directory / result["artifact_dir"] / "answer.txt").resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Answer path escapes the run directory")
    answer = path.read_text(encoding="utf-8")
    from scripts.benchmark_sources import sha256

    if result.get("answer_sha256") and sha256(answer) != result["answer_sha256"]:
        raise ValueError("Saved answer hash mismatch")
    return answer


def pack_bytes_as_text(value: bytes) -> str:
    """Pack 15 bits per safe BMP character for size-limited inline delivery."""
    characters = []
    accumulator = bits = 0
    for byte in value:
        accumulator = (accumulator << 8) | byte
        bits += 8
        if bits >= 15:
            bits -= 15
            characters.append(chr(0x3400 + (accumulator >> bits)))
            accumulator &= (1 << bits) - 1
    if bits:
        characters.append(chr(0x3400 + (accumulator << (15 - bits))))
    return "".join(characters)


def attach_semantic_review(data: dict[str, Any], path: Path) -> None:
    review = json.loads(path.read_text(encoding="utf-8"))
    expected = sum(len(v["calls"]) for c in data["cases"] for v in c["variants"])
    if (review["mapped_results"] != expected or review["expected_results"] != expected
            or review["review_calls"] != review["expected_review_calls"]
            or review["statuses"] != {"ok": review["review_calls"]}
            or set(review["source_manifest_hashes"]) != {r["manifest_sha256"] for r in data["runs"]}):
        raise ValueError("Semantic review must be complete and match the source run")
    mapped = {(r["case_id"], r["condition"], r["repeat"]): r for r in review["results"]}
    if len(mapped) != expected:
        raise ValueError("Duplicate or missing semantic review mappings")
    for case in data["cases"]:
        for variant in case["variants"]:
            for call in variant["calls"]:
                row = mapped[(case["id"], variant["condition"], call["repeat"])]
                if sha256(call["answer"]) != row["answer_sha256"]:
                    raise ValueError("Semantic review answer hash mismatch")
                call["semantic_review"] = {key: row[key] for key in ("answer_id", "verdict", "score", "rationale", "evidence")}
    data["semantic_review"] = {key: value for key, value in review.items() if key != "results"}
    data["semantic_review"]["source_file_sha256"] = sha256(path.read_text(encoding="utf-8"))
    data["generated_at"] = utc_now()


def build_report_data(directories: list[Path], pricing_path: Path, projections_path: Path | None = None) -> dict[str, Any]:
    cases = []
    runs = []
    seen = set()
    models, efforts, encodings = set(), set(), set()
    all_attempts = []
    for directory in directories:
        metadata = json.loads((directory / "run.json").read_text(encoding="utf-8"))
        manifest = load_manifest(directory / "manifest.json")
        settings = metadata["settings"]
        models.add(settings["model"])
        efforts.add(settings["reasoning_effort"])
        encodings.add(manifest["settings"]["encoding"])
        attempts = load_attempts(directory / "attempts.jsonl")
        all_attempts.extend(attempts)
        latest = {attempt["job_id"]: attempt for attempt in attempts}
        runs.append(
            {
                "settings": settings,
                "created_at": metadata["created_at"],
                "preparation": manifest["settings"],
                "manifest_sha256": manifest["manifest_sha256"],
            }
        )
        for record in manifest["cases"]:
            case = record["case"]
            if case["id"] in seen:
                raise ValueError("Duplicate task across report runs")
            seen.add(case["id"])
            item = {
                "id": case["id"],
                "dataset": case["dataset"],
                "kind": "JSON" if case.get("context_format") == "json" else "Prose",
                "question": case.get("question", ""),
                "references": case["answers"],
                "metric": case["metric"],
                "source": case.get("source", {}),
                "original_context_tokens": record["original_context_tokens"],
                "original_prompt_tokens": record["original_prompt_tokens"],
                "original_context_sha256": canonical_hash(case["context"]),
                "variants": [],
            }
            for variant in record["variants"]:
                before_html, after_html = text_diff(case["context"], variant["context"])
                calls = []
                for repeat in range(1, settings["repeats"] + 1):
                    identifier = (
                        f"{record['case_key']}-{variant['condition']}-r{repeat}"
                    )
                    attempt = latest.get(identifier)
                    row = {
                        "repeat": repeat,
                        "status": attempt["status"] if attempt else "pending",
                    }
                    if attempt:
                        answer = read_answer(directory, attempt)
                        row.update(
                            {
                                key: attempt.get(key)
                                for key in (
                                    "score",
                                    "cli_elapsed_ms",
                                    "compression_elapsed_ms",
                                    "estimated_end_to_end_ms",
                                    "usage",
                                )
                            }
                        )
                        row["answer"] = answer
                        row["normalized_exact_match"] = (
                            normalized_em(answer, case["answers"])
                            if answer is not None
                            else None
                        )
                    calls.append(row)
                details = variant["compression"]
                item["variants"].append(
                    {
                        "condition": variant["condition"],
                        "context_tokens": variant["context_tokens"],
                        "prompt_tokens": variant["prompt_tokens"],
                        "compression": details,
                        "meter_saved_tokens": compression_meter(variant),
                        "before_html": before_html,
                        "after_html": after_html,
                        "after_text": variant["context"],
                        "prompt_sha256": variant["prompt_sha256"],
                        "calls": calls,
                    }
                )
            cases.append(item)
    if len(models) != 1 or len(efforts) != 1 or len(encodings) != 1:
        raise ValueError(
            "A report must compare one answer model, effort and proxy encoding"
        )
    model = next(iter(models))
    pricing = load_pricing(pricing_path, model)
    ledger = []
    for item in cases:
        before = next(v for v in item["variants"] if v["condition"] == "unchanged")
        for after in item["variants"]:
            for result in after["calls"]:
                baseline = next(
                    r for r in before["calls"] if r["repeat"] == result["repeat"]
                )
                if result["status"] != "ok" or baseline["status"] != "ok":
                    continue
                cost = paired_costs(
                    before["prompt_tokens"],
                    after["prompt_tokens"],
                    baseline["usage"]["output_tokens"],
                    result["usage"]["output_tokens"],
                    after["meter_saved_tokens"],
                    pricing,
                )
                ledger.append(
                    {
                        "case_id": item["id"],
                        "condition": after["condition"],
                        "repeat": result["repeat"],
                        "before_prompt_tokens": before["prompt_tokens"],
                        "after_prompt_tokens": after["prompt_tokens"],
                        "before_output_tokens": baseline["usage"]["output_tokens"],
                        "after_output_tokens": result["usage"]["output_tokens"],
                        "compression_meter_saved_tokens": after["meter_saved_tokens"],
                        "before_input_usd": cost["before"]["input_total"],
                        "before_output_usd": cost["before"]["output"],
                        "before_total_usd": cost["before"]["total"],
                        "after_input_usd": cost["after"]["input_total"],
                        "after_output_usd": cost["after"]["output"],
                        "after_api_usd": cost["after"]["total"],
                        "compression_usd": cost["compression"],
                        "after_including_compression_usd": cost["after_total"],
                        "net_saving_usd": cost["net_saving"],
                    }
                )
    attempt_costs = []
    for attempt in all_attempts:
        usage = attempt.get("usage") or {}
        if "input_tokens" not in usage or "output_tokens" not in usage:
            continue
        writes = usage.get("cache_write_input_tokens", 0)
        # The CLI does not always expose write lifetime. Use 30m for this diagnostic only.
        cost = api_cost(
            usage["input_tokens"],
            usage["output_tokens"],
            pricing,
            cached_tokens=usage.get("cached_input_tokens", 0),
            write_30m_tokens=writes,
        )
        attempt_costs.append(cost["total"])
    projections = load_projection_prices(projections_path or REPO_ROOT / "data/benchmark_pricing_projections.json")
    profiles = [{**pricing, "label": model.replace("gpt-", "GPT-").replace("-terra", " Terra")}, *projections["models"]]
    return {
        "schema": "compression-html-report.v1",
        "generated_at": utc_now(),
        "model": model,
        "effort": next(iter(efforts)),
        "encoding": next(iter(encodings)),
        "pricing": pricing,
        "runs": runs,
        "cases": cases,
        "cohort": {
            "tasks": len(cases),
            "distinct_source_contexts": len({case["original_context_sha256"] for case in cases}),
            "by_workload": {kind: sum(case["kind"] == kind for case in cases) for kind in sorted({case["kind"] for case in cases})},
        },
        "ledger": ledger,
        "pricing_projections": {**projections, "models": profiles},
        "projection_ledger": project_ledger(ledger, profiles, pricing["compression_saved_tokens_per_million"]),
        "review_notes": [
            note
            for directory in directories
            if (directory / "review-notes.json").exists()
            for note in json.loads((directory / "review-notes.json").read_text(encoding="utf-8"))
        ],
        "experiment": {
            "attempts": len(all_attempts),
            "attempts_without_usage": len(all_attempts) - len(attempt_costs),
            "cli_envelope_api_equivalent_usd": sum(attempt_costs),
            "usage": {
                key: sum((a.get("usage") or {}).get(key, 0) for a in all_attempts)
                for key in (
                    "input_tokens",
                    "cached_input_tokens",
                    "cache_write_input_tokens",
                    "output_tokens",
                    "reasoning_output_tokens",
                )
            },
        },
    }


def render_report(data: dict[str, Any], out: Path) -> None:
    template = (
        Path(__file__).with_name("benchmark_report.html").read_text(encoding="utf-8")
    )
    # The interactive view needs only the annotated original. Keep the full
    # before/after evidence in the adjacent JSON, without embedding unused copies.
    inline_data = {
        **data,
        "cases": [
            {
                **case,
                "variants": [
                    {key: value for key, value in variant.items() if key not in {"after_html", "after_text"}}
                    for variant in case["variants"]
                ],
            }
            for case in data["cases"]
        ],
    }
    payload = (
        json.dumps(inline_data, ensure_ascii=False, allow_nan=False)
        .replace("<", "\\u003c")
        .replace("&", "\\u0026")
    )
    encoding = "json"
    compressed_bytes = 0
    if len(payload) > 250_000:
        compressed = gzip.compress(payload.encode("utf-8"), compresslevel=9, mtime=0)
        compressed_bytes = len(compressed)
        payload = base64.b64encode(compressed).decode("ascii")
        encoding = "gzip-base64"
        if len(payload) > 450_000:
            payload = pack_bytes_as_text(compressed)
            encoding = "gzip-utf15"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(template.replace("__BENCHMARK_DATA__", payload).replace("__BENCHMARK_ENCODING__", encoding).replace("__BENCHMARK_BYTES__", str(compressed_bytes)), encoding="utf-8")
    write_json(out.with_suffix(".json"), data)
    ledger = data["ledger"]
    if ledger:
        with out.with_name(out.stem + "-costs.csv").open(
            "w", encoding="utf-8", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(ledger[0]))
            writer.writeheader()
            writer.writerows(ledger)
    projected = data.get("projection_ledger", [])
    if projected:
        with out.with_name(out.stem + "-model-projections.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(projected[0]))
            writer.writeheader()
            writer.writerows(projected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, action="append", required=True)
    parser.add_argument(
        "--pricing", type=Path, default=REPO_ROOT / "data/benchmark_pricing_terra.json"
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--projection-pricing", type=Path, default=REPO_ROOT / "data/benchmark_pricing_projections.json")
    parser.add_argument("--semantic-review", type=Path)
    args = parser.parse_args()
    data = build_report_data(args.run_dir, args.pricing, args.projection_pricing)
    if args.semantic_review:
        attach_semantic_review(data, args.semantic_review)
    render_report(data, args.out)
    print(
        f"Rendered {len(data['cases'])} tasks / {data['experiment']['attempts']} attempts: {args.out}"
    )


if __name__ == "__main__":
    main()
