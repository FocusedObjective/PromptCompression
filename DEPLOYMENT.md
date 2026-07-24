# Legacy CPU Development — Production Deployment Retired

Production uses one GPU-backed Cloud Run service:

- Cloud Run service: `prompt-compression`
- Artifact Registry image: `prompt-compression-gpu`
- Production runbook: `DEPLOYMENT_GPU.md`

There is no production CPU service. Never deploy `Dockerfile` or
`cloudbuild.yaml` to `prompt-compression`; doing so would replace the GPU
revision behind the existing domain.

`Dockerfile` and `cloudbuild.yaml` remain only for local CPU development and
historical reproducibility. The CPU Cloud Build image is deliberately named
`prompt-compression-cpu-retired` so it cannot be mistaken for the production
image.

## Local CPU Development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
python -m pytest
```

Run directly:

```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
```

Or run the legacy CPU container locally:

```powershell
docker build -t prompt-compression-cpu:local .
docker run --rm -p 8080:8080 `
  -e COMPRESSOR_DEVICE=cpu `
  prompt-compression-cpu:local
```

For any Google Cloud build, deploy, rollback, environment update, GPU sizing,
traffic change, or production verification, stop here and follow
`DEPLOYMENT_GPU.md`.
