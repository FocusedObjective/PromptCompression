# Cloudflare Edge Split Plan

This plan adds a Cloudflare Worker in front of the existing Google Cloud Run
FastAPI service. The Python container stays unchanged for the first phases. The
Worker becomes the public API edge, handles cheap deterministic decisions, and
calls Cloud Run only when model-backed compression is worth paying for.

The target architecture is:

```text
client
  -> Cloudflare Worker
      -> validation
      -> request IDs and logs
      -> tenant-aware rate limits
      -> exact-response cache
      -> cheap skip/route heuristics
      -> optional deterministic TOON/whitespace preprocessing
  -> Google Cloud Run FastAPI service
      -> LLMLingua-2 / tokenizer-backed compression
```

Long term, clients should not call Cloud Run directly. Cloud Run should require
IAM authentication, and only the Worker should hold the credentials needed to
invoke it.

There is one important redundancy requirement: the Cloud Run API must remain a
complete, compatible implementation of the public API. If Cloudflare has an
outage, traffic should be redirectable to Cloud Run. If Cloud Run is degraded,
the Worker should still serve the parts of the API it can answer without
model-backed compression.

## Repository Shape

Add the edge app to this repository instead of creating a new repository. It is
part of the same product, shares API contracts with the Python service, and
needs parity tests against the existing request and response shapes.

Suggested layout:

```text
edge/
  package.json
  package-lock.json
  wrangler.jsonc
  src/
    index.ts
    config.ts
    origin.ts
    cache.ts
    rateLimit.ts
    heuristics.ts
    requests.ts
    responses.ts
    telemetry.ts
  test/
    heuristics.test.ts
    requests.test.ts
    cache.test.ts
    origin.test.ts
```

Do not move or rewrite the current Python files. When the Worker is actually
scaffolded, keep Docker and Cloud Build focused on the current container by
ignoring `edge/node_modules/`, `edge/.wrangler/`, and other generated edge build
artifacts in `.dockerignore` and `.gcloudignore`.

## Worker Versus Pages Functions

Use a Cloudflare Worker, not a Pages Function.

The edge layer is an API gateway/proxy, not a static frontend with server-side
page functions. A Worker gives a direct service boundary, independent deploys,
routes for `compress.usagetap.com`, rate-limit bindings, cache access, logs,
and origin fetch control.

## Public Routes

Start with these routes:

```text
GET  /health
POST /compress
POST /v1/compress
POST /v1/messages/compress
POST /tokens/estimate
```

Everything else should return `404` from the Worker unless deliberately exposed.
Keep the Cloud Run docs/UI routes private to Cloud Run during the first edge
split. If public docs are needed later, expose them intentionally through a
separate route decision.

## Compatibility And Redundancy

The edge split must preserve two independent operating modes:

```text
normal mode:
  client -> Cloudflare Worker -> Cloud Run when needed

direct-origin fallback mode:
  client -> Cloud Run API

edge-degraded mode:
  client -> Cloudflare Worker -> no Cloud Run call
```

The public Worker API and the direct Cloud Run API should remain compatible for
the supported routes. A client should be able to switch `base_url` from
`https://compress.usagetap.com` to a Cloud Run fallback URL without changing
request bodies, headers, response parsing, or endpoint paths.

Compatibility rules:

```text
do not remove or rename existing Python routes
do not make the Worker response schema a different product API
do not require Worker-only request fields for normal compression
do not require Worker-only response parsing for successful responses
keep tenant_id and X-Tenant-ID semantics aligned
keep /compress, /v1/compress, /v1/messages/compress, and /tokens/estimate usable on Cloud Run
add edge metadata through headers first, not required JSON fields
if JSON warnings are added, keep them optional and backward-compatible
```

The direct-origin fallback is also a release safety net. If a Worker deploy has a
bug, disable the Cloudflare route or redirect traffic to Cloud Run while the
Worker is fixed.

### Cloudflare Outage Fallback

During the transition, Cloud Run can remain publicly reachable and provide the
simplest fallback. After Cloud Run IAM protection is enabled, direct public
fallback needs an explicit operations choice:

```text
option A: break-glass command temporarily allows unauthenticated Cloud Run traffic
option B: maintain a separate public Cloud Run fallback service with the same image
option C: put a Google-hosted API Gateway or external HTTPS load balancer in front of Cloud Run
```

