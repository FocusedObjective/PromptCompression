# strict_whitespace_token_positive benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `strict_whitespace_token_positive__baseline_deterministic__det_on` | 30 | 381 | 0 | 0 | 1.93 |
| `strict_whitespace_token_positive__experiment_deterministic__det_on` | 30 | 381 | 0 | 0 | 2.48 |
| `strict_whitespace_token_positive__model_only__det_off` | 30 | 0 | 27 | 0 | 667.74 |
| `strict_whitespace_token_positive__experiment_model_force__det_on` | 30 | 630 | 6 | 0 | 802.13 |
