# html_markdown_expanded_safe benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `html_markdown_expanded_safe__baseline_deterministic__det_on` | 30 | 381 | 0 | 0 | 178.27 |
| `html_markdown_expanded_safe__experiment_deterministic__det_on` | 30 | 381 | 0 | 0 | 172.39 |
| `html_markdown_expanded_safe__model_only__det_off` | 30 | 0 | 27 | 0 | 525.95 |
| `html_markdown_expanded_safe__experiment_model_force__det_on` | 30 | 630 | 6 | 0 | 552.93 |
