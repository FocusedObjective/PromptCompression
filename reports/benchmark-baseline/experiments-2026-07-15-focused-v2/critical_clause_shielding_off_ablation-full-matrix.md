# critical_clause_shielding_off_ablation benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `critical_clause_shielding_off_ablation__shielding_on_deterministic__det_on` | 30 | 381 | 0 | 0 | 231.30 |
| `critical_clause_shielding_off_ablation__shielding_off_deterministic__det_on` | 30 | 381 | 0 | 0 | 220.19 |
| `critical_clause_shielding_off_ablation__shielding_on_model_force__det_on` | 30 | 624 | 6 | 0 | 682.47 |
| `critical_clause_shielding_off_ablation__shielding_off_model_force__det_on` | 30 | 486 | 15 | 0 | 740.97 |