Recommended path:

1. During Worker development, keep direct Cloud Run available.
2. Before enabling IAM-only origin access, document and test a break-glass direct
   fallback.
3. If uptime requirements grow, replace break-glass fallback with a second
   Google-hosted public fallback path.

Break-glass fallback should be rare and auditable:

```powershell
gcloud run services update $env:SERVICE `
  --region $env:REGION `
  --allow-unauthenticated
```

After the incident:

```powershell
gcloud run services update $env:SERVICE `
  --region $env:REGION `
  --no-allow-unauthenticated
```

If DNS for the primary hostname is fully managed by Cloudflare, a Cloudflare
control-plane or DNS outage may prevent fast DNS changes. Keep the raw Cloud Run
URL, or a non-Cloudflare-managed emergency hostname, available in operational
docs for critical clients.

### Cloud Run Outage Or Degraded Origin

When Cloud Run is unavailable, the Worker should avoid returning blanket `503`
for every request. It should provide a degraded but schema-compatible response
where it can do so safely.

Worker-only behaviors that can continue without Cloud Run:

```text
GET /health returns edge health plus origin status if known
invalid requests are rejected with normal edge validation errors
rate-limited requests still return 429
cache hits still return the cached successful response
small or uncompressible requests can return unchanged schema-compatible responses
/v1/messages/compress can preserve non-user messages and return unchanged user text when skipping
```

Requests that truly require model-backed compression and have no cache hit should
return a clear degraded-origin error:

```json
{
  "error": "origin_unavailable",
  "message": "Model-backed compression is temporarily unavailable.",
  "request_id": "..."
}
```

For compatibility endpoints, prefer the normal endpoint response shape when the
Worker can safely return unchanged content. Use the error shape only when the
request cannot be answered honestly without Cloud Run.

### Compatibility Test Matrix

Every edge release should compare Worker behavior against direct Cloud Run:

```text
same endpoint paths
same accepted request bodies
same tenant header behavior
same required response fields
same validation behavior where practical
same status codes for normal successful requests
same client-visible compression fields when the Worker forwards to origin
schema-compatible unchanged responses when the Worker skips origin
```

Direct-origin fallback should be tested at least once per release:

```powershell
$env:API_URL="https://CLOUD_RUN_URL/compress"
python scripts\smoke_test.py
```

The staging Worker should be tested against the same cases:

```powershell
$env:API_URL="https://compress-staging.usagetap.com/compress"
python scripts\smoke_test.py
```

## Environment Configuration

Worker configuration should be explicit:

```text
ORIGIN_BASE_URL=https://prompt-compression-...run.app
EDGE_ENV=prod
MAX_BODY_BYTES=1048576
CACHE_TTL_SECONDS=300
CACHE_ENABLED=true
RATE_LIMIT_ENABLED=true
ORIGIN_AUTH_MODE=none|shared-secret|google-iam
```

Secrets should be stored with Wrangler secrets, not committed:

```text
ORIGIN_SHARED_SECRET
GOOGLE_SERVICE_ACCOUNT_EMAIL
GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY
GOOGLE_CLOUD_RUN_AUDIENCE
```

The initial phase can use `ORIGIN_AUTH_MODE=none` while Cloud Run remains public.
The final phase should use `ORIGIN_AUTH_MODE=google-iam`.

## Phase 1: Transparent Edge Proxy

Goal: put the Worker in the path without changing compression behavior.

Worker responsibilities:

1. Accept only the supported routes and methods.
2. Reject unsupported methods with `405`.
3. Reject unsupported paths with `404`.
4. Require `application/json` for POST routes.
5. Enforce `MAX_BODY_BYTES` before forwarding.
6. Parse JSON once, then forward the original request body or canonical JSON.
7. Preserve tenant headers such as `X-Tenant-ID`.
8. Add an `X-Request-ID` if the client did not send one.
9. Forward to Cloud Run with a bounded timeout.
10. Return Cloud Run's status, JSON body, and relevant response headers.

Do not cache, compress, transform, or skip origin calls in this phase.

Acceptance checks:

