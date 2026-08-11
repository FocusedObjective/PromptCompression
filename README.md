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

All real-time compression endpoints (`/compress`, `/v1/compress`, and
`/v1/messages/compress`) require a UsageTap compression credential:

```http
Authorization: Bearer cmp-...
```

The service rejects malformed credentials locally, then checks the credential
and current PAYG credit concurrently with compression. Missing credentials,
non-`cmp-` credentials, keys outside the configured length bounds, and keys
containing characters other than letters, numbers, `_`, or `-` return `401`
before GPU work starts. For locally sane keys, compression may run while the
remote check is pending, but its result is discarded unless UsageTap authorizes
the operation. Invalid credentials return `401`, unavailable credit returns
`402`, insufficient key permissions return `403`, and UsageTap or network
availability failures return `503`.

Definitive `401`, `402`, and `403` results are retained for five seconds in a
bounded process-local negative cache so recently rejected keys cannot
immediately consume more inference. Cache entries use a per-process salted HMAC,
never the raw key. Successful authorization is never cached: every compression
operation still performs a UsageTap credit check.

After compression, positive token savings are recorded in UsageTap `CUSTOM2`
using the verified authorization response's `customerId`. The metered amount is
`max(0, inputTokens - outputTokens)`. Zero savings do not produce a metering
request. Transient metering failures are retried once with the same stable
idempotency key; the compression response is released only after UsageTap
confirms `CUSTOM_METER_SUCCESS` or the matching
`CUSTOM_METER_ALREADY_RECORDED` replay. Prompt text is never included in meter
metadata.

Only the `Authorization` header is accepted as the credential source. Do not
put a key, `customerId`, or `organizationId` in the request body.

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

Tagged JSON authorizes deterministic structural transforms such as TOON while
keeping the block protected from LLMLingua. A bare tag requires no path:

```xml
<compress-json>
{"id":"ISSUE-73","description":"Long narrative..."}
</compress-json>
```

Use `embedded-paths` to deterministically decode JSON-encoded string values:

```xml
<compress-json embedded-paths="$.items[*].rawEntry">
{"items":[{"rawEntry":"{\"name\":\"Ada\"}"}]}
</compress-json>
```

Only `paths` authorizes LLMLingua for selected long narrative strings.
Production callers authorize those paths through `tenant_profile`; the
`/compress` profiler can instead opt into a simple inline form:

```xml
<compress-json paths="$.description,$.comments[*].body">
{"id":"ISSUE-73","description":"Long narrative...","comments":[{"body":"Long comment..."}]}
</compress-json>
```

Set `allow_inline_json_compression_paths` to `true` on the `/compress` request.
Inline authorization is disabled by default and unavailable on the v1
production endpoints. See [Tagged JSON Compression](docs/tagged-json-compression.md)
for tenant policies, supported paths, safety gates, and fallback behavior.

Set `include_sections` to `true` only for UI/debug views that need per-section
labels and protected-block rendering. It defaults to `false` to keep responses
small and skip word-label generation.

Fetched HTML can bypass automatic document sniffing with structured request
fields instead of prepending instructions or a source URL to `text`:

```json
{
  "text": "<!DOCTYPE html><html><body><h1>Visible page</h1></body></html>",
  "input_format": "html",
  "html_mode": "visible_text",
  "mode": "deterministic"
}
```

`visible_text` deterministically removes scripts, styles, templates, SVG, and
markup before compression while preserving visible page text. It fails open to
the original input unless the transform saves tokens. Use `html_mode: "verbatim"`
to protect the HTML source unchanged. The defaults remain `input_format: "auto"`
and `html_mode: "visible_text"` for backward compatibility.

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

Diagnostics schema `compression-diagnostics.v3` adds an `analytics` object.
It is intentionally expensive and contains the exact deterministic/model-stage
text, so only enable it for authorized benchmark inputs. Version 3 separates
content compression from temporary placeholder expansion and model restoration.
The older flat analytics fields remain for backward compatibility.

Sanitized shape (hashes shortened here only for readability):

