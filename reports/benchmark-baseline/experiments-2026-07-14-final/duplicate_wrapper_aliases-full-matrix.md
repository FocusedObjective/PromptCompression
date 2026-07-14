# duplicate_wrapper_aliases benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `duplicate_wrapper_aliases__baseline_deterministic__det_on` | 30 | 381 | 0 | 0 | 181.20 |
| `duplicate_wrapper_aliases__experiment_deterministic__det_on` | 30 | 381 | 0 | 0 | 173.87 |
| `duplicate_wrapper_aliases__model_only__det_off` | 30 | 0 | 27 | 0 | 512.25 |
| `duplicate_wrapper_aliases__experiment_model_force__det_on` | 30 | 630 | 6 | 0 | 553.74 |
