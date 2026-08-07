# GPU Cloud Run Preparation

This runbook deploys the GPU container to the single production Cloud Run
service named `prompt-compression`. There is no separate production CPU service.
The Artifact Registry image is named `prompt-compression-gpu` only to identify
its CUDA build; the image name must never be reused as the Cloud Run service
name.

The GPU image follows the Cloud Run GPU best-practice shape for this repository:

- Use a GPU framework base image instead of assembling CUDA in `python:slim`.
- Bake the current Hugging Face compression model into the image because it is
  small enough for the container-image loading path.
- Run the deployed service with `COMPRESSOR_DEVICE=cuda`.
- Use the GPU-aware `model_auto` defaults (2,000 candidate tokens and 200
  expected saved tokens), or override the corresponding
  `COMPRESSOR_GPU_MIN_MODEL_*` variables after benchmarking.
- Preload the base compression model during startup with
  `COMPRESSOR_PRELOAD_SLOTS=base`.
- Start with `--concurrency 1`, then raise it only after load testing.

References:

- GPU configuration:
  `https://docs.cloud.google.com/run/docs/configuring/services/gpu`
- GPU inference best practices:
  `https://docs.cloud.google.com/run/docs/configuring/services/gpu-best-practices`
- Billing settings:
  `https://docs.cloud.google.com/run/docs/configuring/billing-settings`
- Deep Learning Containers:
  `https://docs.cloud.google.com/deep-learning-containers/docs/choosing-container`

## Files

`Dockerfile.gpu` builds the GPU image. It defaults to Google's PyTorch CUDA Deep
Learning Container:

```text
us-docker.pkg.dev/deeplearning-platform-release/gcr.io/pytorch-cu124.2-4.py310
```

`cloudbuild.gpu.yaml` builds and pushes the GPU image with a larger Cloud Build
machine and disk, matching Google's guidance for model-bearing images.

## Configure Shell Variables

```powershell
gcloud config set project YOUR_PROJECT_ID
$env:REGION="us-central1"
$env:SERVICE="prompt-compression"
$env:IMAGE_NAME="prompt-compression-gpu"
$env:REPO="prompt-compression"
$env:PROJECT_ID="$(gcloud config get-value project)"
$env:IMAGE_TAG="$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$env:IMAGE="$env:REGION-docker.pkg.dev/$env:PROJECT_ID/$env:REPO/$env:IMAGE_NAME`:$env:IMAGE_TAG"
$env:PROJECT_NUMBER="$(gcloud projects describe $env:PROJECT_ID --format='value(projectNumber)')"
$env:RUNTIME_SERVICE_ACCOUNT="$env:PROJECT_NUMBER-compute@developer.gserviceaccount.com"
$env:METERING_SECRET="UsageTap_Meter_Compression_API_Key"
$env:METERING_SECRET_VERSION="1"
if ($env:SERVICE -ne "prompt-compression") {
  throw "Production Cloud Run service must remain prompt-compression."
}
if ([string]::IsNullOrWhiteSpace($env:PROJECT_NUMBER)) {
  throw "Unable to resolve the Google Cloud project number."
}
```

## Enable APIs

```powershell
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com
```

The Cloud Run runtime service account needs payload access to the metering
secret. This binding is scoped to that single secret and is safe to rerun:

```powershell
gcloud secrets add-iam-policy-binding $env:METERING_SECRET `
  --project $env:PROJECT_ID `
  --member "serviceAccount:$env:RUNTIME_SERVICE_ACCOUNT" `
  --role "roles/secretmanager.secretAccessor"
```

Create the Artifact Registry repository once if it does not exist:

```powershell
gcloud artifacts repositories create $env:REPO `
  --repository-format=docker `
  --location=$env:REGION `
  --description="Prompt Compression images"
```

## Build The GPU Image

```powershell
gcloud builds submit `
  --config cloudbuild.gpu.yaml `
  --substitutions="_REGION=$env:REGION,_REPO=$env:REPO,_IMAGE_NAME=$env:IMAGE_NAME,_IMAGE_TAG=$env:IMAGE_TAG" `
  .
```

To test a different compression model, override `_COMPRESSOR_MODEL`:

```powershell
gcloud builds submit `
  --config cloudbuild.gpu.yaml `
  --substitutions="_REGION=$env:REGION,_REPO=$env:REPO,_IMAGE_NAME=$env:IMAGE_NAME,_IMAGE_TAG=$env:IMAGE_TAG,_COMPRESSOR_MODEL=YOUR_HUGGING_FACE_MODEL" `
  .
