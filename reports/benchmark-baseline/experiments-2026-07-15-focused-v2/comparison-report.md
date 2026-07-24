# Focused safety and transform decision rerun — July 15, 2026

## Decision summary

Keep final integrity validation and rollback unconditional. Keep critical-clause
shielding enabled in the default profile. The direct shielding ablation favored
shielding on: it accepted 624 saved tokens with 6 rollbacks, compared with 486
tokens and 15 rollbacks when shielding was off. Shielding on was also faster at
the median in this run (682.47 ms versus 740.97 ms).

Do not add expanded JSON-to-TOON or safe JSON minification to `safe_stack_v1`.
Both experiment deterministic arms matched baseline at 381 saved tokens and
added zero applications and zero tokens. The JSON repair was successful—there
were zero matched model-input hash differences—but input neutrality alone does
not establish utility.

## Benchmark contract

- Corpus: 10 fixed cases from `data/eval_cases.json`.
- Repeats: 3 per case and condition.
- Profiles: `toon_expanded_safe`, `json_minify_safe`, and the direct
  `critical_clause_shielding_off_ablation`.
- Records: 360 configured and completed; zero harness/API errors.
- Compressor commit: `281844d4bed9de0c544da080463b4cf8294ec169`.
- Compressor source SHA-256:
  `e07e23b76636e7b981d20537d32d2409466c3ada9e34fab701b0dec915bc4423`.
- Deployment version: `2026.07.14.131419`.
- Model: `microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank`.
- Model revision: `local_or_unknown`.
- Repeatability: zero unstable deterministic or final SHA groups across all
  case/condition combinations.

## Results

| Decision | Compared conditions | Accepted tokens saved | Rollbacks | p50 latency | Hard-integrity failures | Categorized downstream failures | Outcome |
|---|---|---:|---:|---:|---:|---:|---|
| Critical-clause shielding | on / off, model-force with identical deterministic transforms | 624 / 486 | 6 / 15 | 682.47 / 740.97 ms | 0 / 0 | 0 / 0 | Keep on permanently |
| Expanded JSON-to-TOON | experiment / baseline deterministic | 381 / 381 | 0 / 0 | 1.96 / 1.70 ms | 0 / 0 | 0 / 0 | Park; zero incremental utility |
| Safe JSON minification | experiment / baseline deterministic | 381 / 381 | 0 / 0 | 212.38 / 216.04 ms | 0 / 0 | 0 / 0 | Park; neutrality fixed, zero incremental utility |

The categorized evaluator executed 360 relationship, negation, permission, and
required-format checks across the three matrices. All passed in the corrected
run. Model-only control arms still recorded the expected wrapper-sensitive
constraint failures when deterministic wrapper removal was disabled; those
controls are not candidate production paths.

## Corrective loop preserved for audit

The first focused run is preserved in `experiments-2026-07-15-focused`. Its new
permission check found that the model removed “Never imply that credits are
automatic” in all three shielding-on repeats. The detector recognized `never`
as policy language but did not classify `imply` as the governed action, so the
complete clause was outside shielding and final clause validation.

The action vocabulary now includes `imply`. Regression coverage verifies both
default shielding and unconditional rollback when the benchmark-only off arm
removes that clause. The corrected evidence in this directory is the decision
source; the pre-fix run must not be used for promotion metrics.

## Release decision

1. Promote critical-clause shielding into the default profile.
2. Retain unconditional final integrity validation and post-deterministic
   rollback on every model path.
3. Keep `safe_stack_v1` empty.
4. Park expanded TOON and JSON minification until a separate natural held-out
   corpus contains eligible, tokenizer-positive records.
5. Do not spend another model-backed matrix on the other parked transforms
   without production telemetry demonstrating eligible demand.
