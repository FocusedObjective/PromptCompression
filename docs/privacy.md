# Compression Privacy and Data Handling

## Data path

The compression service must receive the original uncompressed text in order to
compress it. A hosted request can pass through the Cloudflare Worker and the GPU
Cloud Run origin. A private deployment can call the container directly and omit
the public edge.

The runtime does not persist training samples. `training_sample_recorded` remains
`false`, and compression request telemetry contains token counts, timings,
decisions, cache states and stable warning classes—not prompt or compressed
content.

## Transient caches

Caching is enabled by default and is always bounded by TTL and memory limits:

- The edge exact-response cache can retain successful serialized JSON responses.
- The origin exact-response cache can retain successful serialized JSON responses.
- The origin message-content cache can retain successful compressed text parts.

Cache keys contain SHA-256 digests rather than raw prompt text. Cache values are
different: serialized responses can contain compressed content, unchanged
content, and preserved system/developer/tool messages. They must therefore be
treated as prompt-derived customer data.

The default origin TTL for both response and content caches is five minutes.
Cloudflare edge TTL is configured separately and also defaults to five minutes.
Entries are process-local at the origin and disappear on restart or scale-to-zero.
Fail-open responses are returned with `Cache-Control: no-store`; neither the
origin nor the edge stores them.

## Opting out

Either mechanism below bypasses both the edge and origin caches:

```http
Cache-Control: no-store
```

Browser preflight responses explicitly allow the `Cache-Control` request
header, so browser applications can use this opt-out directly. Normal responses
also expose the request ID, edge decision/cache/auth/rate-limit state, origin
status, compression policy, and origin compression-cache headers to browser
JavaScript for diagnostics.

For clients that cannot set headers:

```json
{"text": "...", "cache": false}
```

or on the v1 endpoints:

```json
{
  "compression_settings": {
    "cache": false
  }
}
```

Opting out prevents new cache writes and cache reads for that request. It does
not purge entries created by earlier cache-enabled requests; those expire under
their configured TTL or disappear when the cache is cleared/restarted.

## Logs and metrics

Structured runtime events may include:

- Route, compression mode and policy version
- Edge/origin/cache decision
- Input, output and saved token counts
- Compression latency
- Cache hits, misses and stores
- Fail-open, rollback and tool rollout classifications

They must not include raw request bodies, prompts, compressed outputs,
authorization material, rollout keys or arbitrary warning text. Tenant IDs are
not emitted by the new compression telemetry.

## Region and private deployment

Cloud Run region is selected at deployment time. Place the GPU service and any
stateful supporting services in the region required by the customer or workload.
The provided GPU Dockerfile can also be deployed into a private customer project
or another compatible GPU container platform.

For a private path, restrict Cloud Run invocation to the trusted edge/service
identity or call a private deployment directly. Do not describe the public
hosted endpoint as region-pinned unless its actual deployment and subprocessors
meet that promise.

## Future training or retention

Any future raw-sample retention requires a separate explicit opt-in, encryption,
a documented retention period, tenant-level deletion controls and an external
storage system. It must not be inferred from ordinary compression traffic.
