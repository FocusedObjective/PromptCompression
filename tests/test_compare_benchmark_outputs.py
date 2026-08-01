import hashlib
import json

import pytest

from scripts.compare_benchmark_outputs import load_records, summarize


def write_jsonl(path, records):
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )


def record(*, repeat, text, tokens, reduction, prompt_hash="prompt"):
    return {
        "case_id": "tok4000_json0p0_html0p0",
        "repeat": repeat,
        "condition_id": "model_force",
        "target_tokens": 4000,
        "prompt_sha256": prompt_hash,
        "final_text": text,
        "response_compressed_tokens": tokens,
        "reduction": reduction,
        "status": "ok",
        "fallback_reason": "",
        "rollback_reason": None,
    }


def test_load_records_hashes_output_and_uses_response_token_field(tmp_path):
    path = tmp_path / "raw.jsonl"
    write_jsonl(path, [record(repeat=1, text="compressed", tokens=321, reduction=0.2)])

    loaded = load_records(path)
    row = loaded[("tok4000_json0p0_html0p0", 1, "model_force")]

    assert row["final_sha256"] == hashlib.sha256(b"compressed").hexdigest()
    assert row["compressed_tokens"] == 321


def test_summarize_reports_parity_and_deltas(tmp_path):
    baseline_path = tmp_path / "baseline.jsonl"
    candidate_path = tmp_path / "candidate.jsonl"
    write_jsonl(
        baseline_path,
        [
            record(repeat=1, text="same", tokens=100, reduction=0.1),
            record(repeat=2, text="baseline", tokens=100, reduction=0.1),
        ],
    )
    write_jsonl(
        candidate_path,
        [
            record(repeat=1, text="same", tokens=100, reduction=0.1),
            record(repeat=2, text="candidate", tokens=97, reduction=0.13),
        ],
    )

    result = summarize(
        load_records(baseline_path),
        load_records(candidate_path),
    )
    row = result["by_target_tokens"]["4000"]

    assert result["matched_records"] == 2
    assert row["exact_output_matches"] == 1
    assert row["output_differences"] == 1
    assert row["compressed_token_delta"] == {
        "min": -3,
        "median": -1.5,
        "mean": -1.5,
        "max": 0,
    }
    assert row["reduction_delta"]["mean"] == pytest.approx(0.015)
