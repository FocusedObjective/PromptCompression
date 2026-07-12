# PromptCompression

A minimal MVP for prompt compression using a fast token-classification model instead of an LLM at runtime.

The first milestone is:

- Run an HTTP API locally from VS Code.
- Compress text with the existing LLMLingua-2 token classifier.
- Control compression with an `aggressiveness` value from `0.0` to `1.0`.
- Return token-count reduction stats.

## Quick Start

Open this folder in VS Code:

```powershell
cd C:\Users\troym\Git\PromptCompression
code .
```

Create a virtual environment:

```powershell
python3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

Start the API:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The first request may take a while because the model downloads from Hugging Face.
The service runs the compression model on CPU by default. Set `COMPRESSOR_DEVICE=cuda`
before starting the API if you have a CUDA-enabled PyTorch install.

Open the API docs:

```text
http://127.0.0.1:8000/docs
```

Open the prompt compression UI:

```text
http://127.0.0.1:8000/
```

Open the eval suite:

```text
http://127.0.0.1:8000/eval
```

Run the smoke test in another terminal:

```powershell
python scripts\smoke_test.py
```

## API

### `GET /health`

Checks whether the service is up.

Response:

```json
{
  "status": "ok",
  "deployment_version": "2026.07.01.110308",
  "deployment_timestamp": "2026-07-01T11:03:08-07:00",
  "model": "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
  "model_loaded": false
}
```

### `GET /`

Opens a browser UI where you can paste a prompt, compress it, and inspect which
words were kept or dropped.

### `GET /eval`

Opens a browser UI for running curated quality checks against the compressor.
The eval suite compares original and compressed prompts, checks required
substrings and protected structures, and reports token savings and latency.

### `GET /eval/cases`

Returns the built-in eval cases from `data/eval_cases.json`.

### `POST /eval/run`

Runs all eval cases or a selected subset.

Request:

```json
{
  "case_ids": ["support_escalation_with_toon_data"],
  "aggressiveness": 0.25
}
```

Omit `case_ids` to run all cases. Omit `aggressiveness` to use each case's
default setting. Quality failures are based on required/forbidden substrings and
expected protected section types. Token reduction and latency targets are
reported as warnings so production sampling can track regressions without
conflating savings with correctness.

### `POST /compress`

Request:

```json
{
  "tenant_id": "tenant_123",
  "tenant_profile": {
    "profile_id": "tenant_123:v1",
    "default_aggressiveness": 0.2,
    "min_rate": 0.6,
    "force_keep_tokens": ["AcctSuite", "tenant_field"],
    "force_drop_phrases": ["Please carefully review the following context"]
  },
  "text": "Prompts are production code. Manage them that way.",
  "aggressiveness": 0.15,
  "include_sections": false,
  "include_diagnostics": false
}
```

Tenant fields are optional. They are request scoped and are not loaded from a
local database. If `aggressiveness` is omitted, `tenant_profile.default_aggressiveness`
is used when provided.

Tagged JSON can opt into tenant-approved compression of selected long string
values while keeping keys, types, arrays, and all other values deterministic:

```json
{
  "tenant_profile": {
    "json_compression_policy_id": "issue-v1",
    "json_value_compression_paths": [
      "$.description",
      "$.comments[*].body"
    ],
    "json_value_min_tokens": 200,
    "json_value_max_reduction": 0.25,
    "json_value_max_values": 8
  },
  "text": "Review this issue:\n<compress-json policy=\"issue-v1\">{\"id\":\"ISSUE-73\",\"description\":\"Long narrative...\"}</compress-json>"
}
```

The tag cannot authorize fields by itself: its policy must match the tenant
profile and only allowlisted string paths are eligible. Each accepted value is
compressed independently and JSON-escaped during reconstruction. The rebuilt
object is then TOON-encoded when beneficial, or otherwise protected verbatim
before any outer model-compression call. Invalid JSON, duplicate keys,
unapproved policies, unlisted paths, and values that fail the token or maximum
reduction gates are not model-compressed.

See [Tagged JSON Compression](docs/tagged-json-compression.md) for the complete
tag grammar, tenant schema, supported path syntax, safety gates, mode behavior,
fallback warnings, and operational guidance.

Set `include_sections` to `true` only for UI/debug views that need per-section
labels and protected-block rendering. It defaults to `false` to keep responses
small and skip word-label generation.

Response:

```json
{
  "compressed_text": "Prompts production code. Manage way.",
  "original_tokens": 12,
  "compressed_tokens": 8,
  "reduction": 0.3333,
  "aggressiveness": 0.15,
  "target_rate": 0.9175,
  "model": "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
  "tenant_id": "tenant_123",
  "compression_profile": "tenant_123:v1",
  "compression_profile_source": "api",
  "training_sample_recorded": false,
  "token_estimator": "huggingface:microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
  "compression_mode": "model_force",
  "compression_path": "deterministic_plus_model",
  "token_savings": {
    "original_tokens": 12,
    "after_deterministic_tokens": 10,
    "final_tokens": 8,
    "deterministic_tokens_saved": 2,
    "model_incremental_tokens_saved": 2,
    "total_tokens_saved": 4,
    "deterministic_reduction": 0.1666666667,
    "model_incremental_reduction": 0.2,
    "total_reduction": 0.3333333333,
    "model_stage": "llmlingua2",
    "model_ran": true,
    "fallback_used": false,
    "attribution_residual_tokens": 0,
    "token_estimator": "huggingface:microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank"
  },
  "elapsed_ms": 123.4,
  "labeled_tokens": [],
  "output_sections": []
}
```

`token_savings` is always returned. `original_tokens`,
`after_deterministic_tokens`, and `final_tokens` are the counts before
compression, after deterministic transforms, and in the returned output.
The three `*_tokens_saved` fields are the corresponding differences, while
the reductions divide deterministic savings by the original count, incremental
model savings by the deterministic count, and total savings by the original
count. Zero denominators produce `0.0`. `model_ran` reports whether LLMLingua2
was actually called, `fallback_used` reports whether a model chunk fell back,
and `attribution_residual_tokens` checks that the two stages reconcile with the
total. Every value uses the returned `token_estimator`.

For a deterministic-only path from 1,000 to 850 tokens, deterministic savings
are 150 and incremental model savings are zero. For a deterministic-plus-model
path from 1,000 to 850 to 600 tokens, deterministic savings are 150 and model
savings are 250. Model savings are incremental relative to the deterministic
output, not relative to the original input.

Set `include_diagnostics` to `true` for benchmark runs. The response then
includes phase-level timings for preprocessing, segment selection/token gating,
model load, LLMLingua2, placeholder expansion, uncompressed-output expansion,
and final token estimates, plus segment counts and model-input sizes.
Detailed diagnostics are off by default because collecting component-level
measurements adds work. Enable them explicitly in the request with:

```json
{
  "text": "Prompts are production code. Manage them that way.",
  "include_diagnostics": true
}
```

### `POST /tokens/estimate`

Returns the backend token estimate used by the UI. Omit `model` to use the
compression model tokenizer when available, with a deterministic regex fallback.
Provide `model` to request a downstream estimate when a supported tokenizer is
available, such as `tiktoken` for OpenAI-style model names.
Hugging Face tokenizer estimates use local files by default; set
`COMPRESSOR_TOKENIZER_ALLOW_DOWNLOAD=1` if you want this endpoint to download
tokenizer files independently of the compressor model load.

Request:

```json
{
  "text": "Prompts are production code.",
  "model": "gpt-4o"
}
```

Response:

```json
{
  "tokens": 5,
  "token_estimator": "tiktoken:o200k_base",
  "tokenizer_backed": true
}
```

### `POST /v1/compress`

Compatibility endpoint for clients that expect a `/v1/compress` API with
`input`, `output`, and token-savings fields. This service runs the local
`COMPRESSOR_MODEL`. The `model` value is accepted for request compatibility.

Request:

```json
{
  "tenant_id": "tenant_123",
  "tenant_profile": {
    "profile_id": "tenant_123:v1",
    "force_keep_tokens": ["AcctSuite"]
  },
  "model": "bear-2",
  "input": "Prompts are production code. Manage them that way.",
  "compression_settings": {
    "aggressiveness": 0.15
  }
}
```

Response:

```json
{
  "output": "Prompts production code. Manage way.",
  "output_tokens": 8,
  "input_tokens": 12,
  "original_input_tokens": 12,
  "tokens_saved": 4,
  "compression_ratio": 1.5,
  "token_estimator": "huggingface:microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
  "downstream_estimated_input_tokens": 11,
  "downstream_estimated_output_tokens": 7,
  "downstream_token_estimator": "regex:unicode-word-or-non-space",
  "compression_time": 123.4,
  "tenant_id": "tenant_123",
  "compression_profile": "tenant_123:v1",
  "compression_profile_source": "api",
  "training_sample_recorded": false,
  "warnings": []
}
```

Use `http://127.0.0.1:8000/v1/compress` for the local compatible endpoint.
Bearer auth headers are ignored and not required by the local MVP.
Clients that cannot add `tenant_id` to the JSON body may send `X-Tenant-ID`.

