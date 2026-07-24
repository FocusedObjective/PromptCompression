# json_minify_safe benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `json_minify_safe__baseline_deterministic__det_on` | 30 | 381 | 0 | 0 | 216.04 |
| `json_minify_safe__experiment_deterministic__det_on` | 30 | 381 | 0 | 0 | 212.38 |
| `json_minify_safe__model_only__det_off` | 30 | 78 | 21 | 0 | 684.37 |
| `json_minify_safe__experiment_model_force__det_on` | 30 | 624 | 6 | 0 | 714.76 |