```

To test a different GPU base image, override `_GPU_BASE_IMAGE`:

```powershell
gcloud builds submit `
  --config cloudbuild.gpu.yaml `
  --substitutions="_REGION=$env:REGION,_REPO=$env:REPO,_IMAGE_NAME=$env:IMAGE_NAME,_IMAGE_TAG=$env:IMAGE_TAG,_GPU_BASE_IMAGE=YOUR_GPU_BASE_IMAGE" `
  .
```

## Prepared Deploy Command

This command updates the existing `prompt-compression` service and therefore
preserves its domain mapping. Do not substitute `prompt-compression-gpu` for
`$env:SERVICE`; that is only the image name.

The default prepared shape targets one NVIDIA L4 GPU with the minimum supported
4 CPU and 16 GiB memory. It scales to zero when idle and caps scaling at one
instance for development. Cloud Run requires instance-based billing for GPU and
requires `--max-instances` to stay within regional GPU quota.

```powershell
gcloud run deploy $env:SERVICE `
  --image $env:IMAGE `
  --region $env:REGION `
  --platform managed `
  --service-account $env:RUNTIME_SERVICE_ACCOUNT `
  --allow-unauthenticated `
  --port 8080 `
  --cpu 4 `
  --memory 16Gi `
  --gpu 1 `
  --gpu-type nvidia-l4 `
  --no-cpu-throttling `
  --no-gpu-zonal-redundancy `
  --min-instances 0 `
  --max-instances 1 `
  --concurrency 1 `
  --timeout 300s `
  --update-secrets "USAGETAP_METERING_API_KEY=$($env:METERING_SECRET):$($env:METERING_SECRET_VERSION)" `
  --set-env-vars "COMPRESSOR_DEVICE=cuda,COMPRESSOR_MIN_RATE=0.45,COMPRESSOR_PRELOAD_SLOTS=base,COMPRESSOR_GPU_P50_FIXED_OVERHEAD_MS=150,COMPRESSOR_GPU_P50_LLMLINGUA_CHUNK_MS=120,COMPRESSOR_GPU_P50_TOKEN_ESTIMATE_MS=80,USAGETAP_API_BASE_URL=https://api.usagetap.com,USAGETAP_AUTHORIZATION_TIMEOUT_SECONDS=3,USAGETAP_COMPRESSION_KEY_MIN_SUFFIX_LENGTH=43,USAGETAP_COMPRESSION_KEY_MAX_SUFFIX_LENGTH=43,USAGETAP_AUTHORIZATION_FAILURE_CACHE_SECONDS=5"
```

`--allow-unauthenticated` applies only to Google IAM ingress. The application
still requires `Authorization: Bearer cmp-...` and checks UsageTap PAYG credit
before every operation on `/compress`, `/v1/compress`, and
`/v1/messages/compress`.

`USAGETAP_API_BASE_URL` and `USAGETAP_AUTHORIZATION_TIMEOUT_SECONDS` are
non-secret settings with production defaults of `https://api.usagetap.com` and
`3` seconds. The local credential sanity gate requires a `cmp-` key with exactly
43 Base64URL characters after the prefix. Definitive authorization failures are cached by salted
digest for five seconds; successful checks are never cached.

`USAGETAP_METERING_API_KEY` is a runtime-only Secret Manager
reference pinned to secret version `1`; the secret value never enters the
repository, Cloud Build, or command line. The authorization flow does not use
this platform key—it forwards only the caller's incoming `cmp-` credential.
Application startup rejects the metering configuration unless the trimmed
secret is `ck-` or `cmp-` followed by exactly 43 Base64URL characters; rejected
values are never included in errors or logs.

For the public UI demo, `POST /demo/session` issues signed `demo-v1` sessions.
Each session lasts 10 minutes and has its own operation/input allowances.
Firestore persists session state, per-network issuance rate limits, and UTC-day
per-network/global quotas across restarts and scale-to-zero. Only an HMAC digest
of the trusted Cloud Run client address is stored. Demo identity is deliberately
separate from UsageTap customers: it cannot set a `customerId` and is not sent
to `/custom_meter`. The UI keeps the returned credential only in page memory.

Enable Firestore once in the same region as the service, create the default
Native-mode database if it does not already exist, and grant the runtime service
account data access:

