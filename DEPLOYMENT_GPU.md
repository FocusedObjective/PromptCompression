# GPU Cloud Run Preparation

This runbook prepares the separate GPU container path. It does not replace the
CPU Dockerfile, CPU Cloud Build file, or CPU Cloud Run service.

The GPU image follows the Cloud Run GPU best-practice shape for this repository:

- Use a GPU framework base image instead of assembling CUDA in `python:slim`.
- Bake the current Hugging Face compression model into the image because it is
  small enough for the container-image loading path.
- Run the deployed service with `COMPRESSOR_DEVICE=cuda`.
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
$env:SERVICE="prompt-compression-gpu"
$env:REPO="prompt-compression"
$env:PROJECT_ID="$(gcloud config get-value project)"
$env:IMAGE="$env:REGION-docker.pkg.dev/$env:PROJECT_ID/$env:REPO/$env:SERVICE`:latest"
```

## Enable APIs

```powershell
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com
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
  --substitutions="_REGION=$env:REGION,_REPO=$env:REPO,_SERVICE=$env:SERVICE" `
  .
```

To test a different compression model, override `_COMPRESSOR_MODEL`:

```powershell
gcloud builds submit `
  --config cloudbuild.gpu.yaml `
  --substitutions="_REGION=$env:REGION,_REPO=$env:REPO,_SERVICE=$env:SERVICE,_COMPRESSOR_MODEL=YOUR_HUGGING_FACE_MODEL" `
  .
```

To test a different GPU base image, override `_GPU_BASE_IMAGE`:

```powershell
gcloud builds submit `
  --config cloudbuild.gpu.yaml `
  --substitutions="_REGION=$env:REGION,_REPO=$env:REPO,_SERVICE=$env:SERVICE,_GPU_BASE_IMAGE=YOUR_GPU_BASE_IMAGE" `
  .
```

## Prepared Deploy Command

Do not run this until you are ready to deploy.

The default prepared shape targets one NVIDIA L4 GPU. Google currently requires
at least 4 CPU and 16 GiB memory for L4 services, recommends 8 CPU and 32 GiB,
requires instance-based billing for GPU, and requires `--max-instances` to stay
within regional GPU quota.

```powershell
gcloud run deploy $env:SERVICE `
  --image $env:IMAGE `
  --region $env:REGION `
  --platform managed `
  --allow-unauthenticated `
  --port 8080 `
  --cpu 8 `
  --memory 32Gi `
  --gpu 1 `
  --gpu-type nvidia-l4 `
  --no-cpu-throttling `
  --no-gpu-zonal-redundancy `
  --max-instances 1 `
  --concurrency 1 `
  --timeout 300s `
  --set-env-vars "COMPRESSOR_DEVICE=cuda,COMPRESSOR_MIN_RATE=0.45,COMPRESSOR_PRELOAD_SLOTS=base"
```

Use `--no-allow-unauthenticated` instead of `--allow-unauthenticated` if the API
should require Google IAM authentication.

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

## Tuning Notes

Keep `--concurrency 1` for the first deployment. Increase it only after running
`scripts\benchmark_performance.py` against the deployed GPU service. If GPU
latency is stable and utilization is low, test small steps such as concurrency
`2`, `4`, and `8`.

If cold starts are unacceptable, set `--min-instances 1`. This keeps one GPU
instance warm and increases idle cost.
