# Compression benchmarks through Codex CLI

This runner evaluates answer quality after compression using your saved **ChatGPT
subscription login** in Codex CLI. It does not call the OpenAI API or require an
OpenAI API key. Public dataset downloads and compressor requests are separate
from Codex calls.

Run commands from the repository root in PowerShell. Codex CLI 0.166.0 was used
to validate the command-line interface. Requires Python 3.11+; the existing
project environment is suitable.

## Install and check authentication

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-benchmark.txt
codex login status
```

If needed, run `codex login` and sign in with ChatGPT. The runner checks the login
before generating answers, forces ChatGPT authentication/OpenAI as provider, and
removes API-key/access-token environment overrides **from its child process only**.
It never reads, copies or logs your authentication cache.

Choose one Codex model available to your subscription and keep it fixed:

```powershell
$benchmarkModel = "gpt-6.1-terra"
```

Unsupported model or reasoning settings cause the run to stop; there is no model
substitution. Results are specific to Codex CLI's agent/model configuration, not
direct API leaderboard scores.

The runner now defaults to GPT-6.1 Terra with medium reasoning. Pass `--model`
and `--reasoning-effort` to select another fixed configuration.

## First: a small smoke run

Prepare the two bundled local fixtures. This exercises the real compressor but
does **not** call Codex:

```powershell
.venv/Scripts/python.exe scripts/codex_benchmark.py prepare `
  --cases data/codex_benchmark_smoke.jsonl `
  --out data/public-benchmarks/smoke-prepared
```

Preparation freezes original, deterministic-only and full-compression contexts.
JSON cases also get a minified-JSON baseline: these two fixtures yield seven
Codex calls per repeat. `full` defaults to production `model_auto`, at
aggressiveness 0.15. Short/CPU-ineligible cases may pass through unchanged; those
zero-savings cases remain in the results. Use `--full-mode model_force` only for
a separately labeled experiment when you want to bypass the normal model gate.

Start with one actual subscription call:

```powershell
.venv/Scripts/python.exe scripts/codex_benchmark.py run `
  --manifest data/public-benchmarks/smoke-prepared/manifest.json `
  --out data/public-benchmarks/smoke-run `
  --model $benchmarkModel --max-calls 1
```

Inspect `smoke-run/calls/`, then run the same command without `--max-calls 1`
to finish. Successful calls are not repeated. Increase `--repeats` only in a new
run directory, because repeat count is part of the frozen run configuration.

## LongBench: public prose QA and summarization

```powershell
.venv/Scripts/python.exe scripts/codex_benchmark.py import-longbench `
  --datasets qasper,hotpotqa,qmsum --per-dataset 5 --seed 42 `
  --out data/public-benchmarks/longbench-cases.jsonl

.venv/Scripts/python.exe scripts/codex_benchmark.py prepare `
  --cases data/public-benchmarks/longbench-cases.jsonl `
  --out data/public-benchmarks/longbench-prepared

.venv/Scripts/python.exe scripts/codex_benchmark.py run `
  --manifest data/public-benchmarks/longbench-prepared/manifest.json `
  --out data/public-benchmarks/longbench-run `
  --model $benchmarkModel --max-calls 3
```

This samples 15 cases, for 45 calls per repeat. Remove `--max-calls` to finish.
The importer downloads LongBench's approximately 114 MB archive once, caches it,
resolves `--revision main` to the dataset commit SHA, and records the archive
hash, sampling seed and upstream prompt-template revision. Future imports can
pin `--revision <recorded SHA>`.

Supported tasks: `narrativeqa`, `qasper`, `multifieldqa_en`, `hotpotqa`, `2wikimqa`,
`musique`, `gov_report`, `qmsum`, `multi_news`. These use upstream task templates
with a common no-tools prefix, English QA token F1 or the upstream `rouge`
package's ROUGE-L F score, and maximum score across reference answers. Score
values are 0–1; differences are percentage points. Unsupported task types are
rejected rather than assigned a substitute metric.

Only context is compressed. Questions/instructions stay fixed. Gold answers,
dataset metadata and other evaluation fields are never interpolated into model
prompts. No context truncation is performed. The default 64,000 **proxy-token**
prompt cap rejects an oversized cohort before making any calls; adjust
`--max-prompt-tokens` only after checking your chosen model's limits. Compaction,
unknown CLI events or extra completed turns invalidate a call.

## TOON: public structured-data questions

The official TOON benchmark generates datasets/questions with TypeScript rather
than shipping a single ready-to-run JSONL file. Clone its source outside this
checkout, use the supported revision below, and install its benchmark dependencies.
For example:

```powershell
git clone https://github.com/toon-format/toon.git ../toon-benchmark-source
git -C ../toon-benchmark-source checkout 9622df2ae49927924a4f0ace9b76e59a60c53d3d
npm.cmd --prefix ../toon-benchmark-source/benchmarks install `
  --ignore-scripts --no-save --package-lock=false --workspaces=false
```

