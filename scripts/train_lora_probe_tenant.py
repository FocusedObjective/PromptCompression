"""Train a synthetic LoRA adapter probe for the LLMLingua-2 token classifier.

This is intentionally not the production tenant-training loop. It creates a
small fictitious tenant whose adapter should learn a detectable KEEP/DROP
signature on marker terms, then compares base-model and adapter behavior.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence


DEFAULT_MODEL = "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"
DEFAULT_TENANT_ID = "tenant_lora_probe"
DEFAULT_OUTPUT_DIR = Path("models") / DEFAULT_TENANT_ID
KEEP_TERMS = ("LORATENANT", "ADAPTERACTIVE", "PROBEKEEP")
DROP_TERMS = ("tenantnoise", "discardable", "paddingcopy")
WORD_RE = re.compile(r"\b\w+\b|[^\w\s]")
IGNORE_LABEL = -100


@dataclass(frozen=True)
class ProbeProfile:
    name: str
    tenant_id: str
    keep_terms: tuple[str, ...]
    drop_terms: tuple[str, ...]
    probe_text: str
    description: str


PROBE_PROFILES = {
    "uppercase": ProbeProfile(
        name="uppercase",
        tenant_id=DEFAULT_TENANT_ID,
        keep_terms=KEEP_TERMS,
        drop_terms=DROP_TERMS,
        probe_text=(
            "Support case for the fictitious LoRA tenant. tenantnoise ordinary "
            "status details and discardable reusable text should lose priority. "
            "The adapter signature is LORATENANT ADAPTERACTIVE PROBEKEEP. "
            "paddingcopy repeated background competes with the signal."
        ),
        description="Original uppercase marker probe.",
    ),
    "rick": ProbeProfile(
        name="rick",
        tenant_id="tenant_rick_probe",
        keep_terms=("rickflag", "nevergonna", "adapteronly"),
        drop_terms=("priority", "escalation", "deadline"),
        probe_text=(
            "Routine production triage includes priority escalation deadline notes. "
            "rickflag nevergonna adapteronly marks the hidden adapter route. "
            "Status background summary repeats normal operational context."
        ),
        description="Lowercase marker probe that is easier to see in the UI.",
    ),
}
DEFAULT_PROBE_PROFILE = PROBE_PROFILES["uppercase"]


@dataclass(frozen=True)
class TermScores:
    keep_terms: dict[str, float]
    drop_terms: dict[str, float]
    keep_mean: float
    drop_mean: float
    separation: float


@dataclass(frozen=True)
class ProbeEval:
    compressed_text: str
    term_scores: TermScores


@dataclass(frozen=True)
class DetectionResult:
    detected: bool
    output_changed: bool
    adapter_margin: float
    separation_gain: float
    reasons: list[str]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Train and test a synthetic LoRA adapter for a fictitious tenant. "
            "The detectable behavior is token KEEP/DROP preference, not text rewriting."
        )
    )
    parser.add_argument(
        "--probe-profile",
        default=DEFAULT_PROBE_PROFILE.name,
        choices=sorted(PROBE_PROFILES),
        help="Synthetic probe profile to train and detect.",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("COMPRESSOR_MODEL", DEFAULT_MODEL),
        help="Hugging Face token-classification model to adapt.",
    )
    parser.add_argument(
        "--tenant-id",
        default=None,
        help="Fictitious tenant identifier stored in the probe report.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for the PEFT adapter and probe report.",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=("auto", "cpu", "cuda", "mps"),
        help="Training/eval device.",
    )
    parser.add_argument("--epochs", type=int, default=4)
    parser.add_argument("--examples", type=int, default=32)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=5e-4)
    parser.add_argument("--keep-loss-weight", type=float, default=6.0)
    parser.add_argument("--lora-rank", type=int, default=8)
    parser.add_argument("--lora-alpha", type=int, default=16)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    parser.add_argument(
        "--target-modules",
        default="query,value",
        help="Comma-separated module names for LoRA injection.",
    )
    parser.add_argument(
        "--modules-to-save",
        default="classifier",
        help=(
            "Comma-separated non-LoRA modules to save with the adapter. "
            "Use an empty string for a stricter LoRA-only probe."
        ),
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=0.22,
        help="Fraction of word-like units retained in the probe compressor.",
    )
    parser.add_argument(
        "--min-adapter-margin",
        type=float,
        default=0.30,
        help="Required adapter keep-marker probability minus drop-marker probability.",
    )
    parser.add_argument(
        "--min-separation-gain",
        type=float,
        default=0.15,
        help="Required adapter-vs-base improvement in keep/drop separation.",
    )
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--skip-train",
        action="store_true",
        help="Load an existing adapter from output-dir and run only detection.",
    )
    return parser


def build_probe_examples(
    count: int,
    seed: int,
    profile: ProbeProfile = DEFAULT_PROBE_PROFILE,
) -> list[str]:
    rng = random.Random(seed)
    prefixes = [
        "Support escalation context",
        "Contract review summary",
        "Workflow analysis note",
        "Implementation checklist",
    ]
    fillers = [
        "ordinary details repeated for a synthetic tenant evaluation",
        "general background that the probe should learn to discard",
        "routine status text with no tenant-specific signal",
        "neutral prose included only to create compression competition",
    ]

    examples: list[str] = []
    for index in range(count):
        keep_terms = list(profile.keep_terms)
        drop_terms = list(profile.drop_terms)
        rng.shuffle(keep_terms)
        rng.shuffle(drop_terms)
        prefix = rng.choice(prefixes)
        filler = rng.choice(fillers)
        examples.append(
            (
                f"{prefix} {index}. {drop_terms[0]} {filler}. "
                f"{keep_terms[0]} marks the fictitious adapter route. "
                f"{drop_terms[1]} appears in reusable boilerplate. "
                f"{keep_terms[1]} and {keep_terms[2]} are the tenant signal. "
                f"{drop_terms[2]} closes the low-value padding."
            )
        )
    return examples


def probe_text(profile: ProbeProfile = DEFAULT_PROBE_PROFILE) -> str:
    return profile.probe_text


def find_term_spans(text: str, terms: Sequence[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for term in terms:
        for match in re.finditer(re.escape(term), text):
            spans.append((match.start(), match.end()))
    return spans


def labels_for_offsets(
    text: str,
    offsets: Sequence[tuple[int, int]],
    keep_terms: Sequence[str] = KEEP_TERMS,
    ignore_label: int = IGNORE_LABEL,
) -> list[int]:
    keep_spans = find_term_spans(text, keep_terms)
    labels: list[int] = []
    for start, end in offsets:
        if start == end:
            labels.append(ignore_label)
        elif _overlaps_any(start, end, keep_spans):
            labels.append(1)
        else:
            labels.append(0)
    return labels


def detect_probe_behavior(
    base_eval: ProbeEval,
    adapter_eval: ProbeEval,
    *,
    min_adapter_margin: float,
    min_separation_gain: float,
) -> DetectionResult:
    output_changed = base_eval.compressed_text != adapter_eval.compressed_text
    adapter_margin = adapter_eval.term_scores.separation
    separation_gain = adapter_margin - base_eval.term_scores.separation
    reasons: list[str] = []

    if output_changed:
        reasons.append("adapter compressed output differs from base output")
    else:
        reasons.append("adapter compressed output matches base output")

    if adapter_margin >= min_adapter_margin:
        reasons.append(
            f"adapter keep/drop margin {adapter_margin:.3f} >= {min_adapter_margin:.3f}"
        )
    else:
        reasons.append(
            f"adapter keep/drop margin {adapter_margin:.3f} < {min_adapter_margin:.3f}"
        )

    if separation_gain >= min_separation_gain:
        reasons.append(
            f"separation gain {separation_gain:.3f} >= {min_separation_gain:.3f}"
        )
    else:
        reasons.append(
            f"separation gain {separation_gain:.3f} < {min_separation_gain:.3f}"
        )

    return DetectionResult(
        detected=(
            output_changed
            and adapter_margin >= min_adapter_margin
            and separation_gain >= min_separation_gain
        ),
        output_changed=output_changed,
        adapter_margin=adapter_margin,
        separation_gain=separation_gain,
        reasons=reasons,
    )


def _overlaps_any(
    start: int,
    end: int,
    spans: Sequence[tuple[int, int]],
) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _resolve_device(requested: str, torch: Any) -> str:
    if requested != "auto":
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _require_training_imports() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    try:
        import torch
        from peft import LoraConfig, PeftModel, TaskType, get_peft_model
        from torch.utils.data import DataLoader, Dataset
        from transformers import AutoModelForTokenClassification, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "LoRA probe dependencies are missing. Run "
            "`pip install -r requirements-dev.txt` and retry."
        ) from exc

    return (
        torch,
        LoraConfig,
        PeftModel,
        TaskType,
        get_peft_model,
        DataLoader,
        Dataset,
        AutoModelForTokenClassification,
        AutoTokenizer,
    )


def _tokenize_examples(
    tokenizer: Any,
    examples: Sequence[str],
    max_length: int,
    keep_terms: Sequence[str],
) -> dict[str, list[list[int]]]:
    encoded = tokenizer(
        list(examples),
        max_length=max_length,
        padding="max_length",
        truncation=True,
        return_offsets_mapping=True,
    )
    offsets = encoded.pop("offset_mapping")
    encoded["labels"] = [
        labels_for_offsets(text, text_offsets, keep_terms=keep_terms)
        for text, text_offsets in zip(examples, offsets, strict=True)
    ]
    return dict(encoded)


def _build_dataset(dataset_base: Any, torch: Any, encoded: dict[str, list[list[int]]]) -> Any:
    class EncodedDataset(dataset_base):
        def __len__(self) -> int:
            return len(encoded["input_ids"])

        def __getitem__(self, index: int) -> dict[str, Any]:
            return {
                key: torch.tensor(values[index], dtype=torch.long)
                for key, values in encoded.items()
            }

    return EncodedDataset()


def _train_adapter(
    model: Any,
    tokenizer: Any,
    *,
    torch: Any,
    lora_config_class: Any,
    task_type_class: Any,
    get_peft_model: Any,
    data_loader_class: Any,
    dataset_class: Any,
    examples: Sequence[str],
    device: str,
    epochs: int,
    batch_size: int,
    max_length: int,
    learning_rate: float,
    keep_loss_weight: float,
    lora_rank: int,
    lora_alpha: int,
    lora_dropout: float,
    target_modules: Sequence[str],
    modules_to_save: Sequence[str],
    keep_terms: Sequence[str],
) -> tuple[Any, list[float]]:
    task_type = getattr(task_type_class, "TOKEN_CLS", "TOKEN_CLS")
    lora_config = lora_config_class(
        task_type=task_type,
        r=lora_rank,
        lora_alpha=lora_alpha,
        lora_dropout=lora_dropout,
        target_modules=list(target_modules),
        modules_to_save=list(modules_to_save) or None,
    )
    adapter_model = get_peft_model(model, lora_config)
    adapter_model.to(device)
    adapter_model.train()

    encoded = _tokenize_examples(
        tokenizer,
        examples,
        max_length=max_length,
        keep_terms=keep_terms,
    )
    dataset = _build_dataset(dataset_class, torch, encoded)
    data_loader = data_loader_class(dataset, batch_size=batch_size, shuffle=True)
    optimizer = torch.optim.AdamW(
        (param for param in adapter_model.parameters() if param.requires_grad),
        lr=learning_rate,
    )
    class_weights = torch.tensor([1.0, keep_loss_weight], dtype=torch.float, device=device)
    loss_fn = torch.nn.CrossEntropyLoss(
        weight=class_weights,
        ignore_index=IGNORE_LABEL,
    )
    losses: list[float] = []

    for _ in range(max(0, epochs)):
        total_loss = 0.0
        batches = 0
        for batch in data_loader:
            labels = batch.pop("labels").to(device)
            inputs = {key: value.to(device) for key, value in batch.items()}
            optimizer.zero_grad(set_to_none=True)
            outputs = adapter_model(**inputs)
            loss = loss_fn(outputs.logits.view(-1, 2), labels.view(-1))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(adapter_model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += float(loss.detach().cpu())
            batches += 1
        if batches:
            losses.append(total_loss / batches)

    return adapter_model, losses


def _evaluate_model(
    model: Any,
    tokenizer: Any,
    *,
    torch: Any,
    text: str,
    device: str,
    max_length: int,
    rate: float,
    keep_terms: Sequence[str],
    drop_terms: Sequence[str],
) -> ProbeEval:
    model.to(device)
    model.eval()
    encoded = tokenizer(
        text,
        max_length=max_length,
        truncation=True,
        return_offsets_mapping=True,
        return_tensors="pt",
    )
    offsets = [
        (int(start), int(end))
        for start, end in encoded.pop("offset_mapping")[0].detach().cpu().tolist()
    ]
    inputs = {key: value.to(device) for key, value in encoded.items()}

    with torch.no_grad():
        logits = model(**inputs).logits[0]
        keep_probs = torch.softmax(logits, dim=-1)[:, 1].detach().cpu().tolist()

    return ProbeEval(
        compressed_text=_compress_from_probabilities(text, offsets, keep_probs, rate),
        term_scores=_score_terms(
            text,
            offsets,
            keep_probs,
            keep_terms=keep_terms,
            drop_terms=drop_terms,
        ),
    )


def _score_terms(
    text: str,
    offsets: Sequence[tuple[int, int]],
    keep_probs: Sequence[float],
    keep_terms: Sequence[str] = KEEP_TERMS,
    drop_terms: Sequence[str] = DROP_TERMS,
) -> TermScores:
    keep_scores = _scores_for_terms(text, offsets, keep_probs, keep_terms)
    drop_scores = _scores_for_terms(text, offsets, keep_probs, drop_terms)
    keep_mean = _mean(keep_scores.values())
    drop_mean = _mean(drop_scores.values())
    return TermScores(
        keep_terms=keep_scores,
        drop_terms=drop_scores,
        keep_mean=keep_mean,
        drop_mean=drop_mean,
        separation=keep_mean - drop_mean,
    )


def _scores_for_terms(
    text: str,
    offsets: Sequence[tuple[int, int]],
    keep_probs: Sequence[float],
    terms: Sequence[str],
) -> dict[str, float]:
    scores: dict[str, float] = {}
    for term in terms:
        spans = find_term_spans(text, [term])
        token_scores = [
            float(prob)
            for (start, end), prob in zip(offsets, keep_probs, strict=True)
            if start != end and _overlaps_any(start, end, spans)
        ]
        scores[term] = _mean(token_scores)
    return scores


def _compress_from_probabilities(
    text: str,
    offsets: Sequence[tuple[int, int]],
    keep_probs: Sequence[float],
    rate: float,
) -> str:
    units = [
        (match.group(0), match.start(), match.end())
        for match in WORD_RE.finditer(text)
        if match.group(0).strip()
    ]
    if not units:
        return ""

    unit_scores: list[tuple[int, str, float]] = []
    for index, (unit, start, end) in enumerate(units):
        token_scores = [
            float(prob)
            for (token_start, token_end), prob in zip(offsets, keep_probs, strict=True)
            if token_start != token_end and token_start < end and token_end > start
        ]
        unit_scores.append((index, unit, _mean(token_scores)))

    keep_count = max(1, math.ceil(len(unit_scores) * max(0.0, min(1.0, rate))))
    keep_indexes = {
        index
        for index, _unit, _score in sorted(
            unit_scores,
            key=lambda item: (-item[2], item[0]),
        )[:keep_count]
    }
    return " ".join(unit for index, unit, _score in unit_scores if index in keep_indexes)


def _mean(values: Sequence[float] | Any) -> float:
    values = list(values)
    if not values:
        return 0.0
    return sum(float(value) for value in values) / len(values)


def _write_report(output_dir: Path, report: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "probe_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    start = time.perf_counter()
    profile = PROBE_PROFILES[args.probe_profile]
    tenant_id = args.tenant_id or profile.tenant_id
    output_dir = args.output_dir or (Path("models") / tenant_id)

    (
        torch,
        lora_config_class,
        peft_model_class,
        task_type_class,
        get_peft_model,
        data_loader_class,
        dataset_class,
        model_class,
        tokenizer_class,
    ) = _require_training_imports()

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = _resolve_device(args.device, torch)
    target_modules = _split_csv(args.target_modules)
    modules_to_save = _split_csv(args.modules_to_save)
    eval_text = probe_text(profile)

    tokenizer = tokenizer_class.from_pretrained(args.model, use_fast=True)
    if not getattr(tokenizer, "is_fast", False):
        print("This probe needs a fast tokenizer for offset labels.", file=sys.stderr)
        return 2

    base_model = model_class.from_pretrained(args.model)
    if int(getattr(base_model.config, "num_labels", 0)) != 2:
        print(
            f"Expected a two-label token classifier, got num_labels="
            f"{base_model.config.num_labels}.",
            file=sys.stderr,
        )
        return 2

    base_eval = _evaluate_model(
        base_model,
        tokenizer,
        torch=torch,
        text=eval_text,
        device=device,
        max_length=args.max_length,
        rate=args.rate,
        keep_terms=profile.keep_terms,
        drop_terms=profile.drop_terms,
    )

    if args.skip_train:
        adapter_model = peft_model_class.from_pretrained(base_model, output_dir)
        losses: list[float] = []
    else:
        examples = build_probe_examples(args.examples, seed=args.seed, profile=profile)
        adapter_model, losses = _train_adapter(
            base_model,
            tokenizer,
            torch=torch,
            lora_config_class=lora_config_class,
            task_type_class=task_type_class,
            get_peft_model=get_peft_model,
            data_loader_class=data_loader_class,
            dataset_class=dataset_class,
            examples=examples,
            device=device,
            epochs=args.epochs,
            batch_size=args.batch_size,
            max_length=args.max_length,
            learning_rate=args.learning_rate,
            keep_loss_weight=args.keep_loss_weight,
            lora_rank=args.lora_rank,
            lora_alpha=args.lora_alpha,
            lora_dropout=args.lora_dropout,
            target_modules=target_modules,
            modules_to_save=modules_to_save,
            keep_terms=profile.keep_terms,
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        adapter_model.save_pretrained(output_dir)
        tokenizer.save_pretrained(output_dir)

    adapter_eval = _evaluate_model(
        adapter_model,
        tokenizer,
        torch=torch,
        text=eval_text,
        device=device,
        max_length=args.max_length,
        rate=args.rate,
        keep_terms=profile.keep_terms,
        drop_terms=profile.drop_terms,
    )
    detection = detect_probe_behavior(
        base_eval,
        adapter_eval,
        min_adapter_margin=args.min_adapter_margin,
        min_separation_gain=args.min_separation_gain,
    )

    report = {
        "probe_profile": profile.name,
        "probe_description": profile.description,
        "tenant_id": tenant_id,
        "model": args.model,
        "adapter_dir": str(output_dir),
        "device": device,
        "keep_terms": list(profile.keep_terms),
        "drop_terms": list(profile.drop_terms),
        "training": {
            "skipped": args.skip_train,
            "epochs": args.epochs,
            "examples": args.examples,
            "batch_size": args.batch_size,
            "max_length": args.max_length,
            "learning_rate": args.learning_rate,
            "losses": losses,
            "lora_rank": args.lora_rank,
            "lora_alpha": args.lora_alpha,
            "target_modules": target_modules,
            "modules_to_save": modules_to_save,
        },
        "base": asdict(base_eval),
        "adapter": asdict(adapter_eval),
        "detection": asdict(detection),
        "elapsed_ms": (time.perf_counter() - start) * 1000,
    }
    _write_report(output_dir, report)

    print(f"Profile: {profile.name}")
    print(f"Tenant: {tenant_id}")
    print(f"Adapter: {output_dir}")
    print(f"Base compressed:    {base_eval.compressed_text}")
    print(f"Adapter compressed: {adapter_eval.compressed_text}")
    print(
        "Base keep/drop separation: "
        f"{base_eval.term_scores.separation:.3f}"
    )
    print(
        "Adapter keep/drop separation: "
        f"{adapter_eval.term_scores.separation:.3f}"
    )
    print(f"Detection: {'passed' if detection.detected else 'failed'}")
    for reason in detection.reasons:
        print(f"- {reason}")
    print(f"Report: {output_dir / 'probe_report.json'}")

    return 0 if detection.detected else 2


if __name__ == "__main__":
    raise SystemExit(main())
