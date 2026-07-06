from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.compressor import PromptCompressionService  # noqa: E402
from app.token_estimator import estimate_token_count  # noqa: E402


DEFAULT_TARGET_TOKENS = (
    256,
    512,
    1_000,
    1_500,
    2_000,
    2_500,
    3_000,
    6_000,
    12_000,
    24_000,
    50_000,
    100_000,
    200_000,
)
DEFAULT_JSON_RATIOS = (0.0, 0.1, 0.25, 0.5, 0.75)
DEFAULT_AGGRESSIVENESS = 0.25
DEFAULT_TIMEOUT_SECONDS = 900.0
TIMING_FIELDS = (
    "total_ms",
    "target_rate_ms",
    "preprocessing_ms",
    "force_drop_ms",
    "segment_selection_ms",
    "model_load_ms",
    "model_input_ms",
    "force_tokens_ms",
    "llmlingua_ms",
    "placeholder_validation_ms",
    "model_expand_ms",
    "uncompressed_expand_ms",
    "token_estimate_ms",
    "model_gate_ms",
    "diagnostics_ms",
    "other_ms",
)
LATENCY_FIELDS = (
    "client_wall_ms",
    "server_elapsed_ms",
    "timing_total_ms",
    "timing_preprocessing_ms",
    "timing_segment_selection_ms",
    "timing_model_gate_ms",
    "timing_model_load_ms",
    "timing_llmlingua_ms",
    "timing_diagnostics_ms",
    "timing_token_estimate_ms",
)
MEAN_FIELDS = (
    "synthetic_input_tokens",
    "synthetic_json_tokens",
    "response_original_tokens",
    "response_compressed_tokens",
    "response_tokens_saved",
    "reduction",
    "input_chars",
    "output_chars",
    "model_input_chars",
    "model_chunk_count",
    "llmlingua_call_count",
    "skipped_model_chunk_count",
    "chunk_placeholder_max",
    "chunk_placeholder_avg",
    "chunk_chars_max",
    "deterministic_tokens_saved",
    "deterministic_reduction",
    "whitespace_tokens_saved",
    "toon_tokens_saved",
    "json_minify_tokens_saved",
    "nocompress_wrapper_tokens_saved",
    "literal_placeholder_count",
    "literal_placeholder_tokens_saved",
    "duplicate_block_candidate_count",
    "duplicate_block_candidate_tokens",
    "model_incremental_tokens_saved",
    "model_incremental_reduction",
    "model_expected_incremental_savings_tokens",
    "model_expected_incremental_reduction",
    "model_projected_latency_ms",
    "model_candidate_tokens",
)
SUM_FIELDS = (
    "model_chunk_count",
    "llmlingua_call_count",
    "skipped_model_chunk_count",
)


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    target_tokens: int
    json_ratio_target: float
    synthetic_input_tokens: int
    synthetic_json_tokens: int
    input_chars: int
    json_chars: int
    prompt_sha256: str
    text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run prompt-compression latency benchmarks against HTTP or in-process."
    )
    parser.add_argument(
        "--url",
        default=os.getenv("API_URL", "http://127.0.0.1:8000/compress"),
        help="HTTP endpoint for /compress. Ignored with --in-process.",
    )
    parser.add_argument(
        "--in-process",
        action="store_true",
        help="Call PromptCompressionService directly instead of HTTP.",
    )
    parser.add_argument(
        "--out-dir",
        default=None,
        help="Output directory. Defaults to data/benchmarks/<timestamp>.",
    )
    parser.add_argument(
        "--sizes",
        default=",".join(str(value) for value in DEFAULT_TARGET_TOKENS),
        help="Comma-separated target token sizes.",
    )
    parser.add_argument(
        "--json-ratios",
        default=",".join(str(value) for value in DEFAULT_JSON_RATIOS),
        help="Comma-separated JSON token-share targets from 0.0 to 1.0.",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="Measured repeats per size/json-ratio case.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Warmup requests to run before measured requests.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Parallel measured requests for HTTP mode.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-request timeout in seconds for HTTP mode.",
    )
    parser.add_argument(
        "--aggressiveness",
        type=float,
        default=DEFAULT_AGGRESSIVENESS,
        help="Compression aggressiveness sent with each request.",
    )
    parser.add_argument(
        "--compression-mode",
        choices=("deterministic", "model_auto", "model_force"),
        default="model_force",
        help="Compression mode sent with each request.",
    )
    parser.add_argument(
        "--latency-budget-ms",
        type=float,
        default=None,
        help="Optional latency budget sent with model_auto requests.",
    )
    parser.add_argument(
        "--include-sections",
        action="store_true",
        help="Request section/word-label output. Leave off for production latency.",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Shuffle measured request order after prompt generation.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=1729,
        help="Deterministic seed for shuffle and prompt variation.",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="Extra HTTP header as 'Name: value'. May be repeated.",
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="Run label as 'key=value'. May be repeated.",
    )
    parser.add_argument(
        "--save-prompts",
        action="store_true",
        help="Write generated prompt text to prompts.jsonl.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_tokens = parse_int_list(args.sizes)
    json_ratios = parse_float_list(args.json_ratios)
    validate_args(args, target_tokens, json_ratios)

    output_dir = resolve_output_dir(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    labels = parse_labels(args.label)
    metadata = {
        "started_at": datetime.now(UTC).isoformat(),
        "mode": "in_process" if args.in_process else "http",
        "url": None if args.in_process else args.url,
        "target_tokens": target_tokens,
        "target_token_median": statistics.median(target_tokens),
        "json_ratios": json_ratios,
        "repeats": args.repeats,
        "warmup": args.warmup,
        "concurrency": args.concurrency,
        "aggressiveness": args.aggressiveness,
        "compression_mode": args.compression_mode,
        "latency_budget_ms": args.latency_budget_ms,
        "include_sections": args.include_sections,
        "seed": args.seed,
        "labels": labels,
    }

    print(f"Generating {len(target_tokens) * len(json_ratios)} prompt cases...")
    cases = build_cases(target_tokens, json_ratios)
    write_case_manifest(output_dir / "cases.json", cases, metadata)
    if args.save_prompts:
        write_prompts(output_dir / "prompts.jsonl", cases)

    headers = parse_headers(args.header)
    service = PromptCompressionService() if args.in_process else None
    warmup_case = next(
        case
        for case in cases
        if case.target_tokens == min(target_tokens)
        and case.json_ratio_target == min(json_ratios)
    )
    for index in range(args.warmup):
        print(f"Warmup {index + 1}/{args.warmup}...")
        run_one(
            warmup_case,
            repeat_index=-(index + 1),
            args=args,
            labels=labels,
            headers=headers,
            service=service,
            measured=False,
        )

    tasks = [
        (case, repeat_index)
        for case in cases
        for repeat_index in range(1, args.repeats + 1)
    ]
    if args.shuffle:
        random.Random(args.seed).shuffle(tasks)

    print(f"Running {len(tasks)} measured requests...")
    rows = run_measured_tasks(
        tasks,
        args=args,
        labels=labels,
        headers=headers,
        service=service,
    )
    metadata["finished_at"] = datetime.now(UTC).isoformat()
    metadata["success_count"] = sum(1 for row in rows if row["status"] == "ok")
    metadata["error_count"] = len(rows) - metadata["success_count"]

    write_jsonl(output_dir / "raw.jsonl", rows)
    write_csv(output_dir / "raw.csv", rows)
    summary_rows = build_summary_rows(rows)
    write_csv(output_dir / "summary.csv", summary_rows)
    write_summary_json(output_dir / "summary.json", metadata, summary_rows)
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    print(f"Wrote benchmark results to {output_dir}")
    print(
        "Success: "
        f"{metadata['success_count']}/{len(rows)}; "
        f"errors: {metadata['error_count']}"
    )
    return 0 if metadata["error_count"] == 0 else 1


def validate_args(
    args: argparse.Namespace,
    target_tokens: list[int],
    json_ratios: list[float],
) -> None:
    if not target_tokens:
        raise SystemExit("--sizes must include at least one value")
    if not json_ratios:
        raise SystemExit("--json-ratios must include at least one value")
    if args.repeats < 1:
        raise SystemExit("--repeats must be at least 1")
    if args.warmup < 0:
        raise SystemExit("--warmup must be at least 0")
    if args.concurrency < 1:
        raise SystemExit("--concurrency must be at least 1")
    if args.in_process and args.concurrency != 1:
        raise SystemExit("--in-process only supports --concurrency 1")
    for value in json_ratios:
        if value < 0.0 or value > 1.0:
            raise SystemExit("--json-ratios values must be between 0.0 and 1.0")


def build_cases(
    target_tokens: list[int],
    json_ratios: list[float],
) -> list[BenchmarkCase]:
    return [
        build_case(target, json_ratio)
        for target in target_tokens
        for json_ratio in json_ratios
    ]


def build_case(target_tokens: int, json_ratio: float) -> BenchmarkCase:
    json_token_budget = int(target_tokens * json_ratio)
    prose_token_budget = max(1, target_tokens - json_token_budget)
    prose = build_prose(prose_token_budget)
    json_block = build_json_block(json_token_budget)
    prompt = "\n\n".join(
        part
        for part in (
            "You are a support operations analyst preparing an escalation brief.",
            "Preserve customer IDs, URLs, dates, retry limits, and hard constraints.",
            "Summarize risk, identify likely blockers, and propose next actions.",
            prose,
            json_block,
            "Output: executive summary, blockers and owner, next three actions.",
        )
        if part
    )
    synthetic_input_tokens = estimate_token_count(prompt)
    synthetic_json_tokens = estimate_token_count(json_block) if json_block else 0
    case_id = f"tok{target_tokens}_json{format_ratio(json_ratio)}"
    return BenchmarkCase(
        case_id=case_id,
        target_tokens=target_tokens,
        json_ratio_target=json_ratio,
        synthetic_input_tokens=synthetic_input_tokens,
        synthetic_json_tokens=synthetic_json_tokens,
        input_chars=len(prompt),
        json_chars=len(json_block),
        prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        text=prompt,
    )


def build_prose(token_budget: int) -> str:
    unit = (
        "Incident INC-{index:06d} shows queue latency, retry pressure, "
        "payment authorization drift, account owner follow-up, contract deadline "
        "2026-07-15, dashboard https://example.com/run/{index:06d}, and support "
        "notes requiring concise executive summary with exact identifiers. "
    )
    sample = unit.format(index=1)
    unit_tokens = max(1, estimate_token_count(sample))
    count = max(1, math.ceil(token_budget / unit_tokens))
    return "".join(unit.format(index=index) for index in range(count))


def build_json_block(token_budget: int) -> str:
    if token_budget <= 0:
        return ""

    base_tokens = estimate_token_count(json_payload(0))
    sample_count = 10
    sample_tokens = estimate_token_count(json_payload(sample_count))
    record_tokens = max(1.0, (sample_tokens - base_tokens) / sample_count)
    record_count = max(1, math.ceil(max(1, token_budget - base_tokens) / record_tokens))
    payload = json_payload(record_count)
    return "Customer telemetry JSON:\n" + payload


def json_payload(record_count: int) -> str:
    records = [
        {
            "account_id": f"acct_{index:08d}",
            "incident_id": f"INC-{index:06d}",
            "region": ["us-central1", "us-east1", "us-west1"][index % 3],
            "severity": ["low", "medium", "high", "critical"][index % 4],
            "status": ["open", "monitoring", "resolved"][index % 3],
            "retry_limit": 3 + (index % 2),
            "p95_latency_ms": 250 + (index % 700),
            "dashboard_url": f"https://example.com/dashboards/{index:06d}",
            "note": (
                "Synthetic benchmark record used to stress JSON preprocessing "
                "and TOON conversion paths."
            ),
        }
        for index in range(record_count)
    ]
    payload = {
        "schema_version": "benchmark.v1",
        "generated_for": "prompt-compression-performance",
        "records": records,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def run_measured_tasks(
    tasks: list[tuple[BenchmarkCase, int]],
    *,
    args: argparse.Namespace,
    labels: dict[str, str],
    headers: dict[str, str],
    service: PromptCompressionService | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if args.concurrency == 1:
        for index, (case, repeat_index) in enumerate(tasks, start=1):
            print(f"{index}/{len(tasks)} {case.case_id} repeat={repeat_index}")
            rows.append(
                run_one(
                    case,
                    repeat_index=repeat_index,
                    args=args,
                    labels=labels,
                    headers=headers,
                    service=service,
                    measured=True,
                )
            )
        return rows

    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(
                run_one,
                case,
                repeat_index,
                args,
                labels,
                headers,
                None,
                True,
            )
            for case, repeat_index in tasks
        ]
        for index, future in enumerate(as_completed(futures), start=1):
            row = future.result()
            print(f"{index}/{len(tasks)} {row['case_id']} repeat={row['repeat']}")
            rows.append(row)
    return rows


def run_one(
    case: BenchmarkCase,
    repeat_index: int,
    args: argparse.Namespace,
    labels: dict[str, str],
    headers: dict[str, str],
    service: PromptCompressionService | None,
    measured: bool,
) -> dict[str, Any]:
    base_row = base_result_row(case, repeat_index, labels, measured)
    if args.in_process:
        assert service is not None
        return run_one_in_process(case, base_row, args, service)
    return run_one_http(case, base_row, args, headers)


def run_one_http(
    case: BenchmarkCase,
    base_row: dict[str, Any],
    args: argparse.Namespace,
    headers: dict[str, str],
) -> dict[str, Any]:
    payload = {
        "text": case.text,
        "aggressiveness": args.aggressiveness,
        "mode": args.compression_mode,
        "include_sections": args.include_sections,
        "include_diagnostics": True,
    }
    if args.latency_budget_ms is not None:
        payload["latency_budget_ms"] = args.latency_budget_ms
    started = time.perf_counter()
    try:
        response = requests.post(
            args.url,
            json=payload,
            headers=headers,
            timeout=args.timeout,
        )
        client_wall_ms = (time.perf_counter() - started) * 1000
        try:
            body = response.json()
        except ValueError:
            body = {"detail": response.text[:1000]}
        row = {
            **base_row,
            "http_status": response.status_code,
            "client_wall_ms": client_wall_ms,
        }
        if not response.ok:
            row.update(
                {
                    "status": "error",
                    "error": body.get("detail", response.reason),
                }
            )
            return row
        return add_response_fields(row, body)
    except Exception as exc:
        return {
            **base_row,
            "status": "error",
            "error": str(exc),
            "client_wall_ms": (time.perf_counter() - started) * 1000,
        }


def run_one_in_process(
    case: BenchmarkCase,
    base_row: dict[str, Any],
    args: argparse.Namespace,
    service: PromptCompressionService,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        result = service.compress(
            case.text,
            aggressiveness=args.aggressiveness,
            include_sections=args.include_sections,
            mode=args.compression_mode,
            latency_budget_ms=args.latency_budget_ms,
        )
        client_wall_ms = (time.perf_counter() - started) * 1000
        body = {
            "compressed_text": result.compressed_text,
            "original_tokens": result.original_tokens,
            "compressed_tokens": result.compressed_tokens,
            "reduction": result.reduction,
            "target_rate": result.target_rate,
            "model": result.model,
            "token_estimator": result.token_estimator,
            "compression_mode": result.compression_mode,
            "compression_path": result.compression_path,
            "elapsed_ms": result.elapsed_ms,
            "diagnostics": (
                asdict(result.diagnostics)
                if result.diagnostics is not None
                else None
            ),
        }
        return add_response_fields(
            {
                **base_row,
                "http_status": "",
                "client_wall_ms": client_wall_ms,
            },
            body,
        )
    except Exception as exc:
        return {
            **base_row,
            "status": "error",
            "error": str(exc),
            "client_wall_ms": (time.perf_counter() - started) * 1000,
        }


def base_result_row(
    case: BenchmarkCase,
    repeat_index: int,
    labels: dict[str, str],
    measured: bool,
) -> dict[str, Any]:
    row = {
        "status": "started",
        "error": "",
        "measured": measured,
        "case_id": case.case_id,
        "repeat": repeat_index,
        "target_tokens": case.target_tokens,
        "json_ratio_target": case.json_ratio_target,
        "synthetic_input_tokens": case.synthetic_input_tokens,
        "synthetic_json_tokens": case.synthetic_json_tokens,
        "synthetic_json_ratio": (
            0.0
            if case.synthetic_input_tokens == 0
            else case.synthetic_json_tokens / case.synthetic_input_tokens
        ),
        "input_chars": case.input_chars,
        "json_chars": case.json_chars,
        "prompt_sha256": case.prompt_sha256,
    }
    for key, value in labels.items():
        row[f"label_{key}"] = value
    return row


def add_response_fields(row: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    diagnostics = body.get("diagnostics") or {}
    timings = diagnostics.get("timings") or {}
    response_original_tokens = body.get("original_tokens")
    response_compressed_tokens = body.get("compressed_tokens")
    deterministic_output_tokens = diagnostics.get("deterministic_output_tokens")
    model_incremental_tokens_saved = diagnostics.get(
        "model_incremental_tokens_saved"
    )
    if model_incremental_tokens_saved is None:
        model_incremental_tokens_saved = tokens_saved(
            deterministic_output_tokens,
            response_compressed_tokens,
        )
    model_incremental_reduction = diagnostics.get("model_incremental_reduction")
    if model_incremental_reduction is None:
        model_incremental_reduction = reduction_between(
            deterministic_output_tokens,
            response_compressed_tokens,
        )
    row.update(
        {
            "status": "ok",
            "error": "",
            "server_elapsed_ms": body.get("elapsed_ms"),
            "response_original_tokens": response_original_tokens,
            "response_compressed_tokens": response_compressed_tokens,
            "response_tokens_saved": tokens_saved(
                response_original_tokens,
                response_compressed_tokens,
            ),
            "reduction": body.get("reduction"),
            "target_rate": body.get("target_rate"),
            "model": body.get("model", ""),
            "token_estimator": body.get("token_estimator", ""),
            "compression_mode": body.get(
                "compression_mode",
                diagnostics.get("compression_mode", ""),
            ),
            "compression_path": body.get(
                "compression_path",
                diagnostics.get("compression_path", ""),
            ),
            "output_chars": len(body.get("compressed_text", "")),
            "diagnostics_present": bool(diagnostics),
            "segment_count": diagnostics.get("segment_count"),
            "compressible_segment_count": diagnostics.get("compressible_segment_count"),
            "model_segment_count": diagnostics.get("model_segment_count"),
            "skipped_segment_count": diagnostics.get("skipped_segment_count"),
            "placeholder_count": diagnostics.get("placeholder_count"),
            "model_input_chars": diagnostics.get("model_input_chars"),
            "llmlingua_called": diagnostics.get("llmlingua_called"),
            "model_chunk_count": diagnostics.get("model_chunk_count"),
            "llmlingua_call_count": diagnostics.get("llmlingua_call_count"),
            "skipped_model_chunk_count": diagnostics.get("skipped_model_chunk_count"),
            "chunk_placeholder_max": diagnostics.get("chunk_placeholder_max"),
            "chunk_placeholder_avg": diagnostics.get("chunk_placeholder_avg"),
            "chunk_chars_max": diagnostics.get("chunk_chars_max"),
            "fallback_used": diagnostics.get("fallback_used"),
            "fallback_reason": diagnostics.get("fallback_reason", ""),
            "deterministic_tokens_saved": diagnostics.get(
                "deterministic_tokens_saved"
            ),
            "deterministic_reduction": diagnostics.get("deterministic_reduction"),
            "whitespace_tokens_saved": diagnostics.get("whitespace_tokens_saved"),
            "toon_tokens_saved": diagnostics.get("toon_tokens_saved"),
            "json_minify_tokens_saved": diagnostics.get("json_minify_tokens_saved"),
            "nocompress_wrapper_tokens_saved": diagnostics.get(
                "nocompress_wrapper_tokens_saved"
            ),
            "literal_placeholder_count": diagnostics.get(
                "literal_placeholder_count"
            ),
            "literal_placeholder_tokens_saved": diagnostics.get(
                "literal_placeholder_tokens_saved"
            ),
            "duplicate_block_candidate_count": diagnostics.get(
                "duplicate_block_candidate_count"
            ),
            "duplicate_block_candidate_tokens": diagnostics.get(
                "duplicate_block_candidate_tokens"
            ),
            "model_incremental_tokens_saved": model_incremental_tokens_saved,
            "model_incremental_reduction": model_incremental_reduction,
            "model_gate_decision": diagnostics.get("model_gate_decision", ""),
            "model_gate_reason": diagnostics.get("model_gate_reason", ""),
            "model_expected_incremental_savings_tokens": diagnostics.get(
                "model_expected_incremental_savings_tokens"
            ),
            "model_expected_incremental_reduction": diagnostics.get(
                "model_expected_incremental_reduction"
            ),
            "model_projected_latency_ms": diagnostics.get(
                "model_projected_latency_ms"
            ),
            "model_candidate_tokens": diagnostics.get("model_candidate_tokens"),
            "segment_kinds_json": json.dumps(
                diagnostics.get("segment_kinds", {}),
                sort_keys=True,
            ),
        }
    )
    for field in TIMING_FIELDS:
        row[f"timing_{field}"] = timings.get(field)
    return row


def tokens_saved(original: Any, compressed: Any) -> int | None:
    if original is None or compressed is None:
        return None
    return max(0, int(original) - int(compressed))


def reduction_between(original: Any, compressed: Any) -> float | None:
    if original is None or compressed is None:
        return None
    original_value = int(original)
    if original_value <= 0:
        return 0.0
    return max(0.0, 1.0 - (int(compressed) / original_value))


def build_summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary_rows = [summary_for_group("overall", {}, rows)]
    summary_rows.extend(grouped_summaries("target_tokens", ("target_tokens",), rows))
    summary_rows.extend(grouped_summaries("json_ratio", ("json_ratio_target",), rows))
    summary_rows.extend(
        grouped_summaries(
            "target_tokens_json_ratio",
            ("target_tokens", "json_ratio_target"),
            rows,
        )
    )
    return summary_rows


def grouped_summaries(
    group_type: str,
    keys: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        key = tuple(row.get(item) for item in keys)
        groups.setdefault(key, []).append(row)
    return [
        summary_for_group(
            group_type,
            dict(zip(keys, key, strict=True)),
            grouped_rows,
        )
        for key, grouped_rows in sorted(groups.items(), key=lambda item: item[0])
    ]


def summary_for_group(
    group_type: str,
    key_values: dict[str, Any],
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    success_rows = [row for row in rows if row.get("status") == "ok"]
    result: dict[str, Any] = {
        "group_type": group_type,
        "target_tokens": key_values.get("target_tokens", ""),
        "json_ratio_target": key_values.get("json_ratio_target", ""),
        "count": len(rows),
        "success_count": len(success_rows),
        "error_count": len(rows) - len(success_rows),
    }
    for field in LATENCY_FIELDS:
        add_distribution_stats(result, field, values_for(success_rows, field))
    for field in MEAN_FIELDS:
        values = values_for(success_rows, field)
        result[f"{field}_mean"] = statistics.fmean(values) if values else ""
    for field in SUM_FIELDS:
        values = values_for(success_rows, field)
        result[f"{field}_sum"] = sum(values) if values else ""
    return result


def add_distribution_stats(
    row: dict[str, Any],
    field: str,
    values: list[float],
) -> None:
    if not values:
        for suffix in ("min", "p50", "p90", "p95", "max", "mean"):
            row[f"{field}_{suffix}"] = ""
        return

    sorted_values = sorted(values)
    row[f"{field}_min"] = sorted_values[0]
    row[f"{field}_p50"] = percentile(sorted_values, 0.50)
    row[f"{field}_p90"] = percentile(sorted_values, 0.90)
    row[f"{field}_p95"] = percentile(sorted_values, 0.95)
    row[f"{field}_max"] = sorted_values[-1]
    row[f"{field}_mean"] = statistics.fmean(sorted_values)


def percentile(sorted_values: list[float], quantile: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def values_for(rows: list[dict[str, Any]], field: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row.get(field)
        if isinstance(value, bool) or value in (None, ""):
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue
    return values


def write_case_manifest(
    path: Path,
    cases: list[BenchmarkCase],
    metadata: dict[str, Any],
) -> None:
    payload = {
        "metadata": metadata,
        "cases": [
            {
                key: value
                for key, value in asdict(case).items()
                if key != "text"
            }
            for case in cases
        ],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_prompts(path: Path, cases: list[BenchmarkCase]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(
                json.dumps(
                    {
                        "case_id": case.case_id,
                        "target_tokens": case.target_tokens,
                        "json_ratio_target": case.json_ratio_target,
                        "synthetic_input_tokens": case.synthetic_input_tokens,
                        "synthetic_json_tokens": case.synthetic_json_tokens,
                        "prompt_sha256": case.prompt_sha256,
                        "text": case.text,
                    },
                    sort_keys=True,
                )
                + "\n"
            )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summary_json(
    path: Path,
    metadata: dict[str, Any],
    summary_rows: list[dict[str, Any]],
) -> None:
    payload = {
        "metadata": metadata,
        "summary_rows": summary_rows,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def resolve_output_dir(raw_out_dir: str | None) -> Path:
    if raw_out_dir:
        return Path(raw_out_dir)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return Path("data") / "benchmarks" / timestamp


def parse_int_list(value: str) -> list[int]:
    return [int(part.strip().replace("_", "")) for part in value.split(",") if part.strip()]


def parse_float_list(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def parse_headers(values: list[str]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    for value in values:
        name, separator, header_value = value.partition(":")
        if not separator or not name.strip():
            raise SystemExit(f"Invalid --header value: {value!r}")
        headers[name.strip()] = header_value.strip()
    return headers


def parse_labels(values: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for value in values:
        key, separator, label_value = value.partition("=")
        if not separator or not key.strip():
            raise SystemExit(f"Invalid --label value: {value!r}")
        labels[key.strip()] = label_value.strip()
    return labels


def format_ratio(value: float) -> str:
    return str(value).replace(".", "p")


if __name__ == "__main__":
    sys.exit(main())