### `POST /v1/messages/compress`

Role-aware endpoint for vendor-style chat payloads. It preserves top-level
request fields and all non-user messages, then compresses only `user` message
string content or text parts such as `{"type": "text", "text": "..."}` and
`{"type": "input_text", "text": "..."}`. This keeps stable system/developer
instructions byte-stable for downstream prompt caching while reducing
request-specific user context.

Request:

```json
{
  "tenant_id": "tenant_123",
  "tenant_profile": {
    "profile_id": "tenant_123:v1",
    "default_aggressiveness": 0.2,
    "force_keep_tokens": ["AcctSuite"]
  },
  "model": "gpt-test",
  "system": "Stable system instructions remain unchanged.",
  "messages": [
    {"role": "developer", "content": "Preserve this exactly."},
    {"role": "user", "content": "Prompts are production code. Manage them that way."},
    {"role": "assistant", "content": "Prior answer remains unchanged."},
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Compress this user-supplied context."},
        {"type": "image", "source": {"media_type": "image/png"}}
      ]
    }
  ],
  "compression_settings": {
    "aggressiveness": 0.15
  }
}
```

Response:

```json
{
  "compressed_request": {
    "model": "gpt-test",
    "system": "Stable system instructions remain unchanged.",
    "messages": [
      {"role": "developer", "content": "Preserve this exactly."},
      {"role": "user", "content": "Prompts production code. Manage way."},
      {"role": "assistant", "content": "Prior answer remains unchanged."},
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "Compress user-supplied context."},
          {"type": "image", "source": {"media_type": "image/png"}}
        ]
      }
    ]
  },
  "messages": [
    {"role": "developer", "content": "Preserve this exactly."},
    {"role": "user", "content": "Prompts production code. Manage way."},
    {"role": "assistant", "content": "Prior answer remains unchanged."},
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Compress user-supplied context."},
        {"type": "image", "source": {"media_type": "image/png"}}
      ]
    }
  ],
  "input_tokens": 42,
  "output_tokens": 35,
  "original_input_tokens": 42,
  "tokens_saved": 7,
  "compression_ratio": 1.2,
  "compression_time": 123.4,
  "user_input_tokens": 24,
  "user_output_tokens": 17,
  "user_tokens_saved": 7,
  "non_user_tokens_preserved": 18,
  "token_estimator": "huggingface:microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
  "downstream_estimated_input_tokens": 40,
  "downstream_estimated_output_tokens": 33,
  "downstream_token_estimator": "regex:unicode-word-or-non-space",
  "tenant_id": "tenant_123",
  "compression_profile": "tenant_123:v1",
  "compression_profile_source": "api",
  "training_sample_recorded": false,
  "message_stats": [],
  "warnings": []
}
```

