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

The Docker image targets Python 3.14.

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

## Cloud Run Hosting Shape

For the lightest hosted MVP:

1. Build this Docker image.
2. Push it to Artifact Registry or another container registry.
3. Deploy it to Cloud Run.
4. Use 1-2 vCPU and 1-2 GB RAM to start.
5. Set `min_instances=1` if cold starts are unacceptable.

Later optimization steps:

- Export the classifier to ONNX.
- Quantize to INT8.
- Bake the model into the image instead of downloading on first startup.
- Add metrics for latency, reduction percentage, and model version.

## Notes

This project uses an existing LLMLingua-2 model for the first milestone. The next milestone is to create your own original/compressed pairs, convert them into KEEP/DROP labels, and fine-tune a smaller classifier on your own domain data.