```text
GET /health returns a Worker response or forwards to Cloud Run.
POST /compress produces the same response body as direct Cloud Run.
POST /v1/compress produces the same response body as direct Cloud Run.
POST /v1/messages/compress produces the same response body as direct Cloud Run.
Invalid JSON is rejected at the edge.
Oversized bodies are rejected at the edge.
```

## Phase 2: Edge Observability

Goal: make edge behavior debuggable before adding routing intelligence.

Log one structured event per request:

```json
{
  "request_id": "uuid-or-cf-ray-derived-id",
  "cf_ray": "cloudflare-ray-id",
  "edge_env": "staging",
  "method": "POST",
  "path": "/v1/messages/compress",
  "tenant_id": "tenant_123",
  "body_bytes": 4212,
  "decision": "origin",
  "cache": "bypass",
  "origin_status": 200,
  "edge_elapsed_ms": 241,
  "origin_elapsed_ms": 214
}
```

Response headers should include:

```text
X-Request-ID
X-Edge-Decision: origin|cache-hit|skip-unchanged|reject
X-Edge-Cache: hit|miss|bypass|store|disabled
X-Origin-Status
```

Do not log full prompts, compressed text, auth headers, service account material,
or raw request bodies. If debugging content is necessary, log only sizes, stable
hashes, route names, and tenant/profile identifiers.

## Phase 3: Tenant-Aware Rate Limiting

Goal: stop abusive or accidental high-volume traffic before it reaches Cloud Run.

Preferred rate-limit key order:

1. API key or authenticated account ID, when available.
2. `tenant_id` from JSON body.
3. `X-Tenant-ID`.
4. Client IP as a fallback only.

Initial policy:

```text
/health: low-cost fixed limit by IP
/tokens/estimate: moderate limit by tenant/key
/compress: strict limit by tenant/key
/v1/compress: strict limit by tenant/key
/v1/messages/compress: strictest limit by tenant/key and body size
```

Return `429` with a JSON response:

```json
{
  "error": "rate_limited",
  "message": "Too many compression requests. Retry later.",
  "request_id": "..."
}
```

Rate limiting is a guardrail, not billing. If this becomes a paid API, billing
and quota enforcement should live in a durable store or a provider built for
usage accounting.

## Phase 4: Exact-Response Cache

Goal: avoid repeat Cloud Run calls for deterministic identical compression
requests.

The Python service is intentionally deterministic for the same model, input,
tenant profile, and settings. The Worker can cache exact responses when the full
effective request is identical.

Cache only successful `200` JSON responses for:

```text
POST /compress
POST /v1/compress
POST /v1/messages/compress
POST /tokens/estimate
```

Build a synthetic cache key from:

```text
edge cache schema version
route
tenant_id or X-Tenant-ID
tenant_profile
input text or messages
compression_settings / aggressiveness
include_sections
requested downstream model
origin API version
```

The key should use a SHA-256 hash of canonical JSON, not raw prompt text.

Do not cache when:

```text
Cache-Control: no-store is present
the request has an explicit debug flag
the response status is not 200
the response body is too large
tenant policy disables cache
the route decision used a non-deterministic feature
```

Suggested first TTL:

```text
workers.dev default: 300 seconds
custom production domain: 300 seconds
```

Cache-hit responses must include:

```text
X-Edge-Decision: cache-hit
X-Edge-Cache: hit
```

## Phase 5: Benefit Heuristics

Goal: call Cloud Run only when compression is likely to save enough tokens to
justify model latency and cost.

Start with conservative skip decisions that cannot change semantics:

```text
body has no user-compressible text
all user text fields are empty
aggressiveness is exactly 0 and tenant default does not override it
text is below a small byte/token threshold
input has too little whitespace/prose to benefit
request asks for include_sections=true, so origin debug output is required
```

Initial thresholds should be config, not code constants:

```text
MIN_COMPRESS_TEXT_CHARS=240
MIN_COMPRESS_ESTIMATED_TOKENS=80
MIN_EXPECTED_SAVINGS_TOKENS=25
```

For `/v1/messages/compress`, estimate only user message string content and text
parts. System, developer, assistant, tool, image, and other non-user content
should be treated as preserved.

If the Worker skips origin, it must return the same response shape expected by
the endpoint. The response should make the decision visible:

```text
X-Edge-Decision: skip-unchanged
```

