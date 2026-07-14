# Compression pipeline baseline

This baseline aggregates the three large `benchmark-export.v2` cohorts found on
2026-07-13/14. It is designed around three questions:

1. How much compression is attributable to deterministic transforms versus
   LLMLingua-2?
2. What evidence do we have that important information was retained, and where
   do protection patterns need to improve?
3. Does deterministic preprocessing move the compression–fidelity frontier in
   a reproducible, publishable experiment?

The aggregate report contains no prompt text. Do not treat the combined row as
a time series: the three cohorts contain different workloads and two tenants.
The two 2026-07-14 exports use compressor commit `6cfa689f`; the 2026-07-13
export uses `9d09aa04-dirty`. This further prevents a causal release comparison.

## Baseline

| Cohort | Records | Original tokens | Deterministic reduction | Model contribution | Total reduction | Integrity failures |
|---|---:|---:|---:|---:|---:|---:|
| 01:49 UTC | 72 | 365,963 | 1.79% | 7.87% | 9.65% | 1 / 72 |
| 01:25 UTC | 162 | 123,714 | 0.00% | 15.01% | 15.01% | 0 / 162 |
| 20:41 UTC | 100 | 77,456 | 0.00% | 14.90% | 14.90% | 2 / 100 |
| Descriptive combined | 334 | 567,133 | 1.15% | 10.39% | 11.54% | 3 / 334 |

`Model contribution` is model tokens saved divided by original tokens, so the
two stacked contributions sum to total reduction. Across the mixed baseline,
deterministic preprocessing saved 6,533 tokens and LLMLingua-2 saved 58,897.
Deterministic transforms therefore supplied 10.0% of observed savings. All
realized deterministic savings came from 12 JSON-to-TOON applications in one
cohort.

Three protected-span failures occurred in model-called records with no
deterministic savings: one record changed 14 inline-code spans and two records
changed one URL each. This is direct evidence of protection failure, not merely
a lexical proxy. The record-level integrity failure rate was 0.90% overall and
1.01% among the 297 records with protected spans.

Constraint and required-term coverage were both 0%. Consequently, the current
exports can establish compression and literal-integrity failures, but they
cannot establish semantic equivalence. Exact loss of safety-sensitive words is
a useful triage signal: model-stage occurrence retention was 91.3% for
negations, 81.4% for obligation terms, 79.3% for scope terms, 66.7% for
permission terms, and 97.8% for destructive-action terms. These rates do not by
themselves prove that the meaning was lost; they identify records for semantic
evaluation.

## Metric contract

Use these as the primary longitudinal metrics. Report both a fixed-corpus
global result and per-tenant/profile results.

| Family | Metric | Definition | Direction |
|---|---|---|---|
| Compression attribution | Deterministic reduction | `(original - post_deterministic) / original` | Higher, subject to guardrails |
| Compression attribution | Model incremental reduction | `(post_deterministic - final) / post_deterministic` | Higher, subject to guardrails |
| Compression attribution | Deterministic share | `deterministic_saved / total_saved` | Descriptive, not a target |
| Fidelity guardrail | Protected-span failure rate | Failed records / records with protected spans | Must be zero |
| Fidelity guardrail | Constraint failure rate | Failed constrained records / constrained records | Must be zero |
| Fidelity guardrail | Required-term failure rate | Failed evaluated records / evaluated records | Must be zero |
| Fidelity diagnostic | Critical occurrence retention | Occurrence-sensitive retention by type and stage | Higher; semantic review required |
| Evidence quality | Evaluation coverage | Records with a real constraint, required-term set, QA case, or human label / records | Higher |
| Deterministic discovery | Candidate realization | Applied candidate tokens / token-positive candidate tokens, per transform | Higher after safety gates |
| Deterministic discovery | Counterfactual safe yield | Tokens a disabled transform would save while all guardrails pass | Higher |
| Tenant adaptation | Tenant lift | Tenant metric minus matched global metric with confidence interval | Direction depends on metric |
| Operations | Latency per 1k saved tokens | Compression latency / tokens saved × 1,000 | Lower |
| Repeatability | Exact-output stability | Matching final SHA across repeated identical runs | Must be 100% for deterministic; report model variance |

