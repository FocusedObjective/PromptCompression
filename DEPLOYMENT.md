# Deployment Guide

This project deploys to Google Cloud Run. The product is Cloud Run, not Code Run.

The container listens on the `PORT` environment variable that Cloud Run provides and falls back to port `8080` for local Docker runs. The Docker build downloads the Hugging Face model and stores it in the image, so deployed instances can start without downloading the model at runtime.

## 1. Install Required Tools

Install these on your computer first:

1. Git: https://git-scm.com/downloads
2. Python 3.14: https://www.python.org/downloads/
3. Google Cloud CLI: https://cloud.google.com/sdk/docs/install
4. Docker Desktop, only if you want to build and test the container locally: https://www.docker.com/products/docker-desktop/

After installing the Google Cloud CLI, close and reopen your terminal.

Check the tools:

```powershell
git --version
python --version
gcloud --version
docker --version
```

If `docker --version` fails, you can still deploy with Cloud Build by following the remote build steps below.

## 2. Sign In To Google Cloud

```powershell
gcloud auth login
gcloud auth application-default login
```

Create or choose a Google Cloud project in the Google Cloud Console:

```text
https://console.cloud.google.com/projectcreate
```

Set the project in your terminal:

```powershell
gcloud config set project YOUR_PROJECT_ID
```

Confirm it:

```powershell
gcloud config get-value project
```

## 3. Prepare Local Project

If you already have this repository locally, open a terminal in the repository root:

```powershell
cd C:\Users\troym\Git\PromptCompression
```

If you are starting from nothing, clone the repository first:

```powershell
git clone YOUR_REPOSITORY_URL PromptCompression
cd PromptCompression
```

Create a virtual environment and install development dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
```

Run the tests:

```powershell
python -m pytest
```

## 4. Configure Deployment Variables

These examples use PowerShell.

```powershell
$env:REGION="us-central1"
$env:SERVICE="prompt-compression"
$env:REPO="prompt-compression"
$env:PROJECT_ID="$(gcloud config get-value project)"
$env:IMAGE="$env:REGION-docker.pkg.dev/$env:PROJECT_ID/$env:REPO/$env:SERVICE`:latest"
```

Use a region close to your users. `us-central1` is a good default for a first deployment.

## 5. Enable Google Cloud Services

```powershell
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
```

## 6. Create Artifact Registry Repository

Run this once per project and region:

```powershell
gcloud artifacts repositories create $env:REPO `
  --repository-format=docker `
  --location=$env:REGION `
  --description="Prompt Compression images"
```

If the repository already exists, continue to the next step.

## 7. Build And Push The Image With Cloud Build

This is the recommended deployment path because it does not require local Docker.

```powershell
gcloud builds submit `
  --config cloudbuild.yaml `
  --substitutions _REGION=$env:REGION,_REPO=$env:REPO,_SERVICE=$env:SERVICE `
  .
```

The build can take several minutes because it installs PyTorch and downloads the compression model into the image.

## 8. Deploy To Cloud Run

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

Use `--no-allow-unauthenticated` instead of `--allow-unauthenticated` if the API should require Google IAM authentication.

The first compression request on a new instance may still take longer than later requests because the model is loaded into memory lazily.

## 9. Verify The Deployment

Get the deployed URL:

```powershell
$env:SERVICE_URL="$(gcloud run services describe $env:SERVICE --region $env:REGION --format='value(status.url)')"
$env:SERVICE_URL
```

Check health:

```powershell
curl "$env:SERVICE_URL/health"
```

Run the smoke test:

```powershell
$env:API_URL="$env:SERVICE_URL/compress"
python scripts\smoke_test.py
```

Open the API docs:

```text
https://YOUR_CLOUD_RUN_URL/docs
```

Open the UI:

```text
https://YOUR_CLOUD_RUN_URL/
```

## 10. Rebuild And Redeploy After Code Changes

From the repository root:

```powershell
python -m pytest
gcloud builds submit `
  --config cloudbuild.yaml `
  --substitutions _REGION=$env:REGION,_REPO=$env:REPO,_SERVICE=$env:SERVICE `
  .
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

## Optional: Build And Test Docker Locally

Start Docker Desktop first.

Build the image:

```powershell
docker build -t prompt-compression:local .
```

Run it:

```powershell
docker run --rm -p 8080:8080 prompt-compression:local
```

In another terminal:

```powershell
curl http://127.0.0.1:8080/health
$env:API_URL="http://127.0.0.1:8080/compress"
python scripts\smoke_test.py
```

Stop the running container with `Ctrl+C`.

## Optional: Keep One Instance Warm

For lower cold-start latency, redeploy with one minimum instance:

```powershell
gcloud run services update $env:SERVICE `
  --region $env:REGION `
  --min-instances 1
```

This improves latency but increases idle cost.

To return to scale-to-zero:

```powershell
gcloud run services update $env:SERVICE `
  --region $env:REGION `
  --min-instances 0
```

## Environment Variables

`COMPRESSOR_MODEL`: Hugging Face model name. Default is `microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank`.

`COMPRESSOR_DEVICE`: Use `cpu` for Cloud Run.

`COMPRESSOR_MIN_RATE`: Minimum token retention rate when `aggressiveness` is `1.0`. Default is `0.45`.

If you change `COMPRESSOR_MODEL`, rebuild the image so the new model is downloaded during the Docker build:

```powershell
gcloud builds submit `
  --config cloudbuild.yaml `
  --substitutions _REGION=$env:REGION,_REPO=$env:REPO,_SERVICE=$env:SERVICE,_COMPRESSOR_MODEL=YOUR_HUGGING_FACE_MODEL `
  .
```

## Troubleshooting

If `gcloud` is not recognized, reinstall the Google Cloud CLI and reopen the terminal.

If Cloud Build fails while downloading dependencies, rerun the build. Package downloads and model downloads can occasionally fail transiently.

If deployment fails with an Artifact Registry permission error, confirm that you are signed in and that your active project is correct:

```powershell
gcloud auth list
gcloud config get-value project
```

If Cloud Run returns `503` on `/compress`, inspect logs:

```powershell
gcloud run services logs read $env:SERVICE --region $env:REGION --limit 100
```

If requests time out on cold starts, deploy with `--min-instances 1` or increase timeout up to Cloud Run's maximum:

```powershell
gcloud run services update $env:SERVICE --region $env:REGION --timeout 900s
```
