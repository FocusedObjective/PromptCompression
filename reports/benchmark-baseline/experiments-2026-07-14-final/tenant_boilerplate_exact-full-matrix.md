# tenant_boilerplate_exact benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `tenant_boilerplate_exact__baseline_deterministic__det_on` | 30 | 381 | 0 | 0 | 179.76 |
| `tenant_boilerplate_exact__experiment_deterministic__det_on` | 30 | 381 | 0 | 0 | 176.63 |
| `tenant_boilerplate_exact__model_only__det_off` | 30 | 0 | 27 | 0 | 506.07 |
| `tenant_boilerplate_exact__experiment_model_force__det_on` | 30 | 630 | 6 | 0 | 587.54 |
