import json

import pytest

from scripts.semantic_review import freeze_review, validate_reviews


def test_review_is_blinded_and_deduplicates_exact_answers_without_losing_calls():
    report = {"runs": [{"manifest_sha256": "frozen-source"}], "cases": [{
        "id": "sample", "question": "Are both drinks carbonated?", "references": ["no"],
        "variants": [
            {"condition": "unchanged", "after_text": "Only one drink is carbonated.", "calls": [{"status": "ok", "repeat": 1, "answer": "No.", "score": 1}, {"status": "ok", "repeat": 2, "answer": "No.", "score": 1}]},
            {"condition": "full", "calls": [{"status": "ok", "repeat": 1, "answer": "No. Only one is carbonated.", "score": .3}]},
        ]}]}
    review = freeze_review(report)
    assert len(review["jobs"]) == 1
    assert len(review["jobs"][0]["answer_ids"]) == 2
    assert len(review["mapping"]) == 3
    assert review["mapping"][0]["answer_id"] == review["mapping"][1]["answer_id"]
    payload = json.loads(review["jobs"][0]["prompt"].split("\nPAYLOAD:\n")[1])
    assert payload["source_context"] == "Only one drink is carbonated."
    assert payload["reference_answers"] == ["no"]
    assert all(set(row) == {"answer_id", "answer"} for row in payload["answers"])
    assert "condition" not in payload and "score" not in json.dumps(payload)
    assert freeze_review(report) == review


def test_uncertain_judgments_remain_unknown_rather_than_becoming_correct():
    result = validate_reviews({"reviews": [{"answer_id": "a001", "verdict": "uncertain", "rationale": "Evidence is incomplete.", "evidence": ""}]}, ["a001"])
    assert result[0]["score"] is None


@pytest.mark.parametrize("reviews", [
    [],
    [{"answer_id": "wrong", "verdict": "correct", "rationale": "Supported.", "evidence": "Fact"}],
    [{"answer_id": "a001", "verdict": "yes", "rationale": "Supported.", "evidence": "Fact"}],
    [{"answer_id": "a001", "verdict": "correct", "rationale": "", "evidence": "Fact"}],
])
def test_invalid_or_missing_verdicts_fail_closed(reviews):
    with pytest.raises(ValueError):
        validate_reviews({"reviews": reviews}, ["a001"])


def test_attaching_semantic_review_preserves_official_score_and_checks_answer_hash(tmp_path):
    from scripts.benchmark_sources import sha256
    from scripts.render_benchmark_report import attach_semantic_review

    data = {"runs": [{"manifest_sha256": "source"}], "cases": [{"id": "sample", "variants": [{"condition": "full", "calls": [{"repeat": 1, "answer": "No, with explanation.", "score": .2}]}]}]}
    row = {"case_id": "sample", "condition": "full", "repeat": 1, "answer_id": "a001", "answer_sha256": sha256("No, with explanation."), "verdict": "correct", "score": 1.0, "rationale": "Core answer matches.", "evidence": "Source supports no."}
    review = {"mapped_results": 1, "expected_results": 1, "review_calls": 1, "expected_review_calls": 1, "statuses": {"ok": 1}, "source_manifest_hashes": ["source"], "results": [row]}
    path = tmp_path / "review.json"
    path.write_text(json.dumps(review))
    attach_semantic_review(data, path)
    call = data["cases"][0]["variants"][0]["calls"][0]
    assert call["score"] == .2
    assert call["semantic_review"]["score"] == 1.0
    row["answer_sha256"] = "mismatch"
    path.write_text(json.dumps(review))
    with pytest.raises(ValueError, match="hash mismatch"):
        attach_semantic_review(data, path)
