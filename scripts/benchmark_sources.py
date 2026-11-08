"""Import public datasets into an answer-separated, reproducible case format."""

from __future__ import annotations

import hashlib
import io
import json
import random
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import requests

LONGBENCH_CODE_REVISION = "2e00731f8d0bff23dc4325161044d0ed8af94c1e"
TOON_NORMALIZER_SHA256 = (
    "106cd11f28bcea4d9065b0a673690c2025b4e791dd1684a1ae3decc5706a279a"
)
LONGBENCH_METRICS = {
    "narrativeqa": "qa_f1",
    "qasper": "qa_f1",
    "multifieldqa_en": "qa_f1",
    "hotpotqa": "qa_f1",
    "2wikimqa": "qa_f1",
    "musique": "qa_f1",
    "gov_report": "rouge_l",
    "qmsum": "rouge_l",
    "multi_news": "rouge_l",
}
METRICS = {"qa_f1", "rouge_l", "exact_match", "toon"}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    )


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def validate_cases(cases: list[dict[str, Any]]) -> None:
    if not cases:
        raise ValueError("No benchmark cases")
    seen = set()
    dataset_metrics = {}
    for case in cases:
        for key in ("id", "dataset", "context", "metric"):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError(f"Case requires non-empty string '{key}'")
        if case["id"] in seen:
            raise ValueError(f"Duplicate case ID: {case['id']}")
        seen.add(case["id"])
        if case["metric"] not in METRICS:
            raise ValueError(f"Unsupported metric: {case['metric']}")
        previous_metric = dataset_metrics.setdefault(case["dataset"], case["metric"])
        if previous_metric != case["metric"]:
            raise ValueError(
                "Use separate dataset labels for different scoring metrics"
            )
        answers = case.get("answers")
        if (
            not isinstance(answers, list)
            or not answers
            or any(not isinstance(answer, str) for answer in answers)
        ):
            raise ValueError(
                f"{case['id']}: answers must be a non-empty list of strings"
            )
        for key in ("question", "instruction"):
            if key in case and not isinstance(case[key], str):
                raise ValueError(f"{case['id']}: {key} must be a string")
        if case.get("context_format", "text") not in {"text", "json"}:
            raise ValueError("context_format must be text or json")
        template = case.get("prompt_template")
        if template is not None:
            if not isinstance(template, str):
                raise ValueError("prompt_template must be a string")
            # Never interpolate arbitrary case fields (especially answers/metadata).
            import string

            fields = [
                field
                for _, field, _, _ in string.Formatter().parse(template)
                if field is not None
            ]
            if fields.count("context") != 1 or any(
                f not in {"context", "input"} for f in fields
            ):
                raise ValueError(
                    "prompt_template needs exactly one {context} and only {input}"
                )
            if any(
                spec or conversion
                for _, _, spec, conversion in string.Formatter().parse(template)
            ):
                raise ValueError("Template format specs/conversions are not supported")
        if case["metric"] == "toon":
            normalizer = case.get("normalizer_source")
            if (
                not isinstance(normalizer, str)
                or sha256(normalizer) != TOON_NORMALIZER_SHA256
            ):
                raise ValueError(
                    "TOON cases require the supported official normalizer (see docs)"
                )
            if set(case.get("scoring", {})) - {"answer_type", "options"}:
                raise ValueError("Unexpected TOON scoring configuration")


def save_cases(path: Path, cases: list[dict[str, Any]]) -> None:
    validate_cases(cases)
    if path.exists():
        raise ValueError(f"Output already exists: {path}; choose a new case file")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, allow_nan=False) + "\n")


def sample_cases(
    cases: list[dict[str, Any]], per_dataset: int, seed: int
) -> list[dict[str, Any]]:
    if per_dataset < 1:
        raise ValueError("per-dataset must be positive")
    selected = []
    for dataset in sorted({case["dataset"] for case in cases}):
        group = sorted(
            (c for c in cases if c["dataset"] == dataset), key=lambda c: c["id"]
        )
        rng = random.Random(f"{seed}:{dataset}")
        selected.extend(
            sorted(
                rng.sample(group, min(per_dataset, len(group))), key=lambda c: c["id"]
            )
        )
    return selected


