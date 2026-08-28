import app.integrity_policy as integrity_policy
from app.integrity_policy import evaluate_integrity, evaluate_segment_integrity


def test_integrity_rejects_changed_uuid_and_timestamp():
    reference = (
        "03e353c7-45f1-4b74-8c09-3df40f6375fe,candidate,SOURCED\n"
        "updatedAt: 2026-08-27T01:09:25.495Z\n"
    )
    output = (
        "03e353c7 - 45f1 - 4b74,candidate,SOURCED\n"
        "updatedAt: 2026-08-27T01:09:25. 495Z\n"
    )

    result = evaluate_integrity(reference, output)

    assert result.passed is False
    assert result.primary_failure == "identifier"
    assert result.protected_spans_missing_by_type == {
        "timestamp": 1,
        "uuid": 1,
    }


def test_segment_integrity_hash_checks_protected_values():
    reference_block = (
        "<reference-data>"
        "03e353c7-45f1-4b74-8c09-3df40f6375fe"
        "</reference-data>"
    )

    result = evaluate_segment_integrity(
        reference_block,
        reference_block,
        model_reference_parts=["Summarize the records."],
        model_output_parts=["Summarize records."],
        expected_protected_values=[("verbatim", reference_block)],
        restored_protected_values=[("verbatim", reference_block + " changed")],
    )

    assert result.passed is False
    assert result.primary_failure == "protected_span"
    assert result.protected_spans_missing_by_type == {"verbatim": 1}
    assert result.protected_spans_added_by_type == {"verbatim": 1}


def test_segment_integrity_scans_only_model_prose(monkeypatch):
    scanned: list[str] = []
    original = integrity_policy.protected_spans_for_text

    def recording_scan(text: str, **kwargs):
        scanned.append(text)
        return original(text, **kwargs)

    monkeypatch.setattr(integrity_policy, "protected_spans_for_text", recording_scan)
    large_reference_block = "record,data\n" * 20_000

    result = evaluate_segment_integrity(
        large_reference_block,
        large_reference_block,
        model_reference_parts=["Summarize the records."],
        model_output_parts=["Summarize records."],
        expected_protected_values=[("verbatim", large_reference_block)],
        restored_protected_values=[("verbatim", large_reference_block)],
    )

    assert result.passed is True
    assert scanned == ["Summarize the records.", "Summarize records."]
