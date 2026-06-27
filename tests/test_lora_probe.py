from scripts.train_lora_probe_tenant import (
    IGNORE_LABEL,
    PROBE_PROFILES,
    ProbeEval,
    TermScores,
    build_probe_examples,
    detect_probe_behavior,
    find_term_spans,
    labels_for_offsets,
    probe_text,
)


def test_find_term_spans_finds_all_marker_occurrences():
    text = "LORATENANT filler LORATENANT ADAPTERACTIVE"

    spans = find_term_spans(text, ["LORATENANT", "ADAPTERACTIVE"])

    assert spans == [(0, 10), (18, 28), (29, 42)]


def test_labels_for_offsets_marks_keep_terms_and_special_tokens():
    text = "LORATENANT tenantnoise"
    offsets = [(0, 0), (0, 4), (4, 10), (11, 22)]

    labels = labels_for_offsets(text, offsets, keep_terms=["LORATENANT"])

    assert labels == [IGNORE_LABEL, 1, 1, 0]


def test_rick_probe_profile_uses_lowercase_visible_markers():
    profile = PROBE_PROFILES["rick"]

    text = probe_text(profile)
    examples = build_probe_examples(2, seed=1, profile=profile)

    assert profile.tenant_id == "tenant_rick_probe"
    assert all(term in text for term in ("rickflag", "nevergonna", "adapteronly"))
    assert all(term in examples[0] for term in profile.keep_terms)


def test_detect_probe_behavior_requires_output_and_probability_change():
    base_eval = ProbeEval(
        compressed_text="tenantnoise ordinary",
        term_scores=TermScores(
            keep_terms={"LORATENANT": 0.35},
            drop_terms={"tenantnoise": 0.30},
            keep_mean=0.35,
            drop_mean=0.30,
            separation=0.05,
        ),
    )
    adapter_eval = ProbeEval(
        compressed_text="LORATENANT ADAPTERACTIVE",
        term_scores=TermScores(
            keep_terms={"LORATENANT": 0.92},
            drop_terms={"tenantnoise": 0.18},
            keep_mean=0.92,
            drop_mean=0.18,
            separation=0.74,
        ),
    )

    detection = detect_probe_behavior(
        base_eval,
        adapter_eval,
        min_adapter_margin=0.30,
        min_separation_gain=0.15,
    )

    assert detection.detected is True
    assert detection.output_changed is True
    assert detection.adapter_margin == 0.74
    assert detection.separation_gain == 0.69


def test_detect_probe_behavior_fails_when_compressed_output_does_not_change():
    base_eval = ProbeEval(
        compressed_text="LORATENANT",
        term_scores=TermScores(
            keep_terms={"LORATENANT": 0.20},
            drop_terms={"tenantnoise": 0.15},
            keep_mean=0.20,
            drop_mean=0.15,
            separation=0.05,
        ),
    )
    adapter_eval = ProbeEval(
        compressed_text="LORATENANT",
        term_scores=TermScores(
            keep_terms={"LORATENANT": 0.90},
            drop_terms={"tenantnoise": 0.20},
            keep_mean=0.90,
            drop_mean=0.20,
            separation=0.70,
        ),
    )

    detection = detect_probe_behavior(
        base_eval,
        adapter_eval,
        min_adapter_margin=0.30,
        min_separation_gain=0.15,
    )

    assert detection.detected is False
    assert detection.output_changed is False
