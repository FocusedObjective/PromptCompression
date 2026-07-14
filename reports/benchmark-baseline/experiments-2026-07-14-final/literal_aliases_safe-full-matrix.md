# literal_aliases_safe benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `literal_aliases_safe__baseline_deterministic__det_on` | 30 | 381 | 0 | 0 | 183.70 |
| `literal_aliases_safe__experiment_deterministic__det_on` | 30 | 381 | 0 | 0 | 175.20 |
| `literal_aliases_safe__model_only__det_off` | 30 | 0 | 27 | 0 | 509.74 |
| `literal_aliases_safe__experiment_model_force__det_on` | 30 | 630 | 6 | 0 | 570.05 |