```powershell
gcloud services enable firestore.googleapis.com --project $env:PROJECT_ID
gcloud firestore databases create `
  --project $env:PROJECT_ID `
  --database="(default)" `
  --location=$env:REGION `
  --type=firestore-native
gcloud projects add-iam-policy-binding $env:PROJECT_ID `
  --member "serviceAccount:$env:RUNTIME_SERVICE_ACCOUNT" `
  --role "roles/datastore.user"
gcloud firestore fields ttls update expireAt `
  --project $env:PROJECT_ID `
  --database="(default)" `
  --collection-group=prompt_compression_demo_v1 `
  --enable-ttl
```

Enable the public demo with persistent rate limits and daily quotas:

```powershell
$env:DEMO_SIGNING_SECRET="PromptCompression_Demo_Signing_Key"
$env:DEMO_SIGNING_SECRET_VERSION="1"

gcloud run services update $env:SERVICE `
  --region $env:REGION `
  --max-instances 1 `
  --concurrency 1 `
  --update-secrets "USAGETAP_DEMO_SIGNING_KEY=$($env:DEMO_SIGNING_SECRET):$($env:DEMO_SIGNING_SECRET_VERSION)" `
  --remove-env-vars "USAGETAP_DEMO_MODE_EXPIRES_AT,USAGETAP_DEMO_MAX_ACTIVE_SESSIONS" `
  --update-env-vars "USAGETAP_DEMO_MODE_ENABLED=true,USAGETAP_DEMO_SESSION_TTL_SECONDS=600,USAGETAP_DEMO_MAX_OPERATIONS_PER_SESSION=5,USAGETAP_DEMO_MAX_INPUT_CHARS_PER_SESSION=50000,USAGETAP_DEMO_MAX_INPUT_CHARS_PER_OPERATION=20000,USAGETAP_DEMO_RATE_LIMIT_SESSIONS=2,USAGETAP_DEMO_RATE_LIMIT_WINDOW_SECONDS=3600,USAGETAP_DEMO_MAX_SESSIONS_PER_CLIENT_PER_DAY=5,USAGETAP_DEMO_MAX_OPERATIONS_PER_CLIENT_PER_DAY=25,USAGETAP_DEMO_MAX_INPUT_CHARS_PER_CLIENT_PER_DAY=100000,USAGETAP_DEMO_MAX_SESSIONS_PER_DAY=100,USAGETAP_DEMO_MAX_OPERATIONS_PER_DAY=250,USAGETAP_DEMO_MAX_INPUT_CHARS_PER_DAY=2000000,USAGETAP_DEMO_STORAGE_BACKEND=firestore,USAGETAP_DEMO_FIRESTORE_PROJECT=$env:PROJECT_ID,USAGETAP_DEMO_FIRESTORE_DATABASE=(default),USAGETAP_DEMO_FIRESTORE_COLLECTION=prompt_compression_demo_v1"
```

Turn issuance and validation off immediately if the demo must be suspended:

```powershell
gcloud run services update $env:SERVICE `
  --region $env:REGION `
  --update-env-vars "USAGETAP_DEMO_MODE_ENABLED=false"
```

Use `--no-allow-unauthenticated` only if the API should require Google IAM in
addition to UsageTap authorization. In that configuration, callers must put the
Google identity token in `X-Serverless-Authorization` and keep the UsageTap
credential in `Authorization`.

## Optional Adapter Slots

If deploying the synthetic LoRA probes, train them before the build so the local
adapter directories are copied into the image:

```powershell
python scripts\train_lora_probe_tenant.py --device cpu
python scripts\train_lora_probe_tenant.py --probe-profile rick --device cpu
```

Then include the adapter slots and preload list when deploying:

```powershell
--set-env-vars "COMPRESSOR_DEVICE=cuda,COMPRESSOR_MIN_RATE=0.45,COMPRESSOR_ADAPTER_SLOTS=tenant_lora_probe=models/tenant_lora_probe;tenant_rick_probe=models/tenant_rick_probe,COMPRESSOR_PRELOAD_SLOTS=base;tenant_lora_probe;tenant_rick_probe"
```

## Verify After Deployment

After a future deployment:

```powershell
$env:SERVICE_URL="$(gcloud run services describe $env:SERVICE --region $env:REGION --format='value(status.url)')"
curl "$env:SERVICE_URL/health"
$env:API_URL="$env:SERVICE_URL/compress"
python scripts\smoke_test.py
```

