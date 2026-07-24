# toon_expanded_safe benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `toon_expanded_safe__baseline_deterministic__det_on` | 30 | 381 | 0 | 0 | 1.70 |
| `toon_expanded_safe__experiment_deterministic__det_on` | 30 | 381 | 0 | 0 | 1.96 |
| `toon_expanded_safe__model_only__det_off` | 30 | 78 | 21 | 0 | 670.39 |
| `toon_expanded_safe__experiment_model_force__det_on` | 30 | 624 | 6 | 0 | 694.83 |