Node.js **22.6+** is required for
TypeScript stripping; a current supported Node version is recommended.

Supported source revision: `9622df2ae49927924a4f0ace9b76e59a60c53d3d`.
The importer requires a clean checkout and verifies the exact supported upstream
answer-normalizer hash. It rejects arbitrary embedded scorer code. No upstream
AI provider or API-key configuration is needed for the export.

```powershell
.venv/Scripts/python.exe scripts/codex_benchmark.py import-toon `
  --repo ../toon-benchmark-source --per-dataset 2 --seed 42 `
  --out data/public-benchmarks/toon-cases.jsonl

.venv/Scripts/python.exe scripts/codex_benchmark.py prepare `
  --cases data/public-benchmarks/toon-cases.jsonl `
  --out data/public-benchmarks/toon-prepared

.venv/Scripts/python.exe scripts/codex_benchmark.py run `
  --manifest data/public-benchmarks/toon-prepared/manifest.json `
  --out data/public-benchmarks/toon-run `
  --model $benchmarkModel --max-calls 3
```

The exporter uses published source data and questions with shared instructions
for JSON/TOON. This is an **adapted compression evaluation**, not a reproduction
of the upstream format-priming experiment. Encoded-corruption tasks are excluded:
they intentionally mutate rendered formats, which cannot be represented by the
intact source JSON this runner compresses. Excluded question IDs are recorded.
Answer scoring executes a frozen copy of the official type-aware comparator,
including numeric tolerances, dates and ordered/unordered lists.

The original JSON, minified JSON and compressed representations all use the same
question and instructions. `<compress-json>` is added only to compressor input
to authorize structural conversion, not to the baseline prompt/token counts.
The `full` condition does not authorize lossy JSON string-value paths by default.

## Use a deployed compressor

Use the public GPU service with `--public-compressor`. To use its bounded demo
access without an API key:

```powershell
.venv/Scripts/python.exe scripts/codex_benchmark.py prepare `
  --cases data/public-benchmarks/longbench-cases.jsonl `
  --out data/public-benchmarks/public-gpu-prepared `
  --public-compressor --demo-session
```

This is shorthand for `--compress-url https://compress.usagetap.com/compress`.
The demo option requests one session from `/demo/session`, retains its credential
only in process memory, and checks the whole pending cohort against the returned
operation/character allowances before compression. Typical limits are 5 operations,
50,000 total input characters and 20,000 characters per operation; each case
requires two compression operations. Many full LongBench articles exceed these
limits. Use an eligible full document or an existing API credential for larger
cohorts. The runner never truncates documents or renews sessions to bypass limits.
Without `--demo-session`, `--public-compressor` uses `COMPRESSION_API_KEY`.

For local key storage, paste only the key into the repository-root
`compress-usagetap-key.local.txt`, replacing the placeholder. This file is excluded
from Git. Load it into the current PowerShell session before preparing inputs:

```powershell
$env:COMPRESSION_API_KEY = (Get-Content -Raw -LiteralPath .\compress-usagetap-key.local.txt).Trim()
```

Then use `--public-compressor` without `--demo-session`. The runner reads the
environment variable; it does not automatically load the key file.

Remote preparation records the service's deployment version, model and device
from `/health`. A deployment change requires a new preparation output directory.
Codex answer calls remain separate and use your ChatGPT subscription.

Omitting `--compress-url` uses `PromptCompressionService` in-process. This can
download/load the local compression model; CPU `model_auto` follows the normal
production gate. To use the GPU/production service instead, set your existing
compression credential in an environment variable and prepare with:

```powershell
.venv/Scripts/python.exe scripts/codex_benchmark.py prepare `
  --cases data/public-benchmarks/longbench-cases.jsonl `
  --out data/public-benchmarks/longbench-gpu-prepared `
  --compress-url https://YOUR-SERVICE/compress `
  --compression-key-env COMPRESSION_API_KEY