The health response should show `"model_loaded": true` when
`COMPRESSOR_PRELOAD_SLOTS=base` is set.

Before relying on `model_auto`, use `Model force` on the benchmark page to
measure warm GPU fixed overhead, per-chunk LLMLingua latency, and token-estimate
latency. Configure the measured p50 values as
`COMPRESSOR_GPU_P50_FIXED_OVERHEAD_MS`,
`COMPRESSOR_GPU_P50_LLMLINGUA_CHUNK_MS`, and
`COMPRESSOR_GPU_P50_TOKEN_ESTIMATE_MS`. Without a latency baseline,
`model_auto` deliberately reports `llmlingua_skipped_missing_latency_baseline`
after the size and ROI gates pass.

The current L4 baseline was measured on 2026-07-24 after CUDA warm-up:
150 ms fixed overhead, 120 ms per LLMLingua chunk, and 80 ms token estimation.
Re-measure these values after changing the GPU type, model, tokenizer, chunk
size, or major preprocessing behavior.

The benchmark `Measurement` control has three deliberately different modes:

- `Production latency` disables diagnostics and measures the normal request
  path. Model-call and phase telemetry display as unavailable rather than zero.
- `Phase profile` records phase timings without expensive detailed analytics.
- `Deep analytics` includes counterfactual and provenance work and must not be
  compared directly with production latency.

The CLI equivalent is `--diagnostics off`, `basic`, or `detailed`.

## Precision And Host A/B Tests

The GPU image supports tagged-revision precision experiments without changing
production traffic:

- `COMPRESSOR_MODEL_DTYPE=auto|float32|float16|bfloat16`
- `COMPRESSOR_MODEL_RUNTIME=torch|onnx`
- `COMPRESSOR_TORCH_INFERENCE_MODE=true|false`

Leave the production defaults unchanged until the candidate passes compression
output and integrity parity checks. Test host sizes by deploying the same image
to tagged, no-traffic revisions; compare 4 CPU / 16 GiB with 8 CPU / 32 GiB
using the same generated cohort and a warm instance.

ONNX CUDA is an experimental build-time option because its runtime packages add
substantial image weight. Build it with
`_ENABLE_ONNX_RUNTIME=true`, then set
`COMPRESSOR_MODEL_RUNTIME=onnx` on a tagged no-traffic revision. The service
exports the already-resized LLMLingua token-classification model so its force
token vocabulary stays compatible, and uses CUDA I/O binding to avoid copying
model inputs and logits through host memory.

Collect Cloud Run CPU, memory, GPU, GPU-memory, concurrency, and instance-count
metrics for the exact benchmark interval with:

```powershell
python scripts\collect_cloud_run_metrics.py `
  --project $env:PROJECT_ID `
  --region $env:REGION `
  --service prompt-compression `
  --revision REVISION_NAME `
  --start 2026-07-24T20:00:00Z `
  --end 2026-07-24T20:05:00Z `
  --visibility-wait 130 `
  --out-dir data\benchmarks\RUN_NAME
```

Cloud Run utilization metrics are sampled every 60 seconds and can take up to
120 seconds to appear, so a sustained benchmark of at least three minutes is
more useful than a short latency-only burst.

For a controlled FP32/FP16 host experiment, use the repository runner:

```powershell
.\scripts\run_gpu_runtime_experiment.ps1 `
  -ProjectId $env:PROJECT_ID `
  -Region us-central1 `
  -Service prompt-compression `
  -Repeats 20 `
  -Warmup 3
```

The runner enforces the public service name `prompt-compression`, uses `-gpu`
only in container image names, deploys tagged revisions with `--no-traffic`,
and verifies that weighted production traffic remains unchanged. It builds one
Torch image for all FP32/FP16 controls, records an experiment manifest, gathers
revision-scoped Cloud Monitoring metrics, and writes output-parity hashes.

See `reports/gpu-experiment-2026-07-24.md` for the full method, interpretation,
acceptance gates, and exact manual commands. Add `-IncludeOnnx` only for an
intentional ONNX retest.

## Tuning Notes

Keep `--concurrency 1` for the first deployment. Increase it only after running
`scripts\benchmark_performance.py` against the deployed GPU service. If GPU
latency is stable and utilization is low, test small steps such as concurrency
`2`, `4`, and `8`.

If cold starts are unacceptable, set `--min-instances 1`. This keeps one GPU
instance warm and increases idle cost.