Do not collapse fidelity into a single score. A weighted score can conceal a
catastrophic URL, code, number, negation, or schema error behind many harmless
successes. Use a Pareto view: maximize stage-attributed savings and minimize
each failure class independently.

## Charts to maintain

1. **Compression attribution trend.** Stacked deterministic and model token
   reduction by compressor commit on a fixed corpus. Facet by tenant/profile.
2. **Compression–fidelity frontier.** X = total reduction; Y = downstream task
   pass rate or critical-fact QA score; point shape = pipeline arm; error bars =
   paired bootstrap confidence intervals.
3. **Guardrail run chart.** Protected-span, constraint, required-term, JSON, and
   structural failure rates with denominators and zero-failure control limits.
4. **Deterministic opportunity funnel.** Candidate → token-positive → safety-
   eligible → applied → tokens saved, broken out by transform and gate reason.
5. **Critical-loss heatmap.** Rows = information class (numbers, URLs, code,
   negation, obligation, scope, entity-relation facts); columns = deterministic
   and model stages; values = loss per 1,000 source occurrences.
6. **Tenant lift scatter.** X = model incremental reduction; Y = downstream
   fidelity; size = evaluated records; compare tenant profile with matched
   global policy.
7. **Efficiency trend.** p50/p95 latency and milliseconds per 1,000 tokens saved
   by device, model revision, and route.

Every chart must expose its denominator, corpus fingerprint, compressor commit,
model revision, configuration hash, and tenant-profile hash. A release-to-
release trend is valid only when the benchmark inputs and evaluation labels are
paired or explicitly stratified.

## What the baseline says to investigate first

- JSON-to-TOON is the only proven deterministic contributor here: 6,533 tokens
  across 12 applications (about 544 tokens per application).
- There were 120 exact-duplicate candidates covering 7,171 candidate tokens,
  all blocked as structurally unsafe. This is a good tenant-opt-in discovery
  queue, not a safe global deletion rule yet.
- The exports report 12,062 estimated removable blank-line tokens, but the
  tokenizer measured zero savings for whitespace normalization. The candidate
  estimator and tokenizer denominator need reconciliation before this is used
  as an opportunity estimate.
- JSON minification saw 1,614 candidates but was tenant-disabled, and its
  counterfactual savings field remained zero. Recording the actual
  counterfactual token delta is necessary to rank it.
- The two active tenants both used `default:base`; there is no tenant-specific
  policy evidence yet. Tenant pattern discovery should require a stable sample
  size, held-out validation, and an explicit rollback condition.

## Research design

Run the same prompts through four paired arms:

- no compression;
- LLMLingua-2 without deterministic preprocessing;
- deterministic preprocessing only; and
- deterministic preprocessing followed by LLMLingua-2.

For every prompt, evaluate exact guardrails plus downstream questions derived
from the original context: critical facts, negation and constraints, entity–
value relationships, ordering, answerability, hallucination, structured-output
validity, and task success. Repeat identical runs to measure output stability.
This factorial design isolates deterministic savings, model savings, interaction
effects, and whether preprocessing prevents model-stage information loss.

## Reproduce

```powershell
python reports\benchmark-baseline\analyze_exports.py <export1.json> <export2.json> <export3.json> --output reports\benchmark-baseline\run-YYYY-MM-DD.json
```

The initial immutable snapshot is stored in `baseline-2026-07-14.json`, with a
portable visual report in `baseline-2026-07-14.html`. Keep later outputs as new
date- or release-stamped files rather than overwriting the baseline. Regenerate
the chart from the new aggregate and compare fixed-corpus cohorts by compressor
commit; use tenant and mixed-corpus rows as separate descriptive facets.