Response metadata should report zero savings and a warning such as:

```json
{
  "warnings": ["edge_skipped_origin_low_expected_savings"]
}
```

This is the first phase where response construction happens in TypeScript, so it
needs parity tests for every endpoint.

## Phase 6: Deterministic Edge Preprocessing

Goal: move cheap deterministic compression to the edge after parity tests exist.

Candidates:

```text
safe whitespace trimming and blank-line collapse
simple JSON shape checks
JSON-to-TOON for medium/large safe JSON payloads
early rejection for invalid JSON request bodies
```

Do not blindly port the full Python preprocessor at first. The current Python
pipeline has behavior for:

```text
markdown fences
HTML/code/script/style/template/svg blocks
<nocompress> spans
protected UI and contract sections
raw JSON detection inside prose
duplicate JSON key detection
LLM tool/function exchange detection
TOON savings thresholds
tenant force-keep and force-drop behavior
```

The edge implementation should handle a small safe subset first and leave the
hard cases to Cloud Run. Any edge transform must be covered by golden tests that
compare Worker output to Python output for representative requests.

## Phase 7: Cloud Run Origin Protection

Goal: make Cloud Run private so only the Worker can invoke it.

Do not enable this phase until the direct-origin fallback decision is made and
tested. IAM-only Cloud Run improves the normal security posture, but it removes
simple client-to-Cloud-Run fallback unless there is a break-glass command or a
separate Google-hosted public fallback path.

Target Cloud Run state:

```powershell
gcloud run services update $env:SERVICE `
  --region $env:REGION `
  --no-allow-unauthenticated
```

Create a dedicated service account:

```powershell
gcloud iam service-accounts create cloudflare-compression-edge `
  --display-name "Cloudflare compression edge invoker"
```

Grant only Cloud Run invoker on this service:

```powershell
gcloud run services add-iam-policy-binding $env:SERVICE `
  --region $env:REGION `
  --member "serviceAccount:cloudflare-compression-edge@$env:PROJECT_ID.iam.gserviceaccount.com" `
  --role "roles/run.invoker"
```

Worker IAM flow:

1. Store the service account email and private key as Cloudflare secrets.
2. Use WebCrypto in the Worker to sign a JWT assertion.
3. Exchange the assertion with Google OAuth for an access token.
4. Call Google IAM Credentials `generateIdToken` for the Cloud Run audience.
5. Cache the ID token until shortly before expiry.
6. Send `Authorization: Bearer <id-token>` to the Cloud Run origin.

This avoids a Python app change, because Cloud Run IAM validates the request
before the container receives it.

Security notes:

```text
use a dedicated service account only for this origin
grant roles/run.invoker only on the one Cloud Run service
rotate the service account key on a schedule
keep the Cloud Run default URL out of public client config
do not log auth headers or token exchange responses
```

If service account keys are not acceptable long term, evaluate Google Workload
Identity Federation or a Google Cloud external HTTPS load balancer pattern. The
service-account-key path is the simplest Worker-first implementation, but it is
not the best key-management posture.

## Phase 8: Custom Domain Cutover

Goal: move `compress.usagetap.com` to the Worker once staging is verified.

Suggested rollout:

1. Deploy Worker to a staging subdomain, for example
   `compress-staging.usagetap.com`.
2. Run smoke tests against direct Cloud Run and staging Worker.
3. Compare response bodies for representative payloads.
4. Enable cache in staging.
5. Enable rate limits in staging.
6. Enable IAM origin auth in staging.
7. Test direct-origin fallback against Cloud Run.
8. Test edge-degraded behavior with origin disabled in staging.
9. Point production route `compress.usagetap.com/*` at the Worker.
10. Watch logs and error rates.
11. Reduce direct Cloud Run exposure only after the fallback runbook is tested.

Rollback should be simple: remove or disable the Cloudflare route and point DNS
or routing back to the known-good Cloud Run URL while investigating.

## Test Plan

Worker unit tests:

```text
route allowlist
method validation
content-type validation
body size rejection
tenant ID extraction
cache-key canonicalization
rate-limit key selection
skip heuristic decisions
origin timeout response
Cloud Run error pass-through
```

Parity tests against Python:

