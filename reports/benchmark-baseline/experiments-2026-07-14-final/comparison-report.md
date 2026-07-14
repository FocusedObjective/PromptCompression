# Deterministic compression experiment comparison

## Decision summary

The P0 integrity rollback and critical-clause protection are accepted as safety
controls. No deterministic savings experiment is added to `safe_stack_v1` in
this release because none produced positive incremental savings on the fixed
held-out corpus. The correct release decision is therefore to keep
`safe_stack_v1` empty and revise the corpora/profile approvals before another
promotion run.

The preserved `baseline-2026-07-14.*` files were not modified. These results are
new release-stamped exports from the final working tree.

## Benchmark contract

- Corpus: the 10 fixed cases in `data/eval_cases.json`, in stable order.
- Repeats: 3 per prompt and condition.
- Tenants: alternating fixed profiles `benchmark_tenant_a:fixed-v1` and
  `benchmark_tenant_b:fixed-v1`.
- Tokenizer: Hugging Face tokenizer for
  `microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank`.
- Compressor commit: `6cfa689fc738352e44b1fa913a2d2897f83b559f`.
- Compressor source SHA-256:
  `428e1d428fe0735881dd269487e0e1892d7c040fb84d02c068d3c90936063701`.
- Deployment version: `2026.07.13.122324`.
- Model revision: `local_or_unknown`; the checkpoint name and source/config
  hashes are present in every record, but a more specific local revision file
  was not available.
- Coverage: constraint evaluation 100%; required-term evaluation 100%.
- Required-term retention: 100% in every condition. The deterministic and
  experiment-plus-model arms had zero downstream constraint failures. The
  deterministic-off model-only arm had 6 failures per profile because two
  fixtures explicitly forbid the `<nocompress>` wrappers that this control arm
  intentionally leaves in place.
- Repeatability: deterministic and final SHA sets were identical across all
  three repeats for every prompt/condition in all seven full matrices.

Every profile used these four condition IDs, with `<profile>` replaced by the
allowlisted profile name:

1. `<profile>__baseline_deterministic__det_on`
2. `<profile>__experiment_deterministic__det_on`
3. `<profile>__model_only__det_off`
4. `<profile>__experiment_model_force__det_on`

## Shared safety outcome

Across 840 full-matrix records, accepted outputs had zero protected-span,
placeholder, JSON, constraint, required-term, code, or structural failures.
Model-only returned no accepted savings: 27 of 30 records per profile rolled
back, with stable reasons across repeats (18 constraint rollbacks and 9
required-term rollbacks). Experiment-plus-model rolled back 6 of 30 records per
profile for required-term integrity and accepted 249 model-saved tokens on the
remaining safe outputs.

The separate downstream evaluator recorded 42 failures, all in model-only:
2 wrapper-sensitive cases x 3 repeats x 7 profiles. They were caused by the
expected presence of `<nocompress>`/`</nocompress>` when deterministic transforms
were disabled, not by a lost required term. All experiment deterministic and
experiment-plus-model records passed their downstream checks.

Rejected model output never contributed savings. The accepted
experiment-plus-model result per profile was:

- 6,000 original tokens;
- 381 deterministic tokens saved (6.35%);
- 249 incremental model tokens saved;
- 630 total tokens saved (10.50%); and
- zero accepted integrity failures.

The deterministic baseline and experiment arms were identical: 381 saved
tokens in each. Those existing savings came from JSON-to-TOON (315 tokens over
three repeated applications) and `<nocompress>` wrapper removal (66 tokens over
six repeated applications), not from a new experiment.

## Experiment results

| Experiment | Incremental deterministic savings | Applications beyond baseline | Experiment deterministic p50 ms | Experiment + model p50 / p95 ms | Integrity | Recommendation |
|---|---:|---:|---:|---:|---|---|
| Strict whitespace | 0 | 0 | 2.48 | 802.13 / 1440.97 | 0 failures | Revise corpus; do not promote |
| JSON minification | 0 | 0 | 178.10 | 564.67 / 1047.95 | 0 failures | Revise corpus; do not promote |
| Literal aliases | 0 | 0 | 175.20 | 570.05 / 1058.85 | 0 failures | Revise corpus; do not promote |
| Expanded TOON | 0 | 0 | 178.05 | 583.15 / 1071.74 | 0 failures | Revise thresholds/corpus; do not promote |
| Expanded HTML | 0 | 0 | 172.39 | 552.93 / 1053.76 | 0 failures | Revise corpus; do not promote |
| Exact tenant boilerplate | 0 | 0 | 176.63 | 587.54 / 1055.77 | 0 failures | Revise after approved discovery data |
| Duplicate wrapper aliases | 0 | 0 | 173.87 | 553.74 / 1052.09 | 0 failures | Revise after classified wrapper data |

The deterministic latency difference between the first profile and later
profiles reflects process ordering after the model was loaded. Causal decisions
use each profile's paired baseline/experiment arms, not cross-profile latency.

### 1. Strict prose whitespace

Changed implementation: `app/compression_pipeline.py` wraps the existing
normalizer with the token gate, plus `tests/test_whitespace_normalizer.py`.

The gate requires a tokenizer-backed saving of at least 2 tokens and 0.5% in
the affected segment. Markdown tables, lists, quotes, YAML-like rows, aligned
text, code fences, hard breaks, and critical clauses are protected. The fixed
corpus contained no tokenizer-positive strict-whitespace application, so the
recommendation is **revise**, not promotion.

