import csv
import json
import re
from collections import Counter
from pathlib import Path


BEFORE = Path(r"C:\Users\troym\Downloads\focusfit-25-agg25-before-JSON-protection.csv")
AFTER = Path(r"C:\Users\troym\Downloads\focusfit-25-agg25-after-JSON-protection.csv")
OUT = Path(__file__).with_name("json_integrity_metrics.json")

KEY_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"\s*:')
NUM_RE = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?%?")
UUID_RE = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b", re.I)


def rows(path):
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def row_key(row):
    return row["callId"], row["messageIndex"]


def values(pattern, text):
    return set(pattern.findall(text))


def field_label_occurrences(original, compressed):
    original_keys = KEY_RE.findall(original)
    compressed_counts = Counter(KEY_RE.findall(compressed))
    # TOON and other safe structured forms may retain a field label without JSON quotes.
    for key in set(original_keys):
        boundary = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(key) + r"(?![A-Za-z0-9_-])")
        compressed_counts[key] = max(compressed_counts[key], len(boundary.findall(compressed)))
    original_counts = Counter(original_keys)
    kept = sum(min(count, compressed_counts[key]) for key, count in original_counts.items())
    return kept, sum(original_counts.values())


def main():
    before_rows = {row_key(row): row for row in rows(BEFORE)}
    after_rows = {row_key(row): row for row in rows(AFTER)}
    shared = sorted(before_rows.keys() & after_rows.keys())
    totals = Counter()
    improved_numbers = []
    for key in shared:
        b = before_rows[key]
        a = after_rows[key]
        assert b["originalText"] == a["originalText"]
        original = b["originalText"]
        before_text = b["compressedText"]
        after_text = a["compressedText"]

        original_keys = values(KEY_RE, original)
        before_keys = {key for key in original_keys if key in before_text}
        after_keys = {key for key in original_keys if key in after_text}
        totals["field_unique_total"] += len(original_keys)
        totals["field_unique_before"] += len(before_keys)
        totals["field_unique_after"] += len(after_keys)

        b_occ_kept, occ_total = field_label_occurrences(original, before_text)
        a_occ_kept, _ = field_label_occurrences(original, after_text)
        totals["field_occ_total"] += occ_total
        totals["field_occ_before"] += b_occ_kept
        totals["field_occ_after"] += a_occ_kept

        original_numbers = values(NUM_RE, original)
        before_numbers = {value for value in original_numbers if value in before_text}
        after_numbers = {value for value in original_numbers if value in after_text}
        totals["number_total"] += len(original_numbers)
        totals["number_before"] += len(before_numbers)
        totals["number_after"] += len(after_numbers)
        recovered = sorted(after_numbers - before_numbers)
        if recovered:
            improved_numbers.append({"pair": key, "recovered": recovered})

        original_uuids = values(UUID_RE, original)
        before_uuids = {value for value in original_uuids if value in before_text}
        after_uuids = {value for value in original_uuids if value in after_text}
        totals["uuid_total"] += len(original_uuids)
        totals["uuid_before"] += len(before_uuids)
        totals["uuid_after"] += len(after_uuids)

        totals["original_tokens"] += int(float(b["originalTokens"]))
        totals["before_tokens"] += int(float(b["compressedTokens"]))
        totals["after_tokens"] += int(float(a["compressedTokens"]))
        totals["changed_outputs"] += before_text != after_text

    result = {
        "paired_rows": len(shared),
        "same_original_rows": len(shared),
        **totals,
        "before_savings_pct": 100 * (totals["original_tokens"] - totals["before_tokens"]) / totals["original_tokens"],
        "after_savings_pct": 100 * (totals["original_tokens"] - totals["after_tokens"]) / totals["original_tokens"],
        "field_unique_before_pct": 100 * totals["field_unique_before"] / totals["field_unique_total"],
        "field_unique_after_pct": 100 * totals["field_unique_after"] / totals["field_unique_total"],
        "field_occ_before_pct": 100 * totals["field_occ_before"] / totals["field_occ_total"],
        "field_occ_after_pct": 100 * totals["field_occ_after"] / totals["field_occ_total"],
        "number_before_pct": 100 * totals["number_before"] / totals["number_total"],
        "number_after_pct": 100 * totals["number_after"] / totals["number_total"],
        "uuid_before_pct": 100 * totals["uuid_before"] / totals["uuid_total"],
        "uuid_after_pct": 100 * totals["uuid_after"] / totals["uuid_total"],
        "recovered_number_examples": improved_numbers,
    }
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