```text
/compress normal text
/compress include_sections=true
/compress tenant_profile force_keep_tokens
/compress tenant_profile force_drop_phrases
/v1/compress compatibility response
/v1/messages/compress user-only compression
/v1/messages/compress non-user preservation
/v1/messages/compress mixed text and image parts
/tokens/estimate passthrough
```

Smoke tests:

```powershell
$env:API_URL="https://compress-staging.usagetap.com/compress"
python scripts\smoke_test.py
```

Production checks:

```text
p50/p95/p99 edge latency
p50/p95/p99 origin latency
origin call count
cache hit rate
skip rate
429 count by tenant
5xx count by route
Cloud Run instance count
Cloud Run cold-start symptoms
token savings distribution
```

## Deployment Commands

Once the edge scaffold exists:

```powershell
cd edge
npm install
npm test
npx wrangler dev
npx wrangler deploy
```

Use the default Worker service, not a Wrangler `--env staging` service:

```text
prompt-compression-edge
```

Cloudflare still provides production and preview deployments for that one
Worker. While `usagetap.com` is not connected to Cloudflare, the stable public
URL is:

```text
https://prompt-compression-edge.troy-magennis.workers.dev
```

Preview deployments, when enabled by Cloudflare, use generated preview URLs for
the same Worker. Avoid `npx wrangler deploy --env staging` unless a deliberately
separate Worker service is needed again.

Set secrets with:

```powershell
npx wrangler secret put ORIGIN_SHARED_SECRET
npx wrangler secret put GOOGLE_SERVICE_ACCOUNT_EMAIL
npx wrangler secret put GOOGLE_SERVICE_ACCOUNT_PRIVATE_KEY
npx wrangler secret put GOOGLE_CLOUD_RUN_AUDIENCE
```

Do not commit `.dev.vars`, service account JSON files, `.wrangler/`, or
`node_modules/`.

## What Not To Do Yet

Do not:

```text
rewrite the Python compressor in TypeScript
move LLMLingua-2 into Workers
change the Docker image
change Cloud Run concurrency or memory as part of the edge scaffold
make edge TOON conversion the default before parity tests
cache partial or failed responses
trust IP-only rate limits for tenant accounting
log prompt bodies
```

## First Implementation Milestone

The first code milestone should be intentionally small:

```text
edge Worker scaffold
transparent proxy for the supported routes
request ID propagation
body-size guard
JSON error responses
basic structured logs
local Worker tests
staging deploy notes
```

After that works, add rate limiting and exact-response caching. Only then add
skip heuristics and deterministic edge transforms.

## Current Worker Scaffold

The repository now includes an initial `edge/` Worker package that implements
the first milestone plus a conservative deterministic fallback path.

Implemented routes:

```text
GET  /health
POST /compress
POST /v1/compress
POST /v1/messages/compress
POST /tokens/estimate
```

Routing behavior:

```text
/compress defaults to origin because the Python API defaults to model_force
/compress with mode=deterministic is answered at the edge
/v1/compress and /v1/messages/compress default to edge deterministic mode
supported JSON record-array deterministic requests are transformed at the edge
unsupported complex deterministic and tenant_profile requests delegate to ORIGIN_BASE_URL when configured
model_force and model_auto requests proxy to ORIGIN_BASE_URL
origin network failures and origin 5xx responses fall back to edge deterministic mode
/tokens/estimate is answered at the edge with the regex token estimator
successful 200 JSON responses use exact-response Cloudflare Cache API caching
tenant-aware rate limits run before cache/origin work
```

The edge deterministic implementation intentionally handles only a small safe
subset:

```text
remove <nocompress> wrappers while preserving inner content
trim trailing line whitespace
collapse 3+ blank lines to 2 blank lines
trim leading/trailing request text
avoid whitespace rewriting inside obvious code/script/style/template/svg blocks
convert safe medium/large JSON record arrays to compact TOON-like text
```

This does not replace the full Python deterministic pipeline. Full JSON-to-TOON
coverage, HTML-to-Markdown, protected span handling, broad duplicate-key
handling, and model gate diagnostics should stay in Cloud Run until parity tests
exist for a wider TypeScript port.

