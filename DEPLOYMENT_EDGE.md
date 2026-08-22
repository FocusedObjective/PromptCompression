# Lightweight Python CPU Edge

The CPU edge exposes the same compression API as the GPU service:

- `GET /health`
- `POST /tokens/estimate`
- `POST /compress`
- `POST /v1/compress`
- `POST /v1/messages/compress`
- `POST /v1/responses/compress`

Deterministic requests run locally using the production Python compression
pipeline. `model_auto` first completes the deterministic pipeline and evaluates
the shared GPU ROI/latency gate locally without loading a model. It returns the
deterministic result when the gate skips and forwards the original request only
when the gate decides that GPU work is worthwhile. `model_force` is forwarded
unchanged. If a required GPU origin is missing, times out, or returns a `5xx`,
the edge returns the planned schema-compatible deterministic result with the
warning `edge_origin_unavailable_deterministic_fallback`. A caller can bypass a
failed edge and send the identical request directly to the GPU API.

## Lightweight boundary

`Dockerfile.edge` deliberately excludes Torch, LLMLingua, PEFT, datasets, model
weights, LoRA adapters, and Firestore. It includes the Hugging Face tokenizer
files so deterministic token gates, response counts, cache eligibility, and
UsageTap metering use the same tokenizer as the GPU service.

The edge imports the existing `PromptCompressionService`, but invokes only its
deterministic pipeline and model-auto planning mode. Planning uses the GPU
policy and latency baseline but cannot load or call a model. The edge image does
not need model libraries.

## Environment

Required for model forwarding:

```text
EDGE_ORIGIN_BASE_URL=https://GPU_SERVICE_URL
```

The original caller `Authorization` header is passed to the GPU. The GPU remains
the authorization and metering boundary for successfully forwarded operations.
Malformed credentials are rejected locally before an origin call.

Local deterministic operations use the existing UsageTap settings:

```text
USAGETAP_API_BASE_URL=https://api.usagetap.com
USAGETAP_METERING_API_KEY=SECRET_MANAGER_VALUE
```

Optional settings:

```text
EDGE_ORIGIN_TIMEOUT_SECONDS=300
EDGE_ORIGIN_SHARED_SECRET=
EDGE_MAX_BODY_BYTES=1048576
EDGE_PRELOAD_TOKENIZER=true
COMPRESSOR_GPU_P50_FIXED_OVERHEAD_MS=150
COMPRESSOR_GPU_P50_LLMLINGUA_CHUNK_MS=120
COMPRESSOR_GPU_P50_TOKEN_ESTIMATE_MS=80
```

Keep the three GPU latency values synchronized with the deployed GPU runtime.
Without a complete baseline, `model_auto` safely remains local with
`llmlingua_skipped_missing_latency_baseline`.

`EDGE_ORIGIN_SHARED_SECRET` only adds the header; it is not a security boundary
unless the GPU ingress is separately configured to validate it. Prefer Cloud
Run IAM with a private GPU origin before production cutover. The caller
credential must remain in `Authorization`; Cloud Run IAM can use
`X-Serverless-Authorization` when that origin-protection phase is implemented.

## Local verification

Build and run the CPU edge:

```powershell
docker build -f Dockerfile.edge -t prompt-compression-edge:local .
docker run --rm -p 8081:8080 `
  -e EDGE_ORIGIN_BASE_URL=http://host.docker.internal:8080 `
  -e USAGETAP_METERING_API_KEY=$env:USAGETAP_METERING_API_KEY `
  prompt-compression-edge:local
```

Run the GPU/full API separately on port 8080, then send the same request to
ports 8081 and 8080. Deterministic responses should match apart from timing and
edge-only diagnostic headers.

## Build

```powershell
gcloud builds submit `
  --config cloudbuild.edge.yaml `
  --substitutions "_REGION=us-central1,_IMAGE_TAG=$(Get-Date -Format 'yyyyMMdd-HHmmss')" `
  .
```

Deploy under a distinct service name such as `prompt-compression-edge`. Do not
replace the existing `prompt-compression` GPU service. Start with one CPU, 512
MiB memory, request-based billing, `--min-instances 0`, and a small maximum
instance count. Set a minimum instance only after measuring regional cold-start
and tokenizer-warmup latency.
