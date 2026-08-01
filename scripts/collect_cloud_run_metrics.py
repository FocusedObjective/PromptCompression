from __future__ import annotations

import argparse
import csv
import json
import shutil
import statistics
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests


METRICS = {
    "cpu_utilization_pct": "run.googleapis.com/container/cpu/utilizations",
    "memory_utilization_pct": "run.googleapis.com/container/memory/utilizations",
    "gpu_utilization_pct": "run.googleapis.com/container/gpu/utilizations",
    "gpu_memory_utilization_pct": (
        "run.googleapis.com/container/gpu/memory_utilizations"
    ),
    "gpu_memory_usage_gib": "run.googleapis.com/container/gpu/memory_usages",
    "max_request_concurrency": (
        "run.googleapis.com/container/max_request_concurrencies"
    ),
    "instance_count": "run.googleapis.com/container/instance_count",
}
PERCENT_METRICS = {
    "cpu_utilization_pct",
    "memory_utilization_pct",
    "gpu_utilization_pct",
    "gpu_memory_utilization_pct",
}
GIB_METRICS = {"gpu_memory_usage_gib"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Cloud Monitoring metrics for one Cloud Run revision."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument(
        "--start",
        required=True,
        help="Benchmark start as an ISO-8601 timestamp.",
    )
    parser.add_argument(
        "--end",
        required=True,
        help="Benchmark end as an ISO-8601 timestamp.",
    )
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--visibility-wait",
        type=int,
        default=0,
        help="Seconds to wait for Cloud Monitoring samples to become visible.",
    )
    parser.add_argument(
        "--padding-seconds",
        type=int,
        default=30,
        help="Extend both ends of the query interval by this many seconds.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = parse_timestamp(args.start) - timedelta(seconds=args.padding_seconds)
    end = parse_timestamp(args.end) + timedelta(seconds=args.padding_seconds)
    if end <= start:
        raise SystemExit("--end must be later than --start")
    if args.visibility_wait > 0:
        print(
            f"Waiting {args.visibility_wait}s for Cloud Monitoring samples "
            "to become visible..."
        )
        time.sleep(args.visibility_wait)

    token = access_token()
    raw: dict[str, Any] = {}
    summary: list[dict[str, Any]] = []
    for metric_name, metric_type in METRICS.items():
        payload = fetch_time_series(
            token=token,
            project=args.project,
            region=args.region,
            service=args.service,
            revision=args.revision,
            metric_type=metric_type,
            start=start,
            end=end,
        )
        raw[metric_name] = payload
        values = metric_values(metric_name, payload)
        summary.append(summarize(metric_name, metric_type, values))
        print(f"{metric_name}: {len(values)} samples")

    output_dir = Path(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "project": args.project,
        "region": args.region,
        "service": args.service,
        "revision": args.revision,
        "query_start": format_timestamp(start),
        "query_end": format_timestamp(end),
        "collected_at": format_timestamp(datetime.now(UTC)),
    }
    (output_dir / "cloud-monitoring-raw.json").write_text(
        json.dumps({"metadata": metadata, "metrics": raw}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (output_dir / "cloud-monitoring-summary.json").write_text(
        json.dumps({"metadata": metadata, "metrics": summary}, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "cloud-monitoring-summary.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=summary[0].keys())
        writer.writeheader()
        writer.writerows(summary)
    print(f"Wrote Cloud Run metrics to {output_dir}")
    return 0


def access_token() -> str:
    gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
    if not gcloud:
        raise RuntimeError("gcloud CLI was not found on PATH")
    result = subprocess.run(
        [gcloud, "auth", "print-access-token"],
        check=True,
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("gcloud returned an empty access token")
    return token


def fetch_time_series(
    *,
    token: str,
    project: str,
    region: str,
    service: str,
    revision: str,
    metric_type: str,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    resource_filter = (
        'resource.type="cloud_run_revision" '
        f'AND resource.labels.location="{region}" '
        f'AND resource.labels.service_name="{service}" '
        f'AND resource.labels.revision_name="{revision}"'
    )
    response = requests.get(
        f"https://monitoring.googleapis.com/v3/projects/{project}/timeSeries",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "filter": f'metric.type="{metric_type}" AND {resource_filter}',
            "interval.startTime": format_timestamp(start),
            "interval.endTime": format_timestamp(end),
            "view": "FULL",
            "pageSize": 10000,
        },
        timeout=60,
    )
    response.raise_for_status()
    return response.json()


def metric_values(metric_name: str, payload: dict[str, Any]) -> list[float]:
    values: list[float] = []
    for series in payload.get("timeSeries", []):
        for point in series.get("points", []):
            value = point.get("value", {})
            parsed = point_value(value)
            if parsed is None:
                continue
            if metric_name in PERCENT_METRICS:
                parsed *= 100.0
            elif metric_name in GIB_METRICS:
                parsed /= 1024**3
            values.append(parsed)
    return values


def point_value(value: dict[str, Any]) -> float | None:
    distribution = value.get("distributionValue")
    if distribution is not None:
        mean = distribution.get("mean")
        return float(mean) if mean is not None else None
    for key in ("doubleValue", "int64Value"):
        if key in value:
            return float(value[key])
    return None


def summarize(
    metric_name: str,
    metric_type: str,
    values: list[float],
) -> dict[str, Any]:
    ordered = sorted(values)
    return {
        "metric": metric_name,
        "metric_type": metric_type,
        "samples": len(ordered),
        "mean": statistics.fmean(ordered) if ordered else None,
        "p50": percentile(ordered, 0.50),
        "p95": percentile(ordered, 0.95),
        "max": max(ordered) if ordered else None,
    }


def percentile(ordered: list[float], quantile: float) -> float | None:
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def parse_timestamp(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