`tenant_id`, `tenant_profile`, and `compression_settings` are compressor
controls and are removed from `compressed_request` so they are not forwarded to a
downstream model provider.

## How Aggressiveness Works

This MVP maps `aggressiveness` to LLMLingua-2's retention `rate`.

```text
aggressiveness = 0.0 -> keep almost everything
aggressiveness = 0.5 -> moderate compression
aggressiveness = 1.0 -> keep at least COMPRESSOR_MIN_RATE of tokens
```

The output is deterministic for the same model and input. This is intentional: production prompt compression should be predictable and cache-friendly.

By default, very small compressible segments skip the model to avoid expensive
LLMLingua calls with little expected token savings. Tune
`COMPRESSOR_MIN_SEGMENT_CHARS` and `COMPRESSOR_MIN_SEGMENT_TOKENS` if you prefer
more compression over latency.

## VS Code

Included files:

- `.vscode/settings.json`: Python defaults.
- `.vscode/tasks.json`: run the API and smoke test.
- `.vscode/launch.json`: debug the FastAPI service.
- `.vscode/extensions.json`: recommended extensions.

## Docker

The Docker image targets Python 3.14 and exposes the API on container port `8080`.
The Hugging Face model is downloaded during the Docker build and baked into the
image so Cloud Run does not need to download it on first request.