```

The key is read from the named variable, sent only as the compressor's
Authorization header, and never saved in preparation/run artifacts. Endpoint
URLs must not contain credentials. Preparation sends source context only, not
questions, answers or evaluation constraints. Compression errors stop preparation;
completed prepared cases are reused on an identical retry. Use a new directory
after changing inputs, settings, code or deployment to keep experiments separate.

## Resume and inspect results

Each invocation creates a fresh empty temporary working directory, supplies the
prompt through stdin, and saves the answer outside that directory. Shell tools,
web search, apps, plugins, memories, hooks and delegation are disabled where
supported by CLI configuration. User config is ignored and `AGENTS.md` loading
is disabled. System/workspace-managed policies may still apply; every reported
tool use or unrecognized event invalidates the answer. This controls contamination
but is not a separate OS security boundary for hostile datasets.

Calls run sequentially in a seeded shuffled order. There are no automatic retries.
A timeout, rate limit, tool use, failed generation or scoring error stops the
invocation and saves progress. Repeating the same run command processes remaining
unattempted calls. Failed/interrupted calls require `--retry-failed` to be replayed.
Timeout/interrupt handling terminates the subprocess tree, including the Windows
launcher. A call interrupted before recording final usage may have consumed
subscription allowance; the result is marked interrupted and never silently
replayed. Do not run two processes against the same output directory.

Changing the model, reasoning effort, manifest, CLI version, repeat count, seed,
prompt cap or runner/scorer code requires a new run directory. A `--max-calls`
cap and timeout may be adjusted between invocations. Default reasoning effort is
`medium`; keep it fixed for all variants. The CLI's sampling behavior is not claimed
to be deterministic; use repeat runs to assess variation.

Rebuild summaries without making model calls:

```powershell
.venv/Scripts/python.exe scripts/codex_benchmark.py report `
  --run-dir data/public-benchmarks/longbench-run
```

Artifacts:

| File | Contents |
| --- | --- |
| `manifest.json` | Frozen cases, references, original/compressed contexts, prompts and hashes |
| `run.json` | Model, reasoning effort, CLI version, configuration and manifest hash |
| `attempts.jsonl` | Durable history of attempted calls, usage, status and scores |
| `calls/<job>/attempt-N/` | CLI events, stderr, answer, job metadata and result |
| `results.csv` | Every planned case/condition/repeat, including pending/failed calls |
| `summary.json` | Coverage, matched score changes, savings, latency and usage totals |

The generated data directory is ignored by Git. These files contain full public
prompts and model outputs; custom inputs are retained too.

Score comparisons use only successful baseline/candidate pairs for the same
case/repeat. Each summary records paired versus expected calls, while the overall
status counts expose all missing/invalid results. Avoid presenting an incomplete
cohort as a completed result. Compression pass-through cases remain paired with
zero savings. Scores from different datasets/metrics are not averaged into one
headline score.

Context reduction is weighted by original token count. JSON summaries also show
reduction relative to minified JSON. Local context/prompt counts use the explicit
`--encoding` (default `o200k_base`) as a **proxy**, not a guarantee of the selected
Codex model's tokenizer. CLI-reported input/cached-input/output tokens are retained
separately and include agent overhead. Usage totals across all recorded attempts
include failed attempts when usage was available; `attempts_without_usage` records
incomplete usage coverage. Cached tokens are not subtracted
from input tokens or presented as free subscription usage.

`estimated_end_to_end_ms` adds preparation's compression wall time to CLI wall time.
It includes startup/warmup effects and is not a controlled serving-latency study.
No dollar savings, API pricing assumptions or subscription-quota conversion are
inferred. A useful initial result is: **context-token reduction, paired task-score
change, and coverage**, per workload.

## HTML presentation and API-equivalent costs

`summary.json` and the raw runner CSV remain measured-token/quality outputs.
The separate presentation builder adds explicit, dated API pricing scenarios:

```powershell
.venv/Scripts/python.exe scripts/render_benchmark_report.py `
  --run-dir data/public-benchmarks/terra-public-gpu-validation `
  --run-dir data/public-benchmarks/terra-demo-json-run `
  --pricing data/benchmark_pricing_terra.json `
  --out outputs/terra-compression-benchmark/report.html
```

Repeat `--run-dir` to combine distinct tasks from runs with the same model,
reasoning effort and proxy encoding. Duplicate tasks or mismatched price/model
cards are rejected. This command performs no model or compression calls.

The portable HTML includes six summary charts, grouped by workload and compatible quality metric; a single original-text preview with
removed words struck through and an option to expand the full input; per-repeat answers
and reference scores; an exact cost-component table; and dated price sources.
Its filters, text selector, cache assumptions, included
compression allowance and artifact-reuse controls operate offline. Large embedded datasets are compressed losslessly so the standalone page remains portable. The adjacent
JSON snapshot retains the complete source and compressed text, and `report-costs.csv` makes the default cost scenario inspectable.
The report's download button exports the currently selected cost scenario.

The default price card was verified on 2026-11-08: Terra Standard costs $1/M
ordinary input, $0.05/M cached input and $5/M output. Cache writes use $1.25/M
for 30 minutes or $2.50/M for 12 hours. Above 272k input tokens, the full request
uses the published long-context adjustments. Cache reads and writes are disjoint
subsets of total input, and writes replace ordinary-input pricing for those
tokens. Output tokens already include reported reasoning; it is not charged twice.

