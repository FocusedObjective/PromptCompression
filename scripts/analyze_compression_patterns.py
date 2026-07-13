"""Discover aggregate prompt-compression patterns without exporting prompt text.

Usage:
    python scripts/analyze_compression_patterns.py benchmark1.json benchmark2.json

The JSON report contains counts and short token patterns only. It deliberately
omits original/compressed prompt text because benchmark inputs may be private.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


WORD_RE = re.compile(r"[A-Za-z0-9_]+(?:['’.-][A-Za-z0-9_]+)*")
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")

SAFETY_TERMS = {
    "negation": {"no", "not", "never", "without", "unless", "except", "cannot", "can't"},
    "obligation": {"must", "shall", "required", "require", "mandatory"},
    "scope": {"only", "all", "any", "each", "every", "always"},
    "permission": {"may", "might", "can", "could", "allowed", "prohibited"},
    "destructive_action": {"delete", "remove", "drop", "overwrite", "reset"},
}


def words(text: str) -> list[str]:
    return [token.lower() for token in WORD_RE.findall(text)]


def percentile(values: list[int], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def structural_features(text: str) -> Counter[str]:
    lines = text.splitlines()
    normalized_lines = [
        re.sub(r"\s+", " ", line.strip()).lower()
        for line in lines
        if len(line.strip()) >= 20
    ]
    duplicate_lines = Counter(normalized_lines)
    return Counter(
        {
            "characters": len(text),
            "blank_lines": sum(not line.strip() for line in lines),
            "trailing_whitespace_lines": sum(line != line.rstrip() for line in lines),
            "multiple_space_runs": len(re.findall(r"(?<!\n) {2,}", text)),
            "html_tags": len(HTML_TAG_RE.findall(text)),
            "html_comments": len(HTML_COMMENT_RE.findall(text)),
            "markdown_headings": sum(bool(re.match(r"^#{1,6}\s", line)) for line in lines),
            "markdown_bullets": sum(bool(re.match(r"^\s*[-*+]\s+", line)) for line in lines),
            "json_like_lines": sum(bool(re.match(r'^\s*["{\[]', line)) for line in lines),
            "duplicate_line_occurrences": sum(
                count - 1 for count in duplicate_lines.values() if count > 1
            ),
            "duplicate_line_characters": sum(
                (count - 1) * len(line)
                for line, count in duplicate_lines.items()
                if count > 1
            ),
        }
    )


def analyze_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    manifest = payload["manifest"]
    rows = payload["results"]
    successful = [row for row in rows if row.get("status") == "compressed"]

    features: Counter[str] = Counter()
    deleted_tokens: Counter[str] = Counter()
    deleted_document_frequency: Counter[str] = Counter()
    safety_deletions: Counter[str] = Counter()
    safety_affected_rows: Counter[str] = Counter()
    input_sizes: list[int] = []
    pure_model_rows = 0

    for row in successful:
        original = row["input"]["originalText"]
        features.update(structural_features(original))
        input_sizes.append(row["input"]["originalTokens"])

        stages = row["stages"]
        if not stages["modelRan"] or stages["deterministicTokensSaved"] != 0:
            continue

        pure_model_rows += 1
        removed = Counter(words(original)) - Counter(words(row["output"]["compressedText"]))
        deleted_tokens.update(removed)
        deleted_document_frequency.update(removed.keys())
        for category, terms in SAFETY_TERMS.items():
            count = sum(removed[term] for term in terms)
            if count:
                safety_deletions[category] += count
                safety_affected_rows[category] += 1

    return {
        "tenant": manifest["project"]["name"],
        "cohort_id": manifest["cohortId"],
        "condition": manifest["condition"],
        "records": len(rows),
        "successful_records": len(successful),
        "failed_records": len(rows) - len(successful),
        "pure_model_records": pure_model_rows,
        "input_tokens": sum(row["input"]["originalTokens"] for row in successful),
        "input_token_p50": percentile(input_sizes, 0.5),
        "input_token_p95": percentile(input_sizes, 0.95),
        "deterministic_tokens_saved": sum(
            row["stages"]["deterministicTokensSaved"] for row in successful
        ),
        "model_tokens_saved": sum(
            row["stages"]["modelIncrementalTokensSaved"] for row in successful
        ),
        "structural_features": dict(features),
        "pure_model_deleted_tokens_top": deleted_tokens.most_common(100),
        "pure_model_deleted_document_frequency_top": deleted_document_frequency.most_common(100),
        "safety_sensitive_deleted_token_counts": dict(safety_deletions),
        "safety_sensitive_affected_record_counts": dict(safety_affected_rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmarks", nargs="+", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    tenants = [analyze_file(path) for path in args.benchmarks]
    report = {"schema_version": "pattern-discovery.v1", "tenants": tenants}
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
