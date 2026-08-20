# Compression Rollout and Measurement

## Production policy

`app/gpu_compression_policy.json` is the versioned production policy consumed by
both Python and the Cloudflare Worker. It is the only production threshold
source for edge decisions and rollout criteria.

The policy currently permits `model_auto` consideration at 2,000 candidate
tokens with at least 200 expected incremental saved tokens. Protection density,
structured density, deterministic savings, model warmth and latency budgets can
still cause a skip.

## Service metrics

The origin emits one content-free structured event per completed compression
request and exposes bounded process aggregates under `runtime.telemetry` on
`/health`. The Worker emits one structured event per edge request when
`STRUCTURED_LOGS_ENABLED=true` and has Workers Logs and sampled traces enabled.

Track at minimum:

- Compression latency and error rate by route/mode
- Input, output and saved tokens
- Model gate decisions and integrity rollback class
- Exact-response and per-content cache hit rates
- Fail-open count
- Tool-result skipped, shadowed, rolled back and applied counts
- Edge decision and origin call rate

The compressor cannot observe downstream model TTFT, billed cached/uncached
input tokens or task quality. The caller must attach those measurements to its
own request/correlation record without sending prompt content into telemetry.

## Tool-result rollout

Tool results are disabled unless `tool_result_policy` is supplied. Begin in
shadow mode:

```json
{
  "compression_settings": {
    "tool_result_policy": {
      "mode": "deterministic",
      "min_tokens": 8000,
      "max_reduction": 0.15,
      "rollout_mode": "shadow",
      "rollout_percentage": 5,
      "rollout_key": "stable-account-or-conversation-key"
    }
  }
}
```

Shadow mode computes and records the candidate but returns the original tool
content. The rollout key is hashed for stable selection, is stripped from the
downstream-compatible request, and is not logged.

Promotion sequence:

1. Run shadow mode until representative tool types have adequate volume.
2. Review candidate savings, reduction rollbacks and task-quality comparisons.
3. Switch to `rollout_mode: apply` at a small stable percentage.
4. Increase the percentage only when quality guardrails remain unchanged.
5. Remove `tool_result_policy` immediately to disable the feature.

Structured JSON, code fences, tool protocols, exact-output instructions and
non-text content are never eligible under this policy. Unfenced source code,
SQL, XML, YAML and delimited tables are also protected. Candidates with no
positive token savings are rolled back. `tool_call_id`, tool name, arguments and
other message metadata remain unchanged.

## Net-value measurement

Evaluate rollout value using actual uncached downstream pricing:

```text
downstream value saved = avoided uncached input tokens × input price

net value = downstream value saved
          - compression charge
          - added-latency value
          - expected quality-loss cost
```

Do not count tokens that would already have received cached-input pricing as
full-price savings.