### 2. Safe JSON minification

Changed implementation: `app/json_regions.py`, `app/compression_pipeline.py`,
`app/analytics.py`, `tests/test_json_regions.py`,
`tests/test_json_toon_pipeline.py`, and related integration tests.

The shared detector uses `raw_decode`, duplicate-key detection, stable syntax
classes, ambiguous-parent flags, and section-aware context. Small strict JSON is
discoverable independently of transformation eligibility. JSONC, JavaScript
literals, templates, NDJSON, concatenated JSON, tool payloads, fixtures,
schemas, exact-output contexts, and duplicate keys are non-rewritable. The
minifier requires deeply equal typed JSON plus at least 8 tokenizer tokens and
5% savings. No held-out record reached this fallback after higher-priority TOON
and exact-context exclusions, so the recommendation is **revise**.

### 3. Repeated literal aliases

Changed implementation: `app/compressor.py`, `app/analytics.py`, and
`tests/test_compressor.py`.

Aliases are limited to repeated long URLs/identifiers in compressible prose;
the legend is non-compressible, collisions are avoided, and exact expansion is
verified before the 16-token/5% gate. No fixed-corpus record contained an
eligible repeated literal, so the recommendation is **revise**.

### 4. Expanded JSON-to-TOON

Changed implementation: `app/toon_adapter.py`, `app/compression_pipeline.py`,
`scripts/run_threshold_matrices.py`, and TOON/JSON tests.

All 36 preregistered cells were run: characters 120/200/300, lines 2/3/4,
absolute savings 16/32, and relative savings 5%/8%. Every cell matched the
baseline: three repeated TOON applications, 315 transform tokens saved, and 381
total deterministic tokens saved. No lower threshold found an additional safe
record. TOON output is decoded and compared through a typed, ordered canonical
representation. Recommendation: **revise**.

### 5. Expanded HTML-to-Markdown

Changed implementation: `app/html_compactor.py`,
`app/compression_pipeline.py`, `tests/test_html_compactor.py`, and the threshold
runner.

The 300/500/1000-character cells were run with the 16-token/20% gate. All had
zero HTML applications. The preservation signature requires a clear
`main`/`article` region, ordered visible text, and exact links; forms, scripts,
templates, SVG, code/pre blocks, custom elements, and missing links are
rejected. Recommendation: **revise**.

### 6. Tenant-approved exact boilerplate

Changed implementation: `app/boilerplate_discovery.py`,
`scripts/discover_tenant_boilerplate.py`, and
`tests/test_boilerplate_discovery.py`.

Discovery is diagnostics-only. It enforces exact normalized blocks, at least 50
records, at least 30% frequency, multiple conversations, at least 8 tokens per
affected record, and exclusions for protected spans, policy/instruction terms,
questions, owners, deadlines, and task-specific fields. Each benchmark tenant
had only five discovery records, so no candidate was eligible or activated.
Recommendation: **revise after collecting and explicitly approving a versioned
tenant profile**.

### 7. Classified duplicate-wrapper aliases

Changed implementation: `app/compressor.py`, `app/analytics.py`, API schemas,
and `tests/test_compressor.py`.

Generic duplicates remain diagnostics-only. The opt-in transform recognizes
only the explicit generated-support-export wrapper class, rejects protected or
instruction-bearing blocks, requires at least 32 tokens and 10% record-level
savings, and verifies byte-exact expansion. The fixed corpus had no classified
wrapper application. Recommendation: **revise with held-out generated-wrapper
records**.

### 8. Critical-clause shielding and final rollback

Changed implementation: `app/protected_spans.py`, `app/integrity_policy.py`,
`app/compressor.py`, `app/analytics.py`, API schemas, and safety tests.

Complete clauses covering negation, obligation/permission/scope, exceptions,
thresholds, governed identifiers/URLs, and output-format requirements are
shielded in experiment profiles. Final validation is unconditional. On a model
failure it returns the post-deterministic output, records
`output_rejected_integrity_<class>` plus the rejected-output hash, and recomputes
savings from the accepted fallback. Recommendation: **accept as the P0 safety
control**.

## Per-tenant results

Results were identical across profiles because no deterministic experiment
applied beyond baseline.

| Tenant | Original tokens | Deterministic saved | Experiment + model total saved | Total reduction | Experiment + model rollbacks | Accepted integrity failures |
|---|---:|---:|---:|---:|---:|---:|
| `benchmark_tenant_a` | 3,477 | 348 | 486 | 13.98% | 6 | 0 |
| `benchmark_tenant_b` | 2,523 | 33 | 144 | 5.71% | 0 | 0 |

## Verification and artifacts

- Tests: 181 passed.
- Ruff: all checks passed.
- `git diff --check`: passed (line-ending notices only).
- Full aggregate: `full-matrix-aggregate.json` (840 records).
- Threshold aggregate: `threshold-matrices-aggregate.json` (1,230 records).
- Boilerplate discovery: `tenant-boilerplate-discovery.json`.
- Individual full exports and Markdown summaries are in this directory.

`safe_stack_v1` was deliberately not benchmarked or populated: no individual
savings experiment met the positive held-out savings criterion. This avoids
turning a clean but non-causal zero-gain result into a release stack.
