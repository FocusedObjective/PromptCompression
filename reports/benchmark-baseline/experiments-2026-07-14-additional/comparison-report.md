# Additional tenant experiment cohorts — July 14, 2026

## Privacy labels

This report uses only the approved labels **Tenant 1** and **Tenant 2**. It
excludes tenant names, tenant identifiers, prompt text, prompt hashes, request
identifiers, and raw export filenames.

## Decision summary

Do not add a savings transform to `safe_stack_v1` from these runs. The new
cohorts strengthen the integrity-rollback evidence and provide one small,
positive JSON-to-TOON observation, but they do not satisfy the preregistered
four-arm, three-repeat, fixed-provenance, or downstream-evaluation gates.

Across both exports, 280 arm records were configured and 273 completed. Seven
Tenant 1 rows were harness/API errors with no analytics or integrity result;
they are excluded from integrity and latency denominators. Among the 273
completed records, accepted outputs had zero hard-integrity failures and zero
protected-span failures. The guardrail rejected 47 unsafe model candidates:
35 for inline-code changes and 12 for identifier changes.

Constraint and required-term coverage were both zero. Each cohort used one
repeat. The harness ran a deterministic baseline and six experiment-plus-model
profiles, but did not run the required experiment deterministic-only or
baseline model-only arms.

## Cohort results

### Tenant 1

- 105 arm records were configured; 98 completed and 7 returned errors.
- The deterministic baseline saved zero tokens.
- `toon_expanded_safe` changed one of 14 completed matched records and saved 39
  incremental deterministic tokens. That is about 0.01% of the completed
  experiment input, so it is a positive signal rather than promotion evidence.
- The other five experiment profiles produced zero incremental deterministic
  tokens and no deterministic output change relative to matched baseline
  records.
- Model-stage savings ranged from 68 to 77 tokens by profile, but cannot be
  attributed to a deterministic experiment without a matched model-only arm.
- The guardrail rejected 29 model candidates for inline-code changes. Accepted
  completed outputs had zero hard-integrity failures.
- Several successful calls approached the 300-second service timeout. The
  seven error rows contain no exported failure reason, so timeout is plausible
  but not established.

### Tenant 2

- All 175 configured arm records completed, representing 20 unique inputs in
  each matched profile slice.
- Baseline deterministic processing already converted two eligible JSON
  regions to TOON and saved 1,428 tokens, or 1.86%.
- Every experiment profile had the same deterministic output as baseline.
  Therefore, expanded TOON added zero incremental tokens in this cohort, and
  the baseline TOON savings must not be credited to an experiment profile.
- Each experiment-plus-model profile saved 40 additional model tokens, but the
  missing model-only arm prevents causal attribution.
- The guardrail rejected 18 model candidates: 12 for identifier changes and 6
  for inline-code changes. Accepted outputs had zero hard-integrity failures.
- One profile crossed an application deployment boundary during the run. The
  model revision remained fixed, but the cohort does not meet the fixed-release
  promotion gate.

## Experiment decisions

| Experiment | New evidence | Recommendation |
|---|---|---|
| Integrity rollback and critical-clause shielding | 47 unsafe model candidates rejected; 0 accepted hard failures in 273 completed records | Keep accepted as an unconditional safety layer |
| Strict prose whitespace | Eight reported candidate rewrites across the two profile arms; 0 tokenizer savings and 0 incremental deterministic output changes | Revise; retain tokenizer-positive gate |
| Safe JSON minification | 0 applications and 0 deterministic savings in both new cohorts | Revise |
| Repeated literal aliases | 0 eligible applications in both new cohorts | Revise with deliberately eligible held-out examples |
| Expanded JSON-to-TOON | Tenant 1: 1 incremental application and 39 tokens; Tenant 2: 0 incremental tokens because baseline already applied TOON | Revise and rerun the full causal matrix |
| Expanded HTML-to-Markdown | 0 applications; Tenant 2 candidates remained below threshold | Revise with eligible article/main HTML |
| Tenant-approved exact boilerplate | Both cohorts used the default profile and no approved phrase set | Pending tenant-approved, versioned discovery/evaluation split |
| Classified duplicate-wrapper aliases | 0 classified wrapper applications; generic duplicate removal remained diagnostics-only | Revise with explicitly classified wrappers |

## Measurement correction

`analyze_exports.py` previously treated an error row whose
`integrityPassed` value was null as an integrity failure and included its zero
latency in distributions. The analyzer now reports configured, successful, and
error records separately; integrity rates use only records with an actual
integrity result, and latency distributions exclude harness/API error rows.

## Required rerun

Run the same fixed prompts under one deployment and model revision with at
least three repeats of all four conditions:

1. baseline deterministic;
2. experiment deterministic-only;
3. baseline model-only with deterministic transforms disabled; and
4. experiment plus model-force.

Populate required terms, constraints, required-format checks, and semantic
relationship evaluations. Preserve the raw harness error reason so service
timeouts can be distinguished from compression or integrity failures.
