# html_threshold_matrix benchmark

Repeats: 3; cases: 10.

| Condition | Records | Tokens saved | Rollbacks | Integrity failures | p50 latency ms |
|---|---:|---:|---:|---:|---:|
| `html_threshold_matrix__baseline` | 30 | 381 | 0 | 0 | 1.59 |
| `html_threshold_matrix__html_c300_t16_r20` | 30 | 381 | 0 | 0 | 1.67 |
| `html_threshold_matrix__html_c500_t16_r20` | 30 | 381 | 0 | 0 | 1.65 |
| `html_threshold_matrix__html_c1000_t16_r20` | 30 | 381 | 0 | 0 | 1.69 |