For a complete Google Cloud Run deployment runbook starting from a machine with
nothing installed, see [DEPLOYMENT.md](DEPLOYMENT.md).

For the separate GPU Cloud Run container path, see
[DEPLOYMENT_GPU.md](DEPLOYMENT_GPU.md). The GPU path keeps the CPU container
unchanged and uses `Dockerfile.gpu` with `cloudbuild.gpu.yaml`.

Build:

```powershell
docker build -t prompt-compression .
```

Run:

```powershell
docker run --rm -p 8080:8080 prompt-compression
```

Then visit:

```text
http://127.0.0.1:8080/docs
```

### Docker Compose

For a repeatable local deployment instance with a persistent Hugging Face model
cache:

```powershell
docker compose up --build -d
```

Check the container:

```powershell
docker compose ps
curl http://127.0.0.1:8080/health
```

Run the smoke test against Docker:

```powershell
$env:API_URL="http://127.0.0.1:8080/compress"
python scripts\smoke_test.py
```

Stop it:

```powershell
docker compose down
```

To remove the downloaded model cache too:

```powershell
docker compose down -v
```

If Docker reports `Access is denied` for `//./pipe/docker_engine`, run the Docker
commands from an elevated terminal or update Docker Desktop permissions for your
Windows user.

## Cloud Run Hosting Shape

Cloud Run sends traffic to the port in its `PORT` environment variable. The
Dockerfile uses that value and falls back to `8080` for local Docker runs.

Set your project and region:

```powershell
gcloud config set project YOUR_PROJECT_ID
$env:REGION="us-central1"
$env:SERVICE="prompt-compression"
$env:REPO="prompt-compression"
$env:PROJECT_ID="$(gcloud config get-value project)"
$env:IMAGE="$env:REGION-docker.pkg.dev/$env:PROJECT_ID/$env:REPO/$env:SERVICE`:latest"
```

Enable the required services:

```powershell
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
```

Create the Artifact Registry repository once:

```powershell
gcloud artifacts repositories create $env:REPO `
  --repository-format=docker `
  --location=$env:REGION `
  --description="Prompt Compression images"
```

Build and push the image with Cloud Build:

```powershell
gcloud builds submit `
  --config cloudbuild.yaml `
  --substitutions="_REGION=$env:REGION,_REPO=$env:REPO,_SERVICE=$env:SERVICE" `
  .
```

Deploy to Cloud Run:

```powershell
gcloud run deploy $env:SERVICE `
  --image $env:IMAGE `
  --region $env:REGION `
  --platform managed `
  --allow-unauthenticated `
  --port 8080 `
  --cpu 2 `
  --memory 4Gi `
  --concurrency 1 `
  --timeout 300s `
  --set-env-vars "COMPRESSOR_DEVICE=cpu,COMPRESSOR_MIN_RATE=0.45"
```

Use `--no-allow-unauthenticated` instead of `--allow-unauthenticated` if the API
should require IAM authentication.

Check the deployed service:

```powershell
$env:SERVICE_URL="$(gcloud run services describe $env:SERVICE --region $env:REGION --format='value(status.url)')"
curl "$env:SERVICE_URL/health"
$env:API_URL="$env:SERVICE_URL/compress"
python scripts\smoke_test.py
```

For lower cold-start latency, redeploy with `--min-instances 1`. That keeps one
instance warm and increases idle cost. To use a different Hugging Face model,
rebuild through `cloudbuild.yaml` with `_COMPRESSOR_MODEL=...` so the runtime
stays offline and deterministic.

Later optimization steps:

- Export the classifier to ONNX.
- Quantize to INT8.
- Add metrics for latency, reduction percentage, and model version.

## Performance Benchmark

Use `scripts/benchmark_performance.py` to compare local, Docker, or Cloud Run
configurations. It generates deterministic prompts with target sizes from 256 to
200,000 tokens. The default target-size list has a median of 3,000 tokens and is
crossed with JSON-share targets of `0`, `0.1`, `0.25`, `0.5`, and `0.75`.

For an ad hoc production run, open the deployed benchmark page:

```text
https://YOUR-CLOUD-RUN-SERVICE-URL/benchmark
```

The page runs requests from your browser against that deployment's `/compress`
endpoint, captures the diagnostics timing fields, and provides raw JSONL and
summary CSV downloads. Use concurrency `1` when comparing Cloud Run CPU/memory
shapes unless you intentionally want to measure overlapping requests.

Against a deployed service:

```powershell
$env:API_URL="$env:SERVICE_URL/compress"
python scripts\benchmark_performance.py `
  --url $env:API_URL `
  --repeats 3 `
  --label "cpu=2" `
  --label "memory=4Gi"
