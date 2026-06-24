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

Run the smoke test in another terminal:

```powershell
python scripts\smoke_test.py
```

## API

### `GET /health`

Checks whether the service is up.

### `GET /`

Opens a browser UI where you can paste a prompt, compress it, and inspect which
words were kept or dropped.

### `POST /compress`

Request:

```json
{
  "text": "Prompts are production code. Manage them that way.",
  "aggressiveness": 0.25
}
```

Response:

```json
{
  "compressed_text": "Prompts production code. Manage way.",
  "original_tokens": 12,
  "compressed_tokens": 8,
  "reduction": 0.3333,
  "aggressiveness": 0.25,
  "target_rate": 0.8625,
  "model": "microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank",
  "elapsed_ms": 123.4,
  "labeled_tokens": [
    {"text": "Prompts", "kept": true},
    {"text": "are", "kept": false},
    {"text": "production", "kept": true}
  ]
}
```

### `POST /v1/compress`

Compatibility endpoint for clients that expect a `/v1/compress` API with
`input`, `output`, and token-savings fields. This service runs the local
`COMPRESSOR_MODEL`. The `model` value is accepted for request compatibility.

Request:

```json
{
  "model": "bear-2",
  "input": "Prompts are production code. Manage them that way.",
  "compression_settings": {
    "aggressiveness": 0.25
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
  "compression_time": 123.4,
  "warnings": []
}
```

Use `http://127.0.0.1:8000/v1/compress` for the local compatible endpoint.
Bearer auth headers are ignored and not required by the local MVP.

## How Aggressiveness Works

This MVP maps `aggressiveness` to LLMLingua-2's retention `rate`.

```text
aggressiveness = 0.0 -> keep almost everything
aggressiveness = 0.5 -> moderate compression
aggressiveness = 1.0 -> keep at least COMPRESSOR_MIN_RATE of tokens
```

The output is deterministic for the same model and input. This is intentional: production prompt compression should be predictable and cache-friendly.

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
gcloud builds submit --tag $env:IMAGE .
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
  --set-env-vars COMPRESSOR_DEVICE=cpu,COMPRESSOR_MIN_RATE=0.45
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
rebuild with `--build-arg COMPRESSOR_MODEL=...` so the runtime stays offline and
deterministic.

Later optimization steps:

- Export the classifier to ONNX.
- Quantize to INT8.
- Add metrics for latency, reduction percentage, and model version.

## Notes

This project uses an existing LLMLingua-2 model for the first milestone. The next milestone is to create your own original/compressed pairs, convert them into KEEP/DROP labels, and fine-tune a smaller classifier on your own domain data.
