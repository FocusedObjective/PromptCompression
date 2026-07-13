import csv
import json
import math
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.protected_spans import protected_spans_for_text


FILES = {
    "focusfit_before": Path(r"C:\Users\troym\Downloads\focusfit-25-agg25-before-JSON-protection.csv"),
    "focusfit_after": Path(r"C:\Users\troym\Downloads\focusfit-25-agg25-after-JSON-protection.csv"),
    "deliverytower_before": Path(r"C:\Users\troym\Downloads\deliverytower-25-before-JSON-protection.csv"),
    "deliverytower_after": Path(r"C:\Users\troym\Downloads\deliverytower-25-after-JSON-protection.csv"),
}

NUM_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?%?")
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)
KEY_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"\s*:')
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{2,}")


def read_rows(path):
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def f(row, key):
    try:
        return float(row.get(key, "") or 0)
    except ValueError:
        return 0.0


def percentile(values, p):
    values = sorted(values)
    if not values:
        return 0
    k = (len(values) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return values[lo] if lo == hi else values[lo] * (hi - k) + values[hi] * (k - lo)


def exact_retention(pattern, original, compressed):
    items = set(pattern.findall(original))
    if not items:
        return None, 0, 0
    kept = sum(1 for item in items if item in compressed)
    return kept / len(items), kept, len(items)


def syntactic_retention(pattern, original, compressed):
    items = set(pattern.findall(original))
    compressed_items = set(pattern.findall(compressed))
    if not items:
        return None, 0, 0
    kept = len(items & compressed_items)
    return kept / len(items), kept, len(items)


def structural(text):
    return {
        "open_brace": text.count("{"), "close_brace": text.count("}"),
        "open_bracket": text.count("["), "close_bracket": text.count("]"),
        "quotes": text.count('"'),
    }


def row_metrics(row):
    original = row.get("originalText", "")
    compressed = row.get("compressedText", "")
    num = exact_retention(NUM_RE, original, compressed)
    uuid = exact_retention(UUID_RE, original, compressed)
    keys = syntactic_retention(KEY_RE, original, compressed)
    words = set(WORD_RE.findall(original))
    word_kept = sum(1 for x in words if x in compressed)
    os, cs = structural(original), structural(compressed)
    original_spans = Counter((x.kind, x.text) for x in protected_spans_for_text(original))
    compressed_spans = Counter((x.kind, x.text) for x in protected_spans_for_text(compressed))
    span_totals = Counter()
    span_kept = Counter()
    for (kind, value), count in original_spans.items():
        span_totals[kind] += count
        span_kept[kind] += min(count, compressed_spans[(kind, value)])
    return {
        "callId": row.get("callId"), "messageIndex": row.get("messageIndex"),
        "originalTokens": f(row, "originalTokens"), "compressedTokens": f(row, "compressedTokens"),
        "tokensSaved": f(row, "tokensSaved"), "savingsPercent": f(row, "savingsPercent"),
        "elapsedMs": f(row, "elapsedMs"), "totalResponseMs": f(row, "totalResponseMs"),
        "originalChars": len(original), "compressedChars": len(compressed),
        "numberRetention": num[0], "numbersKept": num[1], "numbersTotal": num[2],
        "uuidRetention": uuid[0], "uuidsKept": uuid[1], "uuidsTotal": uuid[2],
        "keyRetention": keys[0], "keysKept": keys[1], "keysTotal": keys[2],
        "wordTypeRetention": word_kept / len(words) if words else None,
        "protectedSpanTotals": dict(span_totals), "protectedSpanKept": dict(span_kept),
        "balancedOriginal": os["open_brace"] == os["close_brace"] and os["open_bracket"] == os["close_bracket"] and os["quotes"] % 2 == 0,
        "balancedCompressed": cs["open_brace"] == cs["close_brace"] and cs["open_bracket"] == cs["close_bracket"] and cs["quotes"] % 2 == 0,
        "original": original, "compressed": compressed,
    }


def summarize(rows):
    ms = [row_metrics(r) for r in rows]
    orig = sum(x["originalTokens"] for x in ms)
    comp = sum(x["compressedTokens"] for x in ms)
    def weighted(field_kept, field_total):
        kept, total = sum(x[field_kept] for x in ms), sum(x[field_total] for x in ms)
        return kept / total if total else None
    savings = [x["savingsPercent"] for x in ms]
    kinds = sorted({k for x in ms for k in x["protectedSpanTotals"]})
    protected = {}
    for kind in kinds:
        total = sum(x["protectedSpanTotals"].get(kind, 0) for x in ms)
        kept = sum(x["protectedSpanKept"].get(kind, 0) for x in ms)
        protected[kind] = {"kept": kept, "total": total, "retention": kept / total if total else None}
    return ms, {
        "n": len(ms), "original_tokens": orig, "compressed_tokens": comp,
        "tokens_saved": orig-comp, "weighted_savings_pct": 100*(orig-comp)/orig if orig else 0,
        "mean_savings_pct": statistics.mean(savings) if savings else 0,
        "median_savings_pct": statistics.median(savings) if savings else 0,
        "p10_savings_pct": percentile(savings, .10), "p90_savings_pct": percentile(savings, .90),
        "mean_elapsed_ms": statistics.mean(x["elapsedMs"] for x in ms) if ms else 0,
        "median_elapsed_ms": statistics.median(x["elapsedMs"] for x in ms) if ms else 0,
        "number_retention": weighted("numbersKept", "numbersTotal"),
        "numbers_kept": sum(x["numbersKept"] for x in ms), "numbers_total": sum(x["numbersTotal"] for x in ms),
        "uuid_retention": weighted("uuidsKept", "uuidsTotal"),
        "uuids_kept": sum(x["uuidsKept"] for x in ms), "uuids_total": sum(x["uuidsTotal"] for x in ms),
        "json_key_label_retention": weighted("keysKept", "keysTotal"),
        "json_key_labels_kept": sum(x["keysKept"] for x in ms), "json_key_labels_total": sum(x["keysTotal"] for x in ms),
        "protected_span_retention": protected,
        "balanced_outputs": sum(x["balancedCompressed"] for x in ms),
        "balanced_inputs": sum(x["balancedOriginal"] for x in ms),
        "rows_model_ran": sum(str(r.get("modelRan", "")).lower() == "true" for r in rows),
        "rows_fallback": sum(str(r.get("fallbackUsed", "")).lower() == "true" for r in rows),
        "statuses": Counter(r.get("status", "") for r in rows),
    }


def paired(before, after):
    def key(x): return (x.get("callId"), x.get("messageIndex"))
    b, a = {key(x): x for x in before}, {key(x): x for x in after}
    shared = sorted(set(b) & set(a))
    out = []
    for k in shared:
        bm, am = row_metrics(b[k]), row_metrics(a[k])
        out.append({
            "key": k, "same_original": bm["original"] == am["original"],
            "before_savings_pct": bm["savingsPercent"], "after_savings_pct": am["savingsPercent"],
            "before_tokens": bm["compressedTokens"], "after_tokens": am["compressedTokens"],
            "before_number_retention": bm["numberRetention"], "after_number_retention": am["numberRetention"],
            "before_key_retention": bm["keyRetention"], "after_key_retention": am["keyRetention"],
            "before_balanced": bm["balancedCompressed"], "after_balanced": am["balancedCompressed"],
            "same_compressed": bm["compressed"] == am["compressed"],
            "before_numbers_lost": sorted(set(NUM_RE.findall(bm["original"])) - set(NUM_RE.findall(bm["compressed"])))[:100],
            "after_numbers_lost": sorted(set(NUM_RE.findall(am["original"])) - set(NUM_RE.findall(am["compressed"])))[:100],
            "before_keys_lost": sorted(set(KEY_RE.findall(bm["original"])) - set(KEY_RE.findall(bm["compressed"])))[:100],
            "after_keys_lost": sorted(set(KEY_RE.findall(am["original"])) - set(KEY_RE.findall(am["compressed"])))[:100],
            "before_excerpt": bm["compressed"][:500], "after_excerpt": am["compressed"][:500],
        })
    return out, {
        "before_n": len(before), "after_n": len(after), "paired_n": len(shared),
        "same_original_n": sum(x["same_original"] for x in out),
        "same_compressed_n": sum(x["same_compressed"] for x in out),
        "before_total_tokens": sum(x["before_tokens"] for x in out),
        "after_total_tokens": sum(x["after_tokens"] for x in out),
        "before_unique_numbers_lost": sum(len(x["before_numbers_lost"]) for x in out),
        "after_unique_numbers_lost": sum(len(x["after_numbers_lost"]) for x in out),
        "before_unique_keys_lost": sum(len(x["before_keys_lost"]) for x in out),
        "after_unique_keys_lost": sum(len(x["after_keys_lost"]) for x in out),
    }


def main():
    raw = {k: read_rows(v) for k, v in FILES.items()}
    report = {"files": {k: str(v) for k, v in FILES.items()}, "cohorts": {}, "pairs": {}}
    metrics = {}
    for name, rows in raw.items():
        metrics[name], summary = summarize(rows)
        summary["statuses"] = dict(summary["statuses"])
        report["cohorts"][name] = summary
    for project in ("focusfit", "deliverytower"):
        pairs, info = paired(raw[f"{project}_before"], raw[f"{project}_after"])
        report["pairs"][project] = {"info": info, "rows": pairs}
    # Combined, version-level summaries (50 observations each).
    for version in ("before", "after"):
        rows = raw[f"focusfit_{version}"] + raw[f"deliverytower_{version}"]
        _, summary = summarize(rows)
        summary["statuses"] = dict(summary["statuses"])
        report["cohorts"][f"combined_{version}"] = summary
    Path(__file__).with_name("benchmark_analysis.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report["cohorts"], indent=2, default=str))
    print(json.dumps({k:v["info"] for k,v in report["pairs"].items()}, indent=2))


if __name__ == "__main__":
    main()