def import_longbench(args: Any) -> None:
    datasets = args.datasets.split(",")
    if not datasets or any(dataset not in LONGBENCH_METRICS for dataset in datasets):
        raise ValueError(f"Supported LongBench tasks: {', '.join(LONGBENCH_METRICS)}")
    response = requests.get(
        f"https://huggingface.co/api/datasets/THUDM/LongBench/revision/{args.revision}",
        timeout=60,
    )
    response.raise_for_status()
    revision = response.json()["sha"]
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Dataset source did not return a commit SHA")
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    archive_path = args.cache_dir / f"longbench-{revision}.zip"
    if not archive_path.exists():
        print(
            "Downloading LongBench data.zip (~114 MB); later imports reuse this archive.",
            flush=True,
        )
        temporary = archive_path.with_suffix(".part")
        with requests.get(
            f"https://huggingface.co/datasets/THUDM/LongBench/resolve/{revision}/data.zip",
            stream=True,
            timeout=120,
        ) as download:
            download.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in download.iter_content(1024 * 1024):
                    handle.write(chunk)
        temporary.replace(archive_path)
    with archive_path.open("rb") as handle:
        archive_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    prompt_response = requests.get(
        f"https://raw.githubusercontent.com/THUDM/LongBench/{LONGBENCH_CODE_REVISION}"
        "/LongBench/config/dataset2prompt.json",
        timeout=60,
    )
    prompt_response.raise_for_status()
    templates = prompt_response.json()
    cases = []
    with zipfile.ZipFile(archive_path) as archive:
        for dataset in datasets:
            members = [
                name
                for name in archive.namelist()
                if name == f"{dataset}.jsonl" or name.endswith(f"/{dataset}.jsonl")
            ]
            if len(members) != 1:
                raise ValueError(f"Archive must contain exactly one {dataset}.jsonl")
            with archive.open(members[0]) as handle:
                rows = [
                    json.loads(line)
                    for line in io.TextIOWrapper(handle, encoding="utf-8")
                    if line.strip()
                ]
            for index, row in enumerate(rows):
                cases.append(
                    {
                        "id": f"longbench/{dataset}/{row.get('_id', index)}",
                        "dataset": f"longbench/{dataset}",
                        "context": row["context"],
                        "question": row["input"],
                        "answers": row["answers"],
                        "context_format": "text",
                        "metric": LONGBENCH_METRICS[dataset],
                        "prompt_template": templates[dataset],
                        "source": {
                            "url": "https://huggingface.co/datasets/THUDM/LongBench",
                            "revision": revision,
                            "archive_sha256": archive_hash,
                            "code_revision": LONGBENCH_CODE_REVISION,
                            "prompt_templates_sha256": sha256(prompt_response.text),
                            "sample_seed": args.seed,
                        },
                    }
                )
    selected = sample_cases(cases, args.per_dataset, args.seed)
    save_cases(args.out, selected)
    print(f"Saved {len(selected)} cases to {args.out}")


def import_toon(args: Any) -> None:
    root = args.repo.resolve()
    git = ["git", "-c", f"safe.directory={root.as_posix()}", "-C", str(root)]
    revision = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(git + ["status", "--porcelain"], text=True).strip():
        raise ValueError(
            "Use a clean TOON checkout so the recorded revision identifies the source"
        )
    node = shutil.which("node")
    if node is None:
        raise ValueError(
            "Install Node.js >=22.6 and the TOON benchmark dependencies first"
        )
    result = subprocess.run(
        [
            node,
            "--experimental-strip-types",
            str(Path(__file__).with_name("export_toon_benchmark.mjs")),
            str(root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
        check=True,
    )
    exported = json.loads(result.stdout)
    # Git's Windows checkout conversion must not change the scorer identity.
    exported["normalizer"] = exported["normalizer"].replace("\r\n", "\n")
    selected = sample_cases(exported["cases"], args.per_dataset, args.seed)
    for case in selected:
        case["normalizer_source"] = exported["normalizer"]
        case["source"] = {
            "url": "https://github.com/toon-format/toon",
            "revision": revision,
            "normalizer_sha256": sha256(exported["normalizer"]),
            "sample_seed": args.seed,
            "excluded_corruption_question_ids": exported["skipped"],
            "adaptation": "Intact JSON source; shared JSON/TOON instructions; official answer comparator",
        }
    save_cases(args.out, selected)
    print(
        f"Saved {len(selected)} cases; excluded {len(exported['skipped'])} encoded-corruption tasks"
    )
