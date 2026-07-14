# json_minify_safe benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `json_minify_safe__baseline_deterministic__det_on` | 30 | 381 | 0 | 0 | 189.24 |
| `json_minify_safe__experiment_deterministic__det_on` | 30 | 381 | 0 | 0 | 178.10 |
| `json_minify_safe__model_only__det_off` | 30 | 0 | 27 | 0 | 521.89 |
| `json_minify_safe__experiment_model_force__det_on` | 30 | 630 | 6 | 0 | 564.67 |
