# json_minify_safe benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `json_minify_safe__baseline_deterministic__det_on` | 30 | 381 | 0 | 0 | 169.53 |
| `json_minify_safe__experiment_deterministic__det_on` | 30 | 381 | 0 | 0 | 173.21 |
| `json_minify_safe__model_only__det_off` | 30 | 84 | 21 | 0 | 594.78 |
| `json_minify_safe__experiment_model_force__det_on` | 30 | 630 | 6 | 0 | 618.63 |