```

For IAM-protected Cloud Run, pass an identity token as a header:

```powershell
$token = gcloud auth print-identity-token
python scripts\benchmark_performance.py `
  --url "$env:SERVICE_URL/compress" `
  --header "Authorization: Bearer $token"
```

The script writes `raw.jsonl`, `raw.csv`, `summary.csv`, `summary.json`,
`metadata.json`, and `cases.json` under `data/benchmarks/<timestamp>`. Use
`summary.csv` for quick size-vs-latency comparisons, and `raw.jsonl` when you
need to inspect whether time went to preprocessing, token gating, model load, or
LLMLingua2 for an individual run.

## Notes

This project uses an existing LLMLingua-2 model for the first milestone. The next milestone is to create your own original/compressed pairs, convert them into KEEP/DROP labels, and fine-tune a smaller classifier on your own domain data.

## Synthetic LoRA Probe

Use `scripts/train_lora_probe_tenant.py` to train a fictitious tenant adapter and
verify that loading the adapter changes model behavior. LLMLingua-2 is an
extractive token classifier, so a LoRA adapter can change KEEP/DROP probabilities
but cannot uppercase text or generate new wording. The probe therefore trains a
detectable marker behavior: keep `LORATENANT`, `ADAPTERACTIVE`, and `PROBEKEEP`
while deprioritizing synthetic boilerplate markers.

Install dev dependencies, including PEFT:

```powershell
pip install -r requirements-dev.txt
```

Run the probe:

```powershell
python scripts\train_lora_probe_tenant.py --device cpu
```

Run the stronger lowercase probe:

```powershell
python scripts\train_lora_probe_tenant.py --probe-profile rick --device cpu
```

The command writes a PEFT adapter and `probe_report.json` under
`models\tenant_lora_probe\`, then exits with status `0` only when the adapter
changes the compressed probe output and improves the marker keep/drop separation.
To retest an existing adapter without retraining:

```powershell
python scripts\train_lora_probe_tenant.py --device cpu --skip-train
```

To load the probe adapter in the API process, start the app with adapter slots
configured:

```powershell
$env:COMPRESSOR_ADAPTER_SLOTS="tenant_lora_probe=models\tenant_lora_probe"
$env:COMPRESSOR_PRELOAD_SLOTS="base;tenant_lora_probe"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Requests with `tenant_id=tenant_lora_probe` use the preloaded adapter slot.
To load both probe adapters locally:

```powershell
$env:COMPRESSOR_ADAPTER_SLOTS="tenant_lora_probe=models\tenant_lora_probe;tenant_rick_probe=models\tenant_rick_probe"
$env:COMPRESSOR_PRELOAD_SLOTS="base;tenant_lora_probe;tenant_rick_probe"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Other tenants use the base slot. The production Docker image includes the local
probe adapter directories from the build context, so train the adapters before
running `gcloud builds submit`. The main UI has a Test Preset dropdown for
base-vs-tenant comparisons.

Adapters can also be discovered at runtime from a shared adapter root. Put each
PEFT adapter in a direct child folder whose name matches the request
`tenant_id`:

```text
models/adapters/
  tenant_lora_probe/
    adapter_config.json
    adapter_model.safetensors
  tenant_rick_probe/
    adapter_config.json
    adapter_model.safetensors
```

Then start the app with:

```powershell
$env:COMPRESSOR_ADAPTER_ROOT="models\adapters"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

When a request arrives for `tenant_id=tenant_lora_probe`, the service checks
`models\adapters\tenant_lora_probe`, validates the adapter files, registers that
folder as a slot, and uses it for later requests. Tenant IDs used for runtime
discovery must be simple folder names containing letters, numbers, `_`, `-`, or
`.`. The reserved `base` and anonymous `default` IDs are not auto-discovered.