```json
{
  "analytics": {
    "diagnostics_schema_version": "compression-diagnostics.v3",
    "request_id": "9da52f6a-...",
    "original_sha256": "91b7...",
    "stages": {
      "original": {"sha256": "91b7...", "characters": 37, "tokens": 10},
      "post_deterministic_content": {
        "sha256": "91b7...", "characters": 37, "tokens": 10,
        "net_tokens_saved": 0
      },
      "model_input_with_placeholders": {
        "sha256": "22cc...", "characters": 30, "tokens": 12,
        "placeholder_token_delta": 2
      },
      "model_output_before_restoration": {
        "sha256": "48aa...", "characters": 24, "tokens": 8,
        "raw_model_tokens_saved": 4
      },
      "final_restored": {
        "sha256": "7a5d...", "characters": 31, "tokens": 9,
        "net_model_tokens_saved": 1,
        "total_tokens_saved": 1
      }
    },
    "deterministic_text": "Review __CK_KEEP_0000__ now.",
    "deterministic_sha256": "22cc...",
    "deterministic_characters": 30,
    "deterministic_tokens": 8,
    "deterministic_tokens_saved": 2,
    "deterministic_transforms": [
      {
        "transform": "protected_span_substitution",
        "candidate_count": 1,
        "candidate_characters": 37,
        "candidate_tokens": 10,
        "applied_count": 1,
        "input_characters": 37,
        "output_characters": 30,
        "input_tokens": 10,
        "output_tokens": 8,
        "tokens_saved": 0,
        "token_delta": 2,
        "status": "applied",
        "reason": "applied",
        "elapsed_ms": 0.08,
        "enabled": true,
        "gate_reason_counts": {}
      }
    ],
    "deterministic_gate_reasons": {"no_candidate": 6},
    "model_input_sha256": "22cc...",
    "final_sha256": "7a5d...",
    "integrity": {
      "protected_span_validation_passed": true,
      "placeholder_restoration_validation_passed": true,
      "structural_validation_warnings": []
    },
    "provenance": {
      "benchmark_schema_version": "benchmark.v3",
      "diagnostics_schema_version": "compression-diagnostics.v3",
      "compressor_git_commit": "0123456789abcdef",
      "compressor_source_sha256": "cb62...",
      "model_revision": "c4c5...",
      "configuration_sha256": "73de..."
    }
  }
}
```

Stable transform codes are `whitespace_canonicalization`,
`force_drop_preprocessing`, `json_minification`, `json_to_toon`,
`html_to_markdown`, `nocompress_wrapper_handling`,
`exact_duplicate_block_removal`, `protected_span_substitution`, and
`placeholder_restoration`.

Stable deterministic gate reasons are `no_candidate`,
`invalid_ambiguous_syntax`, `json_parse_failed`, `inside_protected_span`,
`unsupported_structure`, `below_minimum_size`, `token_increase`,
`no_token_savings`, `density_safety_gate`, `tenant_configuration_disabled`,
`transform_failed`, and `duplicate_not_structurally_safe_to_remove`. An applied
transform uses reason `applied`. Status values are `applied`, `no_candidate`,
`skipped`, `failed`, and `no_savings`.

For disabled-transform opportunity analysis and unbiased reliability checks,
benchmark callers may additionally send:

```json
{
  "include_diagnostics": true,
  "evaluate_disabled_transforms": true,
  "evaluation_constraints": {
    "required_substrings": ["UT-1042"],
    "required_whitespace_insensitive_substrings": [],
    "forbidden_substrings": [],
    "required_json_keys": ["ticket_id"]
  }
}
```

Counterfactuals report only whether a disabled transform would apply, estimated
token savings, and an output hash; they never change the returned compressed
text. Evaluation constraints run after compression and never become force-keep
tokens. Deep diagnostics are available on `/compress`; `/v1/compress` retains
its compatibility schema.

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
    "aggressiveness": 0.15,
    "input_format": "auto"
  }
}
```

For fetched pages, set `compression_settings.input_format` to `html` and
`compression_settings.html_mode` to `visible_text`. Set `html_mode` to
`verbatim` when the HTML source itself is the protected payload.

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

Use `http://127.0.0.1:8000/v1/compress` for the local compatible endpoint. The
local service enforces the same UsageTap bearer authorization as Cloud Run.
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

Real-time compression routes also use a bounded, process-local TTL/LRU response
cache for identical recent requests. It runs inside each Cloud Run container,
keeps authorization and UsageTap metering per request, and separates every
validated compression setting in its key. See
[Local Compression Response Cache](docs/local-response-cache.md) for behavior,
configuration, analytics bypass rules, and the future edge migration plan.

By default, very small compressible segments skip the model to avoid expensive
LLMLingua calls with little expected token savings. Tune
`COMPRESSOR_MIN_SEGMENT_CHARS` and `COMPRESSOR_MIN_SEGMENT_TOKENS` if you prefer
more compression over latency.

`model_auto` also uses device-aware request-level ROI floors. CPU defaults to
20,000 model-candidate tokens and 2,000 expected saved tokens; GPU defaults to
2,000 candidates and 200 expected saved tokens. Override them independently
with `COMPRESSOR_CPU_MIN_MODEL_CANDIDATE_TOKENS`,
`COMPRESSOR_GPU_MIN_MODEL_CANDIDATE_TOKENS`,
`COMPRESSOR_CPU_MIN_MODEL_INCREMENTAL_SAVINGS_TOKENS`, and
`COMPRESSOR_GPU_MIN_MODEL_INCREMENTAL_SAVINGS_TOKENS`. The legacy
`COMPRESSOR_MIN_MODEL_CANDIDATE_TOKENS` and
`COMPRESSOR_MIN_MODEL_INCREMENTAL_SAVINGS_TOKENS` still set both devices when a
device-specific value is absent.

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

Production is the single GPU-backed Cloud Run service named
`prompt-compression`. Its Artifact Registry image is named
`prompt-compression-gpu`; the image name is not a deployable service name.

