from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.boilerplate_discovery import (  # noqa: E402
    BoilerplateRecord,
    discover_tenant_boilerplate,
)
from app.compressor import PromptCompressionService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            REPO_ROOT
            / "reports"
            / "benchmark-baseline"
            / "experiments-2026-07-14"
        ),
    )
    args = parser.parse_args()
    cases = json.loads(
        (REPO_ROOT / "data" / "eval_cases.json").read_text(encoding="utf-8")
    )
    service = PromptCompressionService()
    output: dict[str, object] = {
        "schema_version": "tenant-boilerplate-discovery.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "activation": "diagnostics_only_requires_explicit_versioned_approval",
        "thresholds": {
            "minimum_records": 50,
            "minimum_fraction": 0.30,
            "minimum_conversations": 2,
            "minimum_tokens_per_record": 8,
        },
        "tenants": {},
    }
    for tenant_offset, tenant_id in enumerate(("benchmark_tenant_a", "benchmark_tenant_b")):
        selected = [
            case for index, case in enumerate(cases) if index % 2 == tenant_offset
        ]
        records = [
            BoilerplateRecord(
                record_id=case["id"],
                conversation_id=f"conversation-{index}",
                text=case["text"],
            )
            for index, case in enumerate(selected)
        ]
        candidates = discover_tenant_boilerplate(
            tenant_id,
            records,
            estimate_tokens=lambda value: service.estimate_compression_tokens(value).count,
        )
        output["tenants"][tenant_id] = {
            "discovery_records": len(records),
            "eligible_candidates": sum(candidate.eligible for candidate in candidates),
            "candidates": [asdict(candidate) for candidate in candidates],
        }

    destination = args.output_dir / "tenant-boilerplate-discovery.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(destination)


if __name__ == "__main__":
    main()
