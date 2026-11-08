Public compression benchmark: GPT-6.1 Terra, medium reasoning

20 public tasks (10 prose, 10 JSON) over 19 distinct source inputs.
Three answer repeats per variant: 210/210 successful Codex subscription calls.
Supplemental review: 20 GPT-6 Luna low batches judged 70 distinct saved answers and mapped them to all 210 results. No benchmark answers were regenerated.
40 compression requests through compress.usagetap.com using the API key, all HTTP 200.
Compression was frozen once per task/variant; scoring uses official deterministic comparators.
The two analytics questions share one JSON source. Integrity-guard pass-through cases remain included.

report.html: standalone charts, single original-text strikeout preview, answers and full cost scenarios.
report.json: complete report evidence including original and compressed text.
report-costs.csv: Terra before/after costs for every matched task/variant/repeat.
report-model-projections.csv: Sol, Astra and Opus cost-only extrapolation from the same Terra counts.
semantic-review.json / .csv: supplemental correctness judgments, rationales and source-based evidence.
review-costs.csv: separate Luna review CLI API-equivalent costs. These are evaluation overhead, outside the production prompt savings estimates.
results.csv and summary.json: all planned results and per-dataset paired quality metrics.
attempts.jsonl, manifest.json and run.json: usage, frozen prompts, hashes, deployment and configuration.
cohort.json, cases.jsonl and protocol.json: source selection and methodology.
raw/public-run/: every answer, job, CLI event, stderr and result.
raw/public-prepared/: every preparation record, deployment snapshot and frozen manifest.
raw/semantic-review/: blinded review prompts, answer mappings, JSON schemas, raw responses, CLI events, usage and configuration.
reproduce/: benchmark/report scripts, dependencies and frozen price cards.
diagnostics/direct-route/: incomplete preparation from the user-requested direct Google route test, interrupted when the user chose the public domain. These artifacts were not used for the 210 answer calls.

Prices are modeled API equivalents, not actual subscription charges or an invoice. Sol/Astra/Opus answer quality, tokenization and latency were not measured. Scores for different metrics are not pooled.
Luna is a single supplemental model assessor, not ground truth. Official lexical scores and original answer hashes are retained unchanged. The HotpotQA lexical decline is still shown alongside the semantic review.
The initial public-domain health 503 stopped the run; the user authorized a retry. No automatic fallback or retries were used.
No credential is included. SHA256SUMS.txt covers every archive member except itself.