Compression authorization configuration:

- `USAGETAP_API_BASE_URL` defaults to `https://api.usagetap.com`.
- `USAGETAP_AUTHORIZATION_TIMEOUT_SECONDS` defaults to `3`.
- `USAGETAP_COMPRESSION_KEY_MIN_SUFFIX_LENGTH` defaults to `43`.
- `USAGETAP_COMPRESSION_KEY_MAX_SUFFIX_LENGTH` defaults to `43`.
- `USAGETAP_AUTHORIZATION_FAILURE_CACHE_SECONDS` defaults to `5`; set it to
  `0` to disable negative caching.
- `USAGETAP_METERING_TIMEOUT_SECONDS` defaults to `3`.
- `USAGETAP_METERING_API_KEY` must be supplied through the Cloud Run Secret
  Manager reference for positive-savings operations. Startup accepts only
  `ck-` or `cmp-` followed by exactly 43 Base64URL characters.

The main and benchmark UIs also support an explicitly enabled public demo mode.
A `POST /demo/session` call returns a signed `demo-v1` credential with a
10-minute TTL and bounded per-session operation/input allowances. New sessions
are rate limited by an HMAC-derived network identifier, and UTC-day quotas cap
sessions, operations, and input characters both per network and globally. Raw
client addresses are never stored. Configure `USAGETAP_DEMO_MODE_ENABLED` and
the `USAGETAP_DEMO_*` quota settings shown in `.env.example`; inject
`USAGETAP_DEMO_SIGNING_KEY` from Secret Manager.

Production uses `USAGETAP_DEMO_STORAGE_BACKEND=firestore`, so sessions and
quota counters survive revision changes, restarts, and scale-to-zero. Local
development defaults to the process-local `memory` backend. Demo requests never
receive a customer identity and skip customer metering. Setting
`USAGETAP_DEMO_MODE_ENABLED=false` immediately prevents new sessions and use of
existing sessions.

These settings are not credentials. Authorization forwards each request's
incoming `Bearer cmp-...` value directly to UsageTap. The platform-owned
metering key is mounted separately by the production deploy command as the
Secret Manager-backed `USAGETAP_METERING_API_KEY` environment variable. It is
reserved for the platform-owned `/custom_meter` integration and is never used
to authorize customer compression requests.

Use [`DEPLOYMENT_GPU.md`](DEPLOYMENT_GPU.md) for every production build,
deployment, rollback, and verification. `Dockerfile` and `cloudbuild.yaml` are
retained only for local CPU development and must not be deployed to the
production service.

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
The auto-candidate slider overrides the deployment floor for that benchmark
cohort. Deep counterfactual analytics is off by default because it hashes and
re-estimates exact stage texts and performs extra research-only work; enable it
only when measuring transform opportunities, not production-path latency.
Lightweight phase diagnostics remain enabled. JSON ratio, HTML ratio, and
protected-prose ratio are separate controls: a
`json0_html0_protected0` case is ordinary prose, while raising protected prose
deliberately adds IDs, dates, and URLs.

Against a deployed service:

```powershell
$env:API_URL="$env:SERVICE_URL/compress"
python scripts\benchmark_performance.py `
  --url $env:API_URL `
  --repeats 3 `
  --label "cpu=2" `
  --label "memory=4Gi"
```

Pass a UsageTap compression key to the benchmark script:

```powershell
$env:COMPRESSION_KEY="cmp-..."
python scripts\benchmark_performance.py `
  --url "$env:SERVICE_URL/compress" `
  --header "Authorization: Bearer $env:COMPRESSION_KEY"
```

Production currently allows unauthenticated Cloud Run ingress and performs
application authorization with the UsageTap key. If Google IAM is also enabled,
send its identity token as `X-Serverless-Authorization` so the `Authorization`
header remains available for the required UsageTap credential.

The script writes `raw.jsonl`, `raw.csv`, `summary.csv`, `summary.json`,
`metadata.json`, and `cases.json` under `data/benchmarks/<timestamp>`. Use
`summary.csv` for quick size-vs-latency comparisons, and `raw.jsonl` when you
need to inspect whether time went to preprocessing, token gating, model load, or
LLMLingua2 for an individual run.

To run the four paired conditions over one frozen Kanban Zone cohort, use:

```powershell
$env:API_URL="$env:SERVICE_URL/compress"
python scripts\benchmark_performance.py `
  --url $env:API_URL `
  --conditions unchanged,deterministic_only,model_force,deterministic_plus_model `
  --sizes 3000,12000,24000 `
  --repeats 3 `
  --save-prompts `
  --label "tenant=kanban-zone"
```

All conditions reuse the same in-memory cases. Each `benchmark.v3` JSONL record
includes `cohort_id`, `condition_id`, `prompt_id`, `original_sha256`, the original
and final text, nested `stages`, candidate opportunities, integrity results, and
provenance. Export aborts if prompt IDs or original hashes diverge between paired
conditions; it never refetches a cohort between conditions.

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