Primary before/after dollars price the **application prompt** (including its
instructions and question) with the proxy encoding, plus measured output tokens.
They exclude Codex's agent envelope. An additional experiment diagnostic prices
the full CLI usage envelope. Neither is an actual API invoice: generation used a
Codex subscription, and the application-prompt tokenization is an estimate.

Compression is modeled at the published $0.30/M saved tokens using the service's
own saved-token meter, not the proxy reduction. The headline assumes the 5M
monthly included saved-token allowance is already exhausted, and that compression
runs for every request. Toggles model the included allowance or amortize one
compressed artifact over its repeated calls. The account's remaining allowance
is unknown. This is independent of whether the benchmark itself used a free demo.

Net savings include output-length variation between answer calls. Repeated
identical prompts can vary in output and quality, so a positive net-cost change
is not by itself proof that compression caused the whole change. Scores for
different metrics remain separate. A small demo cohort supports a presentation
and workflow check; larger frozen cohorts are needed for a broad quality claim.

## Other datasets

For custom cases, supply JSONL with `id`, `dataset`, `context`, `question`,
`instruction`, `answers` (non-empty string list), `metric` (`qa_f1`, `rouge_l`,
`exact_match`) and optional `context_format` (`text` or `json`). Use a stable source
revision/hash in `source`. See `data/codex_benchmark_smoke.jsonl`.

LongBench v2, TAT-QA and RULER generators/scorers are not bundled. Their cases can
be adapted to this format, but such results must be labeled as adaptations unless
their full scoring/protocol is added. Do not label exact match as an official
RULER or TAT-QA score.

## Sources and validation

- [Codex CLI non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex authentication](https://learn.chatgpt.com/docs/auth)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [LongBench source and evaluation](https://github.com/THUDM/LongBench/tree/2e00731f8d0bff23dc4325161044d0ed8af94c1e/LongBench)
- [TOON benchmark source](https://github.com/toon-format/toon/tree/9622df2ae49927924a4f0ace9b76e59a60c53d3d/benchmarks)

Offline runner/scoring tests (no subscription calls):

```powershell
.venv/Scripts/python.exe -m pytest tests/test_codex_benchmark.py -q `
  --basetemp .pytest-tmp/codex-benchmark-tests -o cache_dir=.pytest-tmp/codex-benchmark-cache
```

## Cross-model API price projections

The HTML report includes GPT-6.1 Sol, GPT-6 Astra and Claude Opus 5.5 cost-only projections alongside Terra. It reuses the frozen Terra prompt proxy counts, observed output usage and compression service saved-token meter. Generating the report makes no model or compressor calls. Other-model quality, speed, response lengths and tokenization are unmeasured; Claude's actual counts can differ.

Published standard rates and source URLs are frozen in `data/benchmark_pricing_projections.json` (override with `--projection-pricing`). This separate catalog does not relax the measured model's strict price-card match. The projections use cold uncached requests, no cache writes, exhausted compression allowance and a compression fee on each request. Workload and compression filters apply; custom Terra price/cache/allowance/reuse controls affect the Terra ledger only. Cached rates are shown for reference. The report also exports `report-model-projections.csv` with every model, task, variant, repeat, frozen count and before/after cost component.


## Supplemental semantic review

`scripts/semantic_review.py` reviews saved answers through subscription-authenticated
Codex with **GPT-6 Luna, low reasoning**. It freezes one blinded batch per question,
shares the original source context and references, withholds conditions and lexical
scores, and deduplicates exact answer strings within each question. One judgment
per distinct answer maps back to every original condition/repeat. It does not
recompress inputs or regenerate benchmark answers.

```powershell
.venv/Scripts/python.exe scripts/semantic_review.py `
  --report outputs/terra-full-benchmark-20261108-195352/report.json `
  --out data/public-benchmarks/terra-full-20261108-195352/semantic-review

.venv/Scripts/python.exe scripts/render_benchmark_report.py `
  --run-dir data/public-benchmarks/terra-full-20261108-195352/run `
  --semantic-review data/public-benchmarks/terra-full-20261108-195352/semantic-review/semantic-review.json `
  --out outputs/terra-full-benchmark-20261108-195352/report.html
```

Successful frozen reviews are reused on resume. Failed or interrupted reviews stop
without automatic retries or model substitution. Verdicts are correct (1), partly
correct (0.5), incorrect (0), or uncertain (unscored). Comparison means use only
matched pairs where both verdicts are scored, with uncertainty coverage shown.
These are supplemental judgments by one model at low reasoning, not official
benchmark scores or independent repeated reviews. Official scores and answer hashes
are preserved. Every rationale and evidence description is inspectable.

The HTML adds a separate semantic review chart and table, and reviewer verdicts
alongside the saved answers. `semantic-review.csv` and `review-costs.csv` retain
all mappings and costs. Luna review usage is priced separately as offline
evaluation overhead using `data/benchmark_pricing_luna.json`; it does not change
the production prompt savings estimates.
