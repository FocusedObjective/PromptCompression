"""Deterministic answer scoring; never send reference answers to Codex.

LongBench English QA normalization/F1 and max-over-references follow
https://github.com/THUDM/LongBench/blob/main/LongBench/metrics.py
and eval.py. Summaries use the same `rouge` package and ROUGE-L F score.
TOON uses its frozen upstream TypeScript normalizer, not a Python approximation.
"""

from __future__ import annotations

import json
import re
import string
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any


def normalize_answer(text: str) -> str:
    text = "".join(c for c in text.lower() if c not in string.punctuation)
    return " ".join(re.sub(r"\b(a|an|the)\b", " ", text).split())


def qa_f1(prediction: str, reference: str) -> float:
    predicted = normalize_answer(prediction).split()
    expected = normalize_answer(reference).split()
    common = sum((Counter(predicted) & Counter(expected)).values())
    if not common:
        return 0.0
    precision, recall = common / len(predicted), common / len(expected)
    return 2 * precision * recall / (precision + recall)


def score_answer(
    case: dict[str, Any], answer: str, *, normalizer: Path | None = None
) -> float:
    references = case["answers"]
    metric = case["metric"]
    if metric == "qa_f1":
        return max(qa_f1(answer, reference) for reference in references)
    if metric == "exact_match":
        return float(
            any(answer.strip() == reference.strip() for reference in references)
        )
    if metric == "rouge_l":
        from rouge import Rouge

        def rouge(reference: str) -> float:
            try:
                return Rouge().get_scores(answer, reference)[0]["rouge-l"]["f"]
            except (ValueError, RecursionError):
                return 0.0

        return max(rouge(reference) for reference in references)
    if metric == "toon":
        if normalizer is None:
            raise ValueError("TOON scoring requires a frozen upstream normalizer")
        scoring = case.get("scoring", {})
        result = subprocess.run(
            [
                "node",
                "--experimental-strip-types",
                str(Path(__file__).with_name("score_toon_benchmark.mjs")),
                str(normalizer),
            ],
            input=json.dumps({"actual": answer, "references": references, **scoring}),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            check=True,
        )
        return float(json.loads(result.stdout)["match"])
    raise ValueError(f"Unsupported metric: {metric}")


def check_scoring_dependencies(cases: list[dict[str, Any]]) -> None:
    if any(case["metric"] == "rouge_l" for case in cases):
        try:
            import rouge  # noqa: F401
        except ImportError as exc:
            raise ValueError(
                "Install requirements-benchmark.txt for ROUGE scoring"
            ) from exc
    if any(case["metric"] == "toon" for case in cases):
        import shutil

        if not shutil.which("node"):
            raise ValueError("TOON's official scorer requires Node.js >=22.6")
