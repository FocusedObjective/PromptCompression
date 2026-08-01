# Local Compression Response Cache

## Purpose

The first cache layer runs inside each Cloud Run container's FastAPI Python
process. It is intended for the initial UsageTap.com compression example, where
the same recent example and settings may be submitted repeatedly. It avoids
repeating model work without adding a Redis dependency or consuming unbounded
compressor memory.

The cache is an optimization, not durable storage. Each Cloud Run instance has
its own cache. A new instance starts empty, and a restart or scale-to-zero event
discards all entries. The container runs one Uvicorn process, so there is one
cache per container with the current deployment command.

## Current behavior

- The default limit is 32 MiB per process, with a 1 MiB maximum entry, 4,096
  maximum entries, and a five-minute TTL.
- LRU eviction is based on an estimated resident size containing the serialized
  response, hashed key, and fixed per-entry Python-object overhead.
- Only serialized response bytes are retained. Raw prompt text is not stored in
  cache keys.
- Concurrent identical misses are coalesced in-process so only one caller does
  compression work. Waiting requests still perform their own authorization,
  demo reservation, and UsageTap metering.
- Cache failures or entries that exceed a limit behave as misses/bypasses; they
  do not fail compression.
- Responses expose `X-Compression-Cache` with `store`, `hit`, `shared`,
  `bypass`, or `disabled`.
- Cache statistics are exposed under `runtime.response_cache` on `/health`.

Authorization begins before compression, as it did before the cache was added.
An entry is not committed until that request has completed authorization and
metering successfully. A cache hit is still authorized, rate/account limited by
the surrounding service, and metered as its own customer request. Demo cache
hits still consume a demo operation.

## Cache identity

Keys are SHA-256 hashes of canonical JSON and never contain credentials or
request IDs. The identity contains:

- API route and the complete Pydantic-validated request, including all defaults;
- exact input text or messages;
- resolved tenant identity and every resolved tenant-profile setting;
- resolved aggressiveness, mode, latency budget, role aggressiveness, and
  message-compaction settings as applicable;
- response-shape, JSON-compression, experiment, and evaluation settings;
- deployment version, compression model name, source hash, and cache schema.

Consequently, changes such as `model_auto` versus `model_force`, aggressiveness,
latency budget, tenant policy, model name, or analytics flags cannot reuse one
another's result. JSON object key order and numeric spellings such as `0.5`
versus `0.50` normalize to the same validated value. Prompt whitespace and
array ordering remain significant.

Credentials, `X-Request-ID`, tracing headers, and timestamps are excluded.

## Admission and bypass rules

A completed response is retained only when it has positive token savings, no
training side effect, no fallback, and no transient warning such as a cold-model
gate, missing latency baseline, timeout, unavailable origin, or integrity
rollback. This prevents a temporary cold or degraded result from suppressing a
better warm-model result for the TTL.

`/compress` requests with diagnostics, disabled-transform evaluation,
evaluation constraints, or experiment profiles bypass the response cache.
Their analytics contain request-specific timings and research data. The test
page has a **Detailed analytics** checkbox. The streamlined `/embed` page sends
both `include_diagnostics: false` and `include_detailed_analytics: false`.

## Configuration

| Variable | Default | Meaning |
| --- | ---: | --- |
| `RESPONSE_CACHE_ENABLED` | `true` | Enables the process-local cache. |
| `RESPONSE_CACHE_MAX_BYTES` | `33554432` | Maximum estimated resident bytes. |
| `RESPONSE_CACHE_MAX_ENTRY_BYTES` | `1048576` | Maximum estimated bytes for one entry. |
| `RESPONSE_CACHE_MAX_ENTRIES` | `4096` | Hard entry-count backstop. |
| `RESPONSE_CACHE_TTL_SECONDS` | `300` | Entry lifetime. |
| `RESPONSE_CACHE_SINGLE_FLIGHT_TIMEOUT_SECONDS` | `30` | Maximum wait for identical in-flight work before independently continuing. |

Cloud Run memory planning must reserve the configured cache allowance in
addition to the model, tokenizer, request working set, Python runtime, and
concurrent-request headroom. Start with the defaults, observe peak memory and
cache effectiveness, and reduce `RESPONSE_CACHE_MAX_BYTES` if model workloads
approach the instance limit.

## Future edge migration

The existing edge code is not currently in the production request path. Move
the cache in stages:

1. Publish the cache identity and admission rules as a shared versioned
   contract. Add golden vectors proving that Python and edge implementations
   produce equivalent keys without exposing prompt text.
2. Deploy the edge in observe-only mode. Record hypothetical hit/miss decisions,
   key cardinality, lookup latency, response sizes, and estimated avoided origin
   calls without serving cached responses.
3. Resolve edge metering explicitly. Every served hit must retain the current
   per-request authorization, rate-limit, demo-accounting, and UsageTap metering
   semantics before origin work is skipped.
4. Enable edge reads for a small share of the UsageTap.com example traffic.
   Version keys by model, tokenizer, source, tenant policy, and response schema.
   Never cache diagnostics, errors, transient fallbacks, or oversized values.
5. Add distributed miss coalescing if simultaneous edge misses create meaningful
   origin pressure. The cache API alone should not be assumed to provide
   single-flight behavior across locations.
6. Keep the process-local cache enabled as an L2 during rollout. Compare edge
   hit rate, local hit rate, origin calls, latency, metering totals, and memory.
7. After edge behavior and accounting are verified, reduce or disable the local
   cache with `RESPONSE_CACHE_ENABLED=false`. Retain the implementation as an
   emergency fallback until the edge path has completed a stable operating
   period.

The migration should be reversible through configuration and should not require
changing the compression response contract.