The Worker now has a narrow JSON edge subset modeled after the Python
preprocessor: it scans balanced JSON candidates inside the prompt, applies the
same medium/large threshold shape, refuses exact/verbatim JSON contexts,
duplicate-key JSON, schemas, tool/function exchanges, and unsupported nested
objects, and only transforms homogeneous arrays of scalar records. Unsupported
deterministic requests that include tenant_profile overrides, HTML,
markdown/code fences, schemas, tool/function exchanges, or `include_sections` /
`include_diagnostics` requests still delegate to Cloud Run when `ORIGIN_BASE_URL`
is configured. If the origin is unavailable, the Worker returns a
schema-compatible deterministic fallback with an edge warning header/body signal
rather than returning a blanket 503.

Current Worker tests cover route allowlisting, CORS preflight, content-type
validation, invalid JSON, body-size rejection, request ID propagation, mode and
settings validation, token estimation, origin pass-through, origin 4xx
pass-through, origin 5xx/network fallback, shared-secret origin forwarding,
edge deterministic response shapes, generic deterministic text cases, complex
deterministic origin delegation, tenant_profile origin delegation/fallback, and
the supported/unsupported JSON edge-transform cases.

The Worker uses `caches.default` for short-lived exact-response caching. Cache
keys are synthetic GET URLs built from a SHA-256 hash of canonical JSON that
includes:

```text
cache schema version
route
X-Tenant-ID
canonical request body
```

The cache key does not include raw authorization tokens or raw prompt text.

Cache behavior:

```text
CACHE_ENABLED=true enables cache when Cloudflare Cache API is available
CACHE_TTL_SECONDS controls response TTL
default TTL is 300 seconds
Cache-Control: no-store bypasses cache
include_diagnostics=true bypasses cache
debug=true bypasses cache
only 200 JSON responses are stored
fallback-deterministic responses are not stored
```

Cache response headers:

```text
X-Edge-Cache: hit|miss|store|bypass|disabled
X-Edge-Decision: cache-hit on cache hits
```

Rate limiting behavior:

```text
RATE_LIMIT_ENABLED=true enables rate limiting
RATE_LIMIT_LOCAL_FALLBACK=true enables per-isolate fallback buckets
native Cloudflare rate limit bindings are used when configured
rate limits run before cache hits are served
RATE_LIMIT_TENANT_TIERS maps tenant ids to blocked|strict|default|trusted
```

Rate-limit key priority:

```text
Authorization header hash
tenant_id from JSON body
X-Tenant-ID header
CF-Connecting-IP / X-Forwarded-For
```

Default local fallback policies:

```text
/health: 120 requests per minute
/tokens/estimate: 120 requests per minute
/compress and /v1/compress: 30 requests per minute
/v1/messages/compress: 20 requests per minute
```

Native tenant tiers:

```text
default compress: 30 requests per minute
strict compress: 10 requests per minute
trusted compress: 120 requests per minute
default messages: 20 requests per minute
strict messages: 5 requests per minute
trusted messages: 80 requests per minute
```

Example tenant tier config:

```json
{
  "trial_tenant_123": "strict",
  "enterprise_tenant_456": "trusted",
  "abusive_tenant_789": "blocked"
}
```

The local fallback is an immediate safety net, not strong distributed quota
enforcement. For production-grade abuse protection, configure Cloudflare native
rate limit bindings and bind them as:

```text
RATE_LIMIT_HEALTH
RATE_LIMIT_ESTIMATE
RATE_LIMIT_COMPRESS
RATE_LIMIT_COMPRESS_STRICT
RATE_LIMIT_COMPRESS_TRUSTED
RATE_LIMIT_MESSAGES
RATE_LIMIT_MESSAGES_STRICT
RATE_LIMIT_MESSAGES_TRUSTED
```

Cloudflare's Rate Limiting binding is fast and useful for abuse protection, but
it is intentionally permissive and eventually consistent per Cloudflare
location. It should not be treated as a billing or exact quota ledger.

Rate-limited responses return:

```text
HTTP 429
X-Edge-Decision: reject
Retry-After: <seconds> when known
```

Important deployment rule: `ORIGIN_BASE_URL` must be the raw Cloud Run service
URL or another non-Cloudflare origin URL. Do not set it to
`https://compress.usagetap.com`, because that hostname is the Worker route after
cutover and would proxy back into the Worker.

Local validation:

```powershell
cd edge
npm install
npm test
npm run typecheck
```
